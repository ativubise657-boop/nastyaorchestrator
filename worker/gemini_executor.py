"""Gemini API executor — прямой доступ к Google Generative AI для чата.

Используется для модели gemini-2.5-flash. API key из remote-config.json.
Трафик через opera-proxy (trust_env=True → HTTPS_PROXY).
Поддерживает multimodal: текст + изображения + PDF/документы нативно.
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from worker.executor import CodexExecutor, PROJECT_ROOT

logger = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"
MAX_INLINE_BYTES = 20 * 1024 * 1024  # 20 МБ


async def _get_gemini_api_key(backend_url: str = "http://127.0.0.1:8781") -> str:
    """Получить Gemini API key: env → backend remote-config API → локальный файл."""
    # 1. Из env
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    # 2. Из backend (где remote-config загружен с GitHub при старте)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{backend_url}/api/system/remote-config")
            if r.status_code == 200:
                key = r.json().get("gemini_api_key", "")
                if key:
                    return key
    except Exception as exc:
        logger.warning("Не удалось получить gemini_api_key от backend: %s", exc)
    # 3. Fallback: локальный remote-config.json (dev-режим)
    for candidate in [
        Path(__file__).resolve().parent.parent / "remote-config.json",
        Path(getattr(sys, "_MEIPASS", "")) / "remote-config.json" if getattr(sys, "frozen", False) else None,
    ]:
        if candidate and candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                key = data.get("gemini_api_key", "")
                if key:
                    return key
            except Exception:
                pass
    return ""


# Расширения которые Gemini может принять как inline_data
_NATIVE_MIME_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/pdf",
}


class GeminiExecutor(CodexExecutor):
    """Executor для Gemini API через Google Generative AI."""

    def __init__(self, task_timeout: int = 600):
        super().__init__(codex_binary="codex", task_timeout=task_timeout)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _build_parts(
        self,
        prompt: str,
        image_paths: list[str],
        documents: list[dict] | None,
    ) -> list[dict]:
        """Собрать parts для Gemini: текст + мультимодальные вложения."""
        parts: list[dict] = []

        # Добавить изображения
        for img_path in image_paths:
            path = Path(img_path)
            if not path.exists() or path.stat().st_size > MAX_INLINE_BYTES:
                continue
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            parts.append({"inline_data": {"mime_type": mime, "data": data}})

        # Добавить PDF/документы нативно (без парсинга через markitdown)
        if documents:
            for doc in documents:
                if not doc.get("requested"):
                    continue
                file_path = doc.get("path", "")
                if not file_path or not Path(file_path).exists():
                    continue
                path = Path(file_path)
                if path.stat().st_size > MAX_INLINE_BYTES:
                    continue
                mime = mimetypes.guess_type(str(path))[0] or ""
                # Нативная поддержка — PDF и изображения
                if mime in _NATIVE_MIME_TYPES:
                    data = base64.b64encode(path.read_bytes()).decode("ascii")
                    parts.append({"inline_data": {"mime_type": mime, "data": data}})

        # Текстовый промпт — последним
        parts.append({"text": prompt})
        return parts

    async def execute(
        self,
        prompt: str,
        project_path: str | None = None,
        mode: str = "solo",
        model: str = "gemini-2.5-flash",
        chat_history: list[dict] | None = None,
        project: dict | None = None,
        git_url: str | None = None,
        all_projects: list[dict] | None = None,
        documents: list[dict] | None = None,
        doc_folders: list[str] | None = None,
        completed_tasks: list[dict] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        # Backend URL из env (worker всегда знает свой сервер)
        backend_url = os.environ.get("ORCH_SERVER_URL", "http://127.0.0.1:8781")
        api_key = await _get_gemini_api_key(backend_url)
        if not api_key:
            return {
                "status": "failed",
                "result": "",
                "error": "GEMINI_API_KEY не найден (ни в env, ни в remote-config, ни в backend).",
            }

        self._cancelled = False

        # Строим контекст как для остальных executor-ов
        context_prompt = await self._build_context_prompt(
            prompt,
            chat_history,
            project,
            None,  # github_context — не нужен для чат-модели
            documents,
            None,  # crm_context
            doc_folders,
            completed_tasks,
        )
        full_prompt = self._build_prompt(context_prompt, mode)

        image_paths = self._extract_image_paths(documents)

        # Собираем multimodal parts
        parts = self._build_parts(full_prompt, image_paths, documents)

        gemini_model = DEFAULT_MODEL
        body: dict[str, Any] = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 16384,
            },
        }

        logger.info(
            "Запускаем Gemini: model=%s, images=%d, docs=%d, prompt_len=%d",
            gemini_model,
            len(image_paths),
            len([d for d in (documents or []) if d.get("requested")]),
            len(full_prompt),
        )

        try:
            async with httpx.AsyncClient(timeout=120, trust_env=True) as client:
                r = await client.post(
                    f"{API_BASE}/{gemini_model}:generateContent",
                    params={"key": api_key},
                    json=body,
                )

            if r.status_code != 200:
                error = f"Gemini HTTP {r.status_code}: {r.text[:500]}"
                logger.error(error)
                return {"status": "failed", "result": "", "error": error}

            data = r.json()
            candidates = data.get("candidates", [])
            if not candidates:
                feedback = data.get("promptFeedback", {})
                error = f"Gemini пустой ответ: {feedback}"
                logger.warning(error)
                return {"status": "failed", "result": "", "error": error}

            text_parts = candidates[0].get("content", {}).get("parts", [])
            result = "\n".join(p.get("text", "") for p in text_parts if "text" in p)

            if on_chunk and result:
                await on_chunk(result)

            logger.info("Gemini ответ: %d символов", len(result))
            return {"status": "completed", "result": result, "error": None}

        except httpx.TimeoutException:
            return {"status": "failed", "result": "", "error": "Gemini timeout (120s)"}
        except Exception as exc:
            logger.exception("Gemini ошибка")
            return {"status": "failed", "result": "", "error": str(exc)}
