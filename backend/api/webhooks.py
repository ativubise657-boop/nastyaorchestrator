"""
Б24-вебхуки — принимает JSON, пишет в таблицу webhooks_raw, возвращает 200.
Заготовка: дальнейшая обработка подключается позже.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/b24", status_code=200)
async def b24_webhook(request: Request):
    """
    Принимает любой JSON/form от Битрикс24.
    Кладёт сырой payload в webhooks_raw для последующей обработки.
    Всегда возвращает 200, чтобы Б24 не retry-ил.
    """
    state = request.app.state.db

    # Пробуем разобрать тело — может быть JSON или form-data
    try:
        body = await request.json()
        payload_str = json.dumps(body, ensure_ascii=False)
    except Exception:
        # Если не JSON — сохраняем как текст
        raw_bytes = await request.body()
        payload_str = raw_bytes.decode("utf-8", errors="replace")

    webhook_id = str(uuid.uuid4())
    now = _now_iso()

    await state.aexecute(
        """
        INSERT INTO webhooks_raw (id, source, payload, received_at, processed)
        VALUES (?, 'b24', ?, ?, 0)
        """,
        (webhook_id, payload_str, now),
    )
    await state.acommit()

    logger.info("Б24 вебхук %s принят (%d chars)", webhook_id, len(payload_str))
    return {"ok": True, "id": webhook_id}
