"""
S-фиксы rev big (v34): тесты Q1 / Q2 / Q3.

Q1 — SSE Queue maxsize=100
Q2 — MAX_FILE_SIZE = 50 МБ
Q3 — delete_project cascade: circuit_breaker, folders, links
"""

import asyncio
import io
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.state import State


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _mk_project(state: State, pid: str | None = None, name: str = "test") -> str:
    pid = pid or str(uuid.uuid4())
    state.execute(
        "INSERT INTO projects (id, name, created_at) VALUES (?, ?, ?)",
        (pid, name, "2026-01-01T00:00:00+00:00"),
    )
    state.commit()
    return pid


# ===========================================================================
# Q1 — SSE Queue maxsize=100
# ===========================================================================

class TestSSEQueueMaxsize:
    """Проверяем что SSE очередь имеет maxsize=100 и не растёт бесконечно."""

    def test_queue_has_maxsize_100(self):
        """asyncio.Queue создаётся с maxsize=100 в event_stream."""
        # Напрямую проверяем константу через импорт модуля
        import importlib
        import inspect
        from backend.api import system as system_mod

        # Получаем исходный код функции event_stream
        src = inspect.getsource(system_mod.event_stream)
        # Должно быть asyncio.Queue(maxsize=100) — не asyncio.Queue()
        assert "asyncio.Queue(maxsize=100)" in src, (
            "event_stream должен создавать asyncio.Queue(maxsize=100), "
            "иначе медленные клиенты накапливают события без ограничений"
        )

    def test_queue_full_raises_queue_full(self):
        """asyncio.Queue с maxsize=100 бросает QueueFull при переполнении."""
        q = asyncio.Queue(maxsize=100)
        for i in range(100):
            q.put_nowait(f"msg-{i}")
        # 101-й — должен бросить QueueFull
        with pytest.raises(asyncio.QueueFull):
            q.put_nowait("overflow")

    def test_publish_event_drops_slow_client(self, temp_db):
        """
        publish_event из main.py удаляет медленного клиента при QueueFull,
        а не крашит приложение.
        """
        from backend.main import _publish_event

        app = FastAPI()
        app.state.event_queues = []

        # Добавляем "медленного" клиента с maxsize=1 — сразу заполним
        slow_q = asyncio.Queue(maxsize=1)
        slow_q.put_nowait("already full")
        app.state.event_queues.append(slow_q)

        # Добавляем нормального клиента
        normal_q = asyncio.Queue(maxsize=100)
        app.state.event_queues.append(normal_q)

        async def run():
            await _publish_event(app, "task_update", {"task_id": "t1"})

        asyncio.run(run())

        # Медленный клиент должен быть удалён из списка
        assert slow_q not in app.state.event_queues
        # Нормальный клиент получил событие
        assert not normal_q.empty()


# ===========================================================================
# Q2 — MAX_FILE_SIZE = 50 МБ
# ===========================================================================

class TestMaxFileSize:
    """Проверяем лимит на размер загружаемого файла."""

    def test_max_file_size_constant(self):
        """MAX_FILE_SIZE равен ровно 50 МБ."""
        from backend.api.documents import MAX_FILE_SIZE
        assert MAX_FILE_SIZE == 50 * 1024 * 1024

    @pytest.fixture
    def app_and_client(self, temp_db, tmp_path):
        """FastAPI-приложение с роутером документов и временной БД."""
        from backend.api.documents import router

        async def mock_publish(event_type: str, data: dict):
            pass

        app = FastAPI()
        app.state.db = temp_db
        app.state.publish_event = mock_publish
        app.include_router(router, prefix="/api/documents")

        # Создаём тестовый проект
        pid = _mk_project(temp_db)
        # Создаём директорию для документов
        docs_dir = tmp_path / "documents"
        docs_dir.mkdir()

        # Переопределяем DOCUMENTS_DIR на tmp_path
        import backend.core.config as cfg_mod
        import backend.api.documents as docs_mod
        original_dir = cfg_mod.DOCUMENTS_DIR
        cfg_mod.DOCUMENTS_DIR = str(docs_dir)
        docs_mod.DOCUMENTS_DIR = str(docs_dir)  # type: ignore[attr-defined]

        client = TestClient(app, raise_server_exceptions=True)
        yield client, pid

        # Восстанавливаем DOCUMENTS_DIR
        cfg_mod.DOCUMENTS_DIR = original_dir
        docs_mod.DOCUMENTS_DIR = original_dir  # type: ignore[attr-defined]

    def test_upload_oversized_file_returns_413(self, app_and_client):
        """Файл > 50 МБ → HTTP 413 Payload Too Large."""
        client, pid = app_and_client

        # Создаём содержимое чуть больше 50 МБ
        oversized_content = b"X" * (50 * 1024 * 1024 + 1)
        response = client.post(
            f"/api/documents/{pid}/upload",
            files={"file": ("big.txt", io.BytesIO(oversized_content), "text/plain")},
        )
        assert response.status_code == 413, (
            f"Ожидался 413 для файла >50МБ, получен {response.status_code}: {response.text}"
        )
        # Сообщение должно содержать понятный текст на русском
        assert "МБ" in response.text or "слишком большой" in response.text

    def test_upload_normal_file_accepted(self, app_and_client):
        """Файл в пределах лимита → HTTP 201."""
        client, pid = app_and_client

        small_content = b"hello world"
        response = client.post(
            f"/api/documents/{pid}/upload",
            files={"file": ("small.txt", io.BytesIO(small_content), "text/plain")},
        )
        assert response.status_code == 201, (
            f"Ожидался 201 для нормального файла, получен {response.status_code}: {response.text}"
        )


# ===========================================================================
# Q3 — delete_project cascade: circuit_breaker
# ===========================================================================

class TestDeleteProjectCascade:
    """Удаление проекта очищает все связанные таблицы включая circuit_breaker."""

    @pytest.fixture
    def app_and_client(self, temp_db):
        """FastAPI-приложение с роутером проектов и временной БД."""
        from backend.api.projects import router

        async def mock_publish(event_type: str, data: dict):
            pass

        app = FastAPI()
        app.state.db = temp_db
        app.state.publish_event = mock_publish
        app.include_router(router, prefix="/api/projects")

        client = TestClient(app, raise_server_exceptions=True)
        return client, temp_db

    def test_delete_project_cascade_circuit_breaker(self, app_and_client):
        """
        DELETE /api/projects/{id} удаляет строку circuit_breaker с project_id.
        До: circuit_breaker содержит запись для проекта.
        После: запись исчезает.
        """
        client, state = app_and_client

        pid = _mk_project(state)

        # Вставляем строку в circuit_breaker для этого проекта
        state.execute(
            "INSERT INTO circuit_breaker (project_id, crash_count, last_crash) VALUES (?, ?, ?)",
            (pid, 3, "2026-01-01T00:00:00+00:00"),
        )
        state.commit()

        # Убеждаемся что строка есть
        row_before = state.fetchone(
            "SELECT project_id FROM circuit_breaker WHERE project_id = ?", (pid,)
        )
        assert row_before is not None, "Тест некорректен: строка circuit_breaker должна быть до удаления"

        # Удаляем проект
        response = client.delete(f"/api/projects/{pid}")
        assert response.status_code == 204, (
            f"DELETE /api/projects/{pid} вернул {response.status_code}: {response.text}"
        )

        # Строка circuit_breaker должна исчезнуть
        row_after = state.fetchone(
            "SELECT project_id FROM circuit_breaker WHERE project_id = ?", (pid,)
        )
        assert row_after is None, (
            "Orphan-запись в circuit_breaker осталась после удаления проекта"
        )

    def test_delete_project_cascade_folders(self, app_and_client):
        """
        DELETE /api/projects/{id} удаляет папки проекта.
        """
        client, state = app_and_client

        pid = _mk_project(state)
        folder_id = str(uuid.uuid4())

        state.execute(
            "INSERT INTO folders (id, project_id, name, created_at) VALUES (?, ?, ?, ?)",
            (folder_id, pid, "Тестовая папка", "2026-01-01T00:00:00+00:00"),
        )
        state.commit()

        response = client.delete(f"/api/projects/{pid}")
        assert response.status_code == 204

        row = state.fetchone("SELECT id FROM folders WHERE id = ?", (folder_id,))
        assert row is None, "Orphan-запись в folders осталась после удаления проекта"

    def test_delete_project_cascade_links(self, app_and_client):
        """
        DELETE /api/projects/{id} удаляет ссылки проекта.
        """
        client, state = app_and_client

        pid = _mk_project(state)
        link_id = str(uuid.uuid4())

        state.execute(
            "INSERT INTO links (id, project_id, title, url, created_at) VALUES (?, ?, ?, ?, ?)",
            (link_id, pid, "Test Link", "https://example.com", "2026-01-01T00:00:00+00:00"),
        )
        state.commit()

        response = client.delete(f"/api/projects/{pid}")
        assert response.status_code == 204

        row = state.fetchone("SELECT id FROM links WHERE id = ?", (link_id,))
        assert row is None, "Orphan-запись в links осталась после удаления проекта"

    def test_delete_project_webhooks_raw_not_touched(self, app_and_client):
        """
        webhooks_raw не привязан к project_id → строки остаются после удаления проекта.
        """
        client, state = app_and_client

        pid = _mk_project(state)
        webhook_id = str(uuid.uuid4())

        # Добавляем вебхук (без project_id — так и задумано по схеме)
        state.execute(
            "INSERT INTO webhooks_raw (id, source, payload, received_at) VALUES (?, ?, ?, ?)",
            (webhook_id, "b24", '{"event": "test"}', "2026-01-01T00:00:00+00:00"),
        )
        state.commit()

        response = client.delete(f"/api/projects/{pid}")
        assert response.status_code == 204

        # Строка webhooks_raw должна ОСТАТЬСЯ — она не привязана к проекту
        row = state.fetchone("SELECT id FROM webhooks_raw WHERE id = ?", (webhook_id,))
        assert row is not None, (
            "webhooks_raw не должен каскадно удаляться при удалении проекта "
            "(нет поля project_id)"
        )
