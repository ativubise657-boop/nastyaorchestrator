"""
CRUD для чат-сессий (ChatGPT-style разделение чатов).
Каждая сессия изолирует историю сообщений внутри проекта.
"""
import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from backend.models import ChatSession, ChatSessionCreate, ChatSessionUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_deps(request: Request):
    """Достаём state и queue из app.state (единый паттерн по всему backend)."""
    return request.app.state.db, request.app.state.queue


def _row_to_session(row) -> ChatSession:
    """Конвертируем sqlite row → Pydantic модель."""
    d = dict(row)
    return ChatSession(
        id=d["id"],
        project_id=d["project_id"],
        title=d["title"],
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
        message_count=d.get("message_count", 0),
    )


# ---------------------------------------------------------------------------
# POST /api/chat/sessions — создать новую сессию
# ---------------------------------------------------------------------------

@router.post("/sessions", response_model=ChatSession, status_code=201)
async def create_session(body: ChatSessionCreate, request: Request):
    """
    Создаёт новую чат-сессию для проекта.
    id = uuid4, created_at = updated_at = now, message_count = 0.
    """
    state, _ = _get_deps(request)

    # Проверяем что проект существует
    project = await state.afetchone("SELECT id FROM projects WHERE id = ?", (body.project_id,))
    if not project:
        raise HTTPException(status_code=404, detail=f"Проект {body.project_id} не найден")

    session_id = str(uuid.uuid4())
    now = _now_iso()

    await state.aexecute(
        """
        INSERT INTO chat_sessions (id, project_id, title, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, body.project_id, body.title, now, now),
    )
    await state.acommit()

    logger.info("Создана сессия %s для проекта %s ('%s')", session_id, body.project_id, body.title)
    return ChatSession(
        id=session_id,
        project_id=body.project_id,
        title=body.title,
        created_at=datetime.fromisoformat(now),
        updated_at=datetime.fromisoformat(now),
        message_count=0,
    )


# ---------------------------------------------------------------------------
# GET /api/chat/sessions/{project_id} — список сессий проекта
# ---------------------------------------------------------------------------

@router.get("/sessions/{project_id}", response_model=list[ChatSession])
async def list_sessions(project_id: str, request: Request):
    """
    Возвращает список сессий проекта, отсортированных по updated_at DESC.
    message_count вычисляется подзапросом (не хранится в таблице).
    """
    state, _ = _get_deps(request)

    # Проверяем проект
    project = await state.afetchone("SELECT id FROM projects WHERE id = ?", (project_id,))
    if not project:
        raise HTTPException(status_code=404, detail=f"Проект {project_id} не найден")

    rows = await state.afetchall(
        """
        SELECT
            s.id,
            s.project_id,
            s.title,
            s.created_at,
            s.updated_at,
            (SELECT COUNT(*) FROM chat_messages WHERE session_id = s.id) AS message_count
        FROM chat_sessions s
        WHERE s.project_id = ?
        ORDER BY s.updated_at DESC
        """,
        (project_id,),
    )
    return [_row_to_session(r) for r in rows]


# ---------------------------------------------------------------------------
# PATCH /api/chat/sessions/{session_id} — переименовать сессию
# ---------------------------------------------------------------------------

@router.patch("/sessions/{session_id}", response_model=ChatSession)
async def rename_session(session_id: str, body: ChatSessionUpdate, request: Request):
    """Переименовать сессию. updated_at обновляется до now."""
    state, _ = _get_deps(request)

    row = await state.afetchone("SELECT * FROM chat_sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Сессия {session_id} не найдена")

    now = _now_iso()
    await state.aexecute(
        "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
        (body.title, now, session_id),
    )
    await state.acommit()

    logger.info("Сессия %s переименована в '%s'", session_id, body.title)
    return ChatSession(
        id=row["id"],
        project_id=row["project_id"],
        title=body.title,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(now),
        message_count=0,  # клиент знает счётчик из list_sessions
    )


# ---------------------------------------------------------------------------
# DELETE /api/chat/sessions/{session_id} — удалить сессию каскадом
# ---------------------------------------------------------------------------

@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    """
    Каскадно удаляет сессию:
    1. Собирает session-scoped документы (is_scratch=1 или session_id IS NOT NULL).
    2. Удаляет файлы на диске для session-scoped документов.
    3. Удаляет chat_messages, documents, tasks, саму запись chat_sessions.
    Возвращает {ok, deleted_messages, deleted_documents}.
    """
    state, _ = _get_deps(request)

    row = await state.afetchone("SELECT * FROM chat_sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Сессия {session_id} не найдена")

    # Блокируем удаление если в сессии есть активная/ожидающая задача
    # (worker держит её в памяти — тихое удаление приведёт к потере ответа)
    running = await state.afetchone(
        "SELECT id FROM tasks WHERE session_id = ? AND status IN ('running', 'queued') LIMIT 1",
        (session_id,),
    )
    if running:
        raise HTTPException(
            status_code=409,
            detail="Нельзя удалить чат: сейчас выполняется задача. Дождись ответа или нажми Стоп.",
        )

    # Собираем session-scoped документы для удаления файлов с диска
    # (project-wide документы с session_id IS NULL — не трогаем!)
    doc_rows = await state.afetchall(
        "SELECT id, path FROM documents WHERE session_id = ?",
        (session_id,),
    )

    # Удаляем физические файлы — fail тихо (файл мог быть уже удалён)
    deleted_files = 0
    for doc in doc_rows:
        file_path = doc["path"]
        if file_path:
            try:
                os.unlink(file_path)
                deleted_files += 1
                # Также пробуем удалить .md-версию (результат парсинга)
                md_path = file_path.rsplit(".", 1)[0] + ".md" if "." in file_path else file_path + ".md"
                if os.path.exists(md_path):
                    os.unlink(md_path)
            except OSError:
                pass  # файл уже удалён или нет прав — пропускаем

    # Считаем что будет удалено (для отчёта)
    msg_count_row = await state.afetchone(
        "SELECT COUNT(*) AS cnt FROM chat_messages WHERE session_id = ?", (session_id,)
    )
    deleted_messages = msg_count_row["cnt"] if msg_count_row else 0
    deleted_documents = len(doc_rows)

    # Удаляем всё session-scoped
    await state.aexecute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
    await state.aexecute("DELETE FROM documents WHERE session_id = ?", (session_id,))
    await state.aexecute("DELETE FROM tasks WHERE session_id = ?", (session_id,))
    await state.aexecute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    await state.acommit()

    logger.info(
        "Сессия %s удалена: %d сообщений, %d документов (%d файлов на диске)",
        session_id, deleted_messages, deleted_documents, deleted_files,
    )
    return {
        "ok": True,
        "deleted_messages": deleted_messages,
        "deleted_documents": deleted_documents,
    }
