"""
Настройки приложения — пока только прокси.

Эндпоинты:
    GET  /api/settings/proxy        — текущие настройки (с паролем — открытое хранение, см. proxy.py)
    PUT  /api/settings/proxy        — обновить и применить мгновенно
    POST /api/settings/proxy/test   — проверочный запрос через указанные настройки
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.core import proxy as proxy_module

logger = logging.getLogger(__name__)
router = APIRouter()


def _read_secrets_file() -> dict:
    """Читает .secrets.json из _MEIPASS (PyInstaller) или корня проекта (dev).

    Этот файл прошивается в .exe через GitHub Actions (release.yml) из секретов
    GitHub. В dev-окружении может отсутствовать — тогда fallback на .env.
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / ".secrets.json")
    candidates.append(Path(__file__).resolve().parent.parent.parent / ".secrets.json")
    for path in candidates:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Не удалось прочитать %s: %s", path, exc)
    return {}


class ProxyPayload(BaseModel):
    enabled: bool = True
    host: str = Field(..., min_length=1)
    port: int = Field(..., ge=1, le=65535)
    user: str = ""
    password: str = ""
    no_proxy: str = proxy_module.DEFAULT_NO_PROXY


def _to_settings(p: ProxyPayload) -> proxy_module.ProxySettings:
    return proxy_module.ProxySettings(
        enabled=p.enabled,
        host=p.host,
        port=p.port,
        user=p.user,
        password=p.password,
        no_proxy=p.no_proxy,
    )


def _to_payload(s: proxy_module.ProxySettings) -> dict:
    # Открытое хранение пароля — UI сам показывает его в обычном input.
    return {
        "enabled": s.enabled,
        "host": s.host,
        "port": s.port,
        "user": s.user,
        "password": s.password,
        "no_proxy": s.no_proxy,
    }


@router.get("/proxy")
async def get_proxy(request: Request):
    state = request.app.state.db
    s = proxy_module.load_settings(state)
    return _to_payload(s)


@router.put("/proxy")
async def put_proxy(payload: ProxyPayload, request: Request):
    state = request.app.state.db
    settings = _to_settings(payload)
    proxy_module.save_settings(state, settings)
    proxy_module.apply_to_env(settings)
    logger.info("Прокси обновлён через API: %s", settings.to_safe_dict())
    return {"ok": True, "applied": _to_payload(settings)}


@router.post("/proxy/test")
async def test_proxy(payload: Optional[ProxyPayload] = None, request: Request = None):
    if payload is None:
        settings = proxy_module.load_settings(request.app.state.db)
    else:
        settings = _to_settings(payload)
    ok, message = proxy_module.test_proxy(settings)
    return {"ok": ok, "message": message, "settings": settings.to_safe_dict()}


# ---------------------------------------------------------------------------
# Codex sandbox (toggle: workspace-write vs danger-full-access)
# ---------------------------------------------------------------------------

SANDBOX_VALID = {"workspace-write", "read-only", "danger-full-access"}
SANDBOX_DEFAULT = "danger-full-access"


class SandboxPayload(BaseModel):
    mode: str = Field(..., description="workspace-write | read-only | danger-full-access")


def _load_sandbox(state) -> str:
    row = state.fetchone("SELECT value FROM app_settings WHERE key = 'codex_sandbox'")
    val = (row["value"] if row else "") or SANDBOX_DEFAULT
    return val if val in SANDBOX_VALID else SANDBOX_DEFAULT


@router.get("/sandbox")
async def get_sandbox(request: Request):
    return {"mode": _load_sandbox(request.app.state.db), "choices": sorted(SANDBOX_VALID)}


@router.put("/sandbox")
async def put_sandbox(payload: SandboxPayload, request: Request):
    if payload.mode not in SANDBOX_VALID:
        return {"ok": False, "error": f"Неверный режим. Разрешены: {sorted(SANDBOX_VALID)}"}
    state = request.app.state.db
    state.execute(
        "INSERT INTO app_settings (key, value) VALUES ('codex_sandbox', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (payload.mode,),
    )
    state.commit()
    logger.info("Codex sandbox mode → %s", payload.mode)
    return {"ok": True, "mode": payload.mode}


# ---------------------------------------------------------------------------
# AITunnel API key (для Gemini PDF parsing через AITunnel)
# Ключ можно ввести прямо в UI Settings — сохраняется в app_settings,
# переопределяет env при старте и мгновенно при PUT.
# ---------------------------------------------------------------------------

class AITunnelKeyPayload(BaseModel):
    api_key: str = Field(default="", description="Пустая строка = удалить ключ, вернуться к env")


def _load_aitunnel_key(state) -> str:
    """Возвращает user-override из БД или fallback из env."""
    row = state.fetchone("SELECT value FROM app_settings WHERE key = 'aitunnel_api_key'")
    db_value = (row["value"] if row else "") or ""
    return db_value or os.getenv("AITUNNEL_API_KEY", "")


def _apply_aitunnel_key_env(state) -> None:
    """Вызывается на startup. Приоритет источников (от высшего к низшему):
      1. БД `app_settings.aitunnel_api_key` (пользовательский override из UI Settings)
      2. .env (уже подгружен через load_dotenv при импорте config.py)
      3. .secrets.json (прошитый в билд через GitHub Actions)
    Итог кладём в `os.environ["AITUNNEL_API_KEY"]` — все `os.getenv` видят актуальное значение.
    """
    row = state.fetchone("SELECT value FROM app_settings WHERE key = 'aitunnel_api_key'")
    db_value = (row["value"] if row else "") or ""
    env_value = os.environ.get("AITUNNEL_API_KEY", "")

    if db_value:
        os.environ["AITUNNEL_API_KEY"] = db_value
        logger.info("AITunnel key: из БД (длина %d)", len(db_value))
        return

    if env_value:
        # Уже в env (из .env или previous startup) — ничего не трогаем
        logger.info("AITunnel key: из env (длина %d)", len(env_value))
        return

    # Fallback — .secrets.json (прошитый в .exe)
    secrets = _read_secrets_file()
    sf_value = (secrets.get("aitunnel_api_key") or "").strip()
    if sf_value:
        os.environ["AITUNNEL_API_KEY"] = sf_value
        logger.info("AITunnel key: из .secrets.json (длина %d)", len(sf_value))
    else:
        logger.info("AITunnel key: не задан ни в одном источнике")


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


@router.get("/aitunnel_key")
async def get_aitunnel_key(request: Request):
    state = request.app.state.db
    key = _load_aitunnel_key(state)
    # Источник: 'db' если в app_settings есть, 'env' если только из .env, '' если нет
    row = state.fetchone("SELECT value FROM app_settings WHERE key = 'aitunnel_api_key'")
    source = "db" if (row and row["value"]) else ("env" if key else "")
    return {"present": bool(key), "masked": _mask(key), "source": source}


@router.put("/aitunnel_key")
async def put_aitunnel_key(payload: AITunnelKeyPayload, request: Request):
    state = request.app.state.db
    key = payload.api_key.strip()
    if not key:
        state.execute("DELETE FROM app_settings WHERE key = 'aitunnel_api_key'")
        # Возвращаемся к env
        env_key = os.getenv("AITUNNEL_API_KEY", "")
        if env_key:
            os.environ["AITUNNEL_API_KEY"] = env_key
        logger.info("AITunnel key: очищен, fallback на env (%s)", "есть" if env_key else "нет")
    else:
        state.execute(
            "INSERT INTO app_settings (key, value) VALUES ('aitunnel_api_key', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key,),
        )
        os.environ["AITUNNEL_API_KEY"] = key
        logger.info("AITunnel key: обновлён (длина %d)", len(key))
    state.commit()
    return {"ok": True, "present": bool(key), "masked": _mask(key)}
