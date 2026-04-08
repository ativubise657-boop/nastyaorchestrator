"""
Точка входа FastAPI-приложения Nastya Orchestrator.

Запуск:
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import APP_TITLE, APP_VERSION, BASE_DIR, CORS_ORIGINS, SERVE_STATIC
from backend.core.state import State
from backend.core.queue import TaskQueue
from backend.core import proxy as proxy_module

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pub/sub для SSE — глобальный список очередей активных клиентов
# ---------------------------------------------------------------------------

async def _publish_event(app: FastAPI, event_type: str, data: dict) -> None:
    """
    Рассылает событие всем подключённым SSE-клиентам.
    Если у клиента очередь переполнена (100+ сообщений) — удаляем его.
    """
    msg = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    dead: list = []
    for q in app.state.event_queues:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        try:
            app.state.event_queues.remove(q)
        except ValueError:
            pass


import uuid

# Проекты по умолчанию (git_url для auto-clone на сервере Насти)
_DEFAULT_PROJECTS = [
    {"name": "geniled.ru", "description": "Корпоративный сайт + каталог (Bitrix + Aspro.Max)", "path": "/mnt/d/workprojects/geniled.ru", "git_url": "https://github.com/Gypsea67/geniled.ru.git"},
    {"name": "geniled-shop", "description": "Интернет-магазин (Bitrix + Аспро Премьер)", "path": "/mnt/d/Share/geniled-shop", "git_url": "https://github.com/Gypsea67/geniled-shop.git"},
    {"name": "analyticsgeniled", "description": "Аналитика продаж (FastAPI + Python)", "path": "/mnt/d/Share/analyticsgeniled", "git_url": "https://github.com/Gypsea67/analyticsgeniled.git"},
    {"name": "parkcalc", "description": "Конструктор парков (React + Docker)", "path": "/mnt/d/Share/parkcalcdocker", "git_url": "https://github.com/Gypsea67/parkcalc.git"},
    {"name": "gypseaorchestrator", "description": "Gypsea Orchestrator (React + FastAPI)", "path": "/mnt/d/Share/gypseaorchestrator", "git_url": "https://github.com/Gypsea67/gypseaorchestrator.git"},
    {"name": "sparta", "description": "Sparta — МП агенты (Next.js + FastAPI)", "path": "/mnt/d/Share/gypseaclawsparta", "git_url": "https://github.com/Gypsea67/sparta.git"},
    {
        "name": "nastyaorchestrator",
        "description": "Оркестратор Насти (FastAPI + React 19)",
        "path": str(BASE_DIR),
        "git_url": "https://github.com/ativubise657-boop/nastyaorchestrator.git",
    },
]


def _purge_old_data(state: State, days: int = 30) -> int:
    """Удаление старых завершённых задач и heartbeat'ов при startup."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    total = 0

    # Старые завершённые задачи (completed/failed/cancelled)
    cur = state.execute(
        "DELETE FROM tasks WHERE status IN ('completed', 'failed', 'cancelled') AND completed_at < ?",
        (cutoff,),
    )
    total += cur.rowcount

    # Старые chat_messages (привязанные к удалённым задачам уже не нужны,
    # но чистим по дате — сохраняем последние 30 дней)
    cur = state.execute(
        "DELETE FROM chat_messages WHERE created_at < ?",
        (cutoff,),
    )
    total += cur.rowcount

    # Старые heartbeat'ы (оставляем только последние 100)
    cur = state.execute(
        """
        DELETE FROM worker_heartbeats
        WHERE id NOT IN (SELECT id FROM worker_heartbeats ORDER BY id DESC LIMIT 100)
        """,
    )
    total += cur.rowcount

    if total:
        state.commit()

    return total


def _seed_projects(state: State) -> None:
    """Upsert проектов — добавляет новые, обновляет path/git_url существующих."""
    now = datetime.now(timezone.utc).isoformat()
    existing = {
        r["name"]: r["id"]
        for r in state.fetchall("SELECT id, name FROM projects")
    }

    added = 0
    updated = 0
    for p in _DEFAULT_PROJECTS:
        git_url = p.get("git_url", "")
        if p["name"] in existing:
            state.execute(
                "UPDATE projects SET path = ?, description = ?, git_url = ? WHERE id = ?",
                (p["path"], p["description"], git_url, existing[p["name"]]),
            )
            updated += 1
        else:
            state.execute(
                "INSERT INTO projects (id, name, description, path, git_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), p["name"], p["description"], p["path"], git_url, now),
            )
            added += 1

    state.commit()
    if added or updated:
        logger.info("Проекты: +%d новых, %d обновлено (всего %d)", added, updated, len(_DEFAULT_PROJECTS))


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    logger.info("Запуск %s v%s", APP_TITLE, APP_VERSION)

    # Инициализируем БД и очередь
    app.state.db = State()
    app.state.queue = TaskQueue(app.state.db)

    # Прокси: загружаем из БД (или дефолты) и применяем в os.environ.
    # Это автоматически прокинется во все subprocess (git/pip/npm/codex)
    # и в httpx-клиенты (trust_env=True по умолчанию).
    try:
        applied = proxy_module.apply_from_db(app.state.db)
        logger.info("Proxy startup: %s", applied.to_safe_dict())
    except Exception as exc:
        logger.warning("Не удалось применить прокси на startup: %s", exc)

    # SSE: список asyncio.Queue для подключённых клиентов
    app.state.event_queues: list[asyncio.Queue] = []
    app.state.app_updates: dict[str, dict] = {}

    # Время старта для uptime
    app.state.start_time = datetime.now(timezone.utc)

    # Биндим publish_event к конкретному инстансу app
    async def publish_event(event_type: str, data: dict):
        await _publish_event(app, event_type, data)

    app.state.publish_event = publish_event

    # Создаём дефолтный проект если БД пустая
    _seed_projects(app.state.db)

    # Purge старых данных (>30 дней)
    purged = _purge_old_data(app.state.db)
    if purged:
        logger.info("Cleanup: удалено %d старых записей", purged)

    logger.info("Сервер готов")
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("Завершение работы сервера")


# ---------------------------------------------------------------------------
# Создание приложения
# ---------------------------------------------------------------------------

app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description="Оркестратор для Насти — управление проектами через Codex CLI",
    lifespan=lifespan,
)

# CORS — на проде nginx ограничит до конкретных origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS if CORS_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Подключение роутеров
# ---------------------------------------------------------------------------

from backend.api.chat import router as chat_router
from backend.api.projects import router as projects_router
from backend.api.results import router as results_router
from backend.api.documents import router as documents_router
from backend.api.links import router as links_router
from backend.api.webhooks import router as webhooks_router
from backend.api.system import router_system, router_queue
from backend.api.settings import router as settings_router

app.include_router(chat_router,      prefix="/api/chat",       tags=["chat"])
app.include_router(projects_router,  prefix="/api/projects",   tags=["projects"])
app.include_router(results_router,   prefix="/api/results",    tags=["results"])
app.include_router(documents_router, prefix="/api/documents",  tags=["documents"])
app.include_router(links_router,     prefix="/api/links",      tags=["links"])
app.include_router(webhooks_router,  prefix="/api/webhooks",   tags=["webhooks"])
app.include_router(router_system,    prefix="/api/system",     tags=["system"])
app.include_router(router_queue,     prefix="/api/queue",      tags=["queue"])
app.include_router(settings_router,  prefix="/api/settings",   tags=["settings"])

# SSE монтируем отдельно (без prefix /api/system, чтобы путь был /api/events/stream)
from backend.api.system import event_stream
app.add_api_route("/api/events/stream", event_stream, methods=["GET"], tags=["system"])


# ---------------------------------------------------------------------------
# Standalone режим — backend раздаёт frontend/dist (без nginx)
# ---------------------------------------------------------------------------

if SERVE_STATIC:
    from pathlib import Path as _Path
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse as _FileResponse

    _DIST_DIR = _Path(__file__).resolve().parent.parent / "frontend" / "dist"

    if _DIST_DIR.is_dir():
        # Статика (JS, CSS, assets)
        _ASSETS_DIR = _DIST_DIR / "assets"
        if _ASSETS_DIR.is_dir():
            app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="static-assets")

        # SPA fallback — всё что не /api/* отдаёт index.html
        @app.get("/{path:path}", tags=["static"])
        async def spa_fallback(path: str):
            file_path = _DIST_DIR / path
            if file_path.is_file():
                return _FileResponse(str(file_path))
            return _FileResponse(str(_DIST_DIR / "index.html"))

        logger.info("Standalone режим: раздаём frontend из %s", _DIST_DIR)
    else:
        logger.warning("SERVE_STATIC=true, но frontend/dist не найден: %s", _DIST_DIR)

        @app.get("/", tags=["system"])
        async def root():
            return {"app": APP_TITLE, "version": APP_VERSION, "error": "frontend/dist not found"}
else:
    @app.get("/", tags=["system"])
    async def root():
        return {
            "app": APP_TITLE,
            "version": APP_VERSION,
            "docs": "/docs",
            "health": "/api/system/health",
        }
