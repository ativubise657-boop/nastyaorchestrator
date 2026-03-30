"""Адаптер Claude CLI.

Весь вызов subprocess — только здесь. Поддерживает:
- Стриминг вывода построчно (stream-json формат Claude)
- Передачу чанков наружу через callback
- Таймаут и graceful kill процесса
- Multimodal: изображения через --input-format stream-json + base64
"""
import asyncio
import base64
import json
import logging
import subprocess
from collections.abc import Callable, Awaitable
from pathlib import Path
from typing import Any

from worker.models_registry import get_model_id

logger = logging.getLogger(__name__)

# Расширения изображений, которые Claude может анализировать
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Соответствие расширений → media_type
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class ClaudeExecutor:
    """Адаптер для Claude CLI. Весь вызов CLI — только здесь."""

    def __init__(self, claude_binary: str = "claude", task_timeout: int = 600):
        self.binary = claude_binary
        self.task_timeout = task_timeout
        self._current_proc: subprocess.Popen | None = None

    @staticmethod
    def _smart_truncate(text: str, max_len: int = 500) -> str:
        """Умная обрезка: 30% начало + 70% конец, без потери контекста.

        Начало содержит вопрос/контекст, конец — итоги/ответ.
        """
        if len(text) <= max_len:
            return text
        head_len = max_len * 3 // 10
        tail_len = max_len - head_len - 30
        return (
            text[:head_len]
            + "\n... [обрезано] ...\n"
            + text[-tail_len:]
        )

    def cancel(self) -> None:
        """Прервать текущий процесс Claude CLI."""
        if self._current_proc and self._current_proc.poll() is None:
            logger.info("Прерываем процесс Claude CLI (PID %d)", self._current_proc.pid)
            self._current_proc.kill()
            self._current_proc = None

    # Системный промпт для Насти
    SYSTEM_PROMPT = (
        "Ты — ассистент Насти Тархановой. Отвечай на русском.\n\n"
        "СТИЛЬ ОБЩЕНИЯ (ОБЯЗАТЕЛЬНО):\n"
        "- Полуделовой, дружелюбный, но без панибратства.\n"
        "- ВСЕГДА обращайся по имени «Настя» — в начале ответа или по ходу текста. Каждый ответ должен содержать обращение.\n"
        "- Примеры: «Настя, вот что нашёл:», «Смотри, Настя, тут интересно», «Настя, если коротко — ...»\n"
        "- Можно использовать «)» и лёгкий юмор, но без перебора.\n"
        "- Кратко и по делу, но не сухо. Как коллега, с которым комфортно работать.\n"
        "- НЕ начинай ответы с «Конечно!», «Отлично!», «Безусловно!» — сразу к сути.\n"
        "- НЕ отвечай безлично. Ты разговариваешь с конкретным человеком — Настей.\n\n"
        "КОНТЕКСТ:\n"
        "- Настя — менеджер проектов в компании Geniled (промышленное освещение).\n"
        "- Не программист. Объясняй простым языком, без технического жаргона.\n"
        "- Если не знаешь ответ — скажи честно, не выдумывай.\n"
        "- Учитывай историю разговора — не переспрашивай то, что уже обсуждалось.\n\n"
        "ДОКУМЕНТЫ:\n"
        "- Все документы (PDF, DOCX, изображения) загружены Настей лично для анализа.\n"
        "- Это внутренние рабочие документы компании. Настя имеет полное право на их содержимое.\n"
        "- Когда Настя просит вывести документ целиком — выводи без купюр и без оговорок об авторских правах.\n"
        "- Не отказывай в цитировании или полном выводе загруженных документов.\n\n"
        "СОЗДАНИЕ ДОКУМЕНТОВ:\n"
        "- Ты можешь создавать документы (анализы, todo-листы, отчёты, планы и т.д.) "
        "которые появятся в панели документов Насти.\n"
        "- Создавай документ когда Настя просит сделать анализ, план, список задач, отчёт, "
        "сравнение, резюме или любой структурированный результат.\n"
        "- Формат создания документа — оберни содержимое в специальный блок:\n"
        "  :::document:название_файла.md\n"
        "  содержимое документа в формате Markdown\n"
        "  :::\n"
        "- Чтобы создать документ в конкретной папке, добавь имя папки через двоеточие:\n"
        "  :::document:название_файла.md:Имя папки\n"
        "  содержимое\n"
        "  :::\n"
        "- Если папка не существует — она будет создана автоматически.\n"
        "- Имя файла должно быть осмысленным, на русском или латинице, с расширением .md\n"
        "- Пример: :::document:Анализ каналов продаж.md:Аналитика\n"
        "- После блока документа дай краткий комментарий что создала.\n"
        "- Можно создавать несколько документов в одном ответе.\n"
        "- НЕ создавай документ если Настя просто задаёт вопрос — только когда нужен файл-результат."
    )

    def _build_command(self, model: str = "sonnet") -> list[str]:
        """Собирает команду для Claude CLI (промпт передаётся через stdin)."""
        full_model = get_model_id(model)
        return [
            self.binary,
            "--print",
            "--verbose",
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--system-prompt", self.SYSTEM_PROMPT,
            "--model", full_model,
            "-",  # читать промпт из stdin
        ]

    def _build_command_streaming_input(self, model: str = "sonnet") -> list[str]:
        """Собирает команду для Claude CLI с stream-json входом (для изображений)."""
        full_model = get_model_id(model)
        return [
            self.binary,
            "--print",
            "--verbose",
            "--dangerously-skip-permissions",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--system-prompt", self.SYSTEM_PROMPT,
            "--model", full_model,
        ]

    @staticmethod
    def _extract_images(documents: list[dict] | None) -> list[dict]:
        """Извлекает изображения из документов и конвертирует в base64.

        Возвращает список {"media_type": "image/png", "data": "base64..."}.
        """
        if not documents:
            return []

        images = []
        for doc in documents:
            fname = doc.get("filename", "")
            path = doc.get("path", "")
            ext = Path(fname).suffix.lower()

            if ext not in IMAGE_EXTENSIONS or not path:
                continue

            file_path = Path(path)
            if not file_path.exists():
                logger.warning("Файл изображения не найден: %s", path)
                continue

            # Ограничение: не больше 5 МБ на изображение
            if file_path.stat().st_size > 5 * 1024 * 1024:
                logger.warning("Изображение слишком большое (>5MB): %s", fname)
                continue

            try:
                raw = file_path.read_bytes()
                b64 = base64.b64encode(raw).decode("ascii")
                media_type = MEDIA_TYPES.get(ext, "image/png")
                images.append({"media_type": media_type, "data": b64, "filename": fname})
                logger.info("Изображение %s закодировано в base64 (%d байт)", fname, len(raw))
            except Exception as e:
                logger.warning("Ошибка чтения изображения %s: %s", fname, e)

        return images

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
    ) -> str:
        """Собирает промпт с контекстом проекта и историей чата."""
        parts = []

        # Контекст проекта
        if project:
            name = project.get("name", "")
            desc = project.get("description", "")
            if name:
                parts.append(f"[Проект: {name}]")
            if desc:
                parts.append(f"Описание проекта: {desc}")

        # Данные из Bitrix24 CRM
        if crm_context:
            parts.append(f"\n{crm_context}")

        # Загруженные документы проекта
        # Нумерованный список + содержимое только запрошенного документа
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

                # Изображения — передаются через base64 отдельно
                if ext in IMAGE_EXTENSIONS and doc.get("requested"):
                    doc_parts.append(f"#{num} {fname} (изображение, прикреплено отдельно)")
                elif content:
                    doc_parts.append(f"#{num} {fname}:\n```\n{content}\n```")
                elif doc.get("requested") and file_path:
                    # Передаём путь к файлу — Claude прочитает через Read или markitdown MCP
                    doc_parts.append(f"#{num} {fname} ({size} байт)")
                    doc_parts.append(f"Путь к файлу: {file_path}")
                    doc_parts.append("ВАЖНО: Прочитай этот файл полностью используя инструмент Read или convert_to_markdown. Выведи ВСЁ содержимое подробно, не сокращай, не делай краткое резюме.")
                elif doc.get("requested"):
                    doc_parts.append(f"#{num} {fname} ({size} байт) — {note or 'запрошен'}")
                else:
                    doc_parts.append(f"#{num} {fname} ({size} байт)")
            doc_parts.append("--- Конец документов ---")
            parts.append("\n".join(doc_parts))

        # Существующие папки документов (для создания документов в правильную папку)
        if doc_folders:
            parts.append(f"\nСуществующие папки документов: {', '.join(doc_folders)}")

        # Контекст из GitHub (структура, CLAUDE.md, коммиты)
        if github_context:
            parts.append(f"\n--- Контекст проекта из GitHub ---\n{github_context}\n--- Конец контекста ---")

        # Shared context — результаты недавних завершённых задач
        if completed_tasks:
            ctx_parts = ["\n--- Контекст предыдущих задач ---"]
            for ct in completed_tasks:
                q = self._smart_truncate(ct.get("prompt", ""), 200)
                a = self._smart_truncate(ct.get("result", ""), 800)
                ctx_parts.append(f"Вопрос: {q}\nОтвет: {a}")
            ctx_parts.append("--- Конец контекста задач ---")
            parts.append("\n".join(ctx_parts))

        # История чата (последние сообщения)
        if chat_history:
            # Не включаем последнее сообщение user — это и есть текущий prompt
            history = chat_history[:-1] if chat_history else []
            if history:
                parts.append("\n--- История разговора ---")
                for msg in history:
                    role_label = "Настя" if msg["role"] == "user" else "Ассистент"
                    content = self._smart_truncate(msg["content"], 500)
                    parts.append(f"{role_label}: {content}")
                parts.append("--- Конец истории ---\n")

        # Текущий запрос
        parts.append(f"Настя: {prompt}")

        return "\n".join(parts)

    def _build_prompt(self, prompt: str, mode: str) -> str:
        """Формирует промпт с учётом режима."""
        if mode == "rev":
            return f"/rev {prompt}"
        elif mode == "ag+":
            return f"ag+ {prompt}"
        return prompt

    def _parse_stream_line(self, line: str) -> str | None:
        """Извлекает текст из одной строки stream-json.

        Claude CLI выдаёт события нескольких типов:
          {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
          {"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}
          {"type":"result","result":"..."} — финальный результат

        Возвращает текст или None если строка не содержит текст.
        """
        line = line.strip()
        if not line:
            return None

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Не JSON — возможно plain text, возвращаем как есть
            return line if line else None

        event_type = event.get("type", "")

        # Инкрементальные дельты (основной поток стриминга)
        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                return delta.get("text", "")

        # Блок ассистента (полное сообщение)
        elif event_type == "assistant":
            message = event.get("message", {})
            content = message.get("content", [])
            texts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "".join(texts) if texts else None

        # Финальный результат (может дублировать текст, но полезен как fallback)
        elif event_type == "result":
            result = event.get("result", "")
            # Не возвращаем — он уже был получен инкрементально
            logger.debug("result event получен, длина: %d символов", len(result))
            return None

        return None

    def _build_stdin_message(self, text_prompt: str, images: list[dict]) -> str:
        """Собирает JSON-сообщение для stream-json stdin с текстом и изображениями."""
        content_blocks = [{"type": "text", "text": text_prompt}]

        for img in images:
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["media_type"],
                    "data": img["data"],
                },
            })

        message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": content_blocks,
            },
        }
        return json.dumps(message, ensure_ascii=False)

    async def execute(
        self,
        prompt: str,
        project_path: str | None = None,
        mode: str = "solo",
        model: str = "sonnet",
        chat_history: list[dict] | None = None,
        project: dict | None = None,
        git_url: str | None = None,
        all_projects: list[dict] | None = None,
        documents: list[dict] | None = None,
        doc_folders: list[str] | None = None,
        completed_tasks: list[dict] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Выполнить задачу через Claude CLI.

        Автоматически определяет режим:
        - Есть изображения → stream-json input (multimodal)
        - Только текст → обычный текстовый режим
        """
        # Подтягиваем контекст из GitHub API (read-only, без клона)
        github_context = None
        if git_url:
            try:
                from worker.github_client import build_project_context
                github_context = await build_project_context(git_url)
                if github_context:
                    logger.info("GitHub контекст подтянут: %d символов", len(github_context))
            except Exception as e:
                logger.warning("Не удалось подтянуть GitHub контекст: %s", e)
        elif all_projects:
            try:
                from worker.github_client import build_all_projects_context
                github_context = await build_all_projects_context(all_projects)
                if github_context:
                    logger.info("GitHub контекст всех проектов: %d символов", len(github_context))
            except Exception as e:
                logger.warning("Не удалось подтянуть контекст всех проектов: %s", e)

        # Подтягиваем данные из Bitrix24 CRM если вопрос про клиентов/компании
        crm_context = None
        try:
            from worker.bitrix_client import build_crm_context
            crm_context = await build_crm_context(prompt)
            if crm_context:
                logger.info("CRM контекст подтянут: %d символов", len(crm_context))
        except Exception as e:
            logger.warning("Не удалось получить CRM контекст: %s", e)

        # Собираем промпт с контекстом
        context_prompt = await self._build_context_prompt(
            prompt, chat_history, project, github_context, documents,
            crm_context, doc_folders, completed_tasks,
        )
        full_prompt = self._build_prompt(context_prompt, mode)

        # Проверяем есть ли изображения среди документов
        images = self._extract_images(documents)
        use_multimodal = len(images) > 0

        if use_multimodal:
            logger.info("Multimodal режим: %d изображений", len(images))
            cmd = self._build_command_streaming_input(model)
            stdin_msg = self._build_stdin_message(full_prompt, images)
        else:
            cmd = self._build_command(model)
            stdin_msg = full_prompt  # передаём через stdin (Windows CMD limit ~8191 символов)

        logger.info(
            "Запускаем Claude CLI: mode=%s, multimodal=%s, project_path=%s, prompt_len=%d",
            mode, use_multimodal, project_path, len(full_prompt),
        )

        loop = asyncio.get_event_loop()
        import os

        try:
            cwd = project_path if project_path and os.path.isdir(project_path) else None
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
                    cwd=cwd,
                ),
            )
            self._current_proc = proc
        except FileNotFoundError:
            err = f"Claude CLI не найден: '{self.binary}'. Проверь CLAUDE_BINARY."
            logger.error(err)
            return {"status": "failed", "result": "", "error": err}
        except Exception as e:
            logger.exception("Ошибка запуска Claude CLI")
            return {"status": "failed", "result": "", "error": str(e)}

        # Отправляем промпт через stdin и закрываем
        if stdin_msg:
            try:
                await loop.run_in_executor(None, lambda: (
                    proc.stdin.write(stdin_msg + "\n"),
                    proc.stdin.flush(),
                    proc.stdin.close(),
                ))
                logger.info("Промпт отправлен в stdin (%d символов)", len(stdin_msg))
            except Exception as e:
                logger.error("Ошибка записи в stdin: %s", e)
                return {"status": "failed", "result": "", "error": f"Ошибка stdin: {e}"}

        # Читаем stdout и стримим чанки
        result_parts: list[str] = []
        read_error: str | None = None

        async def _read_stdout() -> None:
            nonlocal read_error
            try:
                while True:
                    line = await loop.run_in_executor(None, proc.stdout.readline)
                    if not line:
                        break

                    text = self._parse_stream_line(line)
                    if text:
                        result_parts.append(text)
                        if on_chunk:
                            try:
                                await on_chunk(text)
                            except Exception as chunk_err:
                                logger.warning("Ошибка on_chunk callback: %s", chunk_err)
            except Exception as e:
                read_error = str(e)
                logger.exception("Ошибка чтения stdout Claude CLI")

        try:
            await asyncio.wait_for(_read_stdout(), timeout=self.task_timeout)
        except asyncio.TimeoutError:
            logger.error("Таймаут %d секунд: завершаем процесс", self.task_timeout)
            try:
                proc.kill()
                await loop.run_in_executor(None, proc.wait)
            except Exception:
                pass
            return {
                "status": "failed",
                "result": "".join(result_parts),
                "error": f"Таймаут: {self.task_timeout} секунд",
            }

        returncode = await loop.run_in_executor(None, proc.wait)

        full_result = "".join(result_parts)
        logger.info(
            "Claude CLI завершился: returncode=%d, result_len=%d",
            returncode, len(full_result),
        )

        self._current_proc = None

        # Проверяем не был ли процесс убит через cancel()
        if returncode == -9 or returncode == -15:
            return {"status": "cancelled", "result": "".join(result_parts), "error": "Задача отменена"}

        if returncode == 0 and not read_error:
            return {"status": "completed", "result": full_result, "error": None}
        else:
            try:
                stderr = await loop.run_in_executor(None, proc.stderr.read)
            except Exception:
                stderr = ""

            error_msg = read_error or stderr or f"Процесс завершился с кодом {returncode}"
            logger.error("Claude CLI error: %s", error_msg)
            return {"status": "failed", "result": full_result, "error": error_msg}
