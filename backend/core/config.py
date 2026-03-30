"""
Конфигурация приложения — читается из переменных окружения / .env файла.
"""
import os
from pathlib import Path

# Корень проекта (директория, где лежит backend/)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Загружаем .env если есть
from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# База данных
# ---------------------------------------------------------------------------
DB_PATH: str = os.getenv("DB_PATH", str(BASE_DIR / "data" / "nastya.db"))

# ---------------------------------------------------------------------------
# Авторизация worker-а
# ---------------------------------------------------------------------------
WORKER_TOKEN: str = os.getenv("WORKER_TOKEN", "change-me")

# ---------------------------------------------------------------------------
# Хранилище документов
# ---------------------------------------------------------------------------
DOCUMENTS_DIR: str = os.getenv("DOCUMENTS_DIR", str(BASE_DIR / "data" / "documents"))

# ---------------------------------------------------------------------------
# CORS — на проде nginx ограничит, здесь разрешаем всё для разработки
# ---------------------------------------------------------------------------
CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")

# ---------------------------------------------------------------------------
# Прочее
# ---------------------------------------------------------------------------
APP_TITLE: str = "Nastya Orchestrator"
APP_VERSION: str = "0.1.0"

# Считаем worker «живым», если heartbeat был не позже чем N секунд назад
WORKER_HEARTBEAT_TTL: int = int(os.getenv("WORKER_HEARTBEAT_TTL", "60"))

# Standalone режим — backend раздаёт frontend/dist (без nginx)
SERVE_STATIC: bool = os.getenv("SERVE_STATIC", "false").lower() in ("true", "1", "yes")
