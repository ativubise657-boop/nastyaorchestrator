"""
Общие вспомогательные функции для API-модулей.
"""
from datetime import datetime, timezone

from fastapi import HTTPException


# Виртуальный проект для общих сущностей (документы, ссылки без привязки к проекту)
COMMON_PROJECT = "__common__"


def now_iso() -> str:
    """Текущее время UTC в ISO-формате."""
    return datetime.now(timezone.utc).isoformat()


def ensure_project(state, project_id: str) -> None:
    """Кидает 404 если проект не найден. __common__ пропускается."""
    if project_id == COMMON_PROJECT:
        return
    row = state.fetchone("SELECT id FROM projects WHERE id = ?", (project_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Проект {project_id} не найден")
