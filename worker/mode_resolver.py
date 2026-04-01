"""Определение режима выполнения задачи.

v1 — простые правила по ключевым словам.
v2 (TODO) — LLM-классификатор через быстрый codex exec вызов.
"""
import logging

logger = logging.getLogger(__name__)


# Ключевые слова для каждого режима
_REV_KEYWORDS = frozenset([
    "ревью", "review", "rev", "проверь код", "code review",
    "посмотри код", "что не так с кодом",
])

_AG_PLUS_KEYWORDS = frozenset([
    "ag+", "команда", "несколько файлов", "рефакторинг",
    "refactor", "рефактор", "большой", "масштабный",
    "архитектура", "architecture", "мигрир", "migrat",
])


def resolve_mode(prompt: str) -> str:
    """Определяет режим выполнения по содержимому промпта.

    Возвращает:
        "rev"  — код-ревью
        "ag+"  — агентная команда (несколько субагентов)
        "solo" — обычное выполнение (дефолт)
    """
    prompt_lower = prompt.lower()

    if any(kw in prompt_lower for kw in _REV_KEYWORDS):
        logger.debug("Режим определён: rev (ревью кода)")
        return "rev"

    if any(kw in prompt_lower for kw in _AG_PLUS_KEYWORDS):
        logger.debug("Режим определён: ag+ (агентная команда)")
        return "ag+"

    logger.debug("Режим определён: solo (дефолт)")
    return "solo"
