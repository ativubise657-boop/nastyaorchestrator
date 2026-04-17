"""Circuit Breaker — per-project защита от каскадных крашей.

Порог (threshold) крашей подряд → пауза (cooldown_seconds) для проекта.
Успешное завершение → сброс счётчика.

Два режима:
  - Persistent (state передан): читает/пишет в таблицу circuit_breaker SQLite-БД.
    Счётчик крашей выживает рестарт worker'а.
  - In-memory (state=None): хранит состояние в dict процесса.
    Обратная совместимость и unit-тесты без БД.

Cooldown логика:
  - crash_count >= threshold И now - last_crash < cooldown_seconds → circuit OPEN
  - Если cooldown истёк → half-open: пускаем одну попытку (без сброса счётчика).
    При успехе — record_success сбросит счётчик.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.state import State

logger = logging.getLogger(__name__)

# Дефолты для module-level функций (legacy API)
MAX_FAILURES = 3
TTL_MINUTES = 30

# Глобальный экземпляр для module-level функций (создаётся без State → in-memory)
_default_breaker: "CircuitBreaker | None" = None


def _get_default() -> "CircuitBreaker":
    """Ленивая инициализация глобального breaker'а (legacy mode)."""
    global _default_breaker
    if _default_breaker is None:
        _default_breaker = CircuitBreaker(
            state=None,
            threshold=MAX_FAILURES,
            cooldown_seconds=TTL_MINUTES * 60,
        )
    return _default_breaker


def init_default(state: "State", threshold: int = MAX_FAILURES, cooldown_seconds: int = TTL_MINUTES * 60) -> None:
    """Инициализировать глобальный breaker с persistent State.

    Вызывать при старте worker'а (main.py / poller.py), передав готовый State.
    После этого module-level функции (can_execute, record_crash, record_success)
    будут использовать БД.

    Args:
        state: экземпляр State с открытой БД
        threshold: кол-во крашей до открытия circuit
        cooldown_seconds: пауза после открытия circuit
    """
    global _default_breaker
    _default_breaker = CircuitBreaker(
        state=state,
        threshold=threshold,
        cooldown_seconds=cooldown_seconds,
    )
    logger.info(
        "CircuitBreaker инициализирован в persistent-режиме (threshold=%d, cooldown=%ds)",
        threshold, cooldown_seconds,
    )


class CircuitBreaker:
    """Per-project circuit breaker с опциональным persistent store.

    Threshold и cooldown настраиваются при создании. По умолчанию:
      threshold=3 краша, cooldown=30 минут.

    Args:
        state: экземпляр State (persistent) или None (in-memory)
        threshold: сколько крашей подряд открывают circuit
        cooldown_seconds: пауза (в секундах) после открытия circuit
    """

    def __init__(
        self,
        state: "State | None" = None,
        threshold: int = MAX_FAILURES,
        cooldown_seconds: int = TTL_MINUTES * 60,
    ) -> None:
        self._state = state
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        # Fallback in-memory хранилище (если state=None)
        self._mem: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Запись крашей
    # ------------------------------------------------------------------

    def record_crash(self, project_id: str, error: str = "") -> None:
        """Зарегистрировать краш задачи для проекта.

        При достижении порога логирует предупреждение о открытии circuit.
        """
        if self._state:
            self._record_crash_db(project_id)
        else:
            self._record_crash_mem(project_id)

        # Проверяем порог для логирования
        count = self._get_count(project_id)
        if count >= self._threshold:
            logger.warning(
                "Circuit breaker OPEN для %s: %d/%d крашей. Пауза %ds. Ошибка: %s",
                project_id, count, self._threshold, self._cooldown, error[:200],
            )

    def _record_crash_db(self, project_id: str) -> None:
        """Атомарный upsert краша в БД."""
        now = datetime.now(timezone.utc).isoformat()
        row = self._state.fetchone(
            "SELECT crash_count FROM circuit_breaker WHERE project_id=?",
            (project_id,),
        )
        if row:
            self._state.execute(
                "UPDATE circuit_breaker SET crash_count=crash_count+1, last_crash=? WHERE project_id=?",
                (now, project_id),
            )
        else:
            self._state.execute(
                "INSERT INTO circuit_breaker (project_id, crash_count, last_crash) VALUES (?, 1, ?)",
                (project_id, now),
            )
        self._state.commit()

    def _record_crash_mem(self, project_id: str) -> None:
        """Запись краша в in-memory dict."""
        entry = self._mem.get(project_id, {"count": 0, "last_crash": None})
        entry["count"] += 1
        entry["last_crash"] = datetime.now(timezone.utc)
        self._mem[project_id] = entry

    # ------------------------------------------------------------------
    # Проверка состояния
    # ------------------------------------------------------------------

    def is_open(self, project_id: str) -> bool:
        """True если circuit открыт (слишком много крашей, cooldown не истёк).

        Half-open логика: если cooldown истёк — возвращаем False (пускаем
        одну попытку). При успехе caller должен вызвать record_success.
        """
        count = self._get_count(project_id)
        if count < self._threshold:
            return False

        last_crash = self._get_last_crash(project_id)
        if last_crash is None:
            return False

        elapsed = datetime.now(timezone.utc) - last_crash
        if elapsed >= timedelta(seconds=self._cooldown):
            # Cooldown истёк — half-open: пускаем попытку
            logger.info(
                "Circuit breaker half-open для %s (прошло %.0f мин)",
                project_id, elapsed.total_seconds() / 60,
            )
            return False

        return True

    def can_execute(self, project_id: str) -> tuple[bool, str]:
        """Можно ли выполнять задачу для проекта.

        Returns:
            (True, "") если разрешено
            (False, reason) если circuit открыт
        """
        if not self.is_open(project_id):
            return True, ""

        count = self._get_count(project_id)
        last_crash = self._get_last_crash(project_id)
        if last_crash:
            elapsed = datetime.now(timezone.utc) - last_crash
            remaining_s = self._cooldown - elapsed.total_seconds()
            remaining_min = max(0, remaining_s / 60)
            return (
                False,
                f"Circuit breaker: {count} крашей для {project_id}, пауза ещё {remaining_min:.0f} мин",
            )
        return False, f"Circuit breaker: {count} крашей для {project_id}"

    # ------------------------------------------------------------------
    # Сброс счётчика
    # ------------------------------------------------------------------

    def record_success(self, project_id: str) -> None:
        """Успешное завершение задачи → сброс счётчика крашей."""
        prev = self._get_count(project_id)
        if prev == 0:
            return  # Нечего сбрасывать

        if self._state:
            self._state.execute(
                "DELETE FROM circuit_breaker WHERE project_id=?",
                (project_id,),
            )
            self._state.commit()
        else:
            self._mem.pop(project_id, None)

        logger.info("Circuit breaker reset для %s (было %d крашей)", project_id, prev)

    def reset(self, project_id: str) -> None:
        """Алиас record_success — для совместимости с тестами и старым кодом."""
        self.record_success(project_id)

    # ------------------------------------------------------------------
    # Статус
    # ------------------------------------------------------------------

    def get_status(self, project_id: str) -> dict:
        """Текущий статус circuit для проекта."""
        count = self._get_count(project_id)
        last_crash = self._get_last_crash(project_id)
        circuit_open = count >= self._threshold

        ttl_remaining = None
        if circuit_open and last_crash:
            elapsed = (datetime.now(timezone.utc) - last_crash).total_seconds()
            remaining = self._cooldown - elapsed
            ttl_remaining = max(0.0, remaining)

        return {
            "count": count,
            "max": self._threshold,
            "open": circuit_open and (ttl_remaining or 0) > 0,
            "ttl_remaining_seconds": ttl_remaining,
        }

    # ------------------------------------------------------------------
    # Вспомогательные (внутренние)
    # ------------------------------------------------------------------

    def _get_count(self, project_id: str) -> int:
        """Текущий счётчик крашей."""
        if self._state:
            row = self._state.fetchone(
                "SELECT crash_count FROM circuit_breaker WHERE project_id=?",
                (project_id,),
            )
            return row["crash_count"] if row else 0
        return self._mem.get(project_id, {}).get("count", 0)

    def _get_last_crash(self, project_id: str) -> "datetime | None":
        """Время последнего краша (UTC datetime или None)."""
        if self._state:
            row = self._state.fetchone(
                "SELECT last_crash FROM circuit_breaker WHERE project_id=?",
                (project_id,),
            )
            if row and row["last_crash"]:
                dt = datetime.fromisoformat(row["last_crash"])
                # Гарантируем timezone-aware
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            return None
        entry = self._mem.get(project_id)
        if entry and entry.get("last_crash"):
            return entry["last_crash"]
        return None


# ------------------------------------------------------------------
# Module-level API (legacy — обратная совместимость с poller.py)
# ------------------------------------------------------------------

def can_execute(project_id: str) -> tuple[bool, str]:
    """Можно ли выполнять задачу для этого проекта.

    Returns: (можно, причина_блокировки)
    """
    return _get_default().can_execute(project_id)


def record_crash(project_id: str, error: str = "") -> None:
    """Зарегистрировать краш задачи."""
    _get_default().record_crash(project_id, error)


def record_success(project_id: str) -> None:
    """Успешное завершение → сброс счётчика."""
    _get_default().record_success(project_id)


def get_status(project_id: str) -> dict:
    """Текущий статус для проекта."""
    return _get_default().get_status(project_id)
