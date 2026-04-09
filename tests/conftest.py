"""Общие фикстуры для тестов."""
import sys
from pathlib import Path

import pytest

# Добавляем корень проекта в sys.path чтобы импорты backend.* / worker.* работали
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.state import State  # noqa: E402


@pytest.fixture
def temp_db(tmp_path):
    """State на временной SQLite-БД. Чистый teardown."""
    db_file = tmp_path / "test.db"
    state = State(db_path=str(db_file))
    yield state
    # Teardown: закрываем thread-local соединение если было открыто
    try:
        if hasattr(state._local, "conn") and state._local.conn is not None:
            state._local.conn.close()
            state._local.conn = None
    except Exception:
        pass
