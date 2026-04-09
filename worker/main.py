"""Entry point для WSL worker.

Запуск:
    python -m worker.main

Или через переменные окружения:
    ORCH_SERVER_URL=https://nr.gnld.ru WORKER_TOKEN=secret python -m worker.main
"""
import asyncio
import faulthandler
import logging
import logging.handlers
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

# Загружаем .env из корня проекта (если есть)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

from worker.config import WorkerConfig
from worker.poller import Poller


def _get_log_dir() -> Path:
    """Директория для worker.log — рядом с .exe во frozen, иначе cwd."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _setup_logging() -> None:
    """
    Настройка логирования с записью в файл рядом с .exe.

    Файл worker.log нужен чтобы диагностировать падения frozen-режима:
    при резком крахе (SIGSEGV, sys.exit из потока) PyInstaller теряет
    stdout, но FileHandler с autoFlush успевает записать последние строки.

    Дополнительно: faulthandler для C-краш-трейсов + sys.excepthook для
    unhandled Python exceptions.
    """
    level = logging.DEBUG if "--debug" in sys.argv else logging.INFO

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    # Файловый лог с ротацией (10 МБ × 3 файла)
    try:
        log_dir = _get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "worker.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
            delay=False,
        )
        # Форсим flush после каждой записи (важно при crash)
        file_handler.setLevel(level)
        handlers.append(file_handler)
    except Exception as exc:
        print(f"[worker] WARNING: cannot open log file: {exc}", file=sys.stderr)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )

    if level == logging.DEBUG:
        logging.getLogger("httpx").setLevel(logging.INFO)
        logging.getLogger("httpcore").setLevel(logging.INFO)

    # faulthandler — ловит SIGSEGV/SIGFPE/SIGABRT, пишет traceback в stderr.
    # Если есть возможность — направляем в файл tracebacks/crash-<ts>.log
    try:
        fault_file = _get_log_dir() / "worker-fault.log"
        # Открываем в append режиме, faulthandler пишет напрямую в fd
        fault_fd = open(fault_file, "a", encoding="utf-8")
        faulthandler.enable(file=fault_fd, all_threads=True)
    except Exception:
        faulthandler.enable(all_threads=True)

    # Unhandled Python exceptions — логируем через logging (попадёт в файл)
    def _excepthook(exctype, value, tb) -> None:
        logger = logging.getLogger("worker.crash")
        logger.critical(
            "UNHANDLED EXCEPTION: %s: %s\n%s",
            exctype.__name__ if exctype else "?",
            value,
            "".join(traceback.format_exception(exctype, value, tb)),
        )
        # Дублируем в stderr на всякий случай
        sys.__excepthook__(exctype, value, tb)

    sys.excepthook = _excepthook

    # Для asyncio unhandled exceptions
    def _asyncio_handler(loop, context: dict) -> None:
        logger = logging.getLogger("worker.asyncio")
        exc = context.get("exception")
        msg = context.get("message", "unknown asyncio error")
        if exc:
            logger.error(
                "ASYNCIO ERROR: %s\n%s",
                msg,
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            )
        else:
            logger.error("ASYNCIO ERROR (no exception): %s", msg)

    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_asyncio_handler)
    except Exception:
        pass


async def main() -> None:
    """Точка входа: настройка и запуск поллера."""
    _setup_logging()
    logger = logging.getLogger(__name__)

    # Прокси: читаем настройки из той же БД что использует backend и
    # применяем в os.environ ДО создания httpx-клиентов и subprocess.
    try:
        from backend.core.state import State
        from backend.core import proxy as proxy_module
        applied = proxy_module.apply_from_db(State())
        logger.info("Proxy applied: %s", applied.to_safe_dict())
    except Exception as exc:
        logger.warning("Не удалось применить прокси на старте worker: %s", exc)

    config = WorkerConfig()

    # Санити-чек конфигурации
    if config.worker_token == "change-me":
        logger.warning(
            "WORKER_TOKEN не задан! Установи переменную окружения WORKER_TOKEN."
        )

    logger.info("Конфигурация загружена:")
    logger.info("  server_url:  %s", config.server_url)
    logger.info("  worker_id:   %s", config.worker_id)
    logger.info("  codex_bin:   %s", config.codex_binary)
    logger.info("  poll_interval:      %ds", config.poll_interval)
    logger.info("  heartbeat_interval: %ds", config.heartbeat_interval)
    logger.info("  task_timeout:       %ds", config.task_timeout)

    poller = Poller(config)

    try:
        await poller.run()
    except KeyboardInterrupt:
        # asyncio обычно перехватывает это раньше, но на всякий случай
        logger.info("Прерван пользователем.")


if __name__ == "__main__":
    asyncio.run(main())
