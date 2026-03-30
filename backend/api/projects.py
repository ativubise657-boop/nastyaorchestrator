"""
CRUD проектов + auto-clone из git_url.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from backend.models import Project, ProjectCreate, ProjectUpdate

logger = logging.getLogger(__name__)
router = APIRouter()

# Директория для клонированных репо
REPOS_DIR = Path.home() / "repos"


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
