"""
Launcher для backend в frozen-режиме.

backend/main.py экспортирует только `app` (его нормально запускают через
`uvicorn backend.main:app`). PyInstaller'у нужен исполняемый entrypoint —
здесь мы импортируем app и запускаем uvicorn программно.

Параметры host/port читаются из env (BACKEND_HOST / BACKEND_PORT),
по умолчанию — те же, что в start.bat: 0.0.0.0:8781.
"""
import os
import sys


def main() -> int:
    import uvicorn
    from backend.main import app

    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8781"))
    log_level = os.getenv("BACKEND_LOG_LEVEL", "info")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        # reload отключаем — в frozen режиме он не работает
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
