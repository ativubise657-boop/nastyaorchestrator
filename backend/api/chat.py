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
    1. Проверяет, что проект существует.
    2. Создаёт ChatMessage(role=user).
    3. Создаёт Task(status=queued) и добавляет в очередь.
    4. Возвращает {task_id, message_id}.
    """
    state, queue = _get_deps(request)

    # Проверяем существование проекта
    project = state.fetchone("SELECT id FROM projects WHERE id = ?", (body.project_id,))
    if not project:
        raise HTTPException(status_code=404, detail=f"Проект {body.project_id} не найден")

    now = _now_iso()

    # Создаём сообщение пользователя
    message_id = str(uuid.uuid4())
    attachments_json = json.dumps(
        [a.model_dump() for a in body.attachments], ensure_ascii=False
    ) if body.attachments else ""
    state.execute(
        """
        INSERT INTO chat_messages (id, project_id, role, content, task_id, attachments, created_at)
        VALUES (?, ?, 'user', ?, NULL, ?, ?)
        """,
        (message_id, body.project_id, body.message, attachments_json, now),
    )

    # Создаём задачу и ставим в очередь
    task_id = queue.enqueue(
        project_id=body.project_id,
        prompt=body.message,
        mode=body.mode,
        model=body.model,
    )

    # Привязываем сообщение к задаче
    state.execute(
        "UPDATE chat_messages SET task_id = ? WHERE id = ?",
        (task_id, message_id),
    )
    state.commit()

    # SSE для user messages не отправляем — фронт добавляет их оптимистично

    logger.info("Новое сообщение %s → задача %s (проект %s)", message_id, task_id, body.project_id)
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

    row = state.fetchone("SELECT id, status FROM tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    status = row["status"]
    if status in ("completed", "failed", "cancelled"):
        return {"ok": True, "detail": f"Задача уже в статусе {status}"}

    # Ставим cancelled
    now = _now_iso()
    state.execute(
        "UPDATE tasks SET status = 'cancelled', completed_at = ? WHERE id = ?",
        (now, task_id),
    )
    state.commit()

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
# GET /api/chat/history/{project_id}
# ---------------------------------------------------------------------------

@router.get("/history/{project_id}", response_model=list[ChatMessage])
async def get_history(project_id: str, request: Request, limit: int = 50):
    """Возвращает историю сообщений проекта (последние limit штук)."""
    state, _ = _get_deps(request)

    project = state.fetchone("SELECT id FROM projects WHERE id = ?", (project_id,))
    if not project:
        raise HTTPException(status_code=404, detail=f"Проект {project_id} не найден")

    rows = state.fetchall(
        """
        SELECT id, project_id, role, content, task_id, attachments, created_at
        FROM chat_messages
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (project_id, limit),
    )
    # Отдаём в хронологическом порядке; парсим attachments JSON в список
    messages: list[ChatMessage] = []
    for r in reversed(rows):
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

    row = state.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Задача {task_id} не найдена")

    return Task(**dict(row))
