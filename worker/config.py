"""Worker configuration loaded from environment variables."""

import os
import sys


# Когда worker запущен как frozen sidecar внутри Tauri — он всегда ходит на
# локальный backend, поднятый соседним sidecar-ом на 127.0.0.1:8781.
# При dev-запуске (python -m worker.main) остаётся прод-дефолт.
_DEFAULT_SERVER_URL = (
    "http://127.0.0.1:8781" if getattr(sys, "frozen", False) else "https://nr.gnld.ru"
)
# Общий дефолт-токен для backend и worker во frozen-режиме (Tauri sidecar).
# Backend использует тот же дефолт через backend/core/config.py.
_DEFAULT_TOKEN = "nastya-local-dev" if getattr(sys, "frozen", False) else "change-me"
# Во frozen-режиме (Tauri sidecar) системный `codex` не доступен из subprocess
# (WindowsApps sandbox). Используем вложенный wrapper tools\codex-npx.cmd,
# который резолвится через _MEIPASS в executor._build_command().
_DEFAULT_CODEX = r"tools\codex-npx.cmd" if getattr(sys, "frozen", False) else "codex"


class WorkerConfig:
    """All worker settings with sane defaults."""

    server_url: str = os.getenv("ORCH_SERVER_URL", _DEFAULT_SERVER_URL)
    worker_token: str = os.getenv("WORKER_TOKEN", _DEFAULT_TOKEN)
    codex_binary: str = os.getenv("CODEX_BINARY", os.getenv("CLAUDE_BINARY", _DEFAULT_CODEX))
    aitunnel_api_key: str = os.getenv("AITUNNEL_API_KEY", "")
    aitunnel_base_url: str = os.getenv("AITUNNEL_BASE_URL", "https://api.aitunnel.ru/v1")
    aitunnel_request_timeout: int = int(os.getenv("AITUNNEL_REQUEST_TIMEOUT", "120"))
    aitunnel_max_tool_rounds: int = int(os.getenv("AITUNNEL_MAX_TOOL_ROUNDS", "16"))
    poll_interval: int = int(os.getenv("POLL_INTERVAL", "5"))
    heartbeat_interval: int = int(os.getenv("HEARTBEAT_INTERVAL", "5"))
    task_timeout: int = int(os.getenv("TASK_TIMEOUT", "600"))
    worker_id: str = os.getenv("WORKER_ID", f"wsl-worker-{os.getpid()}")
    default_project_path: str | None = os.getenv("DEFAULT_PROJECT_PATH", None)
    stream_chunk_size: int = int(os.getenv("STREAM_CHUNK_SIZE", "512"))
