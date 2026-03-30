"""
CRUD для ссылок проекта (URL + описание).
"""
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from backend.core.helpers import ensure_project, now_iso
from backend.models import Link, LinkCreate, LinkUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/links/{project_id}
# ---------------------------------------------------------------------------

@router.get("/{project_id}", response_model=list[Link])
async def list_links(project_id: str, request: Request):
    """Список ссылок проекта."""
    state = request.app.state.db
    ensure_project(state, project_id)

    rows = state.fetchall(
        """
        SELECT id, project_id, title, url, description, folder_id, created_at
        FROM links
        WHERE project_id = ?
        ORDER BY created_at DESC
        """,
        (project_id,),
    )
    return [Link(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# POST /api/links/{project_id}
# ---------------------------------------------------------------------------

@router.post("/{project_id}", response_model=Link, status_code=201)
async def create_link(project_id: str, body: LinkCreate, request: Request):
    """Добавить ссылку в проект."""
    state = request.app.state.db
    ensure_project(state, project_id)

    if not body.url.strip():
        raise HTTPException(status_code=400, detail="URL не может быть пустым")

    # Нормализуем URL — добавляем схему если нет
    url = body.url.strip()
    if not url.startswith(("http://", "https://", "ftp://")):
        url = "https://" + url

    link_id = str(uuid.uuid4())
    now = now_iso()

    state.execute(
        """
        INSERT INTO links (id, project_id, title, url, description, folder_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (link_id, project_id, body.title.strip() or url, url, body.description.strip(), body.folder_id, now),
    )
    state.commit()

    logger.info("Ссылка '%s' добавлена в проект %s", body.title or url, project_id)
    return Link(
        id=link_id,
        project_id=project_id,
        title=body.title.strip() or url,
        url=url,
        description=body.description.strip(),
        folder_id=body.folder_id,
        created_at=datetime.fromisoformat(now),
    )


# ---------------------------------------------------------------------------
# DELETE /api/links/{project_id}/{link_id}
# ---------------------------------------------------------------------------

@router.delete("/{project_id}/{link_id}", status_code=204)
async def delete_link(project_id: str, link_id: str, request: Request):
    """Удалить ссылку."""
    state = request.app.state.db
    ensure_project(state, project_id)

    row = state.fetchone(
        "SELECT id FROM links WHERE id = ? AND project_id = ?",
        (link_id, project_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Ссылка {link_id} не найдена")

    state.execute("DELETE FROM links WHERE id = ?", (link_id,))
    state.commit()
    logger.info("Ссылка %s удалена из проекта %s", link_id, project_id)


# ---------------------------------------------------------------------------
# PATCH /api/links/{project_id}/{link_id}
# ---------------------------------------------------------------------------

@router.patch("/{project_id}/{link_id}", response_model=Link)
async def update_link(project_id: str, link_id: str, body: LinkUpdate, request: Request):
    """Обновить ссылку (title, url, description, folder_id)."""
    state = request.app.state.db
    ensure_project(state, project_id)

    row = state.fetchone(
        "SELECT * FROM links WHERE id = ? AND project_id = ?",
        (link_id, project_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Ссылка {link_id} не найдена")

    # Собираем обновляемые поля (только переданные, не None)
    updates: dict[str, str] = {}

    if body.url is not None:
        url = body.url.strip()
        if not url:
            raise HTTPException(status_code=400, detail="URL не может быть пустым")
        # Нормализуем URL — добавляем схему если нет
        if not url.startswith(("http://", "https://", "ftp://")):
            url = "https://" + url
        updates["url"] = url

    if body.title is not None:
        updates["title"] = body.title.strip() or updates.get("url", row["url"])

    if body.description is not None:
        updates["description"] = body.description.strip()

    if body.folder_id is not None:
        updates["folder_id"] = body.folder_id

    if not updates:
        # Ничего не обновляем — возвращаем текущее состояние
        return Link(**dict(row))

    # Формируем SQL UPDATE
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [link_id]
    state.execute(f"UPDATE links SET {set_clause} WHERE id = ?", values)
    state.commit()

    # Возвращаем обновлённую ссылку
    updated = state.fetchone("SELECT * FROM links WHERE id = ?", (link_id,))
    logger.info("Ссылка %s обновлена в проекте %s", link_id, project_id)
    return Link(**dict(updated))
