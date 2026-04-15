"""Единый источник правды про типы файлов (Issue 2.2A).

До этого модуля одни и те же расширения были продублированы в 5 местах:
  - backend/api/documents.py: CONVERTIBLE_EXTENSIONS + локальный text_exts
  - backend/api/system.py: локальный binary_exts
  - worker/base_executor.py: IMAGE_EXTENSIONS + NON_READABLE_BINARY_EXTS

Теперь всё здесь. Любое изменение списка (новый формат) — одна правка.

Используется и backend, и worker — оба живут в одном pyproject, общий sys.path.
"""
from __future__ import annotations

from pathlib import Path

# Изображения — рендерятся в multimodal content (image_url data URL)
IMAGE_EXTS: set[str] = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Текстовые файлы — читаются напрямую, без конвертации в markdown
TEXT_EXTS: set[str] = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".log"}

# Форматы которые нужно конвертировать в markdown при upload (см. _convert_to_text).
# markitdown → pdfminer → AITunnel (каскад).
CONVERTIBLE_EXTS: set[str] = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".xls", ".html", ".htm"}

# Бинарные форматы которые Codex CLI без внешних тулов не прочитает (Issue 1.4A).
# Их родителей НЕ добавляем в --add-dir — ложная надежда "прочитать файл сам".
# Контент приходит в промпт через parsed .md.
NON_READABLE_BINARY_EXTS: set[str] = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"}

# Расширенный список бинарных/media файлов — для skip content embedding
# (используется в рендере documents-секции промпта)
BINARY_MEDIA_EXTS: set[str] = IMAGE_EXTS | NON_READABLE_BINARY_EXTS | {
    ".bmp", ".ico", ".svg",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
}


def _ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def is_image(filename: str) -> bool:
    return _ext(filename) in IMAGE_EXTS


def is_text(filename: str) -> bool:
    return _ext(filename) in TEXT_EXTS


def is_convertible(filename: str) -> bool:
    return _ext(filename) in CONVERTIBLE_EXTS


def is_non_readable_binary(filename: str) -> bool:
    return _ext(filename) in NON_READABLE_BINARY_EXTS


def is_binary_media(filename: str) -> bool:
    return _ext(filename) in BINARY_MEDIA_EXTS
