"""
Чат-роутер: приём сообщений от пользователя, запись в очередь задач,
отдача истории и статуса задачи.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.models import (
    ChatAttachment,
    ChatMessage,
    ChatSendRequest,
    ChatSendResponse,
    Task,
    TaskStatus,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_deps(request: Request):
    """Достаём state и queue из app.state."""
    return request.app.state.db, request.app.state.queue


# ---------------------------------------------------------------------------
# POST /api/chat/send
# ---------------------------------------------------------------------------

@router.post("/send", response_model=ChatSendResponse)
async def send_message(body: ChatSendRequest, request: Request):
    """
    Принимает сообщение от пользователя:
    1. Проверяет что проект существует и сессия принадлежит этому проекту.
    2. Создаёт ChatMessage(role=user) с session_id.
    3. Создаёт Task(status=queued) с session_id и добавляет в очередь.
    4. Обновляет updated_at у сессии (активность).
    5. Для clipboard-документов (is_scratch=1) проставляет session_id.
    6. Возвращает {task_id, message_id}.
    """
    state, queue = _get_deps(request)

    # Проверяем существование проекта
    project = await state.afetchone("SELECT id FROM projects WHERE id = ?", (body.project_id,))
    if not project:
        raise HTTPException(status_code=404, detail=f"Проект {body.project_id} не найден")

    # Проверяем сессию и соответствие проекту
    session = await state.afetchone("SELECT id, project_id FROM chat_sessions WHERE id = ?", (body.session_id,))
    if not session:
        raise HTTPException(status_code=404, detail=f"Сессия {body.session_id} не найдена")
    if session["project_id"] != body.project_id:
        raise HTTPException(status_code=400, detail="Сессия принадлежит другому проекту")

    now = _now_iso()

    # Создаём сообщение пользователя с привязкой к сессии
    message_id = str(uuid.uuid4())
    attachments_json = json.dumps(
        [a.model_dump() for a in body.attachments], ensure_ascii=False
    ) if body.attachments else ""
    await state.aexecute(
        """
        INSERT INTO chat_messages (id, project_id, role, content, task_id, attachments, created_at, session_id)
        VALUES (?, ?, 'user', ?, NULL, ?, ?, ?)
        """,
        (message_id, body.project_id, body.message, attachments_json, now, body.session_id),
    )
    # Коммитим chat_message ДО вызова queue.enqueue — queue использует sync execute
    # в основном потоке, и открытая WAL-транзакция из thread pool заблокирует её.
    await state.acommit()

    # Создаём задачу и ставим в очередь.
    # attachment_document_ids — чтобы /queue/next пометил эти файлы requested=true
    # автоматически (они явно приложены Настей к этому сообщению).
    attachment_doc_ids = [a.document_id for a in body.attachments if a.document_id]
    task_id = queue.enqueue(
        project_id=body.project_id,
        prompt=body.message,
        mode=body.mode,
        model=body.model,
        attachment_document_ids=attachment_doc_ids or None,
        session_id=body.session_id,
    )

    # Привязываем сообщение к задаче
    await state.aexecute(
        "UPDATE chat_messages SET task_id = ? WHERE id = ?",
        (task_id, message_id),
    )

    # Для clipboard-картинок (is_scratch=1) проставляем session_id.
    # Project-wide документы (PDF/TZ загруженные через UI) не трогаем.
    if attachment_doc_ids:
        placeholders = ",".join("?" * len(attachment_doc_ids))
        await state.aexecute(
            f"UPDATE documents SET session_id = ? WHERE id IN ({placeholders}) AND COALESCE(is_scratch, 0) = 1",
            tuple([body.session_id] + attachment_doc_ids),
        )

    # Обновляем активность сессии — будет всплывать первой в списке
    await state.aexecute(
        "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
        (now, body.session_id),
    )
    await state.acommit()

    # SSE для user messages не отправляем — фронт добавляет их оптимистично

    logger.info(
        "Новое сообщение %s → задача %s (проект %s, сессия %s)",
        message_id, task_id, body.project_id, body.session_id,
    )
    return ChatSendResponse(task_id=task_id, message_id=message_id)


# ---------------------------------------------------------------------------
# POST /api/chat/cancel
# ---------------------------------------------------------------------------

@router.post("/cancel")
async def cancel_task(request: Request, body: dict):
    """Отменить текущую задачу. Body: {task_id: str}."""
    state, queue = _get_deps(request)
    task_id = body.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id обязателен")

    row = await state.afetchone("SELECT id, status FROM tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    status = row["status"]
    if status in ("completed", "failed", "cancelled"):
        return {"ok": True, "detail": f"Задача уже в статусе {status}"}

    # Ставим cancelled
    now = _now_iso()
    await state.aexecute(
        "UPDATE tasks SET status = 'cancelled', completed_at = ? WHERE id = ?",
        (now, task_id),
    )
    await state.acommit()

    # Убираем из очереди если ещё не взята
    queue.cancel(task_id)

    # SSE уведомление
    await request.app.state.publish_event(
        "task_update",
        {"task_id": task_id, "status": "cancelled"},
    )

    logger.info("Задача %s отменена", task_id)
    return {"ok": True, "detail": "Задача отменена"}


# ---------------------------------------------------------------------------
# GET /api/chat/history/{session_id}
# ---------------------------------------------------------------------------

@router.get("/history/{session_id}", response_model=list[ChatMessage])
async def get_history(session_id: str, request: Request, limit: int = 50):
    """
    Возвращает историю сообщений сессии (хронологически ASC, последние limit штук).
    Изолировано по session_id — каждый чат видит только свои сообщения.
    """
    state, _ = _get_deps(request)

    # Проверяем существование сессии
    session = await state.afetchone("SELECT id FROM chat_sessions WHERE id = ?", (session_id,))
    if not session:
        raise HTTPException(status_code=404, detail=f"Сессия {session_id} не найдена")

    rows = await state.afetchall(
        """
        SELECT id, project_id, role, content, task_id, attachments, created_at, session_id
        FROM chat_messages
        WHERE session_id = ?
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (session_id, limit),
    )
    # Парсим attachments JSON в список (хранится как JSON-строка)
    messages: list[ChatMessage] = []
    for r in rows:
        data = dict(r)
        raw = data.pop("attachments", "") or ""
        try:
            data["attachments"] = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            data["attachments"] = []
        messages.append(ChatMessage(**data))
    return messages


# ---------------------------------------------------------------------------
# GET /api/chat/task/{task_id}
# ---------------------------------------------------------------------------

@router.get("/task/{task_id}", response_model=Task)
async def get_task(task_id: str, request: Request):
    """Возвращает статус и результат задачи."""
    state, _ = _get_deps(request)

    row = await state.afetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Задача {task_id} не найдена")

    return Task(**dict(row))
