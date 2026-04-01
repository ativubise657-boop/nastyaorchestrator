"""Единый реестр моделей — одно место для обновления при смене версий.

Читает config/models.json и предоставляет API для получения полных ID моделей.
Остальные модули используют этот реестр вместо хардкода.
"""

import json
from pathlib import Path

_MODELS_PATH = Path(__file__).parent.parent / "config" / "models.json"
_cache: dict | None = None


def _load() -> dict:
    """Загрузить и закешировать config/models.json."""
    global _cache
    if _cache is None:
        with open(_MODELS_PATH) as f:
            _cache = json.load(f)
    return _cache


def get_model_id(short: str) -> str:
    """Получить полный ID модели по короткому имени.

    Если short уже является полным ID (не найден в маппинге) — возвращает as-is.
    """
    models = _load()["models"]
    return models.get(short, short)


def get_default_chat_model() -> str:
    """Короткое имя модели по умолчанию для чата."""
    return _load().get("default_chat", "glm-5-turbo")


def get_all_models() -> dict[str, str]:
    """Все модели: {short_name: full_id}."""
    return dict(_load()["models"])
