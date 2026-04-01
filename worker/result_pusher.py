"""Отправка результатов на сервер-оркестратор.

Методы:
  push_result  — финальный результат задачи
  stream_chunk — стриминговый чанк (вызывается по мере выполнения)
  send_heartbeat — сигнал что worker жив
"""
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Таймаут одного HTTP-запроса (секунды)
_HTTP_TIMEOUT = 30


class ResultPusher:
    """Клиент для отправки данных на сервер оркестратора."""

    def __init__(self, server_url: str, token: str, worker_id: str = "worker"):
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.worker_id = worker_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "X-Worker-ID": worker_id,
            "Content-Type": "application/json",
        }

    async def push_result(
        self,
        task_id: str,
        status: str,
        result: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Отправить финальный результат задачи.

        Args:
            task_id: ID задачи из очереди
            status: "completed" или "failed"
            result: текст результата
            error: текст ошибки (если failed)

        Returns:
            True если сервер принял, False если ошибка
        """
        payload: dict[str, Any] = {
            "task_id": task_id,
            "status": status,
        }
        if result is not None:
            payload["result"] = result
        if error is not None:
            payload["error"] = error

        return await self._post("/api/results", payload, log_tag="push_result")

    async def stream_chunk(self, task_id: str, chunk: str) -> bool:
        """Отправить стриминговый чанк.

        Вызывается каждый раз когда Codex CLI выдаёт порцию текста.
        Сервер может транслировать это пользователю в реальном времени.

        Returns:
            True если сервер принял, False при ошибке (не прерываем выполнение)
        """
        payload = {
            "task_id": task_id,
            "chunk": chunk,
        }
        return await self._post("/api/results/stream", payload, log_tag="stream_chunk")

    async def send_phase(self, task_id: str, phase: str) -> bool:
        """Отправить текущую фазу выполнения (отображается в UI вместо 'думает...').

        Args:
            task_id: ID задачи
            phase: текст фазы, например "Роюсь в GitHub в проекте geniled.ru..."
        """
        payload = {"task_id": task_id, "phase": phase}
        return await self._post("/api/results/phase", payload, log_tag="send_phase")

    async def find_or_create_folder(
        self,
        project_id: str,
        folder_name: str,
    ) -> str | None:
        """Найти папку по имени или создать новую. Возвращает folder_id."""
        url = f"{self.server_url}/api/documents/{project_id}/folders"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                # Ищем существующую папку
                response = await client.get(url, headers=self.headers)
                if response.status_code == 200:
                    folders = response.json()
                    for f in folders:
                        if f.get("name", "").lower() == folder_name.lower():
                            return f["id"]

                # Не нашли — создаём
                response = await client.post(
                    url,
                    headers={**self.headers, "Content-Type": "application/json"},
                    json={"name": folder_name},
                )
                if response.status_code == 201:
                    return response.json().get("id")
                logger.warning("[find_or_create_folder] Сервер ответил %d", response.status_code)
        except Exception as e:
            logger.error("[find_or_create_folder] Ошибка: %s", e)
        return None

    async def create_document(
        self,
        project_id: str,
        filename: str,
        content: str,
        folder_id: str | None = None,
    ) -> bool:
        """Создать документ в проекте через API оркестратора.

        Используется после извлечения документов из ответа Codex.
        """
        payload: dict[str, Any] = {
            "filename": filename,
            "content": content,
        }
        if folder_id:
            payload["folder_id"] = folder_id

        return await self._post(
            f"/api/documents/{project_id}/create",
            payload,
            log_tag="create_document",
        )

    async def send_heartbeat(self, task_id: str | None = None) -> dict | None:
        """Сигнал что worker жив. Отправляется каждые N секунд.

        Returns:
            Ответ сервера (может содержать cancel_task_id) или None при ошибке
        """
        payload: dict[str, Any] = {"worker_id": self.worker_id}
        if task_id is not None:
            payload["task_id"] = task_id

        url = f"{self.server_url}/api/queue/heartbeat"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(url, headers=self.headers, json=payload)
                if response.status_code >= 400:
                    logger.warning("[heartbeat] Сервер ответил %d: %s", response.status_code, response.text[:200])
                    return None
                return response.json()
        except httpx.TimeoutException:
            logger.warning("[heartbeat] Таймаут запроса к %s", url)
            return None
        except httpx.ConnectError:
            logger.warning("[heartbeat] Не удалось подключиться к %s", url)
            return None
        except Exception as e:
            logger.error("[heartbeat] Неожиданная ошибка: %s", e)
            return None

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        log_tag: str = "request",
    ) -> bool:
        """Выполнить POST-запрос. Возвращает True при успехе."""
        url = f"{self.server_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(url, headers=self.headers, json=payload)
                if response.status_code >= 400:
                    logger.warning(
                        "[%s] Сервер ответил %d: %s",
                        log_tag, response.status_code, response.text[:200],
                    )
                    return False
                return True
        except httpx.TimeoutException:
            logger.warning("[%s] Таймаут запроса к %s", log_tag, url)
            return False
        except httpx.ConnectError:
            logger.warning("[%s] Не удалось подключиться к %s", log_tag, url)
            return False
        except Exception as e:
            logger.error("[%s] Неожиданная ошибка: %s", log_tag, e)
            return False
