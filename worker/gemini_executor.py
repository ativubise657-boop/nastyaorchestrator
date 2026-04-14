"""Gemini API executor — прямой доступ к Google Generative AI для чата.

Используется для модели gemini-2.5-flash. API key из remote-config.json.
Трафик через opera-proxy (trust_env=True → HTTPS_PROXY).
Поддерживает multimodal: текст + изображения + PDF/документы нативно.
"""
from __future__ import annotations

import asyncio
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
FALLBACK_MODEL = "gemini-2.0-flash"
# Retry для временных сбоев Google (перегрузка/rate limit)
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_DELAYS = [1, 3, 6]  # секунды между попытками на той же модели
MAX_INLINE_BYTES = 20 * 1024 * 1024  # 20 МБ


def _read_secrets_file() -> dict:
    """Прочитать .secrets.json из _MEIPASS (frozen) или корня проекта (dev)."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS) / ".secrets.json")  # type: ignore[attr-defined]
    candidates.append(Path(__file__).resolve().parent.parent / ".secrets.json")
    for path in candidates:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Не удалось прочитать %s: %s", path, exc)
    return {}


async def _get_gemini_api_key(backend_url: str = "http://127.0.0.1:8781") -> str:
    """Получить Gemini API key в порядке приоритета:
    1. .secrets.json (прошит в .exe через PyInstaller)
    2. env GEMINI_API_KEY
    3. backend /api/system/remote-config (legacy fallback)
    """
    # 1. Из .secrets.json (основной источник для production .exe)
    key = _read_secrets_file().get("gemini_api_key", "")
    if key:
        return key
    # 2. Из env (для CI/тестов)
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    # 3. Legacy: через backend remote-config (если ключ вдруг там)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{backend_url}/api/system/remote-config")
            if r.status_code == 200:
                key = r.json().get("gemini_api_key", "")
                if key:
                    return key
    except Exception as exc:
        logger.warning("Не удалось получить gemini_api_key от backend: %s", exc)
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

        body: dict[str, Any] = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 16384,
            },
        }

        logger.info(
            "Запускаем Gemini: images=%d, docs=%d, prompt_len=%d",
            len(image_paths),
            len([d for d in (documents or []) if d.get("requested")]),
            len(full_prompt),
        )

        # Пробуем сначала DEFAULT_MODEL (быстрый retry на 503/429),
        # при финальном отказе — FALLBACK_MODEL (старая стабильная).
        last_error = ""
        for model_name in (DEFAULT_MODEL, FALLBACK_MODEL):
            for attempt, delay in enumerate([0, *RETRY_DELAYS], start=1):
                if self._cancelled:
                    return {"status": "cancelled", "result": "", "error": "Задача отменена"}
                if delay:
                    await asyncio.sleep(delay)
                try:
                    async with httpx.AsyncClient(timeout=120, trust_env=True) as client:
                        r = await client.post(
                            f"{API_BASE}/{model_name}:generateContent",
                            params={"key": api_key},
                            json=body,
                        )
                except httpx.TimeoutException:
                    last_error = f"{model_name}: timeout (120s)"
                    logger.warning(last_error)
                    continue
                except Exception as exc:
                    last_error = f"{model_name}: {exc}"
                    logger.warning("Gemini сетевая ошибка: %s", exc)
                    continue

                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        feedback = data.get("promptFeedback", {})
                        last_error = f"{model_name}: пустой ответ {feedback}"
                        logger.warning(last_error)
                        break  # пустой ответ — не ретраим, переходим на fallback
                    text_parts = candidates[0].get("content", {}).get("parts", [])
                    result = "\n".join(p.get("text", "") for p in text_parts if "text" in p)
                    if on_chunk and result:
                        await on_chunk(result)
                    logger.info("Gemini ответ (%s): %d символов", model_name, len(result))
                    return {"status": "completed", "result": result, "error": None}

                if r.status_code in RETRY_STATUSES:
                    last_error = f"{model_name} HTTP {r.status_code}: {r.text[:200]}"
                    logger.warning("Gemini %s (попытка %d/%d)", last_error, attempt, len(RETRY_DELAYS) + 1)
                    continue

                # Невосстановимая ошибка (401/403/400) — fallback не поможет
                last_error = f"{model_name} HTTP {r.status_code}: {r.text[:500]}"
                logger.error(last_error)
                return {"status": "failed", "result": "", "error": last_error}

        return {
            "status": "failed",
            "result": "",
            "error": f"Gemini недоступен после ретраев. {last_error}",
        }
