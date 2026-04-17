"""Тесты circuit breaker — in-memory и persistent (SQLite) режимы.

Покрывает:
  - in-memory режим: порог, изоляция проектов, reset
  - persistent режим: данные выживают создание нового экземпляра
  - cooldown: circuit закрывается после истечения паузы
  - reset сбрасывает счётчик в обоих режимах
  - изоляция проектов в persistent режиме
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Добавляем корень проекта в sys.path (conftest делает это, но на случай прямого запуска)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.circuit_breaker import CircuitBreaker
from backend.core.state import State


# ===========================================================================
# In-memory режим
# ===========================================================================

class TestInMemoryMode:
    """Circuit breaker без БД (state=None)."""

    def test_below_threshold_not_open(self):
        """До порога circuit закрыт."""
        cb = CircuitBreaker(state=None, threshold=3)
        cb.record_crash("p1")
        cb.record_crash("p1")
        # 2 краша < threshold=3 → circuit закрыт
        assert not cb.is_open("p1")

    def test_at_threshold_opens(self):
        """На пороге circuit открывается."""
        cb = CircuitBreaker(state=None, threshold=3)
        cb.record_crash("p1")
        cb.record_crash("p1")
        cb.record_crash("p1")  # 3-й → open
        assert cb.is_open("p1")

    def test_can_execute_blocks_when_open(self):
        """can_execute возвращает (False, reason) когда circuit открыт."""
        cb = CircuitBreaker(state=None, threshold=2, cooldown_seconds=3600)
        cb.record_crash("proj")
        cb.record_crash("proj")
        ok, reason = cb.can_execute("proj")
        assert not ok
        assert "proj" in reason
        assert "2" in reason  # счётчик в сообщении

    def test_reset_clears_counter(self):
        """record_success (reset) сбрасывает счётчик."""
        cb = CircuitBreaker(state=None, threshold=2)
        cb.record_crash("p1")
        cb.record_crash("p1")
        assert cb.is_open("p1")
        cb.record_success("p1")
        assert not cb.is_open("p1")

    def test_different_projects_isolated(self):
        """Краши одного проекта не влияют на другой."""
        cb = CircuitBreaker(state=None, threshold=2)
        cb.record_crash("proj-A")
        cb.record_crash("proj-A")
        # proj-B чист
        assert not cb.is_open("proj-B")
        ok, _ = cb.can_execute("proj-B")
        assert ok

    def test_get_status_structure(self):
        """get_status возвращает корректную структуру."""
        cb = CircuitBreaker(state=None, threshold=3)
        status = cb.get_status("p1")
        assert "count" in status
        assert "max" in status
        assert "open" in status
        assert "ttl_remaining_seconds" in status
        assert status["max"] == 3

    def test_no_crashes_can_execute(self):
        """Без крашей — can_execute всегда True."""
        cb = CircuitBreaker(state=None, threshold=3)
        ok, reason = cb.can_execute("brand-new-project")
        assert ok
        assert reason == ""


# ===========================================================================
# Persistent режим (SQLite)
# ===========================================================================

class TestPersistentMode:
    """Circuit breaker с реальной SQLite БД."""

    def test_crash_persists_across_instances(self, tmp_path):
        """Счётчик крашей сохраняется при создании нового экземпляра."""
        db = str(tmp_path / "test.db")
        state1 = State(db)
        cb1 = CircuitBreaker(state=state1, threshold=3)
        cb1.record_crash("p1")

        # Новый экземпляр с той же БД — должен видеть crash_count=1
        state2 = State(db)
        cb2 = CircuitBreaker(state=state2, threshold=3)
        row = state2.fetchone("SELECT crash_count FROM circuit_breaker WHERE project_id='p1'")
        assert row is not None
        assert row["crash_count"] == 1

    def test_at_threshold_persistent_open(self, tmp_path):
        """В persistent-режиме circuit открывается на пороге и виден из нового экземпляра."""
        db = str(tmp_path / "test.db")
        state = State(db)
        cb1 = CircuitBreaker(state=state, threshold=3, cooldown_seconds=3600)
        cb1.record_crash("p1")
        cb1.record_crash("p1")
        cb1.record_crash("p1")

        # Новый экземпляр с той же БД
        state2 = State(db)
        cb2 = CircuitBreaker(state=state2, threshold=3, cooldown_seconds=3600)
        assert cb2.is_open("p1")

    def test_reset_removes_from_db(self, tmp_path):
        """record_success удаляет запись из circuit_breaker в БД."""
        db = str(tmp_path / "test.db")
        state = State(db)
        cb = CircuitBreaker(state=state, threshold=2)
        cb.record_crash("p1")
        cb.record_crash("p1")
        assert cb.is_open("p1")

        cb.record_success("p1")

        # Запись должна исчезнуть из БД
        row = state.fetchone("SELECT * FROM circuit_breaker WHERE project_id='p1'")
        assert row is None
        assert not cb.is_open("p1")

    def test_different_projects_isolated_persistent(self, tmp_path):
        """Crash одного проекта не влияет на другой в БД."""
        db = str(tmp_path / "test.db")
        state = State(db)
        cb = CircuitBreaker(state=state, threshold=2)
        cb.record_crash("proj-A")
        cb.record_crash("proj-A")
        assert cb.is_open("proj-A")

        # proj-B чист
        assert not cb.is_open("proj-B")
        ok, _ = cb.can_execute("proj-B")
        assert ok

    def test_multiple_crashes_increment_count(self, tmp_path):
        """Каждый record_crash атомарно инкрементирует crash_count в БД."""
        db = str(tmp_path / "test.db")
        state = State(db)
        cb = CircuitBreaker(state=state, threshold=10)
        for _ in range(5):
            cb.record_crash("p1")

        row = state.fetchone("SELECT crash_count FROM circuit_breaker WHERE project_id='p1'")
        assert row["crash_count"] == 5


# ===========================================================================
# Cooldown логика
# ===========================================================================

class TestCooldown:
    """Cooldown: circuit открыт пока не истёк таймаут."""

    def test_cooldown_keeps_circuit_open(self, tmp_path):
        """Свежие краши блокируют выполнение."""
        db = str(tmp_path / "test.db")
        state = State(db)
        # cooldown 1 час — не истечёт за время теста
        cb = CircuitBreaker(state=state, threshold=2, cooldown_seconds=3600)
        cb.record_crash("p1")
        cb.record_crash("p1")
        assert cb.is_open("p1")
        ok, _ = cb.can_execute("p1")
        assert not ok

    def test_cooldown_expiry_half_open(self, tmp_path):
        """После истечения cooldown circuit переходит в half-open (is_open=False)."""
        db = str(tmp_path / "test.db")
        state = State(db)
        # cooldown = 1 секунда (уже истёк — запишем прошлое время напрямую)
        cb = CircuitBreaker(state=state, threshold=2, cooldown_seconds=1)
        cb.record_crash("p1")
        cb.record_crash("p1")

        # Вручную откатываем last_crash в прошлое (5 минут назад) — cooldown 1s точно истёк
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        state.execute(
            "UPDATE circuit_breaker SET last_crash=? WHERE project_id='p1'",
            (past,),
        )
        state.commit()

        # Half-open: is_open должен быть False (разрешаем попытку)
        assert not cb.is_open("p1")
        ok, _ = cb.can_execute("p1")
        assert ok

    def test_in_memory_cooldown_expiry(self):
        """In-memory режим: cooldown истекает — circuit half-open."""
        cb = CircuitBreaker(state=None, threshold=2, cooldown_seconds=1)
        cb.record_crash("p1")
        cb.record_crash("p1")

        # Вручную откатываем last_crash в прошлое через _mem
        cb._mem["p1"]["last_crash"] = datetime.now(timezone.utc) - timedelta(minutes=5)

        assert not cb.is_open("p1")
        ok, _ = cb.can_execute("p1")
        assert ok


# ===========================================================================
# Alias и legacy API
# ===========================================================================

class TestLegacyModuleAPI:
    """Module-level функции (legacy poller.py API) работают корректно."""

    def test_module_functions_importable(self):
        """can_execute, record_crash, record_success, get_status импортируются."""
        from worker.circuit_breaker import can_execute, record_crash, record_success, get_status
        assert callable(can_execute)
        assert callable(record_crash)
        assert callable(record_success)
        assert callable(get_status)

    def test_reset_alias(self, tmp_path):
        """reset() работает как алиас record_success()."""
        db = str(tmp_path / "test.db")
        state = State(db)
        cb = CircuitBreaker(state=state, threshold=2)
        cb.record_crash("p1")
        cb.record_crash("p1")
        cb.reset("p1")  # alias
        assert not cb.is_open("p1")
