"""Извлечение документов из ответа Codex.

Codex может создавать документы в ответе используя формат:
    :::document:имя_файла.md
    содержимое документа
    :::

С указанием папки:
    :::document:имя_файла.md:Название папки
    содержимое документа
    :::

Парсер извлекает такие блоки, возвращает список документов
и очищенный текст (без блоков).
"""
import re
import logging

logger = logging.getLogger(__name__)

# Паттерн: :::document:filename[:folder]\ncontent\n:::
# Группа 1: filename (обязательно)
# Группа 2: :folder (опционально, с двоеточием)
# Группа 3: content
_DOC_PATTERN = re.compile(
    r':::document:([^:\n]+?)(?::([^\n]+?))?\s*\n(.*?):::',
    re.DOTALL,
)


def extract_documents(text: str) -> tuple[str, list[dict]]:
    """Извлекает документы из текста ответа Codex.

    Returns:
        (cleaned_text, documents) — текст без блоков документов и список документов
        Каждый документ: {"filename": str, "content": str, "folder": str | None}
    """
    documents = []

    for match in _DOC_PATTERN.finditer(text):
        filename = match.group(1).strip()
        folder = match.group(2)
        if folder:
            folder = folder.strip()
        content = match.group(3).strip()

        if filename and content:
            documents.append({
                "filename": filename,
                "content": content,
                "folder": folder or None,
            })
            folder_info = f" → папка '{folder}'" if folder else ""
            logger.info("Извлечён документ: %s (%d символов)%s", filename, len(content), folder_info)

    if not documents:
        return text, []

    # Убираем блоки документов из текста
    cleaned = _DOC_PATTERN.sub('', text).strip()

    # Убираем лишние пустые строки подряд (после удаления блоков)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    logger.info("Извлечено %d документов из ответа", len(documents))
    return cleaned, documents
