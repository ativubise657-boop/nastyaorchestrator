"""
Тесты для GET /api/documents/{project_id}/{doc_id}/content

Покрывают блокер B1: endpoint возвращает текстовое содержимое документа.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Переиспользуем инфраструктуру из test_chat_sessions
# ---------------------------------------------------------------------------

def _init_test_db(db_path: str) -> None:
    """Создаём тестовую БД с полной схемой (включая session_id в documents)."""
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

        CREATE TABLE IF NOT EXISTS chat_sessions (
            id         TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title      TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE TABLE IF NOT EXISTS worker_heartbeats (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id   TEXT,
            timestamp TEXT NOT NULL
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

        CREATE TABLE IF NOT EXISTS webhooks_raw (
            id          TEXT PRIMARY KEY,
            source      TEXT DEFAULT 'b24',
            payload     TEXT NOT NULL,
            received_at TEXT NOT NULL,
            processed   INTEGER DEFAULT 0
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

        CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_project    ON tasks(project_id);
        CREATE INDEX IF NOT EXISTS idx_messages_project ON chat_messages(project_id);
        CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);
        CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_project  ON chat_sessions(project_id, updated_at DESC);
    """)
    conn.commit()
    conn.close()


def _build_test_app(state):
    """Минимальное FastAPI-приложение для тестов documents."""
    from fastapi import FastAPI
    from backend.core.queue import TaskQueue
    from backend.api.projects import router as projects_router
    from backend.api.documents import router as documents_router

    test_app = FastAPI(title="Test Documents App")
    test_app.include_router(projects_router, prefix="/api/projects", tags=["projects"])
    test_app.include_router(documents_router, prefix="/api/documents", tags=["documents"])

    queue = TaskQueue(state)
    test_app.state.db = state
    test_app.state.queue = queue
    test_app.state.event_queues = []

    async def _noop_publish(event_type: str, data: dict):
        pass

    test_app.state.publish_event = _noop_publish
    return test_app


@pytest.fixture
def client(tmp_path):
    """TestClient с изолированной тестовой БД."""
    from backend.core.state import State

    db_file = str(tmp_path / "test_docs_content.db")
    _init_test_db(db_file)
    state = State(db_path=db_file)
    test_app = _build_test_app(state)

    with TestClient(test_app, raise_server_exceptions=True) as c:
        yield c, state, tmp_path

    try:
        if hasattr(state._local, "conn") and state._local.conn is not None:
            state._local.conn.close()
            state._local.conn = None
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _create_project(client, *, name: str = "Test Project") -> str:
    resp = client.post("/api/projects/", json={"name": name, "description": ""})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _insert_document(state, project_id: str, file_path: str, filename: str,
                     parse_status: str = "skipped",
                     content_type: str = "text/plain",
                     parse_error: str = "") -> str:
    """Прямая вставка документа в БД без вызова upload endpoint."""
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    state.execute(
        """
        INSERT INTO documents
            (id, project_id, filename, path, size, content_type, parse_status, parse_error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, project_id, filename, file_path, 100, content_type,
         parse_status, parse_error, now),
    )
    state.commit()
    return doc_id


# ===========================================================================
# Тесты GET /{project_id}/{doc_id}/content
# ===========================================================================

class TestDocumentContent:

    def test_content_text_file_direct_read(self, client, tmp_path):
        """Текстовый файл (parse_status=skipped) → 200, содержимое файла."""
        c, state, _ = client
        pid = _create_project(c)

        # Создаём реальный текстовый файл
        text_file = tmp_path / "hello.txt"
        text_file.write_text("Привет, мир! Это тестовый документ.", encoding="utf-8")

        doc_id = _insert_document(
            state, pid,
            file_path=str(text_file),
            filename="hello.txt",
            parse_status="skipped",
            content_type="text/plain",
        )

        resp = c.get(f"/api/documents/{pid}/{doc_id}/content")
        assert resp.status_code == 200, resp.text
        assert "Привет, мир!" in resp.text

    def test_content_parsed_md_cache(self, client, tmp_path):
        """parse_status=parsed + .md кеш → 200, содержимое кеша."""
        c, state, _ = client
        pid = _create_project(c)

        # Создаём исходный PDF (фейковый) и .md кеш
        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        md_cache = tmp_path / "doc.md"
        md_cache.write_text("# Заголовок\n\nСодержимое из кеша markitdown.", encoding="utf-8")

        doc_id = _insert_document(
            state, pid,
            file_path=str(pdf_file),
            filename="doc.pdf",
            parse_status="parsed",
            content_type="application/pdf",
        )

        resp = c.get(f"/api/documents/{pid}/{doc_id}/content")
        assert resp.status_code == 200, resp.text
        assert "Содержимое из кеша markitdown." in resp.text

    def test_content_not_found(self, client):
        """Несуществующий doc_id → 404."""
        c, state, _ = client
        pid = _create_project(c)

        resp = c.get(f"/api/documents/{pid}/nonexistent-doc-id/content")
        assert resp.status_code == 404

    def test_content_parse_failed_returns_422(self, client, tmp_path):
        """parse_status=failed → 422 с описанием ошибки."""
        c, state, _ = client
        pid = _create_project(c)

        pdf_file = tmp_path / "broken.pdf"
        pdf_file.write_bytes(b"not a real pdf")

        doc_id = _insert_document(
            state, pid,
            file_path=str(pdf_file),
            filename="broken.pdf",
            parse_status="failed",
            content_type="application/pdf",
            parse_error="Ни один парсер не смог извлечь текст",
        )

        resp = c.get(f"/api/documents/{pid}/{doc_id}/content")
        assert resp.status_code == 422, resp.text
        assert "не распарсен" in resp.text or "parse" in resp.text.lower()

    def test_content_binary_no_cache_returns_415(self, client, tmp_path):
        """Бинарный файл без .md кеша и без parse_status=parsed → 415."""
        c, state, _ = client
        pid = _create_project(c)

        # PNG без .md кеша, parse_status=skipped
        img_file = tmp_path / "image.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        doc_id = _insert_document(
            state, pid,
            file_path=str(img_file),
            filename="image.png",
            parse_status="skipped",
            content_type="image/png",
        )

        resp = c.get(f"/api/documents/{pid}/{doc_id}/content")
        assert resp.status_code == 415, resp.text

    def test_content_project_not_found(self, client):
        """Несуществующий project_id → 404."""
        c, state, _ = client
        resp = c.get("/api/documents/ghost-project/some-doc/content")
        assert resp.status_code == 404
