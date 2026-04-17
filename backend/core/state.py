"""
SQLite WAL persistence — thread-local соединения, инициализация схемы.
"""
import asyncio
import logging
import os
import sqlite3
import threading
import uuid
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

    -- Чат-сессии: каждый «чат» — изолированная история с привязкой к проекту
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id         TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        title      TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (project_id) REFERENCES projects(id)
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
    CREATE INDEX IF NOT EXISTS idx_sessions_project    ON chat_sessions(project_id, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_messages_session    ON chat_messages(session_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_documents_session   ON documents(session_id);

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
            # busy_timeout=30000 (30 сек) — для стабильности под нагрузкой с async wrappers.
            # На Windows SQLite менее прощающий к параллельным writers из thread pool,
            # 5 сек (прежний дефолт) давал database is locked в CI тестах.
            conn.execute("PRAGMA busy_timeout=30000")
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
        # 4. attachment_document_ids в tasks (JSON list id прикреплённых документов).
        # Нужно чтобы /queue/next понимал "вот эти файлы уже приложены к сообщению"
        # и автоматически помечал их requested=true (даже если в тексте нет триггеров).
        try:
            conn.execute("SELECT attachment_document_ids FROM tasks LIMIT 1")
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN attachment_document_ids TEXT DEFAULT ''")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        # 5. is_scratch в documents — одноразовые картинки из буфера/drag&drop.
        # В списке документов проекта НЕ показываются, удаляются после task.completed.
        try:
            conn.execute("SELECT is_scratch FROM documents LIMIT 1")
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE documents ADD COLUMN is_scratch INTEGER DEFAULT 0")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        # 6. parse_status/parse_error — статус конвертации документа в .md
        # (parsed/failed/skipped). Нужно UI-бейджу "не распарсилось" и для debugging.
        try:
            conn.execute("SELECT parse_status FROM documents LIMIT 1")
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE documents ADD COLUMN parse_status TEXT DEFAULT 'skipped'")
                conn.execute("ALTER TABLE documents ADD COLUMN parse_error TEXT DEFAULT ''")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        # 7. parse_method — каким инструментом распарсили документ
        # (markitdown/pdfminer/aitunnel_gemini/cache). UI показывает бейдж,
        # Настя видит что её картинка ушла в Gemini Flash.
        try:
            conn.execute("SELECT parse_method FROM documents LIMIT 1")
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE documents ADD COLUMN parse_method TEXT DEFAULT ''")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        # 8. session_id в chat_messages — привязка сообщения к чат-сессии (nullable)
        try:
            conn.execute("SELECT session_id FROM chat_messages LIMIT 1")
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE chat_messages ADD COLUMN session_id TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        # 9. session_id в documents — scoped-документы (nullable; NULL = project-wide).
        # Картинки из буфера (is_scratch=1) привязываются к сессии,
        # загруженные PDF/TZ остаются NULL (доступны во всех сессиях проекта).
        try:
            conn.execute("SELECT session_id FROM documents LIMIT 1")
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE documents ADD COLUMN session_id TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        # 10. session_id в tasks — задача может принадлежать конкретной сессии (nullable)
        try:
            conn.execute("SELECT session_id FROM tasks LIMIT 1")
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN session_id TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        # 11. Легаси-миграция: создаём чат-сессию «Chat 1» для каждого проекта,
        # у которого есть несессионные сообщения (session_id IS NULL).
        # Картинки из буфера (is_scratch=1) привязываем к той же сессии,
        # загруженные документы оставляем project-wide (session_id=NULL).
        # Идемпотентно: повторный запуск не создаёт дубликаты.
        try:
            # Явно создаём chat_sessions до миграции — на случай первого запуска
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id         TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title      TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                )
            """)
            conn.commit()

            rows = conn.execute("""
                SELECT DISTINCT project_id FROM chat_messages WHERE session_id IS NULL
            """).fetchall()

            for row in rows:
                project_id = row[0]
                # Проверяем — нет ли уже созданной сессии для этого проекта
                existing = conn.execute(
                    "SELECT id FROM chat_sessions WHERE project_id = ? LIMIT 1",
                    (project_id,)
                ).fetchone()
                if existing:
                    session_id = existing[0]
                else:
                    # Рассчитываем временные метки из истории сообщений
                    ts_row = conn.execute("""
                        SELECT MIN(created_at), MAX(created_at)
                        FROM chat_messages
                        WHERE project_id = ? AND session_id IS NULL
                    """, (project_id,)).fetchone()
                    created_at = ts_row[0] if ts_row and ts_row[0] else ""
                    updated_at = ts_row[1] if ts_row and ts_row[1] else created_at
                    session_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO chat_sessions (id, project_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (session_id, project_id, "Chat 1", created_at, updated_at)
                    )

                # Привязываем несессионные сообщения к новой/существующей сессии
                conn.execute(
                    "UPDATE chat_messages SET session_id = ? WHERE project_id = ? AND session_id IS NULL",
                    (session_id, project_id)
                )
                # Картинки из буфера (is_scratch=1) — сессионные, привязываем к сессии
                conn.execute(
                    "UPDATE documents SET session_id = ? WHERE project_id = ? AND is_scratch = 1 AND session_id IS NULL",
                    (session_id, project_id)
                )
                # PDF/TZ через UI (is_scratch=0) — project-wide, session_id остаётся NULL

            conn.commit()
        except sqlite3.OperationalError:
            # Таблицы ещё не существуют при самом первом запуске —
            # executescript создаст их ниже, миграция пройдёт при следующем старте
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

    # ------------------------------------------------------------------
    # Async-обёртки — совместимость с интерфейсом async handlers.
    #
    # РЕШЕНИЕ (2026-04-17): выполняют sync-метод НАПРЯМУЮ, без thread pool.
    # Причина: asyncio.to_thread создавал новые threads на каждый вызов,
    # thread-local connections открывали разные WAL-транзакции, которые
    # конкурировали за write lock. На Windows SQLite это давало стабильный
    # `database is locked` в CI (даже при busy_timeout=30s), т.к. transaction
    # в одном thread могла не закоммититься к моменту запроса в другом.
    #
    # Трейд-офф: event loop блокируется на SQLite-операциях (типично 1-10мс).
    # Для десктоп-приложения на одного пользователя это приемлемо — SSE и
    # фоновые задачи не страдают (они всё равно I/O-bound).
    # Если появится нагрузка (много concurrent SSE клиентов) — заменим на
    # единый dedicated writer-thread с очередью.
    # ------------------------------------------------------------------

    async def aexecute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.execute(sql, params)

    async def afetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.fetchone(sql, params)

    async def afetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.fetchall(sql, params)

    async def acommit(self) -> None:
        self.commit()

    async def aexecutemany(self, sql: str, params_seq) -> sqlite3.Cursor:
        return self.executemany(sql, params_seq)
