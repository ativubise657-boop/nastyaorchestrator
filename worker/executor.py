"""Codex CLI adapter used by the worker.

После Issue 2.3A shared utilities (AGENTS.md, секции промпта, пути, truncate)
живут в `worker.base_executor.BaseExecutor`. Здесь — только специфика subprocess
CLI: построение argv, parse stream-json, humanize errors, async execute().

Сохраняются реэкспорты PROJECT_ROOT / IMAGE_EXTENSIONS / NON_READABLE_BINARY_EXTS
для совместимости с существующими импортами (gemini_executor, aitunnel_executor).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx  # noqa: F401  # используется в других модулях через re-export паттерн

from worker.base_executor import (
    BaseExecutor,
    ExecuteRequest,  # re-export для удобного импорта из executor
    IMAGE_EXTENSIONS,  # re-export
    NON_READABLE_BINARY_EXTS,  # re-export
    PROJECT_ROOT,  # re-export
)
from worker.models_registry import get_model_id

__all__ = [
    "BaseExecutor",
    "CodexExecutor",
    "ExecuteRequest",
    "StreamEvent",
    "MODEL_REASONING_EFFORTS",
    "IMAGE_EXTENSIONS",
    "NON_READABLE_BINARY_EXTS",
    "PROJECT_ROOT",
]

logger = logging.getLogger(__name__)

MODEL_REASONING_EFFORTS = {
    "gpt-5.4": "high",
    "gpt-5.3-codex": "xhigh",
    "gpt-5.1-codex": "high",
    "gpt-5.1-codex-max": "xhigh",
    "gpt-5.1-codex-mini": "high",
    "gpt-5-codex-mini": "high",
}

# Fix 4.3A: адаптивный downgrade — короткие сообщения в режиме solo не требуют
# максимального thinking. "Привет" или "спасибо" на xhigh = 60-90 секунд —
# чрезмерно. Режимы rev/ag+ остаются на базовом (там глубина нужна).
_ADAPTIVE_EFFORT_MODES = {"solo", "auto", ""}
_SHORT_PROMPT_THRESHOLD = 500
_HEAVY_PROMPT_KEYWORDS = (
    "рефактор", "рефакторинг", "баг", "ошибка", "дебаг", "debug",
    "исправь", "почини", "review", "ревью", "архитектур", "спроектируй",
    "реализуй", "напиши", "код",
)


def compute_reasoning_effort(model: str, mode: str, user_prompt: str) -> str | None:
    """Выбор reasoning effort по задаче (Fix 4.3A).

    Короткое сообщение в solo без кода/багов → downgrade xhigh → high.
    Всё остальное — базовый из MODEL_REASONING_EFFORTS.
    """
    base = MODEL_REASONING_EFFORTS.get(model) or MODEL_REASONING_EFFORTS.get(get_model_id(model) or model)
    if not base:
        return None
    if base != "xhigh":
        return base  # уже не максимум — оставляем как есть
    if mode not in _ADAPTIVE_EFFORT_MODES:
        return base  # rev / ag+ — нужна глубина
    if len(user_prompt) >= _SHORT_PROMPT_THRESHOLD:
        return base
    lowered = user_prompt.lower()
    if any(kw in lowered for kw in _HEAVY_PROMPT_KEYWORDS):
        return base  # короткий, но про код/баг → нужна глубина
    return "high"


@dataclass(slots=True)
class StreamEvent:
    text: str | None = None
    is_final: bool = False
    error: str | None = None


class CodexExecutor(BaseExecutor):
    """Thin runtime wrapper around `codex exec --json`."""

    def __init__(self, codex_binary: str = "codex", task_timeout: int = 600):
        super().__init__(task_timeout=task_timeout)
        # При frozen (PyInstaller onefile) относительные пути нужно резолвить
        # относительно распакованного _MEIPASS, а не cwd worker-процесса —
        # иначе tools\codex-npx.cmd не находится в папке установки приложения.
        if (
            getattr(sys, "frozen", False)
            and codex_binary
            and not Path(codex_binary).is_absolute()
        ):
            candidate = Path(getattr(sys, "_MEIPASS", "")) / codex_binary
            if candidate.is_file():
                logger.info("frozen: codex_binary resolved to %s", candidate)
                codex_binary = str(candidate)
        self.binary = codex_binary
        self._current_proc: subprocess.Popen | None = None

    def cancel(self) -> None:
        if self._current_proc and self._current_proc.poll() is None:
            logger.info("Прерываем процесс Codex CLI (PID %d)", self._current_proc.pid)
            self._current_proc.kill()
            self._current_proc = None

    def _build_command(
        self,
        *,
        model: str,
        workspace: str,
        image_paths: list[str],
        add_dirs: list[str],
        sandbox: str = "danger-full-access",
        reasoning_effort: str | None = None,
    ) -> list[str]:
        cmd = [
            self.binary,
            "--ask-for-approval",
            "never",
            "--sandbox",
            sandbox or "danger-full-access",
            "--cd",
            self._normalize_path_for_cli(workspace),
        ]

        model_id = get_model_id(model)
        if model_id:
            cmd.extend(["--model", model_id])
            # Если effort не передан явно — берём базовый из mapping (обратная совместимость)
            effort = reasoning_effort or MODEL_REASONING_EFFORTS.get(model_id) or MODEL_REASONING_EFFORTS.get(model)
            if effort:
                cmd.extend(["-c", f'model_reasoning_effort="{effort}"'])

        for add_dir in add_dirs:
            cmd.extend(["--add-dir", self._normalize_path_for_cli(add_dir)])

        for image_path in image_paths:
            cmd.extend(["--image", self._normalize_path_for_cli(image_path)])

        cmd.extend([
            "exec",
            "--json",
            "--skip-git-repo-check",
        ])
        cmd.append("-")
        return cmd

    @staticmethod
    def _parse_stream_line(line: str) -> StreamEvent | None:
        line = line.strip()
        if not line:
            return None

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None

        event_type = event.get("type", "")

        if event_type == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                text = item.get("text", "")
                return StreamEvent(text=text or None)
            return None

        if event_type == "turn.completed":
            return StreamEvent(is_final=True)

        if event_type == "error":
            message = event.get("message") or event.get("error") or ""
            return StreamEvent(
                text=message or None,
                is_final=True,
                error=message or "Codex CLI error event",
            )

        return None

    @staticmethod
    def _humanize_error(error_msg: str, model: str) -> str:
        lowered = error_msg.lower()

        if "403 forbidden" in lowered and "codex/responses" in lowered:
            return (
                "Codex CLI видит локальный логин, но OpenAI отклонил выполнение этой сессии. "
                "Обычно помогает заново выполнить `npx.cmd -y @openai/codex login` под нужным аккаунтом ChatGPT/OpenAI.\n\n"
                f"Техническая деталь:\n{error_msg}"
            )

        if "unsupported_value" in lowered and "reasoning.effort" in lowered:
            return (
                f"Для CLI-модели `{model}` выбран слишком высокий уровень thinking. "
                "Нужно либо понизить reasoning, либо использовать другую модель.\n\n"
                f"Техническая деталь:\n{error_msg}"
            )

        return error_msg

    async def execute(self, req: ExecuteRequest) -> dict[str, Any]:
        """Выполнить задачу через Codex CLI.

        Принимает единый ExecuteRequest вместо 13+ отдельных параметров.
        Внутренняя логика не изменена — только обращение к полям через req.
        """
        # Локальные переменные для краткости внутри метода
        prompt = req.prompt
        mode = req.mode
        model = req.model
        documents = req.documents
        on_chunk: Callable[[str], Awaitable[None]] | None = req.on_chunk  # type: ignore[assignment]

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
        )
        full_prompt = self._build_prompt(context_prompt, mode)

        image_paths = self._extract_image_paths(documents)
        add_dirs = self._collect_additional_dirs(
            workspace=workspace,
            documents=documents,
            image_paths=image_paths,
        )
        # Папка документов проекта — всегда добавляем в --add-dir, даже если
        # конкретные файлы не прикреплены. Codex сможет читать/искать по ней
        # когда Настя спросит "что лежит в документах" или "найди файл X".
        documents_dir = req.documents_dir
        if documents_dir and os.path.isdir(documents_dir):
            _dd = str(Path(documents_dir).resolve())
            try:
                Path(_dd).relative_to(Path(workspace).resolve())
            except ValueError:
                if _dd not in add_dirs:
                    add_dirs.append(_dd)
        # Fix 4.3A: адаптивный thinking по исходному prompt'у (до приклейки контекста)
        effort = compute_reasoning_effort(model, mode, prompt)
        cmd = self._build_command(
            model=model,
            workspace=workspace,
            image_paths=image_paths,
            add_dirs=add_dirs,
            sandbox=req.codex_sandbox or "danger-full-access",
            reasoning_effort=effort,
        )

        logger.info(
            "Запускаем Codex CLI: mode=%s, sandbox=%s, images=%d, workspace=%s, add_dirs=%s, prompt_len=%d",
            mode,
            req.codex_sandbox or "danger-full-access",
            len(image_paths),
            workspace,
            add_dirs,
            len(full_prompt),
        )

        loop = asyncio.get_event_loop()
        try:
            proc = await loop.run_in_executor(
                None,
                lambda: subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    cwd=workspace,
                ),
            )
            self._current_proc = proc
        except FileNotFoundError:
            err = f"Codex CLI не найден: '{self.binary}'. Проверь CODEX_BINARY."
            logger.error(err)
            return {"status": "failed", "result": "", "error": err}
        except Exception as exc:
            logger.exception("Ошибка запуска Codex CLI")
            return {"status": "failed", "result": "", "error": str(exc)}

        try:
            await loop.run_in_executor(
                None,
                lambda: (
                    proc.stdin.write(full_prompt + "\n"),
                    proc.stdin.flush(),
                    proc.stdin.close(),
                ),
            )
        except Exception as exc:
            logger.error("Ошибка записи в stdin Codex CLI: %s", exc)
            return {"status": "failed", "result": "", "error": f"Ошибка stdin: {exc}"}

        result_parts: list[str] = []
        stderr_parts: list[str] = []
        read_error: str | None = None
        final_event = asyncio.Event()

        def _append_stderr(line: str) -> None:
            if not line:
                return
            stderr_parts.append(line)
            total_len = sum(len(part) for part in stderr_parts)
            while total_len > 16_000 and stderr_parts:
                removed = stderr_parts.pop(0)
                total_len -= len(removed)

        async def _read_stdout() -> None:
            nonlocal read_error
            try:
                while True:
                    line = await loop.run_in_executor(None, proc.stdout.readline)
                    if not line:
                        break

                    event = self._parse_stream_line(line)
                    if not event:
                        continue

                    if event.text:
                        result_parts.append(event.text)
                        if on_chunk:
                            try:
                                await on_chunk(event.text)
                            except Exception as chunk_err:
                                logger.warning("Ошибка on_chunk callback: %s", chunk_err)

                    if event.error and not read_error:
                        read_error = event.error

                    if event.is_final:
                        final_event.set()
            except Exception as exc:
                read_error = str(exc)
                logger.exception("Ошибка чтения stdout Codex CLI")
                final_event.set()

        async def _read_stderr() -> None:
            try:
                while True:
                    line = await loop.run_in_executor(None, proc.stderr.readline)
                    if not line:
                        break
                    _append_stderr(line)
            except Exception as exc:
                logger.warning("Ошибка чтения stderr Codex CLI: %s", exc)

        async def _wait_process() -> int:
            return await loop.run_in_executor(None, proc.wait)

        stdout_task = asyncio.create_task(_read_stdout(), name="codex-stdout")
        stderr_task = asyncio.create_task(_read_stderr(), name="codex-stderr")
        proc_wait_task = asyncio.create_task(_wait_process(), name="codex-wait")
        final_wait_task = asyncio.create_task(final_event.wait(), name="codex-final-event")

        completed_from_final_event = False
        try:
            done, _ = await asyncio.wait(
                {proc_wait_task, final_wait_task},
                timeout=self.task_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not done:
                raise asyncio.TimeoutError

            completed_from_final_event = final_wait_task in done and final_event.is_set()

            if proc_wait_task not in done:
                grace_done, _ = await asyncio.wait({proc_wait_task}, timeout=5)
                if not grace_done:
                    logger.warning("Codex CLI не завершился после turn.completed - завершаем принудительно")
                    proc.kill()
                    await proc_wait_task
        except asyncio.TimeoutError:
            logger.error("Таймаут %d секунд: завершаем процесс", self.task_timeout)
            try:
                proc.kill()
                await proc_wait_task
            except Exception as exc:
                logger.debug("executor: proc.kill() после таймаута упал (процесс уже завершился?): %s", exc)
            final_event.set()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            self._current_proc = None
            return {
                "status": "failed",
                "result": "".join(result_parts),
                "error": f"Таймаут: {self.task_timeout} секунд",
            }
        finally:
            final_wait_task.cancel()
            await asyncio.gather(final_wait_task, return_exceptions=True)

        returncode = await proc_wait_task
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        full_result = "".join(result_parts)
        stderr = "".join(stderr_parts).strip()
        logger.info(
            "Codex CLI завершился: returncode=%d, result_len=%d",
            returncode,
            len(full_result),
        )
        self._current_proc = None

        if returncode in (-9, -15):
            return {
                "status": "cancelled",
                "result": full_result,
                "error": "Задача отменена",
            }

        if completed_from_final_event and full_result and not read_error:
            return {"status": "completed", "result": full_result, "error": None}

        if returncode == 0 and not read_error:
            return {"status": "completed", "result": full_result, "error": None}

        error_msg = read_error or stderr or f"Процесс завершился с кодом {returncode}"
        error_msg = self._humanize_error(error_msg, model)
        logger.error("Codex CLI error: %s", error_msg)
        return {"status": "failed", "result": full_result, "error": error_msg}
