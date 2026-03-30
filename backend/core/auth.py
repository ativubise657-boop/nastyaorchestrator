"""
Авторизация worker-эндпоинтов через Bearer-токен.
"""
from fastapi import Header, HTTPException

from backend.core.config import WORKER_TOKEN


async def verify_worker(authorization: str = Header(...)):
    """
    FastAPI Depends-зависимость.
    Проверяет заголовок Authorization: Bearer {WORKER_TOKEN}.
    """
    if authorization != f"Bearer {WORKER_TOKEN}":
        raise HTTPException(status_code=401, detail="Неверный worker-токен")
