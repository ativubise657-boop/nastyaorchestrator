"""Quality Gate — автоматическая проверка результата задачи.

Эвристики без LLM: пустой результат, ключевые слова ошибок/успеха.
Дёшево и быстро (~0 токенов).
"""

import logging

logger = logging.getLogger(__name__)

# Ключевые слова провала в хвосте результата
_FAIL_KEYWORDS = [
    "не могу", "не удалось", "ошибка", "error", "failed",
    "exception", "traceback", "cannot", "unable to",
]

# Ключевые слова успеха
_SUCCESS_KEYWORDS = [
    "готово", "done", "выполнено", "завершено", "✅",
    "успешно", "completed", "finished",
]

# Максимум retry
MAX_RETRIES = 2


def evaluate(result: str, prompt: str = "") -> dict:
    """Оценить результат задачи по эвристикам.

    Returns:
        {
            "passed": True/False,
            "score": 0-10,
            "issues": [...],
            "suggestion": "...",
        }
    """
    issues: list[str] = []
    score = 5

    # Пустой или слишком короткий результат
    if not result or len(result.strip()) < 50:
        return {
            "passed": False,
            "score": 0,
            "issues": ["пустой результат — агент ничего не вернул"],
            "suggestion": (
                "Предыдущая попытка завершилась без вывода. "
                "Убедись, что задача выполнима, и опиши конкретный результат."
            ),
        }

    # Проверяем хвост на ошибки
    tail = result[-300:].lower()
    for kw in _FAIL_KEYWORDS:
        if kw in tail:
            issues.append(f"результат содержит признак ошибки: «{kw}»")
            score -= 3
            break

    # Проверяем на успех
    for kw in _SUCCESS_KEYWORDS:
        if kw in tail:
            score += 1
            break

    score = max(0, min(10, score))
    passed = score >= 4

    suggestion = ""
    if not passed:
        problems = "; ".join(issues) if issues else "низкий score"
        suggestion = (
            f"Предыдущая попытка не удалась: {problems}. "
            "Попробуй другой подход или разбей задачу на шаги."
        )

    return {
        "passed": passed,
        "score": score,
        "issues": issues,
        "suggestion": suggestion,
    }


def should_retry(evaluation: dict, retry_count: int) -> bool:
    """Нужен ли повторный запуск."""
    return not evaluation["passed"] and retry_count < MAX_RETRIES
