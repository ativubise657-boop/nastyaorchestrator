"""
Авторизация worker-эндпоинтов через Bearer-токен.
"""
import logging
import sys

from fastapi import Header, HTTPException

from backend.core.config import WORKER_TOKEN

logger = logging.getLogger(__name__)


async def verify_worker(authorization: str = Header(default="")):
    """
    FastAPI Depends-зависимость.
    Проверяет заголовок Authorization: Bearer {WORKER_TOKEN}.

    Во frozen-режиме (installed Tauri app) — пропускаем: оба процесса
    на localhost, single-user desktop, auth не даёт реальной защиты.
    """
    if getattr(sys, "frozen", False):
        return  # installed app — localhost only, auth не нужен

    if authorization != f"Bearer {WORKER_TOKEN}":
        # Логируем для дебага при dev-запуске
        expected = f"Bearer {WORKER_TOKEN}"
        got_prefix = authorization[:20] if authorization else "(empty)"
        logger.warning(
            "Worker auth failed: expected '%s...', got '%s...'",
            expected[:25], got_prefix,
        )
        raise HTTPException(status_code=401, detail="Неверный worker-токен")
