"""Circuit Breaker — per-project защита от каскадных крашей.

3 краша подряд → пауза 30 минут для проекта.
Успешное завершение → сброс счётчика.
Работает через HTTP API backend'а (worker не имеет прямого доступа к БД).
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# In-memory state (worker — один процесс)
_state: dict[str, dict] = {}

MAX_FAILURES = 3
TTL_MINUTES = 30


def can_execute(project_id: str) -> tuple[bool, str]:
    """Можно ли выполнять задачу для этого проекта.

    Returns: (можно, причина_блокировки)
    """
    info = _state.get(project_id)
    if not info or info["count"] < MAX_FAILURES:
        return True, ""

    # Проверяем TTL
    elapsed = datetime.now(timezone.utc) - info["last_crash"]
    if elapsed > timedelta(minutes=TTL_MINUTES):
        # TTL истёк — разрешаем одну попытку (half-open)
        logger.info("Circuit breaker half-open для %s (прошло %d мин)", project_id, elapsed.total_seconds() / 60)
        return True, ""

    remaining = TTL_MINUTES - elapsed.total_seconds() / 60
    return False, f"Circuit breaker: {info['count']} крашей для {project_id}, пауза ещё {remaining:.0f} мин"


def record_crash(project_id: str, error: str = "") -> None:
    """Зарегистрировать краш задачи."""
    info = _state.get(project_id, {"count": 0, "last_crash": None})
    info["count"] += 1
    info["last_crash"] = datetime.now(timezone.utc)
    _state[project_id] = info

    if info["count"] >= MAX_FAILURES:
        logger.warning(
            "Circuit breaker OPEN для %s: %d/%d крашей. Пауза %d мин. Ошибка: %s",
            project_id, info["count"], MAX_FAILURES, TTL_MINUTES, error[:200],
        )


def record_success(project_id: str) -> None:
    """Успешное завершение → сброс счётчика."""
    if project_id in _state and _state[project_id]["count"] > 0:
        prev = _state[project_id]["count"]
        _state[project_id] = {"count": 0, "last_crash": None}
        logger.info("Circuit breaker reset для %s (было %d крашей)", project_id, prev)


def get_status(project_id: str) -> dict:
    """Текущий статус для проекта."""
    info = _state.get(project_id, {"count": 0, "last_crash": None})
    is_open = info["count"] >= MAX_FAILURES
    ttl_remaining = None
    if is_open and info["last_crash"]:
        elapsed = (datetime.now(timezone.utc) - info["last_crash"]).total_seconds()
        ttl_remaining = max(0, TTL_MINUTES * 60 - elapsed)
    return {
        "count": info["count"],
        "max": MAX_FAILURES,
        "open": is_open and (ttl_remaining or 0) > 0,
        "ttl_remaining_seconds": ttl_remaining,
    }
