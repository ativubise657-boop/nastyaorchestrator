"""
CRUD проектов + auto-clone из git_url.
"""
import asyncio
import logging
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from backend.core.config import APP_VERSION, BASE_DIR
from backend.models import Project, ProjectCreate, ProjectUpdate

logger = logging.getLogger(__name__)
router = APIRouter()

# Директория для клонированных репо
REPOS_DIR = Path.home() / "repos"
APP_PROJECT_NAME = "nastyaorchestrator"
APP_RESTART_SCRIPT = BASE_DIR / "tools" / "restart-app.bat"
APP_CHANGELOG_FILE = "CHANGELOG.md"


def _inject_pat(git_url: str) -> str:
    """Подставить GITHUB_PAT в URL если есть в env (https://TOKEN@github.com/...)."""
    import os
    pat = os.environ.get("GITHUB_PAT", "")
    if not pat or "github.com" not in git_url:
        return git_url
    # Если PAT уже в URL — не дублировать
    if "@github.com" in git_url:
        return git_url
    return git_url.replace("https://github.com/", f"https://{pat}@github.com/")


async def _clone_or_pull(git_url: str, name: str) -> str:
    """Клонировать репо или обновить если уже есть. Возвращает путь."""
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = REPOS_DIR / name
    auth_url = _inject_pat(git_url)

    if repo_path.exists() and (repo_path / ".git").exists():
        # Уже клонировано — pull
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_path), "pull", "--ff-only",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        logger.info("git pull %s: %s", name, stdout.decode().strip() or stderr.decode().strip())
    else:
        # Клонируем (shallow — экономим место и время)
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", auth_url, str(repo_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode != 0:
            err = stderr.decode().strip()
            # Не логировать URL с токеном
            safe_err = err.replace(auth_url, git_url)
            logger.error("git clone %s failed: %s", name, safe_err)
            raise RuntimeError(f"git clone failed: {safe_err}")
        logger.info("Клонирован %s → %s", name, repo_path)

    return str(repo_path)


async def _run_command(*args: str, cwd: Path | None = None, timeout: int = 120) -> tuple[str, str]:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GCM_INTERACTIVE", "Never")
    env.setdefault("GIT_ASKPASS", "")
    env.setdefault("SSH_ASKPASS", "")
    command = list(args)
    git_ssh_command = env.get("APP_GIT_SSH_COMMAND") or env.get("GIT_SSH_COMMAND")
    if command and Path(command[0]).name.lower() == "git" and git_ssh_command:
        normalized_ssh_command = _normalize_windows_ssh_command(git_ssh_command)
        command = [command[0], "-c", f"core.sshCommand={normalized_ssh_command}", *command[1:]]
        env.pop("GIT_SSH_COMMAND", None)
    else:
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"

    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    stdout_text = stdout.decode(errors="replace").strip()
    stderr_text = stderr.decode(errors="replace").strip()
    if proc.returncode != 0:
        raise RuntimeError(stderr_text or stdout_text or f"Command failed: {' '.join(args)}")
    return stdout_text, stderr_text


def _schedule_app_restart() -> bool:
    if not APP_RESTART_SCRIPT.exists():
        logger.warning("Restart script not found: %s", APP_RESTART_SCRIPT)
        return False

    subprocess.Popen(
        ["cmd", "/c", f'start "" /min "{APP_RESTART_SCRIPT}"'],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_app_version(config_text: str) -> str | None:
    match = re.search(r'APP_VERSION:\s*str\s*=\s*["\']([^"\']+)["\']', config_text)
    return match.group(1) if match else None


def _normalize_version(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.lower().startswith("v"):
        normalized = normalized[1:]
    return normalized or None


def _extract_changelog_version(title: str) -> str | None:
    match = re.search(r"\bv?(\d+\.\d+\.\d+)\b", title)
    return match.group(1) if match else None


def _parse_changelog_sections(changelog_text: str | None) -> list[dict]:
    if not changelog_text:
        return []

    sections: list[dict] = []
    current: dict | None = None
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if current is not None and paragraph_lines:
            current["items"].append(" ".join(paragraph_lines).strip())
            paragraph_lines = []

    def flush_section() -> None:
        nonlocal current
        flush_paragraph()
        if current is not None and current["items"]:
            sections.append(current)
        current = None

    for raw_line in changelog_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if line.startswith("## "):
            flush_section()
            title = line[3:].strip()
            current = {
                "title": title,
                "version": _extract_changelog_version(title),
                "items": [],
            }
            continue

        if current is None:
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            flush_paragraph()
            current["items"].append(stripped[2:].strip())
            continue

        if stripped and not stripped.startswith("#"):
            paragraph_lines.append(stripped)
            continue

        flush_paragraph()

    flush_section()
    return sections


def _read_local_text(project_path: Path, relative_path: str) -> str | None:
    try:
        return (project_path / relative_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None


async def _read_git_text(project_path: Path, git_ref: str, relative_path: str) -> str | None:
    try:
        text, _ = await _run_command(
            "git",
            "show",
            f"{git_ref}:{relative_path}",
            cwd=project_path,
            timeout=60,
        )
        return text
    except Exception:
        return None


def _select_release_notes(
    changelog_text: str | None,
    *,
    current_version: str | None,
    target_version: str | None,
    needs_update: bool,
) -> list[dict[str, object]]:
    sections = _parse_changelog_sections(changelog_text)
    if not sections:
        return []

    current_version = _normalize_version(current_version)
    target_version = _normalize_version(target_version)

    def find_section_index(version: str | None) -> int | None:
        if not version:
            return None
        for index, section in enumerate(sections):
            if section.get("version") == version:
                return index
        return None

    if needs_update:
        start_index = find_section_index(target_version) or 0
        end_index = find_section_index(current_version)
        if end_index is None or end_index <= start_index:
            selected = sections[start_index:start_index + 1]
        else:
            selected = sections[start_index:end_index]
    else:
        current_index = find_section_index(current_version)
        selected = sections[current_index:current_index + 1] if current_index is not None else sections[:1]

    return [
        {
            "title": str(section["title"]),
            "version": section.get("version"),
            "items": list(section["items"]),
        }
        for section in selected
    ]


def _read_local_app_version(project_path: Path) -> str:
    try:
        config_text = (project_path / "backend" / "core" / "config.py").read_text(encoding="utf-8", errors="ignore")
        return _extract_app_version(config_text) or APP_VERSION
    except Exception:
        return APP_VERSION


def _format_version_label(version: str | None, sha: str) -> str:
    short_sha = sha[:7] if sha else "unknown"
    return f"v{version} ({short_sha})" if version else short_sha


def _normalize_windows_ssh_command(command: str) -> str:
    if os.name != "nt":
        return command

    def replace_drive(match: re.Match[str]) -> str:
        prefix = match.group(1)
        drive = match.group(2).upper()
        return f"{prefix}{drive}:/"

    return re.sub(r'(^|[\s"])\/([a-zA-Z])\/', replace_drive, command)


def _format_update_check_error(exc: Exception) -> str:
    raw = str(exc).strip()
    lowered = raw.lower()

    if "could not read from remote repository" in lowered or "repository not found" in lowered:
        return "Не удалось связаться с GitHub по SSH. Приложение работает дальше, но проверить обновление сейчас нельзя."
    if "permission denied" in lowered or "publickey" in lowered:
        return "GitHub не принял SSH-ключ. Приложение работает дальше, но обновление временно недоступно."
    if "timed out" in lowered or "timeout" in lowered:
        return "GitHub не ответил вовремя. Приложение работает дальше, попробуй проверить обновление позже."
    return "Не удалось проверить GitHub. Приложение продолжает работать, но обновление временно недоступно."


async def _build_app_update_fallback_preview(project_path: Path, check_error: str) -> dict:
    branch = "master"
    origin_url = ""
    current_sha = ""

    try:
        branch_out, _ = await _run_command("git", "branch", "--show-current", cwd=project_path, timeout=60)
        branch = branch_out or branch
    except Exception:
        pass

    try:
        origin_out, _ = await _run_command("git", "remote", "get-url", "origin", cwd=project_path, timeout=60)
        origin_url = origin_out
    except Exception:
        pass

    try:
        current_sha_out, _ = await _run_command("git", "rev-parse", "HEAD", cwd=project_path, timeout=60)
        current_sha = current_sha_out
    except Exception:
        current_sha = ""

    current_version = _read_local_app_version(project_path)
    local_changelog = _read_local_text(project_path, APP_CHANGELOG_FILE)
    release_notes = _select_release_notes(
        local_changelog,
        current_version=current_version,
        target_version=current_version,
        needs_update=False,
    )
    current_label = _format_version_label(current_version, current_sha)

    return {
        "current_version": current_version,
        "target_version": current_version,
        "current_sha": current_sha,
        "target_sha": current_sha,
        "current_label": current_label,
        "target_label": "Проверка недоступна",
        "branch": branch,
        "origin_url": origin_url,
        "needs_update": False,
        "local_changes": False,
        "check_error": check_error,
        "blocked_reason": check_error,
        "release_notes": release_notes,
        "commit_count": 0,
        "commits": [],
    }


def _get_update_store(app) -> dict[str, dict]:
    store = getattr(app.state, "app_updates", None)
    if store is None:
        store = {}
        app.state.app_updates = store
    return store


def _get_update_status(app, project_id: str) -> dict | None:
    return _get_update_store(app).get(project_id)


def _set_update_status(app, project_id: str, **patch) -> dict:
    store = _get_update_store(app)
    current = dict(store.get(project_id, {}))
    logs = current.get("logs", [])
    log_line = patch.pop("append_log", None)
    if log_line:
        logs = [*logs, log_line]
    current.update(patch)
    current["logs"] = logs
    current["updated_at"] = _now_iso()
    store[project_id] = current
    return current


async def _build_app_update_preview(project_path: Path) -> dict:
    if not (project_path / ".git").exists():
        raise RuntimeError(f"Path is not a git repository: {project_path}")

    status_out, _ = await _run_command(
        "git",
        "status",
        "--porcelain",
        "--untracked-files=no",
        cwd=project_path,
        timeout=60,
    )
    local_changes = bool(status_out)
    branch_out, _ = await _run_command("git", "branch", "--show-current", cwd=project_path, timeout=60)
    branch = branch_out or "master"
    origin_out, _ = await _run_command("git", "remote", "get-url", "origin", cwd=project_path, timeout=60)
    current_sha, _ = await _run_command("git", "rev-parse", "HEAD", cwd=project_path, timeout=60)

    try:
        await _run_command("git", "fetch", "origin", branch, cwd=project_path, timeout=300)
    except Exception as exc:
        logger.warning("App update preview fallback: git fetch failed: %s", exc)
        return await _build_app_update_fallback_preview(project_path, _format_update_check_error(exc))
    target_ref = f"origin/{branch}"
    target_sha, _ = await _run_command("git", "rev-parse", target_ref, cwd=project_path, timeout=60)

    current_version = _read_local_app_version(project_path)
    target_version = current_version
    local_changelog = _read_local_text(project_path, APP_CHANGELOG_FILE)
    try:
        remote_config = await _read_git_text(project_path, target_ref, "backend/core/config.py")
        target_version = _extract_app_version(remote_config or "") or APP_VERSION
    except Exception:
        target_version = APP_VERSION
    remote_changelog = await _read_git_text(project_path, target_ref, APP_CHANGELOG_FILE)
    needs_update = current_sha != target_sha
    release_notes = _select_release_notes(
        remote_changelog if needs_update and remote_changelog else local_changelog,
        current_version=current_version,
        target_version=target_version,
        needs_update=needs_update,
    )

    commits: list[dict[str, str]] = []
    commit_count = 0
    if needs_update:
        log_out, _ = await _run_command(
            "git",
            "log",
            "--format=%H%x09%s",
            f"{current_sha}..{target_ref}",
            "--max-count",
            "5",
            cwd=project_path,
            timeout=60,
        )
        count_out, _ = await _run_command(
            "git",
            "rev-list",
            "--count",
            f"{current_sha}..{target_ref}",
            cwd=project_path,
            timeout=60,
        )
        commit_count = int(count_out or "0")
        for line in log_out.splitlines():
            sha, _, summary = line.partition("\t")
            if sha and summary:
                commits.append({"sha": sha[:7], "summary": summary})

    return {
        "current_version": current_version,
        "target_version": target_version,
        "current_sha": current_sha,
        "target_sha": target_sha,
        "current_label": _format_version_label(current_version, current_sha),
        "target_label": _format_version_label(target_version, target_sha),
        "branch": branch,
        "origin_url": origin_out,
        "needs_update": needs_update,
        "local_changes": local_changes,
        "check_error": None,
        "blocked_reason": (
            "Есть локальные изменения. Сначала закоммить их или убери перед обновлением."
            if local_changes
            else None
        ),
        "release_notes": release_notes,
        "commit_count": commit_count,
        "commits": commits,
    }


async def _run_app_update_job(app, project_id: str, operation_id: str) -> None:
    try:
        preview = await _build_app_update_preview(BASE_DIR)
        _set_update_status(
            app,
            project_id,
            operation_id=operation_id,
            status="running",
            phase="prepare",
            progress=8,
            message="Проверяем локальную версию и GitHub...",
            error=None,
            updated=False,
            restarting=False,
            changed_files=[],
            started_at=_now_iso(),
            **preview,
            append_log="Проверяем текущую версию приложения",
        )

        if preview["local_changes"]:
            raise RuntimeError(preview["blocked_reason"] or "Есть локальные изменения")

        if preview.get("check_error"):
            raise RuntimeError(preview["check_error"])

        if not preview["needs_update"]:
            _set_update_status(
                app,
                project_id,
                status="completed",
                phase="done",
                progress=100,
                message="Уже установлена последняя версия.",
                needs_update=False,
                updated=False,
                restarting=False,
                changed_files=[],
                append_log="Обновлений не найдено",
            )
            return

        branch = preview["branch"]
        before_sha = preview["current_sha"]
        target_ref = f"origin/{branch}"

        _set_update_status(
            app,
            project_id,
            phase="pull",
            progress=34,
            message="Скачиваем изменения из GitHub...",
            append_log="Загружаем изменения из GitHub",
        )
        pull_out, _ = await _run_command(
            "git",
            "pull",
            "--ff-only",
            "origin",
            branch,
            cwd=BASE_DIR,
            timeout=300,
        )

        after_sha, _ = await _run_command("git", "rev-parse", "HEAD", cwd=BASE_DIR, timeout=60)
        diff_out, _ = await _run_command(
            "git",
            "diff",
            "--name-only",
            before_sha,
            after_sha,
            cwd=BASE_DIR,
            timeout=60,
        )
        changed_files = [line for line in diff_out.splitlines() if line.strip()]

        if "requirements.txt" in changed_files:
            _set_update_status(
                app,
                project_id,
                phase="python",
                progress=56,
                message="Обновляем Python-зависимости...",
                append_log="Обновляем Python зависимости",
            )
            await _run_command(sys.executable, "-m", "pip", "install", "-r", "requirements.txt", cwd=BASE_DIR, timeout=900)

        frontend_dir = BASE_DIR / "frontend"
        need_npm_install = (
            not (frontend_dir / "node_modules").exists()
            or "frontend/package.json" in changed_files
            or "frontend/package-lock.json" in changed_files
        )
        if need_npm_install:
            _set_update_status(
                app,
                project_id,
                phase="node",
                progress=70,
                message="Обновляем frontend-зависимости...",
                append_log="Обновляем npm зависимости",
            )
            await _run_command("npm.cmd", "install", "--silent", cwd=frontend_dir, timeout=900)

        _set_update_status(
            app,
            project_id,
            phase="build",
            progress=86,
            message="Собираем новую версию интерфейса...",
            append_log="Собираем frontend",
        )
        await _run_command("npm.cmd", "run", "build", cwd=frontend_dir, timeout=900)

        preview_after = await _build_app_update_preview(BASE_DIR)
        _set_update_status(
            app,
            project_id,
            phase="restart",
            progress=96,
            message="Подготавливаем перезапуск приложения...",
            changed_files=changed_files,
            current_version=preview["current_version"],
            target_version=preview_after["current_version"],
            needs_update=False,
            updated=True,
            current_sha=before_sha,
            target_sha=after_sha,
            current_label=_format_version_label(preview["current_version"], before_sha),
            target_label=_format_version_label(preview_after["current_version"], after_sha),
            append_log="Готовим перезапуск сервисов",
        )

        restarting = _schedule_app_restart()
        _set_update_status(
            app,
            project_id,
            status="completed",
            phase="done",
            progress=100,
            message=(
                "Обновление установлено. Приложение перезапускается..."
                if restarting
                else "Код обновлён, но автоперезапуск не сработал."
            ),
            current_version=preview["current_version"],
            target_version=preview_after["current_version"],
            needs_update=False,
            restarting=restarting,
            changed_files=changed_files,
            pull_output=pull_out,
            current_label=_format_version_label(preview["current_version"], before_sha),
            target_label=_format_version_label(preview_after["current_version"], after_sha),
            append_log="Обновление завершено",
        )
    except Exception as exc:
        logger.exception("App update failed")
        _set_update_status(
            app,
            project_id,
            status="failed",
            phase="failed",
            error=str(exc),
            message=f"Обновление остановлено: {exc}",
            append_log=f"Ошибка: {exc}",
        )


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------

@router.get("", response_model=list[Project])
async def list_projects(request: Request):
    """Возвращает все проекты, сортировка по дате создания."""
    state = request.app.state.db
    rows = state.fetchall(
        "SELECT id, name, description, path, git_url, created_at FROM projects ORDER BY created_at ASC"
    )
    return [Project(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# POST /api/projects
# ---------------------------------------------------------------------------

@router.post("", response_model=Project, status_code=201)
async def create_project(body: ProjectCreate, request: Request):
    """Создаёт новый проект. Если git_url — клонирует репо."""
    state = request.app.state.db
    project_id = str(uuid.uuid4())
    now = _now_iso()

    path = body.path
    git_url = body.git_url

    # Auto-clone: если есть git_url и нет path (или path не существует)
    if git_url and (not path or not Path(path).exists()):
        try:
            path = await _clone_or_pull(git_url, body.name)
        except Exception as e:
            logger.error("Ошибка клонирования %s: %s", body.name, e)
            # Не блокируем создание проекта — просто без path

    state.execute(
        """
        INSERT INTO projects (id, name, description, path, git_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (project_id, body.name, body.description, path, git_url, now),
    )
    state.commit()

    logger.info("Создан проект %s (%s)", project_id, body.name)
    return Project(
        id=project_id,
        name=body.name,
        description=body.description,
        path=path,
        git_url=git_url,
        created_at=datetime.fromisoformat(now),
    )


# ---------------------------------------------------------------------------
# PUT /api/projects/{id}
# ---------------------------------------------------------------------------

@router.put("/{project_id}", response_model=Project)
async def update_project(project_id: str, body: ProjectUpdate, request: Request):
    """Частично обновляет поля проекта."""
    state = request.app.state.db

    row = state.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Проект {project_id} не найден")

    # Формируем SET-часть только из переданных полей
    updates: dict = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.description is not None:
        updates["description"] = body.description
    if body.path is not None:
        updates["path"] = body.path
    if body.git_url is not None:
        updates["git_url"] = body.git_url
        # Auto-clone при обновлении git_url
        try:
            proj_name = body.name or dict(row)["name"]
            cloned_path = await _clone_or_pull(body.git_url, proj_name)
            if not body.path:
                updates["path"] = cloned_path
        except Exception as e:
            logger.error("Ошибка клонирования при update: %s", e)

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [project_id]
        state.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", tuple(values))
        state.commit()
        logger.info("Проект %s обновлён: %s", project_id, list(updates.keys()))

    updated = state.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    return Project(**dict(updated))


# ---------------------------------------------------------------------------
# DELETE /api/projects/{id}
# ---------------------------------------------------------------------------

@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request):
    """
    Удаляет проект и все связанные сущности (задачи, сообщения, документы).
    Файлы документов на диске не удаляются — только записи в БД.
    """
    state = request.app.state.db

    row = state.fetchone("SELECT id FROM projects WHERE id = ?", (project_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Проект {project_id} не найден")

    # Удаляем каскадно
    state.execute("DELETE FROM chat_messages WHERE project_id = ?", (project_id,))
    state.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
    state.execute("DELETE FROM documents WHERE project_id = ?", (project_id,))
    state.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    state.commit()

    logger.info("Проект %s удалён", project_id)


# ---------------------------------------------------------------------------
# POST /api/projects/sync-repos
# ---------------------------------------------------------------------------

@router.post("/sync-repos")
async def sync_repos(request: Request):
    """Git pull всех проектов с git_url. Клонирует если ещё нет."""
    state = request.app.state.db
    rows = state.fetchall(
        "SELECT id, name, git_url, path FROM projects WHERE git_url != '' AND git_url IS NOT NULL"
    )
    results = []
    for r in rows:
        row = dict(r)
        try:
            new_path = await _clone_or_pull(row["git_url"], row["name"])
            # Обновить path если изменился
            if new_path != row.get("path"):
                state.execute("UPDATE projects SET path = ? WHERE id = ?", (new_path, row["id"]))
                state.commit()
            results.append({"name": row["name"], "status": "ok", "path": new_path})
        except Exception as e:
            results.append({"name": row["name"], "status": "error", "error": str(e)})
    return {"synced": len([r for r in results if r["status"] == "ok"]), "results": results}


def _get_app_project_or_404(state, project_id: str) -> dict:
    row = state.fetchone("SELECT id, name FROM projects WHERE id = ?", (project_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Проект {project_id} не найден")

    project = dict(row)
    if project["name"] != APP_PROJECT_NAME:
        raise HTTPException(status_code=400, detail="Обновление доступно только для приложения nastyaorchestrator")
    return project


@router.get("/{project_id}/update-app")
async def preview_app_update(project_id: str, request: Request):
    state = request.app.state.db
    _get_app_project_or_404(state, project_id)

    try:
        preview = await _build_app_update_preview(BASE_DIR)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("App update preview failed")
        raise HTTPException(status_code=500, detail=f"Не удалось подготовить обновление: {exc}") from exc

    return {
        **preview,
        "project_path": str(BASE_DIR),
        "active_status": _get_update_status(request.app, project_id),
    }


@router.get("/{project_id}/update-app/status")
async def app_update_status(project_id: str, request: Request):
    state = request.app.state.db
    _get_app_project_or_404(state, project_id)

    current = _get_update_status(request.app, project_id)
    if current:
        return current

    try:
        preview = await _build_app_update_preview(BASE_DIR)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("App update status failed")
        raise HTTPException(status_code=500, detail=f"Не удалось получить статус обновления: {exc}") from exc

    return {
        **preview,
        "operation_id": None,
        "status": "idle",
        "phase": "idle",
        "progress": 0,
        "message": (
            "Проверка GitHub временно недоступна."
            if preview.get("check_error")
            else "Доступно обновление."
            if preview["needs_update"]
            else "Уже установлена последняя версия."
        ),
        "error": None,
        "updated": False,
        "restarting": False,
        "changed_files": [],
        "logs": [],
        "started_at": None,
        "project_path": str(BASE_DIR),
        "updated_at": _now_iso(),
    }


@router.post("/{project_id}/update-app")
async def start_app_update(project_id: str, request: Request):
    state = request.app.state.db
    _get_app_project_or_404(state, project_id)

    current = _get_update_status(request.app, project_id)
    if current and current.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Обновление уже выполняется")

    try:
        preview = await _build_app_update_preview(BASE_DIR)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("App update start failed")
        raise HTTPException(status_code=500, detail=f"Не удалось запустить обновление: {exc}") from exc

    if preview.get("check_error"):
        raise HTTPException(status_code=409, detail=preview["check_error"])

    operation_id = str(uuid.uuid4())
    status = _set_update_status(
        request.app,
        project_id,
        operation_id=operation_id,
        status="queued",
        phase="queued",
        progress=2,
        message="Готовим обновление...",
        error=None,
        updated=False,
        restarting=False,
        changed_files=[],
        logs=[],
        started_at=_now_iso(),
        project_path=str(BASE_DIR),
        **preview,
    )
    asyncio.create_task(_run_app_update_job(request.app, project_id, operation_id))
    return status
