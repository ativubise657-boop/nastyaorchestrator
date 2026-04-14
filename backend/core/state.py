"""
SQLite WAL persistence — thread-local соединения, инициализация схемы.
"""
import logging
import os
import sqlite3
import threading
from pathlib import Path

from backend.core.config import DB_PATH

logger = logging.getLogger(__name__)


class State:
    """
    Хранилище на основе SQLite с WAL-режимом.

    Использует thread-local соединения, чтобы FastAPI-воркеры не конкурировали
    за один объект sqlite3.Connection.
    """

    # DDL всех таблиц
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS projects (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT DEFAULT '',
        path        TEXT DEFAULT '',
        git_url     TEXT DEFAULT '',
        created_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id           TEXT PRIMARY KEY,
        project_id   TEXT NOT NULL,
        prompt       TEXT NOT NULL,
        mode         TEXT DEFAULT 'auto',
        model        TEXT DEFAULT 'gpt-5.4',
        status       TEXT DEFAULT 'queued',
        result       TEXT,
        error        TEXT,
        created_at   TEXT NOT NULL,
        started_at   TEXT,
        completed_at TEXT,
        FOREIGN KEY (project_id) REFERENCES projects(id)
    );

    CREATE TABLE IF NOT EXISTS chat_messages (
        id          TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL,
        role        TEXT NOT NULL,
        content     TEXT NOT NULL,
        task_id     TEXT,
        attachments TEXT DEFAULT '',
        created_at  TEXT NOT NULL
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
        folder_id    TEXT,
        created_at   TEXT NOT NULL,
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

    -- Индексы для частых запросов
    CREATE INDEX IF NOT EXISTS idx_tasks_status        ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_project       ON tasks(project_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_id, status);
    CREATE INDEX IF NOT EXISTS idx_messages_project    ON chat_messages(project_id);
    CREATE INDEX IF NOT EXISTS idx_documents_project   ON documents(project_id);
    CREATE INDEX IF NOT EXISTS idx_documents_folder    ON documents(folder_id);
    CREATE INDEX IF NOT EXISTS idx_folders_project     ON folders(project_id);
    CREATE INDEX IF NOT EXISTS idx_heartbeats_ts       ON worker_heartbeats(timestamp);

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

    CREATE INDEX IF NOT EXISTS idx_links_project ON links(project_id);

    CREATE TABLE IF NOT EXISTS app_settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE IF NOT EXISTS circuit_breaker (
        project_id  TEXT PRIMARY KEY,
        crash_count INTEGER DEFAULT 0,
        last_crash  TEXT
    );
    """

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._local = threading.local()
        # Убедимся, что директория для БД существует
        os.makedirs(Path(db_path).parent, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Соединение (thread-local)
    # ------------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        """Возвращает соединение для текущего потока, создаёт при необходимости."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            logger.debug("Открыто новое SQLite-соединение для потока %s", threading.current_thread().name)
        return self._local.conn

    # ------------------------------------------------------------------
    # Инициализация схемы
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Создаёт таблицы и индексы при первом запуске."""
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # Миграции ПЕРЕД executescript (иначе CREATE INDEX на folder_id упадёт)
        # 1. git_url в projects
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN git_url TEXT DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        # 2. folder_id в documents
        try:
            conn.execute("SELECT folder_id FROM documents LIMIT 1")
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE documents ADD COLUMN folder_id TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # таблица ещё не существует — executescript создаст
        # 3. attachments в chat_messages (JSON string со списком прикреплённых файлов)
        try:
            conn.execute("SELECT attachments FROM chat_messages LIMIT 1")
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE chat_messages ADD COLUMN attachments TEXT DEFAULT ''")
                conn.commit()
            except sqlite3.OperationalError:
                pass

        conn.executescript(self._SCHEMA)
        conn.commit()
        conn.close()
        logger.info("БД инициализирована: %s", self._db_path)

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Выполняет запрос и возвращает курсор."""
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_seq) -> sqlite3.Cursor:
        return self.conn.executemany(sql, params_seq)

    def commit(self) -> None:
        self.conn.commit()

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()
