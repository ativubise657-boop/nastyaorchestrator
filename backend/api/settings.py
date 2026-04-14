"""
Настройки приложения — пока только прокси.

Эндпоинты:
    GET  /api/settings/proxy        — текущие настройки (с паролем — открытое хранение, см. proxy.py)
    PUT  /api/settings/proxy        — обновить и применить мгновенно
    POST /api/settings/proxy/test   — проверочный запрос через указанные настройки
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.core import proxy as proxy_module

logger = logging.getLogger(__name__)
router = APIRouter()


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
SANDBOX_DEFAULT = "workspace-write"


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
