"""Codex CLI adapter used by the worker."""

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

import httpx

from worker.models_registry import get_model_id

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_REASONING_EFFORTS = {
    "gpt-5.4": "high",
    "gpt-5.3-codex": "xhigh",
    "gpt-5.1-codex": "high",
    "gpt-5.1-codex-max": "xhigh",
    "gpt-5.1-codex-mini": "high",
    "gpt-5-codex-mini": "high",
}


@dataclass(slots=True)
class StreamEvent:
    text: str | None = None
    is_final: bool = False
    error: str | None = None


class CodexExecutor:
    """Thin runtime wrapper around `codex exec --json`."""

    # Минимальный системный промпт — общие правила для всех моделей.
    # Всё остальное (стиль, шорткаты, правила работы с vault) — в AGENTS.md
    # в корне workspace проекта. Он подгружается автоматически (_load_agents_md).
    SYSTEM_PROMPT = (
        "Ты — ассистент Насти. Отвечай на русском, обращайся к Насте по имени.\n"
        "Если не уверен — скажи честно, не выдумывай.\n"
        "Не начинай ответы с «Конечно», «Отлично», «Безусловно».\n\n"
        "СОЗДАНИЕ ФАЙЛА-РЕЗУЛЬТАТА:\n"
        "Если Настя просит оформить результат как документ, используй:\n"
        "  :::document:имя_файла.md\n"
        "  содержимое markdown\n"
        "  :::\n"
        "Для папки: :::document:имя_файла.md:Имя папки\n"
        "После блока коротко скажи, что создала. Без запроса файл не создавай."
    )

    # Максимум байт AGENTS.md для подмешивания в промпт (защита от гигантских файлов)
    _AGENTS_MD_MAX_BYTES = 32 * 1024

    @classmethod
    def _load_agents_md(cls, workspace: str) -> str | None:
        """Читает AGENTS.md из корня workspace. Возвращает None если нет/ошибка."""
        if not workspace:
            return None
        path = Path(workspace) / "AGENTS.md"
        if not path.is_file():
            return None
        try:
            data = path.read_bytes()[: cls._AGENTS_MD_MAX_BYTES]
            return data.decode("utf-8", errors="replace").strip() or None
        except OSError as exc:
            logger.warning("Не удалось прочитать AGENTS.md: %s", exc)
            return None

    def __init__(self, codex_binary: str = "codex", task_timeout: int = 600):
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
        self.task_timeout = task_timeout
        self._current_proc: subprocess.Popen | None = None

    @staticmethod
    def _smart_truncate(text: str, max_len: int = 500) -> str:
        if len(text) <= max_len:
            return text
        head_len = max_len * 3 // 10
        tail_len = max_len - head_len - 30
        return text[:head_len] + "\n... [обрезано] ...\n" + text[-tail_len:]

    def cancel(self) -> None:
        if self._current_proc and self._current_proc.poll() is None:
            logger.info("Прерываем процесс Codex CLI (PID %d)", self._current_proc.pid)
            self._current_proc.kill()
            self._current_proc = None

    @staticmethod
    def _normalize_path_for_cli(path: str) -> str:
        if not path:
            return path
        return str(Path(path))

    @staticmethod
    def _existing_dir(path: str | None) -> str | None:
        if not path:
            return None
        return path if os.path.isdir(path) else None

    def _build_command(
        self,
        *,
        model: str,
        workspace: str,
        image_paths: list[str],
        add_dirs: list[str],
    ) -> list[str]:
        cmd = [
            self.binary,
            "--ask-for-approval",
            "never",
            "--sandbox",
            "workspace-write",
            "--cd",
            self._normalize_path_for_cli(workspace),
        ]

        model_id = get_model_id(model)
        if model_id:
            cmd.extend(["--model", model_id])
            reasoning_effort = MODEL_REASONING_EFFORTS.get(model_id) or MODEL_REASONING_EFFORTS.get(model)
            if reasoning_effort:
                cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])

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
    def _extract_image_paths(documents: list[dict] | None) -> list[str]:
        if not documents:
            return []

        paths: list[str] = []
        for doc in documents:
            file_path = doc.get("path", "")
            suffix = Path(doc.get("filename", "")).suffix.lower()
            if not file_path or suffix not in IMAGE_EXTENSIONS:
                continue
            if not doc.get("requested"):
                continue
            if Path(file_path).exists():
                paths.append(str(Path(file_path).resolve()))

        return paths

    @staticmethod
    def _collect_additional_dirs(
        *,
        workspace: str,
        documents: list[dict] | None,
        image_paths: list[str],
    ) -> list[str]:
        workspace_path = Path(workspace).resolve()
        dirs: set[str] = set()

        for path in image_paths:
            parent = Path(path).resolve().parent
            try:
                parent.relative_to(workspace_path)
            except ValueError:
                dirs.add(str(parent))

        if documents:
            for doc in documents:
                file_path = doc.get("path", "")
                if not file_path:
                    continue
                parent = Path(file_path).resolve().parent
                try:
                    parent.relative_to(workspace_path)
                except ValueError:
                    dirs.add(str(parent))

        return sorted(dirs)

    async def _build_context_prompt(
        self,
        prompt: str,
        chat_history: list[dict] | None = None,
        project: dict | None = None,
        github_context: str | None = None,
        documents: list[dict] | None = None,
        crm_context: str | None = None,
        doc_folders: list[str] | None = None,
        completed_tasks: list[dict] | None = None,
        workspace: str | None = None,
    ) -> str:
        parts: list[str] = [self.SYSTEM_PROMPT]

        # AGENTS.md из workspace — основной источник персоны, шорткатов и правил.
        # Единый для всех моделей (Codex / AI Tunnel / Gemini).
        if workspace:
            agents = self._load_agents_md(workspace)
            if agents:
                parts.append(
                    "\n--- Инструкции ассистента (AGENTS.md) ---\n"
                    f"{agents}\n"
                    "--- Конец инструкций ---"
                )

        if project:
            name = project.get("name", "")
            desc = project.get("description", "")
            if name:
                parts.append(f"[Проект: {name}]")
            if desc:
                parts.append(f"Описание проекта: {desc}")

        if crm_context:
            parts.append(crm_context)

        if documents:
            doc_parts = ["\n--- Документы проекта (обращайся по #номеру) ---"]
            for doc in documents:
                num = doc.get("num", "?")
                fname = doc.get("filename", "?")
                size = doc.get("size", 0)
                ext = Path(fname).suffix.lower()
                content = doc.get("content")
                note = doc.get("note")
                file_path = doc.get("path", "")

                if ext in IMAGE_EXTENSIONS and doc.get("requested"):
                    doc_parts.append(f"#{num} {fname} (изображение прикреплено к первому сообщению)")
                elif content:
                    doc_parts.append(f"#{num} {fname}:\n```\n{content}\n```")
                elif doc.get("requested") and file_path:
                    doc_parts.append(f"#{num} {fname} ({size} байт)")
                    doc_parts.append(f"Путь к файлу: {file_path}")
                    doc_parts.append(
                        "Важно: прочитай этот файл полностью через доступные файловые инструменты и только потом отвечай."
                    )
                elif doc.get("requested"):
                    doc_parts.append(f"#{num} {fname} ({size} байт) — {note or 'запрошен'}")
                else:
                    doc_parts.append(f"#{num} {fname} ({size} байт)")
            doc_parts.append("--- Конец документов ---")
            parts.append("\n".join(doc_parts))

        if doc_folders:
            parts.append(f"\nСуществующие папки документов: {', '.join(doc_folders)}")

        if github_context:
            parts.append(
                f"\n--- Контекст проекта из GitHub ---\n{github_context}\n--- Конец контекста ---"
            )

        if completed_tasks:
            ctx_parts = ["\n--- Контекст предыдущих задач ---"]
            for task in completed_tasks:
                q = self._smart_truncate(task.get("prompt", ""), 200)
                a = self._smart_truncate(task.get("result", ""), 800)
                ctx_parts.append(f"Вопрос: {q}\nОтвет: {a}")
            ctx_parts.append("--- Конец контекста задач ---")
            parts.append("\n".join(ctx_parts))

        if chat_history:
            history = chat_history[:-1]
            if history:
                parts.append("\n--- История разговора ---")
                for msg in history:
                    role_label = "Настя" if msg["role"] == "user" else "Ассистент"
                    content = self._smart_truncate(msg["content"], 500)
                    parts.append(f"{role_label}: {content}")
                parts.append("--- Конец истории ---\n")

        parts.append(f"Настя: {prompt}")
        return "\n".join(parts)

    @staticmethod
    def _build_prompt(prompt: str, mode: str) -> str:
        if mode == "rev":
            return (
                "Режим: code review.\n"
                "Сначала перечисли конкретные проблемы и риски, затем вопросы и только потом краткий итог.\n\n"
                f"{prompt}"
            )
        if mode == "ag+":
            return (
                "Режим: широкий инженерный проход.\n"
                "Сначала быстро спланируй работу, затем внеси изменения end-to-end. "
                "Если реально помогает, используй параллельную работу и декомпозицию.\n\n"
                f"{prompt}"
            )
        return prompt

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

    async def execute(
        self,
        prompt: str,
        project_path: str | None = None,
        mode: str = "solo",
        model: str = "gpt-5.4",
        chat_history: list[dict] | None = None,
        project: dict | None = None,
        git_url: str | None = None,
        all_projects: list[dict] | None = None,
        documents: list[dict] | None = None,
        doc_folders: list[str] | None = None,
        completed_tasks: list[dict] | None = None,
        documents_dir: str | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        github_context = None
        if git_url:
            try:
                from worker.github_client import build_project_context

                github_context = await build_project_context(git_url)
                if github_context:
                    logger.info("GitHub контекст подтянут: %d символов", len(github_context))
            except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
                logger.warning("Не удалось подтянуть GitHub контекст: %s", exc)
        elif all_projects:
            try:
                from worker.github_client import build_all_projects_context

                github_context = await build_all_projects_context(all_projects)
                if github_context:
                    logger.info("GitHub контекст всех проектов: %d символов", len(github_context))
            except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
                logger.warning("Не удалось подтянуть контекст всех проектов: %s", exc)

        crm_context = None
        try:
            from worker.bitrix_client import build_crm_context

            crm_context = await build_crm_context(prompt)
            if crm_context:
                logger.info("CRM контекст подтянут: %d символов", len(crm_context))
        except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
            logger.warning("Не удалось получить CRM контекст: %s", exc)

        workspace = self._existing_dir(project_path) or str(PROJECT_ROOT)

        context_prompt = await self._build_context_prompt(
            prompt,
            chat_history,
            project,
            github_context,
            documents,
            crm_context,
            doc_folders,
            completed_tasks,
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
        if documents_dir and os.path.isdir(documents_dir):
            _dd = str(Path(documents_dir).resolve())
            try:
                Path(_dd).relative_to(Path(workspace).resolve())
            except ValueError:
                if _dd not in add_dirs:
                    add_dirs.append(_dd)
        cmd = self._build_command(
            model=model,
            workspace=workspace,
            image_paths=image_paths,
            add_dirs=add_dirs,
        )

        logger.info(
            "Запускаем Codex CLI: mode=%s, images=%d, workspace=%s, prompt_len=%d",
            mode,
            len(image_paths),
            workspace,
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
            except Exception:
                pass
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
