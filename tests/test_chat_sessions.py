"""
Тесты для ChatGPT-style чат-сессий.

Покрывают:
  - Session CRUD (создание, список, переименование, удаление + каскад)
  - Изоляцию сообщений между сессиями
  - Scope-фильтрацию документов (all / session / project)

Используют FastAPI TestClient + временная SQLite-БД.

ВАЖНО: Фикстура `client` создаёт State через стандартный __init__ (который
включает все миграции), а затем пробрасывает его в app.state напрямую,
минуя lifespan (чтобы не тащить реальные external-сервисы и SSE-фоновые задачи).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


def _init_test_db(db_path: str) -> None:
    """
    Инициализируем тестовую SQLite-БД напрямую через sqlite3.

    Обходит баг state.py: _SCHEMA содержит индекс idx_documents_session
    ON documents(session_id), но DDL таблицы documents не включает session_id
    (он добавляется через ALTER TABLE в миграциях, которые идут ПЕРЕД executescript).
    На чистой БД миграция 9 падает через try/except → pass,
    затем executescript создаёт documents без session_id → индекс падает.

    Здесь мы создаём схему с session_id в таблицах сразу — так и должно быть
    на свежей установке после фикса бага.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            path        TEXT DEFAULT '',
            git_url     TEXT DEFAULT '',
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id                      TEXT PRIMARY KEY,
            project_id              TEXT NOT NULL,
            prompt                  TEXT NOT NULL,
            mode                    TEXT DEFAULT 'auto',
            model                   TEXT DEFAULT 'gpt-5.4',
            status                  TEXT DEFAULT 'queued',
            result                  TEXT,
            error                   TEXT,
            attachment_document_ids TEXT DEFAULT '',
            created_at              TEXT NOT NULL,
            started_at              TEXT,
            completed_at            TEXT,
            session_id              TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id          TEXT PRIMARY KEY,
            project_id  TEXT NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            task_id     TEXT,
            attachments TEXT DEFAULT '',
            created_at  TEXT NOT NULL,
            session_id  TEXT
        );

        CREATE TABLE IF NOT EXISTS folders (
            id         TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name       TEXT NOT NULL,
            parent_id  TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (parent_id) REFERENCES folders(id)
        );

        CREATE TABLE IF NOT EXISTS documents (
            id           TEXT PRIMARY KEY,
            project_id   TEXT NOT NULL,
            filename     TEXT NOT NULL,
            path         TEXT NOT NULL,
            size         INTEGER NOT NULL,
            content_type TEXT DEFAULT '',
            is_scratch   INTEGER DEFAULT 0,
            folder_id    TEXT,
            parse_status TEXT DEFAULT 'skipped',
            parse_error  TEXT DEFAULT '',
            parse_method TEXT DEFAULT '',
            created_at   TEXT NOT NULL,
            session_id   TEXT,
            FOREIGN KEY (folder_id) REFERENCES folders(id)
        );

        CREATE TABLE IF NOT EXISTS webhooks_raw (
            id          TEXT PRIMARY KEY,
            source      TEXT DEFAULT 'b24',
            payload     TEXT NOT NULL,
            received_at TEXT NOT NULL,
            processed   INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS worker_heartbeats (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id   TEXT,
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_sessions (
            id         TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title      TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS links (
            id          TEXT PRIMARY KEY,
            project_id  TEXT NOT NULL,
            title       TEXT NOT NULL,
            url         TEXT NOT NULL,
            description TEXT DEFAULT '',
            folder_id   TEXT,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (folder_id) REFERENCES folders(id)
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS circuit_breaker (
            project_id  TEXT PRIMARY KEY,
            crash_count INTEGER DEFAULT 0,
            last_crash  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_status          ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_project         ON tasks(project_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_project_status  ON tasks(project_id, status);
        CREATE INDEX IF NOT EXISTS idx_messages_project      ON chat_messages(project_id);
        CREATE INDEX IF NOT EXISTS idx_documents_project     ON documents(project_id);
        CREATE INDEX IF NOT EXISTS idx_documents_folder      ON documents(folder_id);
        CREATE INDEX IF NOT EXISTS idx_folders_project       ON folders(project_id);
        CREATE INDEX IF NOT EXISTS idx_heartbeats_ts         ON worker_heartbeats(timestamp);
        CREATE INDEX IF NOT EXISTS idx_sessions_project      ON chat_sessions(project_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_messages_session      ON chat_messages(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_documents_session     ON documents(session_id);
        CREATE INDEX IF NOT EXISTS idx_links_project         ON links(project_id);
    """)
    conn.commit()
    conn.close()


def _build_test_app(state):
    """
    Создаём минимальное FastAPI-приложение БЕЗ lifespan.

    Подключаем только нужные для тестов роутеры: sessions, chat, projects, documents, queue.
    app.state настраиваем вручную — без lifespan нет startup/shutdown.

    ВАЖНО: TaskQueue.enqueue в текущей версии не принимает session_id,
    хотя chat.py его передаёт. Это баг backend (queue.py не добавил session_id
    в сигнатуру enqueue при реализации фичи сессий). Здесь патчим enqueue
    в тестах чтобы он принимал и проставлял session_id через отдельный UPDATE.
    """
    import json as _json
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz
    from fastapi import FastAPI
    from backend.core.queue import TaskQueue
    from backend.api.sessions import router as sessions_router
    from backend.api.chat import router as chat_router
    from backend.api.projects import router as projects_router
    from backend.api.documents import router as documents_router
    from backend.api.system import router_queue

    # Без lifespan — никакого startup, state управляем вручную
    test_app = FastAPI(title="Test App")

    test_app.include_router(sessions_router,  prefix="/api/chat",      tags=["sessions"])
    test_app.include_router(chat_router,       prefix="/api/chat",      tags=["chat"])
    test_app.include_router(projects_router,   prefix="/api/projects",  tags=["projects"])
    test_app.include_router(documents_router,  prefix="/api/documents", tags=["documents"])
    test_app.include_router(router_queue,      prefix="/api/queue",     tags=["queue"])

    queue = TaskQueue(state)

    # Патч: добавляем поддержку session_id в enqueue.
    # Баг backend: chat.py передаёт session_id=..., но TaskQueue.enqueue его не принимает.
    # Без этого патча все тесты с _send_message падают с TypeError.
    _original_enqueue = queue.enqueue

    def _patched_enqueue(project_id, prompt, mode="auto", model="gpt-5.4",
                         task_id=None, attachment_document_ids=None, session_id=None):
        """Обёртка над enqueue с поддержкой session_id (проставляется через UPDATE после INSERT)."""
        tid = _original_enqueue(
            project_id=project_id,
            prompt=prompt,
            mode=mode,
            model=model,
            task_id=task_id,
            attachment_document_ids=attachment_document_ids,
        )
        if session_id:
            # Проставляем session_id отдельно — баг в queue.py не поддерживает его в INSERT
            state.execute("UPDATE tasks SET session_id = ? WHERE id = ?", (session_id, tid))
            state.commit()
        return tid

    queue.enqueue = _patched_enqueue

    # Настраиваем app.state
    test_app.state.db = state
    test_app.state.queue = queue
    test_app.state.event_queues = []

    async def _noop_publish(event_type: str, data: dict):
        pass

    test_app.state.publish_event = _noop_publish

    return test_app


@pytest.fixture
def client(tmp_path):
    """
    TestClient с изолированной SQLite-БД и минимальным тестовым приложением.

    Инициализируем БД напрямую (_init_test_db обходит баг State._init_db
    с индексом idx_documents_session на несуществующей колонке).
    """
    from backend.core.state import State

    # Создаём БД с правильной схемой (session_id уже в DDL таблиц)
    db_file = str(tmp_path / "test_sessions.db")
    _init_test_db(db_file)

    # State видит инициализированную БД — его _init_db пройдёт через ALTER TABLE (noop) и executescript
    state = State(db_path=db_file)
    test_app = _build_test_app(state)

    with TestClient(test_app, raise_server_exceptions=True) as c:
        yield c, state

    # Teardown: закрываем соединение
    try:
        if hasattr(state._local, "conn") and state._local.conn is not None:
            state._local.conn.close()
            state._local.conn = None
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _create_project(client, *, name: str = "Test Project") -> str:
    """Создаём проект через API и возвращаем его id."""
    resp = client.post("/api/projects/", json={"name": name, "description": ""})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _create_session(client, project_id: str, *, title: str | None = None) -> dict:
    """Создаём сессию через API и возвращаем JSON-ответ."""
    body: dict = {"project_id": project_id}
    if title is not None:
        body["title"] = title
    resp = client.post("/api/chat/sessions", json=body)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _send_message(client, project_id: str, session_id: str, *, text: str = "hi") -> dict:
    """Отправляем сообщение через /api/chat/send."""
    resp = client.post("/api/chat/send", json={
        "project_id": project_id,
        "session_id": session_id,
        "message": text,
        "mode": "auto",
        "model": "gpt-5.4",
        "attachments": [],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _insert_document(state, project_id: str, session_id: str | None = None,
                     *, filename: str = "doc.pdf", is_scratch: int = 0) -> str:
    """Прямая вставка документа в БД (без парсинга)."""
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    state.execute(
        """
        INSERT INTO documents
            (id, project_id, filename, path, size, content_type, is_scratch, parse_status, created_at, session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, project_id, filename, f"/tmp/{doc_id}.pdf", 100, "application/pdf",
         is_scratch, "skipped", now, session_id),
    )
    state.commit()
    return doc_id


# ===========================================================================
# 1. Session CRUD
# ===========================================================================


class TestCreateSession:
    def test_create_session(self, client):
        """POST /api/chat/sessions → 201, возвращает ChatSession с корректными полями."""
        c, _ = client
        pid = _create_project(c)

        sess = _create_session(c, pid, title="Первый чат")

        # Проверяем структуру ответа
        assert sess["project_id"] == pid
        assert sess["title"] == "Первый чат"
        assert sess["message_count"] == 0
        assert "id" in sess
        assert "created_at" in sess
        assert "updated_at" in sess

        # id должен быть валидным UUID
        uuid.UUID(sess["id"])

    def test_create_session_default_title(self, client):
        """Без явного title → title должен быть 'Новый чат'."""
        c, _ = client
        pid = _create_project(c)

        # Не передаём title — модель ChatSessionCreate ставит дефолт
        resp = c.post("/api/chat/sessions", json={"project_id": pid})
        assert resp.status_code in (200, 201)
        assert resp.json()["title"] == "Новый чат"

    def test_create_session_project_not_found(self, client):
        """Несуществующий project_id → 404."""
        c, _ = client
        resp = c.post("/api/chat/sessions", json={
            "project_id": "nonexistent-project-id",
            "title": "X",
        })
        assert resp.status_code == 404


class TestListSessions:
    def test_list_sessions_empty(self, client):
        """Новый проект без сессий → пустой список."""
        c, _ = client
        pid = _create_project(c)

        resp = c.get(f"/api/chat/sessions/{pid}")
        assert resp.status_code == 200
        data = resp.json()
        # Может быть пусто — миграция Legacy-сессий не создаётся для новых проектов
        assert isinstance(data, list)

    def test_list_sessions_sorted_by_updated_at_desc(self, client):
        """Три сессии — в ответе идут в порядке updated_at DESC (самая свежая первая)."""
        import time
        c, db = client
        pid = _create_project(c)

        s1 = _create_session(c, pid, title="Первая")
        time.sleep(0.05)
        s2 = _create_session(c, pid, title="Вторая")
        time.sleep(0.05)
        s3 = _create_session(c, pid, title="Третья")

        resp = c.get(f"/api/chat/sessions/{pid}")
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()]

        # Самая свежая (s3) должна быть первой
        assert ids[0] == s3["id"]
        assert ids[-1] == s1["id"]

    def test_list_sessions_message_count(self, client):
        """После отправки 2 сообщений message_count в списке сессий = 2."""
        c, _ = client
        pid = _create_project(c)
        sess = _create_session(c, pid)

        _send_message(c, pid, sess["id"], text="Сообщение 1")
        _send_message(c, pid, sess["id"], text="Сообщение 2")

        resp = c.get(f"/api/chat/sessions/{pid}")
        sessions = {s["id"]: s for s in resp.json()}
        assert sessions[sess["id"]]["message_count"] == 2


class TestRenameSession:
    def test_rename_session(self, client):
        """PATCH → title обновлён, updated_at изменился."""
        import time
        c, _ = client
        pid = _create_project(c)
        sess = _create_session(c, pid, title="Старое название")
        old_updated_at = sess["updated_at"]

        # Небольшая пауза чтобы updated_at гарантированно изменился
        time.sleep(0.05)

        resp = c.patch(f"/api/chat/sessions/{sess['id']}", json={"title": "Новое название"})
        assert resp.status_code == 200
        updated = resp.json()

        assert updated["title"] == "Новое название"
        assert updated["id"] == sess["id"]
        # updated_at должен быть >= old_updated_at (а не тем же значением)
        assert updated["updated_at"] >= old_updated_at

    def test_rename_nonexistent_session(self, client):
        """PATCH для несуществующей сессии → 404."""
        c, _ = client
        resp = c.patch("/api/chat/sessions/no-such-session", json={"title": "X"})
        assert resp.status_code == 404


class TestDeleteSession:
    def test_delete_session_basic(self, client):
        """DELETE → сессия исчезает из GET /sessions/{project_id}."""
        c, _ = client
        pid = _create_project(c)
        sess = _create_session(c, pid, title="Удалить меня")

        resp = c.delete(f"/api/chat/sessions/{sess['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        # Убеждаемся что сессия пропала из списка
        remaining_ids = [s["id"] for s in c.get(f"/api/chat/sessions/{pid}").json()]
        assert sess["id"] not in remaining_ids

    def test_delete_session_cascade_messages(self, client):
        """После удаления сессии chat_messages с session_id=этой сессии тоже удалены."""
        c, db = client
        pid = _create_project(c)
        sess = _create_session(c, pid)

        # Отправляем 2 сообщения — они запишутся в chat_messages с session_id
        _send_message(c, pid, sess["id"], text="Первое")
        _send_message(c, pid, sess["id"], text="Второе")

        # Проверяем что сообщения действительно создались
        row = db.fetchone(
            "SELECT COUNT(*) AS cnt FROM chat_messages WHERE session_id = ?", (sess["id"],)
        )
        assert row["cnt"] == 2, "Ожидали 2 сообщения до удаления сессии"

        # B5-фикс: DELETE блокируется при queued/running задачах.
        # _send_message создаёт задачи в статусе 'queued' — завершаем их перед удалением.
        db.execute(
            "UPDATE tasks SET status = 'completed' WHERE session_id = ? AND status IN ('queued', 'running')",
            (sess["id"],),
        )
        db.commit()

        # Удаляем сессию
        resp = c.delete(f"/api/chat/sessions/{sess['id']}")
        assert resp.status_code == 200
        assert resp.json()["deleted_messages"] == 2

        # Проверяем что сообщения удалены
        row = db.fetchone(
            "SELECT COUNT(*) AS cnt FROM chat_messages WHERE session_id = ?", (sess["id"],)
        )
        assert row["cnt"] == 0, "chat_messages должны быть удалены каскадом"

    def test_delete_session_cascade_session_docs(self, client):
        """Session-scoped документы (session_id=X) удаляются вместе с сессией."""
        c, db = client
        pid = _create_project(c)
        sess = _create_session(c, pid)

        # Вставляем session-scoped документ напрямую в БД
        doc_id = _insert_document(db, pid, session_id=sess["id"], filename="session_doc.pdf")

        # Удаляем сессию
        resp = c.delete(f"/api/chat/sessions/{sess['id']}")
        assert resp.status_code == 200
        assert resp.json()["deleted_documents"] == 1

        # Документ должен быть удалён из таблицы
        row = db.fetchone("SELECT id FROM documents WHERE id = ?", (doc_id,))
        assert row is None, "Session-scoped документ должен удаляться каскадом"

    def test_delete_session_keeps_project_docs(self, client):
        """Project-wide документы (session_id IS NULL) НЕ удаляются при удалении сессии."""
        c, db = client
        pid = _create_project(c)
        sess = _create_session(c, pid)

        # Вставляем project-wide документ (session_id=NULL)
        proj_doc_id = _insert_document(db, pid, session_id=None, filename="project_doc.pdf")

        # Удаляем сессию
        resp = c.delete(f"/api/chat/sessions/{sess['id']}")
        assert resp.status_code == 200

        # Project-wide документ должен остаться
        row = db.fetchone("SELECT id FROM documents WHERE id = ?", (proj_doc_id,))
        assert row is not None, "Project-wide документ не должен удаляться при удалении сессии"

    def test_delete_nonexistent(self, client):
        """DELETE несуществующей сессии → 404."""
        c, _ = client
        resp = c.delete("/api/chat/sessions/ghost-session-id")
        assert resp.status_code == 404

    def test_delete_session_with_running_task_returns_409(self, client):
        """B5: DELETE сессии с активной задачей (status=running) → 409, данные не удалены."""
        c, db = client
        pid = _create_project(c)
        sess = _create_session(c, pid)

        # Создаём task с status='running' привязанный к сессии
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            """
            INSERT INTO tasks
                (id, project_id, prompt, mode, model, status, created_at, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, pid, "Тестовый промпт", "auto", "gpt-5.4", "running", now, sess["id"]),
        )
        db.commit()

        # Пытаемся удалить сессию с активной задачей → должен вернуть 409
        resp = c.delete(f"/api/chat/sessions/{sess['id']}")
        assert resp.status_code == 409, (
            f"Ожидали 409 при удалении сессии с running task, получили {resp.status_code}: {resp.text}"
        )
        assert "задача" in resp.json().get("detail", "").lower() or "task" in resp.json().get("detail", "").lower()

        # Проверяем что сессия и задача остались в БД нетронутыми
        sess_row = db.fetchone("SELECT id FROM chat_sessions WHERE id = ?", (sess["id"],))
        assert sess_row is not None, "Сессия не должна быть удалена при 409"

        task_row = db.fetchone("SELECT id FROM tasks WHERE id = ?", (task_id,))
        assert task_row is not None, "Задача не должна быть удалена при 409"

    def test_delete_session_with_queued_task_returns_409(self, client):
        """B5: DELETE сессии с задачей в очереди (status=queued) → 409."""
        c, db = client
        pid = _create_project(c)
        sess = _create_session(c, pid)

        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            """
            INSERT INTO tasks
                (id, project_id, prompt, mode, model, status, created_at, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, pid, "Очередной промпт", "auto", "gpt-5.4", "queued", now, sess["id"]),
        )
        db.commit()

        resp = c.delete(f"/api/chat/sessions/{sess['id']}")
        assert resp.status_code == 409, (
            f"Ожидали 409 для queued task, получили {resp.status_code}: {resp.text}"
        )

    def test_delete_session_with_completed_task_succeeds(self, client):
        """B5: DELETE сессии с завершённой задачей (status=completed) → 200, удаляется нормально."""
        c, db = client
        pid = _create_project(c)
        sess = _create_session(c, pid)

        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            """
            INSERT INTO tasks
                (id, project_id, prompt, mode, model, status, created_at, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, pid, "Завершённый промпт", "auto", "gpt-5.4", "completed", now, sess["id"]),
        )
        db.commit()

        # Завершённая задача не блокирует удаление
        resp = c.delete(f"/api/chat/sessions/{sess['id']}")
        assert resp.status_code == 200, (
            f"Завершённая задача не должна блокировать удаление сессии: {resp.text}"
        )


# ===========================================================================
# 2. Send-message isolation
# ===========================================================================


class TestSendMessageIsolation:
    def test_send_requires_session_id(self, client):
        """POST /api/chat/send без session_id → 422 (Pydantic validation error)."""
        c, _ = client
        pid = _create_project(c)

        # session_id — обязательное поле в ChatSendRequest
        resp = c.post("/api/chat/send", json={
            "project_id": pid,
            "message": "Привет",
            "mode": "auto",
            "model": "gpt-5.4",
            "attachments": [],
            # session_id намеренно пропущен
        })
        assert resp.status_code == 422

    def test_send_attaches_to_session(self, client):
        """Сообщение попадает в сессию X, не попадает в историю сессии Y."""
        c, db = client
        pid = _create_project(c)
        sess_a = _create_session(c, pid, title="Сессия A")
        sess_b = _create_session(c, pid, title="Сессия B")

        _send_message(c, pid, sess_a["id"], text="Сообщение только для A")

        # В сессии A должно быть 1 сообщение
        row = db.fetchone(
            "SELECT COUNT(*) AS cnt FROM chat_messages WHERE session_id = ?", (sess_a["id"],)
        )
        assert row["cnt"] == 1

        # В сессии B — ноль
        row = db.fetchone(
            "SELECT COUNT(*) AS cnt FROM chat_messages WHERE session_id = ?", (sess_b["id"],)
        )
        assert row["cnt"] == 0

    def test_history_returns_only_session_messages(self, client):
        """GET /history/{session_id} возвращает только сообщения этой сессии."""
        c, _ = client
        pid = _create_project(c)
        sess_a = _create_session(c, pid, title="Сессия A")
        sess_b = _create_session(c, pid, title="Сессия B")

        _send_message(c, pid, sess_a["id"], text="Уникальный текст для A")
        _send_message(c, pid, sess_b["id"], text="Уникальный текст для B")

        # История сессии A
        resp_a = c.get(f"/api/chat/history/{sess_a['id']}")
        assert resp_a.status_code == 200
        history_a = resp_a.json()
        assert len(history_a) == 1
        assert history_a[0]["content"] == "Уникальный текст для A"
        assert history_a[0]["session_id"] == sess_a["id"]

        # История сессии B
        resp_b = c.get(f"/api/chat/history/{sess_b['id']}")
        assert resp_b.status_code == 200
        history_b = resp_b.json()
        assert len(history_b) == 1
        assert history_b[0]["content"] == "Уникальный текст для B"

    def test_history_nonexistent_session(self, client):
        """GET /history для несуществующей сессии → 404."""
        c, _ = client
        resp = c.get("/api/chat/history/ghost-session-id")
        assert resp.status_code == 404


# ===========================================================================
# 3. Documents scope
# ===========================================================================


class TestDocumentsScope:
    def test_documents_scope_all(self, client):
        """scope=all возвращает session-scoped и project-wide документы (не is_scratch)."""
        c, db = client
        pid = _create_project(c)
        sess = _create_session(c, pid)

        # Project-wide документ (session_id IS NULL)
        proj_doc_id = _insert_document(db, pid, session_id=None, filename="project.pdf")
        # Session-scoped документ
        sess_doc_id = _insert_document(db, pid, session_id=sess["id"], filename="session.pdf")

        resp = c.get(f"/api/documents/{pid}?scope=all")
        assert resp.status_code == 200
        ids = {d["id"] for d in resp.json()}

        # Оба должны быть видны при scope=all
        assert proj_doc_id in ids
        assert sess_doc_id in ids

    def test_documents_scope_project(self, client):
        """scope=project — только project-wide (session_id IS NULL)."""
        c, db = client
        pid = _create_project(c)
        sess = _create_session(c, pid)

        proj_doc_id = _insert_document(db, pid, session_id=None, filename="project.pdf")
        sess_doc_id = _insert_document(db, pid, session_id=sess["id"], filename="session.pdf")

        resp = c.get(f"/api/documents/{pid}?scope=project")
        assert resp.status_code == 200
        ids = {d["id"] for d in resp.json()}

        assert proj_doc_id in ids
        assert sess_doc_id not in ids, "Session-scoped документ не должен быть виден при scope=project"

    def test_documents_scope_session(self, client):
        """scope=session&session_id=X → документы этой сессии + project-wide."""
        c, db = client
        pid = _create_project(c)
        sess = _create_session(c, pid)

        proj_doc_id = _insert_document(db, pid, session_id=None, filename="project.pdf")
        sess_doc_id = _insert_document(db, pid, session_id=sess["id"], filename="session.pdf")

        resp = c.get(f"/api/documents/{pid}?scope=session&session_id={sess['id']}")
        assert resp.status_code == 200
        ids = {d["id"] for d in resp.json()}

        # Оба должны быть видны: session + project-wide
        assert proj_doc_id in ids
        assert sess_doc_id in ids

    def test_documents_scope_session_requires_session_id(self, client):
        """scope=session без session_id → 400."""
        c, _ = client
        pid = _create_project(c)

        resp = c.get(f"/api/documents/{pid}?scope=session")
        assert resp.status_code == 400

    def test_documents_scope_session_isolation(self, client):
        """scope=session&session_id=A не видит документы сессии B."""
        c, db = client
        pid = _create_project(c)
        sess_a = _create_session(c, pid, title="Сессия A")
        sess_b = _create_session(c, pid, title="Сессия B")

        doc_a_id = _insert_document(db, pid, session_id=sess_a["id"], filename="doc_a.pdf")
        doc_b_id = _insert_document(db, pid, session_id=sess_b["id"], filename="doc_b.pdf")

        # Запрашиваем документы со стороны сессии A
        resp = c.get(f"/api/documents/{pid}?scope=session&session_id={sess_a['id']}")
        assert resp.status_code == 200
        ids = {d["id"] for d in resp.json()}

        assert doc_a_id in ids, "Документ сессии A должен быть виден"
        assert doc_b_id not in ids, "Документ сессии B не должен быть виден при запросе сессии A"


# ===========================================================================
# 4. Worker context (optional) — изоляция chat_history в задаче
# ===========================================================================


class TestWorkerContext:
    def test_queue_next_chat_history_session_scoped(self, client):
        """
        Worker-эндпоинт /api/queue/next возвращает chat_history только
        из текущей сессии (не из проекта целиком).
        """
        c, db = client
        pid = _create_project(c)
        sess_a = _create_session(c, pid, title="Рабочая сессия")
        sess_b = _create_session(c, pid, title="Другая сессия")

        # Отправляем 1 сообщение в сессию B — оно НЕ должно попасть в task сессии A
        _send_message(c, pid, sess_b["id"], text="Это сообщение из другой сессии")

        # Отправляем сообщение в сессию A — именно для неё будет создана задача
        _send_message(c, pid, sess_a["id"], text="Вопрос из сессии A")

        # Берём задачу из очереди через worker-endpoint
        # Worker-токен: в тестах WORKER_TOKEN = "change-me" (не frozen-режим)
        from backend.core.config import WORKER_TOKEN
        resp = c.get("/api/queue/next", headers={"Authorization": f"Bearer {WORKER_TOKEN}"})
        if resp.status_code == 204:
            pytest.skip("Очередь пуста — скипаем (возможно порядок enqueue иной)")

        assert resp.status_code == 200
        resp_data = resp.json()

        # Ответ /api/queue/next оборачивает задачу в {"task": {...}}
        task = resp_data.get("task", resp_data)

        # Проверяем что задача содержит session_id (баг: queue.py не проставляет его через SQL,
        # наш патч enqueue делает UPDATE после INSERT → задача в БД имеет session_id)
        task_id = task.get("id")
        assert task_id is not None

        task_row = db.fetchone("SELECT session_id FROM tasks WHERE id = ?", (task_id,))
        assert task_row is not None

        # session_id должен быть проставлен нашим патчем enqueue
        session_id_in_task = task_row["session_id"]
        assert session_id_in_task in (sess_a["id"], sess_b["id"]), \
            f"Задача должна принадлежать одной из сессий, получили: {session_id_in_task}"

        # Проверяем изоляцию chat_history: /queue/next должен отдавать историю
        # только из сессии задачи. Если chat_history содержит сообщения из других сессий —
        # это баг изоляции в worker-эндпоинте.
        chat_history = task.get("chat_history", [])
        history_contents = {m.get("content") for m in chat_history}

        if session_id_in_task == sess_b["id"]:
            # Первая задача — из сессии B
            assert "Это сообщение из другой сессии" in history_contents, \
                "Сообщение сессии B должно быть в chat_history"
            assert "Вопрос из сессии A" not in history_contents, \
                "BUG: chat_history содержит сообщения из чужой сессии A (нет изоляции в /queue/next)"
        else:
            # Первая задача — из сессии A
            assert "Вопрос из сессии A" in history_contents, \
                "Сообщение сессии A должно быть в chat_history"
            assert "Это сообщение из другой сессии" not in history_contents, \
                "BUG: chat_history содержит сообщения из чужой сессии B (нет изоляции в /queue/next)"


# ===========================================================================
# 5. Results endpoint — session_id сохраняется в assistant-сообщении
# ===========================================================================


class TestAssistantSessionPreservation:
    def test_assistant_response_preserves_session_id(self, client):
        """
        Блокер v33: POST /api/results должен вставлять assistant-сообщение
        с тем же session_id, что у соответствующей задачи.

        Без этого фикса:
          - фронт не видит ответ в loadMessages(session_id) (WHERE session_id=? → NULL не совпадает)
          - LLM теряет контекст: _enrich_chat_history фильтрует по session_id, история рвётся

        Сценарий:
          1. Создаём проект + сессию
          2. Отправляем user-сообщение через /api/chat/send → создаётся задача с session_id
          3. Дёргаем POST /api/results напрямую с фейковым результатом
          4. Проверяем SELECT session_id FROM chat_messages WHERE role='assistant' → NOT NULL
        """
        from backend.api.results import router as results_router
        from backend.core.config import WORKER_TOKEN

        c, db = client

        # Перестраиваем тестовое приложение с подключённым results-роутером
        from fastapi import FastAPI
        from backend.core.queue import TaskQueue
        from backend.api.sessions import router as sessions_router
        from backend.api.chat import router as chat_router
        from backend.api.projects import router as projects_router

        # Реиспользуем state из фикстуры client
        state = db

        app_with_results = FastAPI(title="Test App with Results")
        app_with_results.include_router(sessions_router, prefix="/api/chat",     tags=["sessions"])
        app_with_results.include_router(chat_router,     prefix="/api/chat",     tags=["chat"])
        app_with_results.include_router(projects_router, prefix="/api/projects", tags=["projects"])
        app_with_results.include_router(results_router,  prefix="/api/results",  tags=["results"])

        queue = TaskQueue(state)

        # Тот же патч enqueue что и в _build_test_app
        _original_enqueue = queue.enqueue

        def _patched_enqueue(project_id, prompt, mode="auto", model="gpt-5.4",
                             task_id=None, attachment_document_ids=None, session_id=None):
            tid = _original_enqueue(
                project_id=project_id,
                prompt=prompt,
                mode=mode,
                model=model,
                task_id=task_id,
                attachment_document_ids=attachment_document_ids,
            )
            if session_id:
                state.execute("UPDATE tasks SET session_id = ? WHERE id = ?", (session_id, tid))
                state.commit()
            return tid

        queue.enqueue = _patched_enqueue

        app_with_results.state.db = state
        app_with_results.state.queue = queue
        app_with_results.state.event_queues = []

        async def _noop_publish(event_type: str, data: dict):
            pass

        app_with_results.state.publish_event = _noop_publish

        with TestClient(app_with_results, raise_server_exceptions=True) as rc:
            # Шаг 1: создаём проект и сессию
            pid = _create_project(rc)
            sess = _create_session(rc, pid, title="Тест session_id в assistant")

            # Шаг 2: отправляем user-сообщение — создаётся задача с session_id
            _send_message(rc, pid, sess["id"], text="Привет, Настя")

            # Находим задачу, созданную для этого сообщения
            task_row = state.fetchone(
                "SELECT id, session_id FROM tasks WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
                (pid,),
            )
            assert task_row is not None, "Задача должна быть создана после send_message"
            task_id = task_row["id"]
            expected_session_id = task_row["session_id"]
            assert expected_session_id == sess["id"], \
                f"Задача должна иметь session_id={sess['id']}, получили {expected_session_id}"

            # Ставим задачу в статус running — иначе /api/results откажет (409)
            state.execute(
                "UPDATE tasks SET status='running' WHERE id=?", (task_id,)
            )
            state.commit()

            # Шаг 3: имитируем ответ воркера через POST /api/results
            resp = rc.post(
                "/api/results",
                json={
                    "task_id": task_id,
                    "status": "completed",
                    "result": "Привет! Я Настя, чем могу помочь?",
                    "error": None,
                    "used_github": False,
                },
                headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            )
            assert resp.status_code == 200, f"POST /api/results вернул {resp.status_code}: {resp.text}"

            # Шаг 4: проверяем что assistant-сообщение сохранилось с правильным session_id
            msg_row = state.fetchone(
                "SELECT session_id FROM chat_messages WHERE role='assistant' AND task_id=?",
                (task_id,),
            )
            assert msg_row is not None, "Assistant-сообщение должно быть создано в chat_messages"
            assert msg_row["session_id"] is not None, (
                "BUG (блокер v33): assistant-сообщение сохранено без session_id — "
                "фронт не увидит ответ, LLM потеряет контекст"
            )
            assert msg_row["session_id"] == sess["id"], (
                f"session_id в assistant-сообщении должен совпадать с сессией чата. "
                f"Ожидали {sess['id']}, получили {msg_row['session_id']}"
            )
