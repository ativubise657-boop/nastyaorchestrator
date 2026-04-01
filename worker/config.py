"""Worker configuration loaded from environment variables."""

import os


class WorkerConfig:
    """All worker settings with sane defaults."""

    server_url: str = os.getenv("ORCH_SERVER_URL", "https://nr.gnld.ru")
    worker_token: str = os.getenv("WORKER_TOKEN", "change-me")
    codex_binary: str = os.getenv("CODEX_BINARY", os.getenv("CLAUDE_BINARY", "codex"))
    aitunnel_api_key: str = os.getenv("AITUNNEL_API_KEY", "")
    aitunnel_base_url: str = os.getenv("AITUNNEL_BASE_URL", "https://api.aitunnel.ru/v1")
    aitunnel_request_timeout: int = int(os.getenv("AITUNNEL_REQUEST_TIMEOUT", "120"))
    aitunnel_max_tool_rounds: int = int(os.getenv("AITUNNEL_MAX_TOOL_ROUNDS", "16"))
    poll_interval: int = int(os.getenv("POLL_INTERVAL", "5"))
    heartbeat_interval: int = int(os.getenv("HEARTBEAT_INTERVAL", "5"))
    task_timeout: int = int(os.getenv("TASK_TIMEOUT", "600"))
    worker_id: str = os.getenv("WORKER_ID", f"wsl-worker-{os.getpid()}")
    default_project_path: str | None = os.getenv("DEFAULT_PROJECT_PATH", None)
    stream_chunk_size: int = int(os.getenv("STREAM_CHUNK_SIZE", "512"))
