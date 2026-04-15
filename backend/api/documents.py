"""
Загрузка и просмотр документов проекта.
Файлы хранятся в data/documents/{project_id}/
"""
import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, Response

from backend.core.config import DOCUMENTS_DIR
from backend.core.file_types import CONVERTIBLE_EXTS as CONVERTIBLE_EXTENSIONS, TEXT_EXTS
from backend.core.helpers import ensure_project, now_iso, COMMON_PROJECT
from backend.models import Document, DocumentCreate, Folder, FolderCreate, FolderRename, DocumentMove, DocumentRename

logger = logging.getLogger(__name__)
router = APIRouter()


def _try_markitdown(file_path: Path, filename: str) -> str | None:
    """Уровень 1: markitdown (PDF через pdfminer внутри, DOCX/XLSX/PPTX/HTML напрямую)."""
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(str(file_path))
        if result and result.text_content and result.text_content.strip():
            return result.text_content
        logger.info("markitdown вернул пустой результат для %s", filename)
    except Exception as e:
        logger.warning("markitdown ошибка для %s: %s", filename, e)
    return None


def _try_pdfminer(file_path: Path, filename: str) -> str | None:
    """Уровень 2: pdfminer напрямую. Иногда работает где markitdown-обёртка споткнулась."""
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(str(file_path))
        if text and text.strip():
            return text
        logger.info("pdfminer вернул пустой текст для %s (вероятно скан без text layer)", filename)
    except Exception as e:
        logger.warning("pdfminer ошибка для %s: %s", filename, e)
    return None


def _try_aitunnel_pdf(file_path: Path, filename: str) -> str | None:
    """Уровень 3: AITunnel → Gemini 2.5 Flash. OCR для сканов, таблицы, формулы."""
    try:
        from backend.core.aitunnel_pdf import parse_pdf
        text = parse_pdf(file_path)
        if text and text.strip():
            return text
        logger.info("AITunnel/Gemini не справился с %s", filename)
    except Exception as e:
        logger.warning("AITunnel/Gemini ошибка для %s: %s", filename, e)
    return None


def _convert_to_text(file_path: Path, filename: str) -> Path | None:
    """Конвертирует документ в markdown при загрузке. Сохраняет .md файл рядом.

    Fix 4.2A: перед каскадом — check content-hash кеша. Тот же PDF загружен
    второй раз → мгновенный cache hit без вызова парсеров/API.

    Каскад для PDF:
      1. markitdown (быстро, локально, text-layer PDF)
      2. pdfminer напрямую (иногда работает где markitdown-обёртка падает)
      3. AITunnel → Gemini 2.5 Flash (OCR для сканов, таблицы, формулы)

    Для остальных (xlsx, docx, pptx, html): только markitdown.
    Возвращает путь к .md файлу или None если ни один уровень не сработал.
    """
    from backend.core import parse_cache

    ext = Path(filename).suffix.lower()
    if ext not in CONVERTIBLE_EXTENSIONS:
        return None

    text_path = file_path.with_suffix(".md")

    # Уровень 0: content-hash кеш
    cached = parse_cache.get(file_path)
    if cached:
        text_path.write_text(cached, encoding="utf-8")
        logger.info("Документ %s → cache hit (%d символов)", filename, len(cached))
        return text_path

    # Уровень 1: markitdown
    text = _try_markitdown(file_path, filename)
    if text:
        text_path.write_text(text, encoding="utf-8")
        parse_cache.put(file_path, text)
        logger.info("Документ %s → markitdown (%d символов)", filename, len(text))
        return text_path

    # Дальше только PDF — для DOCX/XLSX/HTML других парсеров нет
    if ext != ".pdf":
        logger.warning("Не удалось распарсить %s (формат %s, fallback'ов нет)", filename, ext)
        return None

    # Уровень 2: pdfminer
    text = _try_pdfminer(file_path, filename)
    if text:
        text_path.write_text(text, encoding="utf-8")
        parse_cache.put(file_path, text)
        logger.info("PDF %s → pdfminer (%d символов)", filename, len(text))
        return text_path

    # Уровень 3: AITunnel → Gemini (OCR)
    text = _try_aitunnel_pdf(file_path, filename)
    if text:
        text_path.write_text(text, encoding="utf-8")
        parse_cache.put(file_path, text)
        logger.info("PDF %s → AITunnel/Gemini (%d символов)", filename, len(text))
        return text_path

    logger.warning("Все 3 парсера упали на PDF %s — content пустой", filename)
    return None


def _parse_and_status(file_path: Path, filename: str) -> tuple[Path | None, str, str]:
    """Парсит документ и возвращает (md_path|None, parse_status, parse_error).

    parse_status:
      - 'parsed'  — текст извлечён, .md файл создан
      - 'failed'  — конвертируемый формат, но все парсеры упали (content=None)
      - 'skipped' — формат не поддерживается (png, jpg, zip, ...) — парсить и не пытались
    """
    ext = Path(filename).suffix.lower()
    if ext not in CONVERTIBLE_EXTENSIONS:
        return (None, 'skipped', '')
    try:
        text_path = _convert_to_text(file_path, filename)
        if text_path:
            return (text_path, 'parsed', '')
        return (
            None,
            'failed',
            'Ни один парсер не смог извлечь текст (markitdown → pdfminer → AITunnel)',
        )
    except Exception as e:
        logger.exception("Непойманное исключение при парсинге %s", filename)
        return (None, 'failed', f"{type(e).__name__}: {e}"[:500])


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
    if ext in TEXT_EXTS and p.exists() and p.stat().st_size < 500_000:
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
        SELECT id, project_id, filename, path, size, content_type, folder_id,
               COALESCE(parse_status, 'skipped') AS parse_status,
               COALESCE(parse_error, '') AS parse_error,
               created_at
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

    # Фильтруем scratch — это одноразовые картинки из буфера/drag&drop,
    # не показываем их в списке документов проекта.
    rows = state.fetchall(
        """
        SELECT id, project_id, filename, path, size, content_type, folder_id,
               COALESCE(parse_status, 'skipped') AS parse_status,
               COALESCE(parse_error, '') AS parse_error,
               created_at
        FROM documents
        WHERE project_id = ? AND COALESCE(is_scratch, 0) = 0
        ORDER BY created_at DESC
        """,
        (project_id,),
    )
    return [Document(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# POST /api/documents/{project_id}/upload
# ---------------------------------------------------------------------------

async def _background_parse(
    app_state,
    doc_id: str,
    project_id: str,
    file_path: Path,
    filename: str,
) -> None:
    """Фоновый парсинг (Fix 4.1A): upload возвращается мгновенно с parse_status='pending',
    здесь гоняем каскад парсинга в thread-pool, обновляем БД, пушим SSE event.
    Ошибки ловим — background task не должен падать и тянуть за собой процесс."""
    loop = asyncio.get_event_loop()
    try:
        text_path, status, error = await loop.run_in_executor(
            None, _parse_and_status, file_path, filename
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Фоновый парсинг упал для %s", filename)
        status, error, text_path = "failed", f"{type(exc).__name__}: {exc}"[:500], None

    # UPDATE БД — если документ удалили параллельно, просто логируем
    try:
        app_state.db.execute(
            "UPDATE documents SET parse_status = ?, parse_error = ? WHERE id = ?",
            (status, error, doc_id),
        )
        app_state.db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("background_parse UPDATE failed for %s: %s", doc_id, exc)

    # SSE — фронт обновит badge без F5
    try:
        await app_state.publish_event(
            "document_parsed",
            {
                "id": doc_id,
                "project_id": project_id,
                "parse_status": status,
                "parse_error": error,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("background_parse SSE publish failed: %s", exc)

    logger.info(
        "Фоновый парсинг %s: status=%s, filename=%s",
        doc_id, status, filename,
    )


@router.post("/{project_id}/upload", response_model=Document, status_code=201)
async def upload_document(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    folder_id: str | None = None,
    is_scratch: bool = False,
):
    """
    Загружает файл на сервер, сохраняет запись в БД.
    Имя файла сохраняется как есть, конфликты разрешаются добавлением uuid-префикса.
    folder_id — опциональная папка, куда загрузить документ.
    is_scratch=true — одноразовый файл (картинка из буфера/drag-n-drop),
    не показывается в списке документов, удаляется после выполнения задачи.

    Fix 4.1A: парсинг (markitdown/pdfminer/AITunnel) запускается в BackgroundTask.
    Upload возвращает документ с parse_status='pending' мгновенно. Фронт узнаёт
    о завершении парсинга через SSE-событие 'document_parsed'.
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

    # Fix 4.1A: начальный parse_status зависит от формата.
    # Конвертируемый (PDF/DOCX/...) → pending (парсер отработает в фоне).
    # Остальное (png/zip/...) → skipped сразу, background не нужен.
    ext = Path(original_name).suffix.lower()
    if ext in CONVERTIBLE_EXTENSIONS:
        initial_status, initial_error = "pending", ""
    else:
        initial_status, initial_error = "skipped", ""

    state.execute(
        """
        INSERT INTO documents (id, project_id, filename, path, size, content_type, folder_id, is_scratch, parse_status, parse_error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, project_id, original_name, str(file_path), file_size, content_type, folder_id, 1 if is_scratch else 0, initial_status, initial_error, now),
    )
    state.commit()

    # Парсинг запускаем в фоне — upload НЕ ждёт его
    if initial_status == "pending":
        background_tasks.add_task(
            _background_parse,
            request.app.state,
            doc_id,
            project_id,
            file_path,
            original_name,
        )

    logger.info(
        "Документ %s загружен в проект %s (%d bytes, parse=%s, folder=%s, scratch=%s)",
        original_name, project_id, file_size, initial_status, folder_id, is_scratch,
    )
    return Document(
        id=doc_id,
        project_id=project_id,
        filename=original_name,
        path=str(file_path),
        size=file_size,
        content_type=content_type,
        folder_id=folder_id,
        parse_status=initial_status,
        parse_error=initial_error,
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
