"""Конфигурация worker — читается из переменных окружения."""
import os


class WorkerConfig:
    """Все настройки worker из env-переменных с дефолтами."""

    # URL сервера-оркестратора
    server_url: str = os.getenv("ORCH_SERVER_URL", "https://nr.gnld.ru")

    # Токен авторизации worker
    worker_token: str = os.getenv("WORKER_TOKEN", "change-me")

    # Путь к бинарнику Claude CLI
    claude_binary: str = os.getenv("CLAUDE_BINARY", "claude")

    # Интервал поллинга очереди (секунды)
    poll_interval: int = int(os.getenv("POLL_INTERVAL", "5"))

    # Интервал heartbeat (секунды)
    heartbeat_interval: int = int(os.getenv("HEARTBEAT_INTERVAL", "5"))

    # Таймаут выполнения задачи (секунды)
    task_timeout: int = int(os.getenv("TASK_TIMEOUT", "600"))

    # Идентификатор этого worker (для heartbeat и логов)
    worker_id: str = os.getenv("WORKER_ID", f"wsl-worker-{os.getpid()}")

    # Директория проекта по умолчанию (если задача не указывает свою)
    default_project_path: str | None = os.getenv("DEFAULT_PROJECT_PATH", None)

    # Размер буфера для стриминга чанков (байты)
    stream_chunk_size: int = int(os.getenv("STREAM_CHUNK_SIZE", "512"))
