"""AI Tunnel runtime adapter for worker tasks."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from worker.aitunnel_tools import AITunnelToolRunner, get_tool_definitions
from worker.base_executor import BaseExecutor, ExecuteRequest, PROJECT_ROOT
from worker.models_registry import get_model_id

logger = logging.getLogger(__name__)

DEFAULT_AITUNNEL_BASE_URL = "https://api.aitunnel.ru/v1"
DEFAULT_AITUNNEL_TIMEOUT = 120
DEFAULT_MAX_TOOL_ROUNDS = 16
STREAM_CHUNK_SIZE = 80


class AITunnelExecutor(BaseExecutor):
    """OpenAI-compatible AI Tunnel executor with project tools."""

    TOOL_PROMPT = (
        "Ты работаешь через AI Tunnel API, а не через локальный CLI.\n"
        "Для доступа к проекту используй встроенные tools: read_file, write_file, list_directory, search_files, "
        "execute_command, get_project_info.\n"
        "Перед изменением файла сначала прочитай его.\n"
        "Если нужно проверить сборку, тесты или git-статус, используй execute_command.\n"
        "Не придумывай содержимое файлов, которых не видел.\n"
        "Работай только внутри рабочей директории проекта."
    )

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = DEFAULT_AITUNNEL_BASE_URL,
        request_timeout: int = DEFAULT_AITUNNEL_TIMEOUT,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
        task_timeout: int = 600,
    ):
        super().__init__(task_timeout=task_timeout)
        self.api_key = api_key or os.getenv("AITUNNEL_API_KEY", "")
        self.base_url = (base_url or os.getenv("AITUNNEL_BASE_URL", DEFAULT_AITUNNEL_BASE_URL)).rstrip("/")
        self.request_timeout = int(os.getenv("AITUNNEL_REQUEST_TIMEOUT", str(request_timeout)))
        self.max_tool_rounds = int(os.getenv("AITUNNEL_MAX_TOOL_ROUNDS", str(max_tool_rounds)))
        self._cancelled = False
        self._tool_runner: AITunnelToolRunner | None = None

    def cancel(self) -> None:
        self._cancelled = True
        if self._tool_runner is not None:
            self._tool_runner.cancel()

    async def execute(self, req: ExecuteRequest) -> dict[str, Any]:
        """Выполнить задачу через AI Tunnel API.

        Принимает единый ExecuteRequest вместо 13+ отдельных параметров.
        Внутренняя логика не изменена — только обращение к полям через req.
        """
        if not self.api_key:
            return {
                "status": "failed",
                "result": "",
                "error": "AITUNNEL_API_KEY не настроен. Добавь ключ AI Tunnel в окружение worker.",
            }

        # Локальные переменные для краткости внутри метода
        prompt = req.prompt
        mode = req.mode
        model = req.model
        documents = req.documents
        on_chunk: Callable[[str], Awaitable[None]] | None = req.on_chunk  # type: ignore[assignment]

        self._cancelled = False

        # Fix 4.4A: GitHub и CRM контексты параллельно (asyncio.gather в BaseExecutor)
        github_context, crm_context = await self._fetch_contexts_parallel(
            git_url=req.git_url, all_projects=req.all_projects, prompt=prompt,
        )

        workspace = self._existing_dir(req.workspace) or str(PROJECT_ROOT)

        context_prompt = await self._build_context_prompt(
            prompt,
            req.chat_history,
            req.project,
            github_context,
            documents,
            crm_context,
            req.doc_folders,
            req.completed_tasks,
            workspace=workspace,
            task_id=req.task_id,
        )
        full_prompt = self._build_prompt(context_prompt, mode)
        user_prompt = self._strip_embedded_system_prompt(full_prompt)

        image_paths = self._extract_image_paths(documents)
        self._tool_runner = AITunnelToolRunner(workspace)

        # AGENTS.md из workspace → в system message (стиль, шорткаты, правила).
        # SYSTEM_PROMPT остаётся базовым, AGENTS.md его расширяет.
        agents_md = self._load_agents_md(workspace)
        system_parts = [self.SYSTEM_PROMPT, self.TOOL_PROMPT, f"Рабочая директория: {workspace}"]
        if agents_md:
            system_parts.append(
                f"\n--- Инструкции ассистента (AGENTS.md) ---\n{agents_md}\n--- Конец инструкций ---"
            )
        model_id = get_model_id(model)
        tools = get_tool_definitions()
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": "\n\n".join(system_parts),
            },
            {
                "role": "user",
                "content": self._build_user_content(user_prompt, image_paths),
            },
        ]

        result_parts: list[str] = []

        logger.info(
            "Запускаем AI Tunnel: model=%s, workspace=%s, images=%d, prompt_len=%d",
            model_id,
            workspace,
            len(image_paths),
            len(user_prompt),
        )

        try:
            async with httpx.AsyncClient(timeout=min(self.task_timeout, self.request_timeout)) as client:
                for _ in range(self.max_tool_rounds):
                    if self._cancelled:
                        return {
                            "status": "cancelled",
                            "result": "".join(result_parts),
                            "error": "Задача отменена",
                        }

                    response = await self._call_api(
                        client=client,
                        model=model_id,
                        messages=messages,
                        tools=tools,
                    )

                    choice = (response.get("choices") or [{}])[0]
                    message = choice.get("message", {}) or {}
                    finish_reason = choice.get("finish_reason") or ""
                    content_text = self._extract_message_text(message)
                    tool_calls = message.get("tool_calls") or []

                    if content_text:
                        result_parts.append(content_text)
                        if on_chunk:
                            await self._stream_text(content_text, on_chunk)

                    if tool_calls:
                        messages.append(
                            {
                                "role": "assistant",
                                "content": content_text,
                                "tool_calls": tool_calls,
                            }
                        )

                        for tool_call in tool_calls:
                            if self._cancelled:
                                return {
                                    "status": "cancelled",
                                    "result": "".join(result_parts),
                                    "error": "Задача отменена",
                                }

                            function = tool_call.get("function", {}) or {}
                            name = function.get("name", "")
                            raw_args = function.get("arguments", "{}")
                            try:
                                arguments = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                            except json.JSONDecodeError:
                                arguments = {}

                            tool_result = await self._tool_runner.run(name, arguments)
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tool_call.get("id", ""),
                                    "content": tool_result,
                                }
                            )

                        if finish_reason == "tool_calls" or tool_calls:
                            continue

                    break
                else:
                    return {
                        "status": "failed",
                        "result": "".join(result_parts),
                        "error": f"AI Tunnel превысил лимит из {self.max_tool_rounds} tool-раундов",
                    }
        except httpx.HTTPStatusError as exc:
            details = exc.response.text[:500]
            logger.error("AI Tunnel HTTP error %s: %s", exc.response.status_code, details)
            return {
                "status": "failed",
                "result": "".join(result_parts),
                "error": f"AI Tunnel HTTP {exc.response.status_code}: {details}",
            }
        except httpx.TimeoutException:
            logger.error("AI Tunnel timeout after %d seconds", self.request_timeout)
            return {
                "status": "failed",
                "result": "".join(result_parts),
                "error": f"Таймаут AI Tunnel: {self.request_timeout} секунд",
            }
        except Exception as exc:
            logger.exception("Ошибка AI Tunnel executor")
            return {
                "status": "failed",
                "result": "".join(result_parts),
                "error": str(exc),
            }
        finally:
            self._tool_runner = None

        return {"status": "completed", "result": "".join(result_parts), "error": None}

    def _strip_embedded_system_prompt(self, prompt: str) -> str:
        if prompt.startswith(self.SYSTEM_PROMPT):
            return prompt[len(self.SYSTEM_PROMPT):].lstrip()
        return prompt

    def _build_user_content(self, prompt: str, image_paths: list[str]) -> str | list[dict[str, Any]]:
        if not image_paths:
            return prompt

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_path in image_paths:
            data_url = self._image_to_data_url(image_path)
            if data_url:
                content.append({"type": "image_url", "image_url": {"url": data_url}})

        # DEBUG: логируем content с плейсхолдерами вместо base64 чтобы не захламлять лог.
        # Активируется через env NASTYAORC_LOG_PROMPT=1.
        if os.environ.get("NASTYAORC_LOG_PROMPT") == "1":
            sanitized: list[dict[str, Any]] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        sanitized.append({
                            "type": "image_url",
                            "image_url_placeholder": f"[IMAGE {len(url)} байт data-url]",
                        })
                    else:
                        sanitized.append(part)
                else:
                    sanitized.append(part)
            logger.info("DEBUG aitunnel content (%d частей): %r", len(content), sanitized)

        return content

    @staticmethod
    def _image_to_data_url(image_path: str) -> str | None:
        path = Path(image_path)
        if not path.exists():
            return None

        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _extract_message_text(message: dict[str, Any]) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "".join(parts)
        return ""

    async def _stream_text(
        self,
        text: str,
        on_chunk: Callable[[str], Awaitable[None]],
    ) -> None:
        for i in range(0, len(text), STREAM_CHUNK_SIZE):
            if self._cancelled:
                break
            await on_chunk(text[i:i + STREAM_CHUNK_SIZE])
            await asyncio.sleep(0.01)

    async def _call_api(
        self,
        *,
        client: httpx.AsyncClient,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "tools": tools,
            "tool_choice": "auto",
        }
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
