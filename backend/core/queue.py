"""
Очередь задач — тонкая обёртка над таблицей tasks в SQLite.

Логика:
  - enqueue()  — добавить задачу со статусом queued
  - dequeue()  — атомарно взять следующую queued → running
  - complete() — пометить задачу completed/failed + записать результат
  - size()     — сколько задач ждёт выполнения
"""
import logging
import uuid
from datetime import datetime, timezone

from backend.core.state import State

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskQueue:
    def __init__(self, state: State):
        self._state = state

    # ------------------------------------------------------------------
    # Добавить задачу в очередь
    # ------------------------------------------------------------------

    def enqueue(
        self,
        project_id: str,
        prompt: str,
        mode: str = "auto",
        model: str = "gpt-5.4",
        task_id: str | None = None,
    ) -> str:
        """
        Создаёт запись задачи со статусом queued.
        Возвращает id новой задачи.
        """
        tid = task_id or str(uuid.uuid4())
        now = _now_iso()
        self._state.execute(
            """
            INSERT INTO tasks (id, project_id, prompt, mode, model, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'queued', ?)
            """,
            (tid, project_id, prompt, mode, model, now),
        )
        self._state.commit()
        logger.info("Задача %s добавлена в очередь (проект %s, режим %s)", tid, project_id, mode)
        return tid

    # ------------------------------------------------------------------
    # Забрать следующую задачу (для worker-а)
    # ------------------------------------------------------------------

    def dequeue(self) -> dict | None:
        """
        Атомарно берёт первую queued-задачу и переводит её в running.
        Возвращает словарь с данными задачи или None, если очередь пуста.
        """
        # Блокируем таблицу через BEGIN IMMEDIATE, чтобы два worker-а
        # не схватили одну и ту же задачу
        conn = self._state.conn
        # Завершаем неявную транзакцию Python sqlite3 перед BEGIN IMMEDIATE
        try:
            conn.execute("COMMIT")
        except Exception:
            pass
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                """
                SELECT id, project_id, prompt, mode, model, created_at
                FROM tasks
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()

            if row is None:
                conn.execute("ROLLBACK")
                return None

            now = _now_iso()
            conn.execute(
                "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            conn.execute("COMMIT")
            logger.info("Задача %s передана в работу", row["id"])
            # Возвращаем задачу уже с актуальным статусом running
            result = dict(row)
            result["status"] = "running"
            result["started_at"] = now
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------
    # Завершить задачу
    # ------------------------------------------------------------------

    def complete(
        self,
        task_id: str,
        status: str,  # "completed" | "failed"
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        """Обновляет статус задачи и сохраняет результат/ошибку."""
        now = _now_iso()
        self._state.execute(
            """
            UPDATE tasks
            SET status = ?, result = ?, error = ?, completed_at = ?
            WHERE id = ?
            """,
            (status, result, error, now, task_id),
        )
        self._state.commit()
        logger.info("Задача %s → %s", task_id, status)

    # ------------------------------------------------------------------
    # Размер очереди
    # ------------------------------------------------------------------

    def size(self) -> int:
        """Количество задач в статусе queued."""
        row = self._state.fetchone("SELECT COUNT(*) FROM tasks WHERE status = 'queued'")
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Отменить задачу (убрать из очереди если queued)
    # ------------------------------------------------------------------

    def cancel(self, task_id: str) -> bool:
        """Отменяет задачу если она ещё в очереди (queued). Возвращает True если отменена."""
        row = self._state.fetchone(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        )
        if row and row["status"] == "queued":
            now = _now_iso()
            self._state.execute(
                "UPDATE tasks SET status = 'cancelled', completed_at = ? WHERE id = ?",
                (now, task_id),
            )
            self._state.commit()
            logger.info("Задача %s отменена (была в очереди)", task_id)
            return True
        return False

    # ------------------------------------------------------------------
    # Проверить отменена ли задача
    # ------------------------------------------------------------------

    def is_cancelled(self, task_id: str) -> bool:
        """Проверяет отменена ли задача."""
        row = self._state.fetchone(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        )
        return row["status"] == "cancelled" if row else False

    # ------------------------------------------------------------------
    # Получить задачу по id
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> dict | None:
        row = self._state.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return dict(row) if row else None
