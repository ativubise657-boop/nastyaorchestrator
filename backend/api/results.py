"""
Приём результатов от worker-а:
  POST /api/results        — финальный результат задачи
  POST /api/results/stream — промежуточный чанк (стриминг)
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.models import ResultRequest, StreamChunkRequest, TaskPhaseRequest, TaskStatus
from backend.core.auth import verify_worker

logger = logging.getLogger(__name__)
router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# POST /api/results
# ---------------------------------------------------------------------------

@router.post("", status_code=200, dependencies=[Depends(verify_worker)])
async def submit_result(body: ResultRequest, request: Request):
    """
    Worker сообщает о завершении задачи (completed или failed).
    Создаёт сообщение assistant в истории чата.
    Публикует SSE-событие task_update.
    """
    state = request.app.state.db
    queue = request.app.state.queue

    row = state.fetchone("SELECT * FROM tasks WHERE id = ?", (body.task_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Задача {body.task_id} не найдена")

    if row["status"] not in ("running", "queued"):
        raise HTTPException(
            status_code=409,
            detail=f"Задача {body.task_id} уже в статусе {row['status']}",
        )

    # Обновляем статус задачи
    queue.complete(
        task_id=body.task_id,
        status=body.status.value,
        result=body.result,
        error=body.error,
    )

    # Добавляем сообщение ассистента в историю чата
    if body.result or body.error:
        content = body.result if body.result else f"Ошибка выполнения: {body.error}"
        role = "assistant" if body.status == TaskStatus.completed else "system"
        msg_id = str(uuid.uuid4())
        state.execute(
            """
            INSERT INTO chat_messages (id, project_id, role, content, task_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (msg_id, row["project_id"], role, content, body.task_id, _now_iso()),
        )
        state.commit()

    # Публикуем SSE-событие
    await request.app.state.publish_event(
        "task_update",
        {
            "task_id": body.task_id,
            "project_id": row["project_id"],
            "status": body.status.value,
            "result": body.result,
            "error": body.error,
            "used_github": body.used_github,
        },
    )

    logger.info("Результат задачи %s: %s", body.task_id, body.status.value)
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/results/stream
# ---------------------------------------------------------------------------

@router.post("/stream", status_code=200, dependencies=[Depends(verify_worker)])
async def submit_stream_chunk(body: StreamChunkRequest, request: Request):
    """
    Worker шлёт промежуточный чанк текста.
    Кладём его в SSE-поток для фронтенда — в БД не записываем.
    """
    state = request.app.state.db

    row = state.fetchone(
        "SELECT project_id, status FROM tasks WHERE id = ?", (body.task_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Задача {body.task_id} не найдена")

    if row["status"] != "running":
        raise HTTPException(
            status_code=409,
            detail=f"Задача {body.task_id} не в статусе running",
        )

    await request.app.state.publish_event(
        "task_chunk",
        {
            "task_id": body.task_id,
            "project_id": row["project_id"],
            "chunk": body.chunk,
        },
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/results/phase
# ---------------------------------------------------------------------------

@router.post("/phase", status_code=200, dependencies=[Depends(verify_worker)])
async def submit_task_phase(body: TaskPhaseRequest, request: Request):
    """
    Worker сообщает о текущей фазе выполнения.
    Например: "Роюсь в GitHub в проекте geniled.ru..."
    Публикуется как SSE task_phase для отображения в UI.
    """
    await request.app.state.publish_event(
        "task_phase",
        {
            "task_id": body.task_id,
            "phase": body.phase,
        },
    )
    return {"ok": True}
