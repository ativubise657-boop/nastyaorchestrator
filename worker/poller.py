"""Основной цикл поллинга очереди задач.

Алгоритм:
  1. GET /api/queue/next — забрать задачу
  2. Если задача есть — выполнить (executor) + стримить результат (result_pusher)
  3. Если нет задач — sleep poll_interval
  4. Heartbeat каждые heartbeat_interval секунд в отдельной asyncio.Task
  5. Graceful shutdown на SIGINT/SIGTERM — дождаться завершения текущей задачи
"""
import asyncio
import logging
import signal
from typing import Any

import httpx

from worker.circuit_breaker import can_execute, record_crash, record_success
from worker.quality_gate import evaluate as qg_evaluate, should_retry as qg_should_retry
from worker.commands import is_command, handle_command
from worker.aitunnel_executor import AITunnelExecutor
from worker.gemini_executor import GeminiExecutor
from worker.config import WorkerConfig
from worker.document_extractor import extract_documents
from worker.executor import CodexExecutor
from worker.mode_resolver import resolve_mode
from worker.models_registry import get_model_id
from worker.result_pusher import ResultPusher

logger = logging.getLogger(__name__)

# Таймаут запроса к очереди (секунды)
_QUEUE_HTTP_TIMEOUT = 15
_AITUNNEL_MODELS = {"glm-4.7-flash", "glm-5-turbo", "gpt-5.4-nano"}
_GEMINI_MODELS = {"gemini-2.5-flash"}


def _is_aitunnel_model(model: str) -> bool:
    resolved = (get_model_id(model) or model).strip().lower()
    return resolved in _AITUNNEL_MODELS


def _is_gemini_model(model: str) -> bool:
    resolved = (get_model_id(model) or model).strip().lower()
    return resolved in _GEMINI_MODELS


class Poller:
    """Поллер очереди задач."""

    def __init__(self, config: WorkerConfig):
        self.config = config
        self.codex_executor = CodexExecutor(
            codex_binary=config.codex_binary,
            task_timeout=config.task_timeout,
        )
        self.aitunnel_executor = AITunnelExecutor(
            api_key=config.aitunnel_api_key,
            base_url=config.aitunnel_base_url,
            request_timeout=config.aitunnel_request_timeout,
            max_tool_rounds=config.aitunnel_max_tool_rounds,
            task_timeout=config.task_timeout,
        )
        self.gemini_executor = GeminiExecutor(
            task_timeout=config.task_timeout,
        )
        # Shared httpx-клиент — пулинг соединений через opera-proxy,
        # экономит TCP+TLS handshake на каждый heartbeat/stream_chunk.
        self._http = httpx.AsyncClient(timeout=_QUEUE_HTTP_TIMEOUT)
        self.pusher = ResultPusher(
            server_url=config.server_url,
            token=config.worker_token,
            worker_id=config.worker_id,
            http_client=self._http,
        )
        self._headers = {
            "Authorization": f"Bearer {config.worker_token}",
            "X-Worker-ID": config.worker_id,
        }

        # Флаг остановки (выставляется SIGINT/SIGTERM)
        self._stop_event = asyncio.Event()

        # ID текущей задачи (для heartbeat)
        self._current_task_id: str | None = None
        self._current_executor: CodexExecutor | AITunnelExecutor | GeminiExecutor | None = None

    def _get_executor(self, model: str) -> CodexExecutor | AITunnelExecutor | GeminiExecutor:
        if _is_gemini_model(model):
            return self.gemini_executor
        if _is_aitunnel_model(model):
            return self.aitunnel_executor
        return self.codex_executor

    # ─── Определение фазы по интенту ────────────────────────────────

    @staticmethod
    def _detect_phase(
        prompt: str,
        documents: list[dict] | None,
        git_url: str | None,
        all_projects: list[dict] | None,
        project: dict | None,
    ) -> str | None:
        """Автоматически подбирает текст фазы по промпту и контексту."""
        import re
        prompt_lower = prompt.lower()

        # CRM — Bitrix24
        try:
            from worker.bitrix_client import is_crm_query
            if is_crm_query(prompt):
                return "Ищу в Bitrix24 CRM..."
        except Exception:
            pass

        # Документы — определяем конкретный запрошенный
        if documents:
            image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            # Ищем запрошенный документ по номеру
            num_match = re.search(r'#(\d+)|(?:документ|файл|doc)\s*(?:№|#)?\s*(\d+)', prompt_lower)
            requested_num = int(num_match.group(1) or num_match.group(2)) if num_match else None

            # По имени файла
            requested_doc = None
            for d in documents:
                fname = d.get("filename", "")
                if fname.lower() in prompt_lower or fname.rsplit(".", 1)[0].lower() in prompt_lower:
                    requested_doc = d
                    break

            # По номеру
            if requested_num and 1 <= requested_num <= len(documents):
                requested_doc = documents[requested_num - 1]

            if requested_doc:
                fname = requested_doc.get("filename", "файл")
                ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                num = requested_doc.get("num", "")
                prefix = f"#{num} " if num else ""

                if ext in ("png", "jpg", "jpeg", "gif", "webp"):
                    return f"Смотрю изображение {prefix}{fname}..."
                elif ext == "pdf":
                    return f"Читаю PDF {prefix}{fname}..."
                elif ext in ("doc", "docx"):
                    return f"Читаю документ {prefix}{fname}..."
                elif ext in ("xls", "xlsx", "csv"):
                    return f"Читаю таблицу {prefix}{fname}..."
                else:
                    return f"Читаю файл {prefix}{fname}..."

            # Общий запрос к документам
            has_images = any(
                any(d.get("filename", "").lower().endswith(e) for e in image_exts)
                for d in documents
            )
            has_content = any(d.get("content") or d.get("requested") for d in documents)
            if has_images and any(kw in prompt_lower for kw in ["изображен", "картинк", "фото", "скрин"]):
                return "Смотрю изображение..."
            elif has_content:
                return "Читаю документы..."

        # GitHub контекст
        if all_projects:
            return f"Сканирую все проекты ({len(all_projects)})..."
        elif git_url and project:
            project_name = project.get("name", "проект")
            return f"Роюсь в GitHub: {project_name}..."

        # Ничего специального — вернём None (дефолтное "Codex думает...")
        return None

    # ─── Точка входа ────────────────────────────────────────────────

    async def run(self) -> None:
        """Запустить worker. Блокирует до получения сигнала остановки."""
        self._setup_signal_handlers()
        logger.info(
            "Worker %s запущен. Сервер: %s, интервал: %ds",
            self.config.worker_id,
            self.config.server_url,
            self.config.poll_interval,
        )

        # Heartbeat в фоне
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="heartbeat"
        )

        try:
            await self._poll_loop()
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            try:
                await self._http.aclose()
            except Exception:
                pass
            logger.info("Worker остановлен.")

    # ─── Внутренние методы ──────────────────────────────────────────

    def _setup_signal_handlers(self) -> None:
        """Регистрируем SIGINT/SIGTERM для graceful shutdown."""
        loop = asyncio.get_event_loop()

        def _handle_signal(signame: str) -> None:
            logger.info("Получен %s — завершаем после текущей задачи...", signame)
            self._stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig.name: _handle_signal(s))
            except NotImplementedError:
                # Windows/некоторые среды не поддерживают add_signal_handler
                pass

    async def _poll_loop(self) -> None:
        """Основной цикл: поллинг → выполнение → результат."""
        while not self._stop_event.is_set():
            task = await self._fetch_next_task()

            if task is None:
                # Нет задач — ждём перед следующим запросом
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.config.poll_interval,
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            await self._process_task(task)

    async def _fetch_next_task(self) -> dict[str, Any] | None:
        """GET /api/queue/next — забрать следующую задачу из очереди.

        Returns:
            dict с полями задачи или None если очередь пуста / ошибка
        """
        url = f"{self.config.server_url}/api/queue/next"
        try:
            response = await self._http.get(url, headers=self._headers)

            if response.status_code == 204:
                # Очередь пуста
                return None

            if response.status_code == 200:
                data = response.json()
                # Сервер может вернуть {"task": {...}} или сразу {...}
                task = data.get("task", data)
                if task and task.get("id"):
                    logger.info(
                        "Получена задача: id=%s, mode=%s",
                        task.get("id"), task.get("mode", "auto"),
                    )
                    return task
                return None

            logger.warning(
                "Неожиданный статус от /api/queue/next: %d", response.status_code
            )
            return None

        except httpx.TimeoutException:
            logger.warning("Таймаут запроса к очереди")
            return None
        except httpx.ConnectError:
            logger.warning("Нет связи с сервером %s", self.config.server_url)
            # Дополнительная пауза при проблемах с сетью
            await asyncio.sleep(self.config.poll_interval * 2)
            return None
        except Exception as e:
            logger.error("Ошибка получения задачи: %s", e)
            return None

    async def _process_task(self, task: dict[str, Any]) -> None:
        """Обработать одну задачу: определить режим → выполнить → отправить результат."""
        task_id: str = str(task["id"])
        prompt: str = task.get("prompt", "")
        project_path: str | None = task.get("project_path") or self.config.default_project_path

        # Проверяем не отменена ли задача до начала выполнения
        resp = await self.pusher.send_heartbeat(task_id=task_id)
        if resp and resp.get("cancel_task_id") == task_id:
            logger.info("Задача %s отменена до начала выполнения — пропускаем", task_id)
            await self.pusher.push_result(
                task_id=task_id, status="cancelled", result="⛔ Задача отменена"
            )
            return

        # Circuit breaker — пропускаем проект если слишком много крашей
        project_id = task.get("project_id", "")
        cb_ok, cb_reason = can_execute(project_id)
        if not cb_ok:
            logger.warning("Задача %s пропущена: %s", task_id, cb_reason)
            await self.pusher.push_result(
                task_id=task_id,
                status="failed",
                error=f"⚠️ {cb_reason}",
            )
            return

        # Режим может быть задан явно в задаче или определяется автоматически
        mode: str = task.get("mode") or resolve_mode(prompt)
        model: str = task.get("model", "gpt-5.4")

        # Контекст из backend (история чата + проект + git_url + all_projects + документы)
        chat_history: list[dict] | None = task.get("chat_history")
        project: dict | None = task.get("project")
        git_url: str | None = task.get("git_url")
        all_projects: list[dict] | None = task.get("all_projects")
        documents: list[dict] | None = task.get("documents")
        doc_folders: list[str] | None = task.get("doc_folders")
        completed_tasks: list[dict] | None = task.get("completed_tasks")
        documents_dir: str | None = task.get("documents_dir")
        codex_sandbox: str | None = task.get("codex_sandbox")

        # Перехват встроенных команд (без вызова Codex CLI)
        if is_command(prompt):
            logger.info("Встроенная команда: %s (задача %s)", prompt.strip(), task_id)
            self._current_task_id = task_id
            try:
                result = await handle_command(prompt, project, chat_history)
                await self.pusher.push_result(
                    task_id=task_id,
                    status=result["status"],
                    result=result.get("result"),
                    error=result.get("error"),
                )
            except Exception as e:
                logger.exception("Ошибка выполнения команды %s", prompt.strip())
                await self.pusher.push_result(
                    task_id=task_id,
                    status="failed",
                    error=f"Ошибка команды: {e}",
                )
            finally:
                self._current_task_id = None
            return

        # Умное определение фазы по интенту промпта и контексту
        phase_text = self._detect_phase(prompt, documents, git_url, all_projects, project)
        if phase_text:
            await self.pusher.send_phase(task_id, phase_text)

        logger.info(
            "Начинаем задачу %s: mode=%s, model=%s, history=%d msgs, git_url=%s",
            task_id, mode, model, len(chat_history) if chat_history else 0, bool(git_url),
        )

        self._current_task_id = task_id
        executor = self._get_executor(model)
        self._current_executor = executor

        try:
            # Флаг: получили ли хоть один чанк текста
            got_first_chunk = False

            # Callback для стриминга чанков
            async def on_chunk(chunk: str) -> None:
                nonlocal got_first_chunk
                if not got_first_chunk:
                    # Первый чанк — переключаем фазу
                    await self.pusher.send_phase(task_id, "")
                got_first_chunk = True
                await self.pusher.stream_chunk(task_id, chunk)

            # Периодические пинги "думает" пока нет текста
            async def thinking_pinger() -> None:
                dots = 0
                while not got_first_chunk:
                    await asyncio.sleep(3)
                    if not got_first_chunk:
                        dots = (dots + 1) % 4
                        await self.pusher.stream_chunk(task_id, "")

            thinking_task = asyncio.create_task(thinking_pinger())

            result = await executor.execute(
                prompt=prompt,
                project_path=project_path,
                mode=mode,
                model=model,
                chat_history=chat_history,
                project=project,
                git_url=git_url,
                all_projects=all_projects,
                documents=documents,
                doc_folders=doc_folders,
                completed_tasks=completed_tasks,
                documents_dir=documents_dir,
                codex_sandbox=codex_sandbox,
                on_chunk=on_chunk,
            )

            thinking_task.cancel()

            # Извлекаем документы из ответа Codex (:::document:filename[:folder]\n...\n:::)
            result_text = result.get("result", "")
            if result["status"] == "completed" and result_text:
                cleaned_text, docs = extract_documents(result_text)
                if docs:
                    project_id = task.get("project_id") or (project and project.get("id")) or ""
                    for doc in docs:
                        # Определяем папку по имени
                        folder_id = None
                        if doc.get("folder"):
                            folder_id = await self.pusher.find_or_create_folder(
                                project_id, doc["folder"]
                            )
                        ok = await self.pusher.create_document(
                            project_id=project_id,
                            filename=doc["filename"],
                            content=doc["content"],
                            folder_id=folder_id,
                        )
                        if ok:
                            logger.info("Документ '%s' создан в проекте %s (folder=%s)", doc["filename"], project_id, doc.get("folder"))
                        else:
                            logger.warning("Не удалось создать документ '%s'", doc["filename"])
                    result["result"] = cleaned_text

            logger.info(
                "Задача %s завершена: status=%s, result_len=%d",
                task_id, result["status"], len(result.get("result", "")),
            )

            # Circuit breaker: фиксируем результат
            if result["status"] == "completed":
                record_success(project_id)
            elif result["status"] == "failed":
                record_crash(project_id, result.get("error", ""))

            # Quality gate: проверяем результат перед отправкой
            if result["status"] == "completed" and result.get("result"):
                qg = qg_evaluate(result["result"], prompt)
                if not qg["passed"]:
                    logger.warning(
                        "Quality gate не пройден для задачи %s (score=%d): %s",
                        task_id, qg["score"], "; ".join(qg["issues"]),
                    )
                    # Отправляем результат, но помечаем проблему в error
                    await self.pusher.push_result(
                        task_id=task_id,
                        status=result["status"],
                        result=result.get("result"),
                        error=f"⚠️ QG score={qg['score']}: {'; '.join(qg['issues'])}",
                    )
                    return

            await self.pusher.push_result(
                task_id=task_id,
                status=result["status"],
                result=result.get("result"),
                error=result.get("error"),
            )

        except Exception as e:
            logger.exception("Необработанная ошибка при выполнении задачи %s", task_id)
            record_crash(project_id, str(e))
            await self.pusher.push_result(
                task_id=task_id,
                status="failed",
                error=f"Внутренняя ошибка worker: {e}",
            )
        finally:
            self._current_executor = None
            self._current_task_id = None

    async def _heartbeat_loop(self) -> None:
        """Периодически отправляет heartbeat на сервер. Проверяет отмену задачи."""
        while True:
            await asyncio.sleep(self.config.heartbeat_interval)
            resp = await self.pusher.send_heartbeat(task_id=self._current_task_id)
            if resp:
                # Проверяем не отменена ли текущая задача
                cancel_id = resp.get("cancel_task_id")
                if cancel_id and cancel_id == self._current_task_id:
                    logger.info("Задача %s отменена пользователем — прерываем", cancel_id)
                    if self._current_executor is not None:
                        self._current_executor.cancel()
            else:
                logger.warning("Heartbeat не доставлен")
