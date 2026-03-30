"""Entry point для WSL worker.

Запуск:
    python -m worker.main

Или через переменные окружения:
    ORCH_SERVER_URL=https://nr.gnld.ru WORKER_TOKEN=secret python -m worker.main
"""
import asyncio
import logging
import sys
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


def _setup_logging() -> None:
    """Настройка логирования: INFO по умолчанию, DEBUG если передан --debug."""
    level = logging.DEBUG if "--debug" in sys.argv else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # httpx слишком многословен на DEBUG — ограничиваем
    if level == logging.DEBUG:
        logging.getLogger("httpx").setLevel(logging.INFO)
        logging.getLogger("httpcore").setLevel(logging.INFO)


async def main() -> None:
    """Точка входа: настройка и запуск поллера."""
    _setup_logging()
    logger = logging.getLogger(__name__)

    config = WorkerConfig()

    # Санити-чек конфигурации
    if config.worker_token == "change-me":
        logger.warning(
            "WORKER_TOKEN не задан! Установи переменную окружения WORKER_TOKEN."
        )

    logger.info("Конфигурация загружена:")
    logger.info("  server_url:  %s", config.server_url)
    logger.info("  worker_id:   %s", config.worker_id)
    logger.info("  claude_bin:  %s", config.claude_binary)
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
