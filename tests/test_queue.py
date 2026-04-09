"""Smoke-тесты для TaskQueue — атомарность dequeue и базовый CRUD."""
import time

from backend.core.queue import TaskQueue


def _mk_project(state, pid="p1"):
    state.execute(
        "INSERT INTO projects (id, name, created_at) VALUES (?, ?, ?)",
        (pid, "test", "2026-01-01T00:00:00+00:00"),
    )
    state.commit()


def test_enqueue_creates_queued_row(temp_db):
    _mk_project(temp_db)
    q = TaskQueue(temp_db)
    tid = q.enqueue("p1", "hello")
    row = temp_db.fetchone("SELECT status, prompt FROM tasks WHERE id = ?", (tid,))
    assert row["status"] == "queued"
    assert row["prompt"] == "hello"


def test_dequeue_moves_queued_to_running(temp_db):
    _mk_project(temp_db)
    q = TaskQueue(temp_db)
    tid = q.enqueue("p1", "hello")
    task = q.dequeue()
    assert task is not None
    assert task["id"] == tid
    assert task["status"] == "running"
    row = temp_db.fetchone("SELECT status FROM tasks WHERE id = ?", (tid,))
    assert row["status"] == "running"


def test_dequeue_empty_returns_none(temp_db):
    q = TaskQueue(temp_db)
    assert q.dequeue() is None


def test_dequeue_fifo_order(temp_db):
    _mk_project(temp_db)
    q = TaskQueue(temp_db)
    t1 = q.enqueue("p1", "first")
    time.sleep(0.01)  # гарантируем разные created_at
    t2 = q.enqueue("p1", "second")
    time.sleep(0.01)
    t3 = q.enqueue("p1", "third")
    assert q.dequeue()["id"] == t1
    assert q.dequeue()["id"] == t2
    assert q.dequeue()["id"] == t3


def test_complete_stores_result(temp_db):
    _mk_project(temp_db)
    q = TaskQueue(temp_db)
    tid = q.enqueue("p1", "hello")
    q.dequeue()
    q.complete(tid, status="completed", result="ok output")
    row = temp_db.fetchone("SELECT status, result, completed_at FROM tasks WHERE id = ?", (tid,))
    assert row["status"] == "completed"
    assert row["result"] == "ok output"
    assert row["completed_at"] is not None


def test_cancel_only_on_queued(temp_db):
    _mk_project(temp_db)
    q = TaskQueue(temp_db)
    # queued → можно отменить
    t1 = q.enqueue("p1", "a")
    assert q.cancel(t1) is True
    # running → нельзя
    t2 = q.enqueue("p1", "b")
    q.dequeue()
    assert q.cancel(t2) is False
    # completed → нельзя
    t3 = q.enqueue("p1", "c")
    q.dequeue()
    q.complete(t3, status="completed", result="r")
    assert q.cancel(t3) is False


def test_dequeue_atomic_no_double_pickup(temp_db):
    """После первого dequeue задача в running, второй dequeue её не возьмёт."""
    _mk_project(temp_db)
    q = TaskQueue(temp_db)
    tid = q.enqueue("p1", "only")
    first = q.dequeue()
    assert first["id"] == tid
    second = q.dequeue()
    assert second is None  # уже в running — не должна повторно выдаваться
