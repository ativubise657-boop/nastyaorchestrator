"""
Системные эндпоинты:
  GET  /api/system/health       — health check + статус worker-а
  GET  /api/events/stream       — SSE поток событий для фронтенда
  POST /api/queue/next          — worker забирает следующую задачу
  POST /api/queue/heartbeat     — worker сигнализирует о жизни
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path as _Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from backend.core.auth import verify_worker
from backend.core.config import WORKER_HEARTBEAT_TTL, get_local_app_version
from backend.models import HeartbeatRequest, HealthResponse, WorkerStatus

logger = logging.getLogger(__name__)

# Два роутера — один для system, один для queue
router_system = APIRouter()
router_queue = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_worker_status(state, queue) -> WorkerStatus:
    """Вычисляет статус worker-а на основе последнего heartbeat."""
    row = state.fetchone(
        "SELECT task_id, timestamp FROM worker_heartbeats ORDER BY id DESC LIMIT 1"
    )
    if not row:
        return WorkerStatus(online=False, last_heartbeat=None, current_task_id=None, queue_size=queue.size())

    last_ts = datetime.fromisoformat(row["timestamp"])
    # Если heartbeat был недавно — считаем worker живым
    alive = (datetime.now(timezone.utc) - last_ts).total_seconds() < WORKER_HEARTBEAT_TTL

    return WorkerStatus(
        online=alive,
        last_heartbeat=last_ts,
        current_task_id=row["task_id"],
        queue_size=queue.size(),
    )


# ---------------------------------------------------------------------------
# GET /api/system/health
# ---------------------------------------------------------------------------

@router_system.get("/health", response_model=HealthResponse)
async def health(request: Request):
    """Проверка работоспособности сервиса."""
    state = request.app.state.db
    queue = request.app.state.queue
    uptime = (datetime.now(timezone.utc) - request.app.state.start_time).total_seconds()

    worker_status = _get_worker_status(state, queue)

    return HealthResponse(
        status="ok",
        worker=worker_status,
        uptime=uptime,
        queue_size=queue.size(),
        app_version=get_local_app_version(),
    )


# ---------------------------------------------------------------------------
# GET /api/system/statusline  — метрики из Codex CLI statusline
# ---------------------------------------------------------------------------

import json as _json

_STATUSLINE_PATH = _Path(
    os.getenv(
        "STATUSLINE_PATH",
        os.getenv("CODEX_STATUSLINE_PATH", os.getenv("CLAUDE_STATUSLINE_PATH", "/tmp/codex-statusline.json")),
    )
)
_SESSIONS_DIR = _Path(
    os.getenv(
        "CODEX_SESSIONS_DIR",
        os.getenv("CLAUDE_SESSIONS_DIR", str(_Path.home() / ".codex" / "sessions")),
    )
)
_sl_cache: dict = {}
_sl_mtime: float = 0
_sl_sessions_cache: dict = {}
_sl_sessions_signature: tuple[tuple[str, int], ...] = ()


def _parse_iso_to_unix(ts: str | None) -> int:
    if not ts:
        return 0
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
    except Exception as exc:
        logger.debug("system: не удалось распарсить ISO timestamp %r: %s", ts, exc)
        return 0


def _build_session_statusline(event: dict) -> dict:
    payload = event.get("payload", {})
    info = payload.get("info", {}) if isinstance(payload, dict) else {}
    if not isinstance(info, dict):
        info = {}
    rate_limits = payload.get("rate_limits") or info.get("rate_limits") or {}
    primary = rate_limits.get("primary") or {}
    secondary = rate_limits.get("secondary") or {}
    last_usage = info.get("last_token_usage") or {}
    context_window = info.get("model_context_window") or 0

    context_used_pct = None
    total_tokens = last_usage.get("total_tokens")
    if context_window and isinstance(total_tokens, (int, float)):
        context_used_pct = round(min(100.0, max(0.0, (float(total_tokens) / float(context_window)) * 100.0)), 1)

    return {
        "rl_5h_pct": primary.get("used_percent"),
        "rl_5h_reset": primary.get("resets_at"),
        "rl_7d_pct": secondary.get("used_percent"),
        "ram_used_gb": 0,
        "ram_total_gb": 0,
        "ram_pct": 0,
        "session_cost_usd": 0,
        "context_used_pct": context_used_pct,
        "model": rate_limits.get("limit_id"),
        "ts": _parse_iso_to_unix(event.get("timestamp")) or int(datetime.now(timezone.utc).timestamp()),
        "source": "codex_sessions",
        "plan_type": rate_limits.get("plan_type"),
    }


def _load_statusline_from_sessions() -> dict:
    global _sl_sessions_cache, _sl_sessions_signature

    try:
        if not _SESSIONS_DIR.exists():
            return {}

        files = sorted(_SESSIONS_DIR.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
        signature = tuple((str(path), int(path.stat().st_mtime)) for path in files[:8])
        if signature == _sl_sessions_signature:
            return _sl_sessions_cache

        for path in files:
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception as exc:
                logger.debug("system: не удалось прочитать sessions файл %s: %s", path.name, exc)
                continue

            for line in reversed(lines):
                if '"type":"event_msg"' not in line or '"type":"token_count"' not in line:
                    continue
                try:
                    event = _json.loads(line)
                except Exception as exc:
                    logger.debug("system: JSON parse error в %s: %s", path.name, exc)
                    continue

                payload = event.get("payload", {})
                if payload.get("type") != "token_count":
                    continue

                info = payload.get("info", {}) if isinstance(payload, dict) else {}
                if not isinstance(info, dict):
                    info = {}
                rate_limits = payload.get("rate_limits") or info.get("rate_limits") or {}
                if not rate_limits:
                    continue

                parsed = _build_session_statusline(event)
                _sl_sessions_signature = signature
                _sl_sessions_cache = parsed
                return parsed

        _sl_sessions_signature = signature
        _sl_sessions_cache = {}
        return {}
    except Exception as exc:
        logger.debug("system: ошибка чтения sessions statusline, возвращаем кэш: %s", exc)
        return _sl_sessions_cache or {}


@router_system.get("/remote-config")
async def remote_config(request: Request):
    """
    Возвращает remote-config загруженный при startup с GitHub.
    Frontend дёргает этот endpoint при старте чтобы применить настройки
    (header_emoji, default_model, feature flags).

    Пусто при ошибке сети (opera-proxy не готов, GitHub down) — UI показывает дефолт.
    """
    return getattr(request.app.state, "remote_config", {}) or {}


@router_system.post("/remote-config/refresh")
async def remote_config_refresh(request: Request):
    """
    Форс-обновление remote config без рестарта приложения.
    Используется dev-gui или UI-кнопкой "Проверить обновление".
    """
    from backend.core.remote_config import fetch_remote_config as _fetch

    try:
        new_cfg = _fetch()
        old_cfg = getattr(request.app.state, "remote_config", {}) or {}
        request.app.state.remote_config = new_cfg
        changed = old_cfg != new_cfg
        if changed:
            # SSE событие для UI чтобы сразу показал всплывашку
            await request.app.state.publish_event(
                "remote_config_updated",
                {"config": new_cfg, "changed": True},
            )
        return {"ok": True, "changed": changed, "config": new_cfg}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router_system.get("/statusline")
async def statusline():
    """Метрики из Codex CLI statusline (rate limits, RAM, model)."""
    global _sl_cache, _sl_mtime
    try:
        if _STATUSLINE_PATH.exists():
            mtime = _STATUSLINE_PATH.stat().st_mtime
            if mtime != _sl_mtime:
                _sl_cache = _json.loads(_STATUSLINE_PATH.read_text())
                _sl_mtime = mtime
        if _sl_cache:
            return _sl_cache
        return _load_statusline_from_sessions()
    except Exception as exc:
        logger.debug("system: ошибка чтения statusline файла, возвращаем кэш: %s", exc)
        return _sl_cache or _load_statusline_from_sessions() or {}


# ---------------------------------------------------------------------------
# GET /api/events/stream  — SSE
# ---------------------------------------------------------------------------

async def event_stream(request: Request):
    """
    SSE поток событий для фронтенда.
    Типы событий: task_update, task_chunk, worker_status, new_message.
    Keepalive каждые 30 секунд.
    """
    q: asyncio.Queue = asyncio.Queue()
    request.app.state.event_queues.append(q)
    logger.debug("SSE клиент подключился, всего подключений: %d", len(request.app.state.event_queues))

    async def generate():
        try:
            while True:
                # Проверяем disconnect клиента (критично для uvicorn reload)
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    # Keepalive — не дать прокси-серверу закрыть соединение
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("SSE генератор прервался: %s", exc)
        finally:
            try:
                request.app.state.event_queues.remove(q)
            except ValueError:
                pass
            logger.debug("SSE клиент отключился")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # отключаем буферизацию nginx
        },
    )


# ---------------------------------------------------------------------------
# Вспомогательные функции обогащения задачи контекстом (_enrich_*)
# ---------------------------------------------------------------------------

def _enrich_chat_history(state, task: dict) -> list[dict]:
    """
    Возвращает последние 10 сообщений чата.
    Основной путь: только текущая сессия (session-scoped изоляция).
    Fallback для legacy-задач без session_id: последние 10 по проекту.
    """
    project_id = task["project_id"]
    task_session_id = task.get("session_id")

    if task_session_id:
        # Основной путь: история только текущей сессии
        rows = state.fetchall(
            """
            SELECT role, content FROM chat_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            LIMIT 10
            """,
            (task_session_id,),
        )
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    else:
        # Fallback для legacy-задач без session_id (перенесённые данные, старые задачи)
        rows = state.fetchall(
            """
            SELECT role, content FROM chat_messages
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (project_id,),
        )
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _enrich_completed_tasks(state, task: dict) -> list[dict] | None:
    """
    Возвращает результаты последних 5 завершённых задач проекта.
    Даёт Codex понимание что уже было сделано/обсуждено.
    Возвращает None если завершённых задач нет.
    """
    rows = state.fetchall(
        """
        SELECT prompt, result FROM tasks
        WHERE project_id = ? AND status = 'completed' AND result IS NOT NULL
        ORDER BY completed_at DESC
        LIMIT 5
        """,
        (task["project_id"],),
    )
    if not rows:
        return None
    return [{"prompt": r["prompt"][:200], "result": r["result"][:800]} for r in rows]


def _enrich_project(state, task: dict) -> None:
    """
    Заполняет task["project"], task["project_path"], task["git_url"],
    task["all_projects"] — данные проекта и связанных проектов.

    Изменяет task на месте (несколько полей — нельзя вернуть одним значением).
    Для "общего" проекта (без git_url) дополнительно прокидывает all_projects.
    """
    project_id = task["project_id"]
    proj_row = state.fetchone(
        "SELECT name, description, path, git_url FROM projects WHERE id = ?",
        (project_id,),
    )
    task["project"] = {
        "name": proj_row["name"] if proj_row else "Неизвестный проект",
        "description": proj_row["description"] if proj_row else "",
    }
    # project_path используется worker-ом для --cd Codex CLI
    if proj_row and proj_row["path"]:
        task["project_path"] = proj_row["path"]

    # git_url для read-only контекста через GitHub API
    git_url = ""
    if proj_row:
        git_url = proj_row["git_url"] if "git_url" in proj_row.keys() else ""
        if git_url:
            task["git_url"] = git_url

    # Для "общего" проекта (без git_url) — передаём список всех проектов
    if not git_url:
        all_rows = state.fetchall(
            "SELECT name, description, git_url FROM projects WHERE git_url != '' AND git_url IS NOT NULL"
        )
        if all_rows:
            task["all_projects"] = [
                {"name": r["name"], "description": r["description"], "git_url": r["git_url"]}
                for r in all_rows
            ]


def _enrich_documents_dir(task: dict) -> None:
    """
    Заполняет task["documents_dir"] — путь к папке документов проекта.
    Передаётся worker-у чтобы Codex мог читать папку через --add-dir
    даже если конкретный файл не прикреплён к сообщению.
    Изменяет task на месте (условная установка одного поля).
    """
    from backend.core.config import DOCUMENTS_DIR
    import os as _os
    docs_dir = _os.path.join(DOCUMENTS_DIR, task["project_id"])
    if _os.path.isdir(docs_dir):
        task["documents_dir"] = docs_dir


def _enrich_documents(state, task: dict) -> list[dict]:
    """
    Возвращает список документов проекта с метаданными.

    Гибридная выборка: session-scoped + project-wide.
    Если есть session_id: документы текущей сессии + project-wide (session_id IS NULL).
    Если нет session_id (legacy): только project-wide.

    Content подгружается только для явно запрошенных документов:
    1) attachment_document_ids из задачи
    2) упомянут в промпте по #номер или имени файла
    Остальные передаются как listing (имя, размер, статус) — модель сама запросит нужное.
    """
    import re
    import json as _json
    from backend.core.file_types import is_image as _is_image
    from backend.api.documents import _get_text_content

    project_id = task["project_id"]
    task_session_id = task.get("session_id")

    # Выбираем документы с учётом scope сессии
    if task_session_id:
        doc_rows = state.fetchall(
            "SELECT id, filename, path, size, content_type, session_id, "
            "COALESCE(parse_status, 'skipped') AS parse_status "
            "FROM documents WHERE project_id = ? "
            "AND (session_id = ? OR session_id IS NULL) "
            "ORDER BY created_at DESC LIMIT 50",
            (project_id, task_session_id),
        )
    else:
        doc_rows = state.fetchall(
            "SELECT id, filename, path, size, content_type, session_id, "
            "COALESCE(parse_status, 'skipped') AS parse_status "
            "FROM documents WHERE project_id = ? ORDER BY created_at DESC LIMIT 50",
            (project_id,),
        )

    if not doc_rows:
        return []

    # Документы, явно приложенные к этому сообщению (записаны в chat.py при /send)
    attached_ids: set[str] = set()
    try:
        raw_att = task.get("attachment_document_ids") or ""
        if raw_att:
            parsed_att = _json.loads(raw_att)
            if isinstance(parsed_att, list):
                attached_ids = {str(x) for x in parsed_att if x}
    except (ValueError, TypeError):
        attached_ids = set()

    # Определяем целевой документ по тексту промпта
    prompt_lower = task.get("prompt", "").lower()

    # 1) По номеру: "#1", "#2", "документ 3", "файл №2"
    num_match = re.search(r'#(\d+)|(?:документ|файл|doc)\s*(?:№|#)?\s*(\d+)', prompt_lower)
    requested_num = int(num_match.group(1) or num_match.group(2)) if num_match else None

    # 2) По имени файла в промпте
    requested_by_name = None
    for i, d in enumerate(doc_rows):
        fname = d["filename"].lower()
        name_no_ext = fname.rsplit(".", 1)[0] if "." in fname else fname
        if fname in prompt_lower or name_no_ext in prompt_lower:
            requested_by_name = i + 1  # 1-based
            break

    # Issue 2.1C: убрана эвристика wants_docs (magic keyword list).
    # Content подгружается только для явно запрошенного документа.
    target_num = requested_num or requested_by_name

    docs = []
    for i, d in enumerate(doc_rows):
        doc_num = i + 1
        doc_info: dict = {
            "num": doc_num,
            "filename": d["filename"],
            "size": d["size"],
            "path": d["path"],
            "content_type": d["content_type"],
            "parse_status": d["parse_status"],
        }

        # Документ считается запрошенным если:
        # 1) явно приложен к сообщению (attachment_document_ids)
        # 2) ИЛИ упомянут в промпте по #номер/имени
        is_attached = str(d["id"]) in attached_ids
        is_target = is_attached or (target_num == doc_num)

        if is_target:
            # Подгружаем Gemini/markitdown-описание из .md рядом — и для image, и для PDF/DOCX.
            # Раньше image-ветка пропускала _get_text_content → модель видела только
            # "(прикреплено)" без содержимого. Теперь image с parse_status=parsed
            # даёт в промпте text-описание от Gemini Flash.
            text = _get_text_content(d["path"], d["filename"])
            if text:
                doc_info["content"] = text
            if _is_image(d["filename"]):
                # image ВСЕГДА requested=True (Codex с vision увидит через --image,
                # и текстовое описание тоже будет в промпте — двойная подстраховка)
                doc_info["requested"] = True
            elif not text:
                # не-image без content — честная ветка "не распарсилось" (1.1A)
                doc_info["note"] = f"Запрошен ({d['content_type'] or 'binary'})"
                doc_info["requested"] = True

        docs.append(doc_info)

    return docs


def _enrich_sandbox(state, task: dict) -> str:
    """
    Возвращает режим sandbox для Codex CLI.
    Worker передаст значение в `--sandbox <mode>`.
    При ошибке загрузки настроек — безопасный дефолт danger-full-access.
    """
    try:
        from backend.api.settings import _load_sandbox
        return _load_sandbox(state)
    except Exception as exc:
        logger.debug("system: не удалось загрузить sandbox настройки, используем danger-full-access: %s", exc)
        return "danger-full-access"


def _enrich_doc_folders(state, task: dict) -> list[str] | None:
    """
    Возвращает список имён папок документов проекта.
    Нужен Codex для контекста при создании документов.
    Возвращает None если папок нет.
    """
    rows = state.fetchall(
        "SELECT name FROM folders WHERE project_id = ? ORDER BY name",
        (task["project_id"],),
    )
    if not rows:
        return None
    return [r["name"] for r in rows]


# ---------------------------------------------------------------------------
# GET /api/queue/next  — worker забирает задачу
# ---------------------------------------------------------------------------

@router_queue.get("/next", dependencies=[Depends(verify_worker)])
async def queue_next(request: Request):
    """
    Атомарно берёт следующую queued-задачу и переводит в running.
    Возвращает задачу с обогащённым контекстом или {task: null} если очередь пуста.
    """
    queue = request.app.state.queue
    state = request.app.state.db
    task = queue.dequeue()

    if task is None:
        return {"task": None}

    # Обогащаем задачу контекстом — каждая функция независима
    task["chat_history"] = _enrich_chat_history(state, task)

    completed = _enrich_completed_tasks(state, task)
    if completed:
        task["completed_tasks"] = completed

    _enrich_project(state, task)         # устанавливает project / project_path / git_url / all_projects
    _enrich_documents_dir(task)          # устанавливает documents_dir если папка существует

    documents = _enrich_documents(state, task)
    if documents:
        task["documents"] = documents

    task["codex_sandbox"] = _enrich_sandbox(state, task)

    doc_folders = _enrich_doc_folders(state, task)
    if doc_folders:
        task["doc_folders"] = doc_folders

    # Публикуем обновление статуса
    await request.app.state.publish_event(
        "task_update",
        {"task_id": task["id"], "project_id": task["project_id"], "status": "running"},
    )

    logger.info("Worker забрал задачу %s (с контекстом: %d сообщений)", task["id"], len(task["chat_history"]))
    return {"task": task}


# ---------------------------------------------------------------------------
# POST /api/queue/heartbeat  — worker сигнализирует о жизни
# ---------------------------------------------------------------------------

@router_queue.post("/heartbeat", dependencies=[Depends(verify_worker)])
async def queue_heartbeat(body: HeartbeatRequest, request: Request):
    """Записывает heartbeat от worker-а. Используется для определения онлайн-статуса."""
    state = request.app.state.db
    now = _now_iso()

    await state.aexecute(
        "INSERT INTO worker_heartbeats (task_id, timestamp) VALUES (?, ?)",
        (body.task_id, now),
    )
    await state.acommit()

    # Чистим старые heartbeat-записи (оставляем последние 100)
    await state.aexecute(
        """
        DELETE FROM worker_heartbeats
        WHERE id NOT IN (
            SELECT id FROM worker_heartbeats ORDER BY id DESC LIMIT 100
        )
        """
    )
    await state.acommit()

    # Публикуем статус worker-а в SSE
    worker_status = _get_worker_status(state, request.app.state.queue)
    await request.app.state.publish_event(
        "worker_status",
        {
            "online": worker_status.online,
            "last_heartbeat": worker_status.last_heartbeat.isoformat() if worker_status.last_heartbeat else None,
            "current_task_id": worker_status.current_task_id,
            "queue_size": worker_status.queue_size,
        },
    )

    # Если worker работает над задачей — проверяем не отменена ли она
    cancel_task_id = None
    if body.task_id:
        queue = request.app.state.queue
        if queue.is_cancelled(body.task_id):
            cancel_task_id = body.task_id

    return {"ok": True, "timestamp": now, "cancel_task_id": cancel_task_id}


# ---------------------------------------------------------------------------
# GET /api/queue/cancelled/{task_id} — проверить отменена ли задача
# ---------------------------------------------------------------------------

@router_queue.get("/cancelled/{task_id}", dependencies=[Depends(verify_worker)])
async def check_cancelled(task_id: str, request: Request):
    """Worker проверяет отменена ли текущая задача."""
    queue = request.app.state.queue
    return {"cancelled": queue.is_cancelled(task_id)}
