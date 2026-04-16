"""
Конфигурация приложения — читается из переменных окружения / .env файла.
"""
import os
import re
import sys
from pathlib import Path

# Корень проекта (директория, где лежит backend/)
# При frozen-режиме (PyInstaller onefile) исходники распакованы во временный
# _MEIPASS, но runtime-данные (data/, documents/, frontend/dist/, .env) должны
# лежать рядом с .exe — поэтому BASE_DIR = директория исполняемого файла.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
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
# Во frozen-режиме (Tauri sidecar) backend и worker используют один общий
# дефолт-токен. Worker тоже использует его через worker/config.py _DEFAULT_TOKEN.
import sys as _sys
_DEFAULT_WORKER_TOKEN = "nastya-local-dev" if getattr(_sys, "frozen", False) else "change-me"
WORKER_TOKEN: str = os.getenv("WORKER_TOKEN", _DEFAULT_WORKER_TOKEN)

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
APP_VERSION: str = "28.0.0"
CHANGELOG_PATH: Path = BASE_DIR / "CHANGELOG.md"


def _extract_config_version(config_text: str | None) -> str | None:
    if not config_text:
        return None
    match = re.search(r'APP_VERSION:\s*str\s*=\s*["\']([^"\']+)["\']', config_text)
    return match.group(1) if match else None


def _extract_changelog_version(changelog_text: str | None) -> str | None:
    if not changelog_text:
        return None

    for raw_line in changelog_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("## "):
            continue
        match = re.search(r"\bv?(\d+\.\d+\.\d+)\b", line)
        if match:
            return match.group(1)
    return None


def _version_key(version: str | None) -> tuple[int, ...] | None:
    if not version:
        return None
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def resolve_app_version(
    *,
    config_text: str | None = None,
    changelog_text: str | None = None,
    fallback: str | None = None,
) -> str:
    fallback_version = fallback or APP_VERSION
    config_version = _extract_config_version(config_text) or fallback_version
    changelog_version = _extract_changelog_version(changelog_text)

    if not changelog_version:
        return config_version
    if not config_version:
        return changelog_version

    config_key = _version_key(config_version)
    changelog_key = _version_key(changelog_version)
    if config_key and changelog_key:
        return changelog_version if changelog_key >= config_key else config_version
    return changelog_version or config_version


def get_local_app_version(base_dir: Path | None = None) -> str:
    root = base_dir or BASE_DIR

    try:
        config_text = (root / "backend" / "core" / "config.py").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        config_text = None

    try:
        changelog_text = (root / "CHANGELOG.md").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        changelog_text = None

    return resolve_app_version(
        config_text=config_text,
        changelog_text=changelog_text,
        fallback=APP_VERSION,
    )

# Считаем worker «живым», если heartbeat был не позже чем N секунд назад
WORKER_HEARTBEAT_TTL: int = int(os.getenv("WORKER_HEARTBEAT_TTL", "60"))

# Standalone режим — backend раздаёт frontend/dist (без nginx)
SERVE_STATIC: bool = os.getenv("SERVE_STATIC", "false").lower() in ("true", "1", "yes")
