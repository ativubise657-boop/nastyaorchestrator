"""SHA256-кеш распарсенных документов (Fix 4.2A).

Путь кеша: `data/documents/_cache/{sha256}.md` — один файл = один уникальный
документ по содержимому, не зависит от имени/папки/проекта. При повторной
загрузке того же PDF парсеры каскада не вызываются — копируем кешированный
.md в нужное место. Экономит и время (несколько сек markitdown), и деньги
(AITunnel на сканах).

Инвалидация не нужна: hash меняется при изменении одного байта → новый ключ.
Старые записи могут висеть — сборщик мусора по LRU в будущем (сейчас не нужен).
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from backend.core.config import DOCUMENTS_DIR

logger = logging.getLogger(__name__)

CACHE_DIR: Path = Path(DOCUMENTS_DIR) / "_cache"
_CHUNK_SIZE = 65536


def _hash_file(file_path: Path) -> str:
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def get(file_path: Path) -> str | None:
    """Вернёт cached markdown если этот документ уже парсили (по content-hash)."""
    try:
        digest = _hash_file(file_path)
        cached = CACHE_DIR / f"{digest}.md"
        if cached.is_file():
            return cached.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("parse_cache.get failed: %s", exc)
    return None


def put(file_path: Path, text: str) -> None:
    """Сохранить распарсенный markdown в кеш по content-hash."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        digest = _hash_file(file_path)
        (CACHE_DIR / f"{digest}.md").write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.debug("parse_cache.put failed: %s", exc)
