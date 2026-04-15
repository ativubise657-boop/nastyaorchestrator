"""Извлечение документов из ответа Codex.

Codex может создавать документы в ответе используя формат:
    :::document:имя_файла.md
    содержимое документа
    :::

С указанием папки:
    :::document:имя_файла.md:Название папки
    содержимое документа
    :::

Парсер — state-machine (Issue 3.2A), а не одноразовый regex. Это важно для
случая когда содержимое документа само включает пример кода с ```, внутри
которого есть `:::` — раньше regex non-greedy останавливался на нём,
теперь парсер игнорирует `:::` внутри code fence.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Строка открытия блока: "^:::document:filename[:folder]\s*$"
_OPEN_RE = re.compile(r'^:::document:([^:\n]+?)(?::([^\n]+?))?\s*$')
# Строка закрытия: ровно ":::" (возможны пробелы вокруг)
_CLOSE_RE = re.compile(r'^:::\s*$')
# Строка code-fence (``` или ``` с языком)
_CODE_FENCE_RE = re.compile(r'^```')


def extract_documents(text: str) -> tuple[str, list[dict]]:
    """Извлекает документы из текста ответа Codex.

    Returns:
        (cleaned_text, documents) — текст без блоков документов и список документов
        Каждый документ: {"filename": str, "content": str, "folder": str | None}

    Алгоритм (state-machine):
      - IDLE   — обычный текст, попадает в cleaned
      - IN_DOC — после :::document:..., собираем content до закрывающего :::;
                 внутри отслеживаем code-fence (``` ... ```) чтобы не принять
                 `:::` из примера кода за разделитель
    """
    lines = text.splitlines(keepends=False)
    documents: list[dict] = []

    cleaned_lines: list[str] = []
    # IN_DOC state
    current_filename: str | None = None
    current_folder: str | None = None
    current_content: list[str] = []
    in_code_fence = False

    for line in lines:
        if current_filename is None:
            # --- IDLE ---
            m = _OPEN_RE.match(line)
            if m:
                current_filename = m.group(1).strip()
                folder = m.group(2)
                current_folder = folder.strip() if folder else None
                current_content = []
                in_code_fence = False
                continue
            cleaned_lines.append(line)
        else:
            # --- IN_DOC ---
            if _CODE_FENCE_RE.match(line):
                in_code_fence = not in_code_fence
                current_content.append(line)
                continue
            if not in_code_fence and _CLOSE_RE.match(line):
                # Валидное закрытие — фиксируем документ
                content = "\n".join(current_content).strip()
                if current_filename and content:
                    documents.append({
                        "filename": current_filename,
                        "content": content,
                        "folder": current_folder,
                    })
                    folder_info = f" → папка '{current_folder}'" if current_folder else ""
                    logger.info(
                        "Извлечён документ: %s (%d символов)%s",
                        current_filename, len(content), folder_info,
                    )
                current_filename = None
                current_folder = None
                current_content = []
                in_code_fence = False
                continue
            current_content.append(line)

    # Незакрытый блок (нет закрывающего :::) — возвращаем исходный текст без удаления,
    # совместимо с прежним поведением regex-парсера в том же случае.
    if current_filename is not None:
        return text, []

    if not documents:
        return text, []

    cleaned = "\n".join(cleaned_lines).strip()
    # Убираем лишние пустые строки подряд (после удаления блоков)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    logger.info("Извлечено %d документов из ответа", len(documents))
    return cleaned, documents
