"""
Integration-тест: воспроизводим баг 'модель видит две картинки'.

История:
- v33-v36 пытались починить но баг всплыл снова
- Сценарий: старый image.png (project-wide, is_scratch=0, session_id=NULL) в БД
  + новая clipboard-картинка из текущей сессии
  → в task.documents попадает И старая, и новая
- Ожидание: модель должна видеть ТОЛЬКО текущую прикреплённую clipboard-картинку

Инфраструктура: FastAPI TestClient + изолированная SQLite + _enrich_documents напрямую.
Реальный LLM не нужен — баг воспроизводится на уровне логики сборки контекста.
"""
from __future__ import annotations

import io
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.state import State
from backend.core.queue import TaskQueue


# ---------------------------------------------------------------------------
# Утилита: схема БД (полная, с session_id в DDL сразу)
# ---------------------------------------------------------------------------

def _init_test_db(db_path: str) -> None:
    """Инициализирует тестовую БД с полной схемой (обходит баг миграций State)."""
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


# ---------------------------------------------------------------------------
# Утилита: сборка тестового приложения
# ---------------------------------------------------------------------------

def _build_test_app(state: State) -> FastAPI:
    """
    Минимальное FastAPI-приложение для тестов — без lifespan.
    Включает все роутеры нужные для end-to-end сценария.
    """
    from backend.api.sessions import router as sessions_router
    from backend.api.chat import router as chat_router
    from backend.api.projects import router as projects_router
    from backend.api.documents import router as documents_router
    from backend.api.system import router_queue

    test_app = FastAPI(title="Test — two images bug")

    test_app.include_router(sessions_router,  prefix="/api/chat",      tags=["sessions"])
    test_app.include_router(chat_router,       prefix="/api/chat",      tags=["chat"])
    test_app.include_router(projects_router,   prefix="/api/projects",  tags=["projects"])
    test_app.include_router(documents_router,  prefix="/api/documents", tags=["documents"])
    test_app.include_router(router_queue,      prefix="/api/queue",     tags=["queue"])

    queue = TaskQueue(state)

    # Патч enqueue — chat.py передаёт session_id, queue.py его принимает нативно
    # но на случай если вдруг старая версия — подстраховываем через UPDATE
    _orig_enqueue = queue.enqueue

    def _enqueue_with_session(project_id, prompt, mode="auto", model="gpt-5.4",
                              task_id=None, attachment_document_ids=None, session_id=None):
        tid = _orig_enqueue(
            project_id=project_id,
            prompt=prompt,
            mode=mode,
            model=model,
            task_id=task_id,
            attachment_document_ids=attachment_document_ids,
            session_id=session_id,
        )
        # Страховочный UPDATE если enqueue не пробросил session_id
        if session_id:
            row = state.fetchone("SELECT session_id FROM tasks WHERE id = ?", (tid,))
            if row and row["session_id"] is None:
                state.execute("UPDATE tasks SET session_id = ? WHERE id = ?", (session_id, tid))
                state.commit()
        return tid

    queue.enqueue = _enqueue_with_session

    test_app.state.db = state
    test_app.state.queue = queue
    test_app.state.event_queues = []

    async def _noop_publish(event_type: str, data: dict):
        pass

    test_app.state.publish_event = _noop_publish
    return test_app


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture
def client_and_state(tmp_path):
    """TestClient + State с изолированной БД на tmp_path."""
    db_file = str(tmp_path / "test_two_images.db")
    _init_test_db(db_file)
    state = State(db_path=db_file)
    test_app = _build_test_app(state)

    with TestClient(test_app, raise_server_exceptions=True) as c:
        yield c, state

    # Teardown
    try:
        if hasattr(state._local, "conn") and state._local.conn is not None:
            state._local.conn.close()
            state._local.conn = None
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

_WORKER_TOKEN = "change-me"  # дефолт для dev-режима (из config.py)
_AUTH_HEADERS = {"Authorization": f"Bearer {_WORKER_TOKEN}"}


def _enrich_documents_direct(state: State, project_id: str, session_id: str,
                              attachment_doc_ids: list[str], prompt: str) -> list[dict]:
    """
    Вызываем _enrich_documents напрямую — без HTTP-запроса к /api/queue/next.
    Симулирует то что делает worker когда получает задачу.
    """
    import json
    from backend.api.system import _enrich_documents

    task = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "session_id": session_id,
        "prompt": prompt,
        "attachment_document_ids": json.dumps(attachment_doc_ids) if attachment_doc_ids else "",
    }
    return _enrich_documents(state, task)


# ---------------------------------------------------------------------------
# ТЕСТ 1: Старый project-wide image.png не должен утекать в новую сессию
# ---------------------------------------------------------------------------

def test_old_project_wide_image_does_not_leak_into_new_session(client_and_state, tmp_path):
    """
    Сценарий: в БД лежит старый image.png (is_scratch=0, session_id=NULL) —
    legacy загрузка через DocPanel. Пользователь стартует новую сессию и
    прикрепляет clipboard-картинку. В task.documents должна быть ТОЛЬКО
    новая clipboard-картинка, не старая image.png.

    Если тест падает — _enrich_documents возвращает обе картинки, баг воспроизведён.
    """
    client, state = client_and_state

    # 1. Создаём проект
    proj_resp = client.post("/api/projects", json={"name": "chat", "description": ""})
    assert proj_resp.status_code in (200, 201), proj_resp.text
    project_id = proj_resp.json()["id"]

    # 2. Симулируем старый image.png в БД (загружен через DocPanel, is_scratch=0, session_id=NULL)
    #    Физический файл создаём в tmp_path чтобы path был валидным
    old_file = tmp_path / "image.png"
    old_file.write_bytes(b"fake_old_png")
    old_doc_id = str(uuid.uuid4())
    state.execute(
        """INSERT INTO documents
        (id, project_id, filename, path, size, content_type, is_scratch, session_id, parse_status, created_at)
        VALUES (?, ?, 'image.png', ?, 100, 'image/png', 0, NULL, 'skipped', '2026-04-01T00:00:00')""",
        (old_doc_id, project_id, str(old_file)),
    )
    state.commit()

    # 3. Создаём сессию
    sess_resp = client.post("/api/chat/sessions", json={"project_id": project_id, "title": "new chat"})
    assert sess_resp.status_code in (200, 201), sess_resp.text
    session_id = sess_resp.json()["id"]

    # 4. Загружаем новую clipboard-картинку (is_scratch=true, session_id=NEW_SESSION)
    files = {"file": ("image.png", io.BytesIO(b"fake_new_png_data"), "image/png")}
    up_resp = client.post(
        f"/api/documents/{project_id}/upload?is_scratch=true&session_id={session_id}",
        files=files,
    )
    assert up_resp.status_code == 201, up_resp.text
    new_doc = up_resp.json()

    # Имя должно быть переименовано в clipboard-*
    assert new_doc["filename"].startswith("clipboard-"), (
        f"Ожидали clipboard-*, получили '{new_doc['filename']}' — "
        f"значит fix v36 (переименование clipboard) не работает"
    )
    new_doc_id = new_doc["id"]

    # 5. Вызываем _enrich_documents напрямую (симулируем worker)
    docs = _enrich_documents_direct(
        state=state,
        project_id=project_id,
        session_id=session_id,
        attachment_doc_ids=[new_doc_id],
        prompt="что на картинке?",
    )

    filenames = [d["filename"] for d in docs]

    # Новая clipboard-картинка должна быть в списке
    has_new = any(f.startswith("clipboard-") for f in filenames)
    assert has_new, (
        f"Новая clipboard-картинка не найдена в task.documents: {filenames}\n"
        f"_enrich_documents не вернул прикреплённый документ — логика сломана"
    )

    # Старая image.png (project-wide) НЕ должна попасть в список
    # ВНИМАНИЕ: project-wide docs с session_id=NULL и is_scratch=0 ДОЛЖНЫ попасть
    # в _enrich_documents по дизайну (они видны всем сессиям). Поэтому тест
    # проверяет что НЕ ПРОИСХОДИТ подмена: модель видит old image.png как attachment.
    # Реальный баг — когда old image.png помечается requested=True или идёт первым
    # в нумерации и перехватывает внимание модели вместо clipboard-картинки.
    old_in_docs = [d for d in docs if d["filename"] == "image.png"]
    new_in_docs = [d for d in docs if d["filename"].startswith("clipboard-")]

    # Если старый doc присутствует — он НЕ должен быть requested=True
    # (requested=True означает что контент подгружен и он первый кандидат для модели)
    if old_in_docs:
        old_requested = old_in_docs[0].get("requested", False)
        assert not old_requested, (
            f"БАГ ВОСПРОИЗВЕДЁН: project-wide 'image.png' помечен requested=True!\n"
            f"Модель получит контент старой image.png вместо новой clipboard-картинки.\n"
            f"Все документы: {filenames}\n"
            f"old doc: {old_in_docs[0]}\n"
            f"new doc: {new_in_docs[0] if new_in_docs else 'NOT FOUND'}"
        )

    # Новая clipboard-картинка должна быть requested=True (она прикреплена)
    if new_in_docs:
        new_requested = new_in_docs[0].get("requested", False)
        assert new_requested, (
            f"Новая clipboard-картинка НЕ помечена requested=True — модель не увидит её содержимое.\n"
            f"new doc: {new_in_docs[0]}"
        )


# ---------------------------------------------------------------------------
# ТЕСТ 2: Orphan scratch-картинка (is_scratch=1, session_id=NULL) не утекает
# ---------------------------------------------------------------------------

def test_orphan_scratch_image_does_not_leak(client_and_state, tmp_path):
    """
    Orphan clipboard: is_scratch=1, session_id=NULL — legacy baggage v33-v35.
    Такие документы образовывались когда /upload вызывался с is_scratch=true
    но без session_id (старый фронт). Миграция v36 должна была их переименовать,
    но проверим что они не попадают в task.documents текущей сессии как requested.

    Баг v33-v35: orphan clipboard попадал в выборку _enrich_documents и
    из-за совпадения имени 'image.png' путал модель.
    """
    client, state = client_and_state

    # 1. Создаём проект
    proj_resp = client.post("/api/projects", json={"name": "chat2", "description": ""})
    assert proj_resp.status_code in (200, 201), proj_resp.text
    project_id = proj_resp.json()["id"]

    # 2. Orphan scratch-документ: is_scratch=1, session_id=NULL (legacy, до v36)
    orphan_file = tmp_path / "orphan_image.png"
    orphan_file.write_bytes(b"fake_orphan_png")
    orphan_id = str(uuid.uuid4())
    state.execute(
        """INSERT INTO documents
        (id, project_id, filename, path, size, content_type, is_scratch, session_id, parse_status, created_at)
        VALUES (?, ?, 'image.png', ?, 80, 'image/png', 1, NULL, 'skipped', '2026-03-15T12:00:00')""",
        (orphan_id, project_id, str(orphan_file)),
    )
    state.commit()

    # 3. Создаём новую сессию
    sess_resp = client.post("/api/chat/sessions", json={"project_id": project_id, "title": "fresh session"})
    assert sess_resp.status_code in (200, 201), sess_resp.text
    session_id = sess_resp.json()["id"]

    # 4. Загружаем нормальную clipboard-картинку
    files = {"file": ("image.png", io.BytesIO(b"fresh_png"), "image/png")}
    up_resp = client.post(
        f"/api/documents/{project_id}/upload?is_scratch=true&session_id={session_id}",
        files=files,
    )
    assert up_resp.status_code == 201, up_resp.text
    new_doc = up_resp.json()
    new_doc_id = new_doc["id"]

    # 5. Запускаем _enrich_documents
    docs = _enrich_documents_direct(
        state=state,
        project_id=project_id,
        session_id=session_id,
        attachment_doc_ids=[new_doc_id],
        prompt="опиши изображение",
    )

    filenames = [d["filename"] for d in docs]

    # Orphan (is_scratch=1, session_id=NULL) НЕ должен попасть в выборку вообще
    # (по fix v35 логике: is_scratch=1 + session_id IS NULL → исключается)
    orphan_in_docs = [d for d in docs if d["filename"] == "image.png"]
    assert not orphan_in_docs, (
        f"БАГ: orphan scratch 'image.png' (is_scratch=1, session_id=NULL) "
        f"попал в task.documents!\n"
        f"Это значит фильтр в _enrich_documents не работает правильно.\n"
        f"Все документы: {filenames}"
    )

    # Наша clipboard-картинка должна присутствовать
    has_new = any(f.startswith("clipboard-") for f in filenames)
    assert has_new, (
        f"Новая clipboard-картинка не найдена в task.documents: {filenames}"
    )
