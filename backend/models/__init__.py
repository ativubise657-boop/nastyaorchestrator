"""
Pydantic-модели проекта Nastya Orchestrator.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Проект
# ---------------------------------------------------------------------------

class Project(BaseModel):
    id: str
    name: str
    description: str = ""
    path: str = ""  # локальный путь (необязательно)
    git_url: str = ""  # GitHub URL для клонирования
    created_at: datetime


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    path: str = ""
    git_url: str = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    path: Optional[str] = None
    git_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Задача
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Task(BaseModel):
    id: str
    project_id: str
    prompt: str
    mode: str = "auto"
    status: TaskStatus = TaskStatus.queued
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Сообщение чата
# ---------------------------------------------------------------------------

class ChatAttachment(BaseModel):
    """Прикреплённый к сообщению файл (превью в чате)."""
    filename: str
    size: int = 0
    content_type: str = ""
    document_id: Optional[str] = None  # id в таблице documents


class ChatMessage(BaseModel):
    id: str
    project_id: str
    role: str  # user / assistant / system
    content: str
    task_id: Optional[str] = None
    attachments: list[ChatAttachment] = []
    created_at: datetime


class ChatSendRequest(BaseModel):
    project_id: str
    message: str
    mode: str = "auto"  # auto / ag+ / rev / solo
    model: str = "gpt-5.4"  # glm-4.7-flash / glm-5-turbo / gpt-5.4-nano / gpt-5.4 / gpt-5.3-codex / gemini-2.5-flash
    attachments: list[ChatAttachment] = []


class ChatSendResponse(BaseModel):
    task_id: str
    message_id: str


# ---------------------------------------------------------------------------
# Документ
# ---------------------------------------------------------------------------

class Document(BaseModel):
    id: str
    project_id: str
    filename: str
    path: str
    size: int
    content_type: str
    folder_id: Optional[str] = None
    parse_status: str = 'skipped'  # parsed | failed | skipped | pending
    parse_error: str = ''
    parse_method: str = ''  # markitdown | pdfminer | aitunnel_gemini | cache
    created_at: datetime


# ---------------------------------------------------------------------------
# Папка документов
# ---------------------------------------------------------------------------

class Folder(BaseModel):
    id: str
    project_id: str
    name: str
    parent_id: Optional[str] = None
    created_at: datetime


class FolderCreate(BaseModel):
    name: str
    parent_id: Optional[str] = None


class FolderRename(BaseModel):
    name: str


class DocumentCreate(BaseModel):
    """Создание документа из текстового содержимого (без загрузки файла)."""
    filename: str
    content: str
    folder_id: Optional[str] = None


class DocumentMove(BaseModel):
    folder_id: Optional[str] = None  # None = переместить в корень


class DocumentRename(BaseModel):
    filename: str


# ---------------------------------------------------------------------------
# Ссылка (URL с описанием)
# ---------------------------------------------------------------------------

class Link(BaseModel):
    id: str
    project_id: str
    title: str
    url: str
    description: str = ''
    folder_id: Optional[str] = None
    created_at: datetime


class LinkCreate(BaseModel):
    title: str
    url: str
    description: str = ''
    folder_id: Optional[str] = None


class LinkUpdate(BaseModel):
    title: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    folder_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Worker-статус
# ---------------------------------------------------------------------------

class WorkerStatus(BaseModel):
    online: bool
    last_heartbeat: Optional[datetime] = None
    current_task_id: Optional[str] = None
    queue_size: int


# ---------------------------------------------------------------------------
# Вспомогательные payload-модели
# ---------------------------------------------------------------------------

class HeartbeatRequest(BaseModel):
    task_id: str | None = None
    worker_id: str | None = None


class ResultRequest(BaseModel):
    task_id: str
    status: TaskStatus  # completed / failed
    result: Optional[str] = None
    error: Optional[str] = None
    used_github: bool = False


class StreamChunkRequest(BaseModel):
    task_id: str
    chunk: str


class TaskPhaseRequest(BaseModel):
    task_id: str
    phase: str


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    worker: WorkerStatus
    uptime: float
    queue_size: int
    app_version: str
