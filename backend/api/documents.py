"""
Загрузка и просмотр документов проекта.
Файлы хранятся в data/documents/{project_id}/
"""
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, Response

from backend.core.config import DOCUMENTS_DIR
from backend.core.helpers import ensure_project, now_iso, COMMON_PROJECT
from backend.models import Document, DocumentCreate, Folder, FolderCreate, FolderRename, DocumentMove, DocumentRename

logger = logging.getLogger(__name__)
router = APIRouter()

# Расширения которые markitdown может конвертировать в текст
CONVERTIBLE_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".xls", ".html", ".htm"}


def _convert_to_text(
    file_path: Path, filename: str, *, gemini_api_key: str = ""
) -> Path | None:
    """Конвертирует документ в markdown при загрузке. Сохраняет .md файл рядом.

    Для PDF: сначала Gemini API (OCR, таблицы, формулы), fallback на markitdown.
    Для остальных (xlsx, docx, pptx, html): markitdown.

    Возвращает путь к .md файлу или None если конвертация не нужна/не удалась.
    """
    ext = Path(filename).suffix.lower()
    if ext not in CONVERTIBLE_EXTENSIONS:
        return None

    text_path = file_path.with_suffix(".md")

    # PDF: сначала Gemini (лучше качество — OCR, таблицы, формулы)
    if ext == ".pdf" and gemini_api_key:
        try:
            from backend.core.gemini_pdf import parse_pdf
            text = parse_pdf(file_path, gemini_api_key)
            if text:
                text_path.write_text(text, encoding="utf-8")
                logger.info("PDF %s → Gemini (%d символов)", filename, len(text))
                return text_path
            logger.info("Gemini не справился с %s, fallback на markitdown", filename)
        except Exception as exc:
            logger.warning("Gemini PDF ошибка для %s: %s, fallback на markitdown", filename, exc)

    # Markitdown для всех типов (и fallback для PDF)
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(str(file_path))
        if result and result.text_content:
            text_path.write_text(result.text_content, encoding="utf-8")
            logger.info("Документ %s → markitdown (%d символов)", filename, len(result.text_content))
            return text_path
        else:
            logger.warning("markitdown вернул пустой результат для %s", filename)
            return None
    except Exception as e:
        logger.warning("Ошибка конвертации %s: %s", filename, e)
        return None


def _get_text_content(file_path: str, filename: str) -> str | None:
    """Получить текстовое содержимое документа.

    1. Если есть .md версия (сконвертирована при загрузке) — вернуть её
    2. Если текстовый файл — прочитать напрямую
    3. Иначе — None
    """
    p = Path(file_path)

    # Проверяем есть ли .md версия (от markitdown)
    md_path = p.with_suffix(".md")
    if md_path.exists():
        try:
            return md_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # Текстовые файлы — читаем напрямую
    ext = Path(filename).suffix.lower()
    text_exts = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".log"}
    if ext in text_exts and p.exists() and p.stat().st_size < 500_000:
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            if "\x00" not in content:
                return content
        except Exception:
            pass

    return None


def _project_dir(project_id: str) -> Path:
    """Путь к директории документов проекта."""
    return Path(DOCUMENTS_DIR) / project_id


# ---------------------------------------------------------------------------
# GET /api/documents/all — все документы по всем проектам
# ---------------------------------------------------------------------------

@router.get("/all")
async def list_all_documents(request: Request):
    """Все документы из всех проектов (включая __common__)."""
    state = request.app.state.db
    rows = state.fetchall(
        """
        SELECT id, project_id, filename, path, size, content_type, folder_id, created_at
        FROM documents
        ORDER BY created_at DESC
        """
    )
    folders_rows = state.fetchall(
        """
        SELECT id, project_id, name, parent_id, created_at
        FROM folders
        ORDER BY name
        """
    )
    return {
        "documents": [Document(**dict(r)) for r in rows],
        "folders": [Folder(**dict(r)) for r in folders_rows],
    }


# ---------------------------------------------------------------------------
# GET /api/documents/{project_id}
# ---------------------------------------------------------------------------

@router.get("/{project_id}", response_model=list[Document])
async def list_documents(project_id: str, request: Request):
    """Список документов проекта."""
    state = request.app.state.db
    ensure_project(state, project_id)

    rows = state.fetchall(
        """
        SELECT id, project_id, filename, path, size, content_type, folder_id, created_at
        FROM documents
        WHERE project_id = ?
        ORDER BY created_at DESC
        """,
        (project_id,),
    )
    return [Document(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# POST /api/documents/{project_id}/upload
# ---------------------------------------------------------------------------

@router.post("/{project_id}/upload", response_model=Document, status_code=201)
async def upload_document(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    folder_id: str | None = None,
):
    """
    Загружает файл на сервер, сохраняет запись в БД.
    Имя файла сохраняется как есть, конфликты разрешаются добавлением uuid-префикса.
    folder_id — опциональная папка, куда загрузить документ.
    """
    state = request.app.state.db
    ensure_project(state, project_id)

    # Проверяем существование папки если указана
    if folder_id:
        folder_row = state.fetchone(
            "SELECT id FROM folders WHERE id = ? AND project_id = ?",
            (folder_id, project_id),
        )
        if not folder_row:
            raise HTTPException(status_code=404, detail=f"Папка {folder_id} не найдена")

    # Создаём директорию если её нет
    doc_dir = _project_dir(project_id)
    doc_dir.mkdir(parents=True, exist_ok=True)

    # Уникальный id документа
    doc_id = str(uuid.uuid4())

    # Безопасное имя файла — оригинальное имя, но с uuid-префиксом во избежание коллизий
    original_name = file.filename or "upload"
    safe_filename = f"{doc_id[:8]}_{original_name}"
    file_path = doc_dir / safe_filename

    # Читаем и сохраняем файл
    content = await file.read()
    file_size = len(content)
    file_path.write_bytes(content)

    now = now_iso()
    content_type = file.content_type or ""

    # Конвертируем в текст при загрузке (PDF, DOCX, XLSX → Markdown)
    # Gemini API key: из remote-config (ротация без ребилда) → env fallback
    gemini_key = ""
    try:
        rc = getattr(request.app.state, "remote_config", {}) or {}
        gemini_key = rc.get("gemini_api_key", "")
    except Exception:
        pass
    if not gemini_key:
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
    text_path = _convert_to_text(file_path, original_name, gemini_api_key=gemini_key)

    state.execute(
        """
        INSERT INTO documents (id, project_id, filename, path, size, content_type, folder_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, project_id, original_name, str(file_path), file_size, content_type, folder_id, now),
    )
    state.commit()

    logger.info("Документ %s загружен в проект %s (%d bytes, text=%s, folder=%s)", original_name, project_id, file_size, bool(text_path), folder_id)
    return Document(
        id=doc_id,
        project_id=project_id,
        filename=original_name,
        path=str(file_path),
        size=file_size,
        content_type=content_type,
        folder_id=folder_id,
        created_at=datetime.fromisoformat(now),
    )


# ---------------------------------------------------------------------------
# POST /api/documents/{project_id}/create — создание документа из текста
# ---------------------------------------------------------------------------

@router.post("/{project_id}/create", response_model=Document, status_code=201)
async def create_document(project_id: str, body: DocumentCreate, request: Request):
    """
    Создаёт документ из текстового содержимого (без загрузки файла).
    Используется worker-ом когда Codex генерирует документы в ответе.
    Публикует SSE-событие document_created для обновления панели на фронте.
    """
    state = request.app.state.db
    ensure_project(state, project_id)

    # Проверяем папку если указана
    if body.folder_id:
        folder_row = state.fetchone(
            "SELECT id FROM folders WHERE id = ? AND project_id = ?",
            (body.folder_id, project_id),
        )
        if not folder_row:
            raise HTTPException(status_code=404, detail=f"Папка {body.folder_id} не найдена")

    doc_dir = _project_dir(project_id)
    doc_dir.mkdir(parents=True, exist_ok=True)

    doc_id = str(uuid.uuid4())
    safe_filename = f"{doc_id[:8]}_{body.filename}"
    file_path = doc_dir / safe_filename

    # Записываем содержимое
    file_path.write_text(body.content, encoding="utf-8")
    file_size = len(body.content.encode("utf-8"))

    now = now_iso()
    content_type = "text/markdown" if body.filename.endswith(".md") else "text/plain"

    state.execute(
        """
        INSERT INTO documents (id, project_id, filename, path, size, content_type, folder_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, project_id, body.filename, str(file_path), file_size, content_type, body.folder_id, now),
    )
    state.commit()

    doc = Document(
        id=doc_id,
        project_id=project_id,
        filename=body.filename,
        path=str(file_path),
        size=file_size,
        content_type=content_type,
        folder_id=body.folder_id,
        created_at=datetime.fromisoformat(now),
    )

    # SSE-уведомление — фронтенд обновит панель документов
    await request.app.state.publish_event(
        "document_created",
        {
            "id": doc_id,
            "project_id": project_id,
            "filename": body.filename,
            "size": file_size,
            "folder_id": body.folder_id,
        },
    )

    logger.info("Документ %s создан из текста в проекте %s (%d bytes, folder=%s)", body.filename, project_id, file_size, body.folder_id)
    return doc


# ---------------------------------------------------------------------------
# Папки документов (ВАЖНО: до /{project_id}/{doc_id} чтобы /folders не матчился как doc_id)
# ---------------------------------------------------------------------------


@router.get("/{project_id}/folders", response_model=list[Folder])
async def list_folders(project_id: str, request: Request):
    """Список всех папок проекта (flat list, фронт строит дерево)."""
    state = request.app.state.db
    ensure_project(state, project_id)

    rows = state.fetchall(
        """
        SELECT id, project_id, name, parent_id, created_at
        FROM folders
        WHERE project_id = ?
        ORDER BY name
        """,
        (project_id,),
    )
    return [Folder(**dict(r)) for r in rows]


@router.post("/{project_id}/folders", response_model=Folder, status_code=201)
async def create_folder(project_id: str, body: FolderCreate, request: Request):
    """Создать папку в проекте."""
    state = request.app.state.db
    ensure_project(state, project_id)

    # Проверяем родительскую папку если указана
    if body.parent_id:
        parent = state.fetchone(
            "SELECT id FROM folders WHERE id = ? AND project_id = ?",
            (body.parent_id, project_id),
        )
        if not parent:
            raise HTTPException(status_code=404, detail=f"Родительская папка {body.parent_id} не найдена")

    folder_id = str(uuid.uuid4())
    now = now_iso()

    state.execute(
        """
        INSERT INTO folders (id, project_id, name, parent_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (folder_id, project_id, body.name, body.parent_id, now),
    )
    state.commit()

    logger.info("Папка '%s' создана в проекте %s (parent=%s)", body.name, project_id, body.parent_id)
    return Folder(
        id=folder_id,
        project_id=project_id,
        name=body.name,
        parent_id=body.parent_id,
        created_at=datetime.fromisoformat(now),
    )


@router.patch("/{project_id}/folders/{folder_id}", response_model=Folder)
async def rename_folder(project_id: str, folder_id: str, body: FolderRename, request: Request):
    """Переименовать папку."""
    state = request.app.state.db
    ensure_project(state, project_id)

    row = state.fetchone(
        "SELECT * FROM folders WHERE id = ? AND project_id = ?",
        (folder_id, project_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Папка {folder_id} не найдена")

    state.execute(
        "UPDATE folders SET name = ? WHERE id = ?",
        (body.name, folder_id),
    )
    state.commit()

    logger.info("Папка %s переименована в '%s'", folder_id, body.name)
    return Folder(
        id=row["id"],
        project_id=row["project_id"],
        name=body.name,
        parent_id=row["parent_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


@router.delete("/{project_id}/folders/{folder_id}", status_code=204)
async def delete_folder(project_id: str, folder_id: str, request: Request):
    """Удалить папку. Все документы и подпапки перемещаются в родительскую (или корень)."""
    state = request.app.state.db
    ensure_project(state, project_id)

    row = state.fetchone(
        "SELECT * FROM folders WHERE id = ? AND project_id = ?",
        (folder_id, project_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Папка {folder_id} не найдена")

    parent_id = row["parent_id"]  # куда переместить содержимое (None = корень)

    # Перемещаем документы из этой папки в родительскую
    state.execute(
        "UPDATE documents SET folder_id = ? WHERE folder_id = ?",
        (parent_id, folder_id),
    )
    # Перемещаем подпапки в родительскую
    state.execute(
        "UPDATE folders SET parent_id = ? WHERE parent_id = ?",
        (parent_id, folder_id),
    )
    # Удаляем саму папку
    state.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    state.commit()

    logger.info("Папка %s удалена, содержимое перемещено в parent=%s", folder_id, parent_id)


# ---------------------------------------------------------------------------
# Перемещение и переименование документов
# ---------------------------------------------------------------------------


@router.patch("/{project_id}/{doc_id}/move", response_model=Document)
async def move_document(project_id: str, doc_id: str, body: DocumentMove, request: Request):
    """Переместить документ в другую папку (или в корень если folder_id=null)."""
    state = request.app.state.db
    ensure_project(state, project_id)

    row = state.fetchone(
        "SELECT * FROM documents WHERE id = ? AND project_id = ?",
        (doc_id, project_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    # Проверяем целевую папку если указана
    if body.folder_id:
        folder_row = state.fetchone(
            "SELECT id FROM folders WHERE id = ? AND project_id = ?",
            (body.folder_id, project_id),
        )
        if not folder_row:
            raise HTTPException(status_code=404, detail=f"Папка {body.folder_id} не найдена")

    state.execute(
        "UPDATE documents SET folder_id = ? WHERE id = ?",
        (body.folder_id, doc_id),
    )
    state.commit()

    logger.info("Документ %s перемещён в папку %s", doc_id, body.folder_id)
    return Document(
        id=row["id"],
        project_id=row["project_id"],
        filename=row["filename"],
        path=row["path"],
        size=row["size"],
        content_type=row["content_type"],
        folder_id=body.folder_id,
        created_at=datetime.fromisoformat(row["created_at"]),
    )


@router.patch("/{project_id}/{doc_id}/rename", response_model=Document)
async def rename_document(project_id: str, doc_id: str, body: DocumentRename, request: Request):
    """Переименовать документ (в БД и физический файл на диске)."""
    state = request.app.state.db
    ensure_project(state, project_id)

    row = state.fetchone(
        "SELECT * FROM documents WHERE id = ? AND project_id = ?",
        (doc_id, project_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    old_path = Path(row["path"])
    old_filename = row["filename"]
    new_filename = body.filename.strip()

    if not new_filename:
        raise HTTPException(status_code=400, detail="Имя файла не может быть пустым")

    # Формируем новый путь на диске (сохраняем uuid-префикс)
    if old_path.exists():
        # Имя на диске: {uuid8}_{original_name} → меняем original_name
        disk_name = old_path.name
        # Выделяем uuid-префикс (первые 8 символов id + подчёркивание)
        prefix = doc_id[:8] + "_"
        if disk_name.startswith(prefix):
            new_disk_name = prefix + new_filename
        else:
            new_disk_name = prefix + new_filename
        new_path = old_path.parent / new_disk_name
        old_path.rename(new_path)

        # Также переименовываем .md версию если есть
        old_md = old_path.with_suffix(".md")
        if old_md.exists():
            new_md = new_path.with_suffix(".md")
            old_md.rename(new_md)
    else:
        new_path = old_path  # файл не найден на диске, обновляем только БД

    state.execute(
        "UPDATE documents SET filename = ?, path = ? WHERE id = ?",
        (new_filename, str(new_path), doc_id),
    )
    state.commit()

    logger.info("Документ %s переименован: '%s' → '%s'", doc_id, old_filename, new_filename)
    return Document(
        id=row["id"],
        project_id=row["project_id"],
        filename=new_filename,
        path=str(new_path),
        size=row["size"],
        content_type=row["content_type"],
        folder_id=row["folder_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ---------------------------------------------------------------------------
# GET /api/documents/{project_id}/{doc_id} — ПОСЛЕ folders чтобы /folders не перехватывался
# ---------------------------------------------------------------------------

@router.get("/{project_id}/{doc_id}")
async def get_document(project_id: str, doc_id: str, request: Request):
    """Возвращает содержимое документа."""
    state = request.app.state.db
    ensure_project(state, project_id)

    row = state.fetchone(
        "SELECT * FROM documents WHERE id = ? AND project_id = ?",
        (doc_id, project_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    file_path = Path(row["path"])
    if not file_path.exists():
        logger.error("Файл документа %s не найден на диске: %s", doc_id, file_path)
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    return FileResponse(
        path=str(file_path),
        filename=row["filename"],
        media_type=row["content_type"] or "application/octet-stream",
    )


# ---------------------------------------------------------------------------
# DELETE /api/documents/{project_id}/{doc_id}
# ---------------------------------------------------------------------------

@router.delete("/{project_id}/{doc_id}", status_code=204)
async def delete_document(project_id: str, doc_id: str, request: Request):
    """Удаляет документ из БД и с диска."""
    state = request.app.state.db
    ensure_project(state, project_id)

    row = state.fetchone(
        "SELECT * FROM documents WHERE id = ? AND project_id = ?",
        (doc_id, project_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Документ {doc_id} не найден")

    file_path = Path(row["path"])
    if file_path.exists():
        file_path.unlink()
        logger.info("Файл документа удалён: %s", file_path)

    state.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    state.commit()
    logger.info("Документ %s удалён из проекта %s", doc_id, project_id)
