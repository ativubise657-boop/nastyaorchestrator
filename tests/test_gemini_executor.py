"""Тесты для worker/gemini_executor.py — Gemini API executor.

Все внешние вызовы (httpx, файловая система, .secrets.json) замоканы.
Реальный Gemini API не дёргается.
"""
import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest
import httpx

from worker.gemini_executor import GeminiExecutor, _get_gemini_api_key, _read_secrets_file
from worker.base_executor import ExecuteRequest


def _make_req(**kwargs) -> ExecuteRequest:
    """Создать минимальный ExecuteRequest для тестов."""
    defaults = dict(
        prompt="тест",
        workspace="/tmp/workspace",
        model="gemini-2.5-flash",
        mode="solo",
    )
    defaults.update(kwargs)
    return ExecuteRequest(**defaults)


# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------

def _make_gemini_response(text: str, status_code: int = 200) -> MagicMock:
    """Создать фейковый httpx.Response с Gemini-совместимым JSON."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text}]
                }
            }
        ]
    }
    mock_resp.text = json.dumps({"error": "mock error"})
    return mock_resp


def _make_empty_gemini_response() -> MagicMock:
    """Gemini ответ без candidates (промпт заблокирован)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [],
        "promptFeedback": {"blockReason": "SAFETY"}
    }
    mock_resp.text = ""
    return mock_resp


def _make_error_response(status_code: int, text: str = "error") -> MagicMock:
    """Gemini ответ с ошибочным HTTP статусом."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {}
    mock_resp.text = text
    return mock_resp


# ---------------------------------------------------------------------------
# Патч для BaseExecutor._build_context_prompt и _build_prompt
# — не тестируем сборку контекста здесь, изолируем Gemini-специфику
# ---------------------------------------------------------------------------

PATCH_BUILD_CONTEXT = patch(
    "worker.gemini_executor.GeminiExecutor._build_context_prompt",
    new_callable=AsyncMock,
    return_value="context prompt",
)
PATCH_BUILD_PROMPT = patch(
    "worker.gemini_executor.GeminiExecutor._build_prompt",
    return_value="full prompt text",
)
PATCH_EXTRACT_IMAGES = patch(
    "worker.gemini_executor.GeminiExecutor._extract_image_paths",
    return_value=[],
)
PATCH_EXISTING_DIR = patch(
    "worker.gemini_executor.GeminiExecutor._existing_dir",
    return_value="/tmp/workspace",
)


# ---------------------------------------------------------------------------
# Тест 1: успешный текстовый запрос без картинок
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_text_only():
    """Промпт без картинок → Gemini API вызван, результат распарсен."""
    executor = GeminiExecutor()
    mock_response = _make_gemini_response("Вот ответ на твой вопрос.")

    with (
        patch("worker.gemini_executor._get_gemini_api_key", new=AsyncMock(return_value="fake-key")),
        PATCH_BUILD_CONTEXT,
        PATCH_BUILD_PROMPT,
        PATCH_EXTRACT_IMAGES,
        PATCH_EXISTING_DIR,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        # Настраиваем мок httpx-клиента
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await executor.execute(_make_req(prompt="Что такое asyncio?"))

    assert result["status"] == "completed"
    assert "ответ" in result["result"]
    assert result["error"] is None


# ---------------------------------------------------------------------------
# Тест 2: промпт с изображением — изображение кодируется в base64 и попадает в parts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_with_image(tmp_path):
    """Путь к изображению в documents → inline_data попадает в parts."""
    # Создаём временный PNG-файл с минимальным содержимым
    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)  # минимальный PNG-заголовок

    executor = GeminiExecutor()
    mock_response = _make_gemini_response("Вижу изображение.")

    captured_body = {}

    async def fake_post(url, **kwargs):
        captured_body.update(kwargs.get("json", {}))
        return mock_response

    with (
        patch("worker.gemini_executor._get_gemini_api_key", new=AsyncMock(return_value="fake-key")),
        PATCH_BUILD_CONTEXT,
        PATCH_BUILD_PROMPT,
        patch(
            "worker.gemini_executor.GeminiExecutor._extract_image_paths",
            return_value=[str(img_file)],
        ),
        PATCH_EXISTING_DIR,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_client_cls.return_value = mock_client

        result = await executor.execute(_make_req(prompt="Опиши картинку"))

    assert result["status"] == "completed"
    # Проверяем что в тело запроса попал inline_data для изображения
    parts = captured_body.get("contents", [{}])[0].get("parts", [])
    inline_parts = [p for p in parts if "inline_data" in p]
    assert len(inline_parts) == 1
    assert inline_parts[0]["inline_data"]["mime_type"] == "image/png"


# ---------------------------------------------------------------------------
# Тест 3: retry при временной ошибке 503 → вторая попытка успешна
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_on_transient_error():
    """503 на первой попытке → retry, вторая попытка возвращает успех."""
    executor = GeminiExecutor()
    error_response = _make_error_response(503, "Service Unavailable")
    success_response = _make_gemini_response("Со второй попытки получилось.")

    # Первый вызов — 503, второй — 200
    call_count = 0

    async def fake_post(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return error_response
        return success_response

    with (
        patch("worker.gemini_executor._get_gemini_api_key", new=AsyncMock(return_value="fake-key")),
        PATCH_BUILD_CONTEXT,
        PATCH_BUILD_PROMPT,
        PATCH_EXTRACT_IMAGES,
        PATCH_EXISTING_DIR,
        patch("asyncio.sleep", new=AsyncMock()),  # не ждём реально
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_client_cls.return_value = mock_client

        result = await executor.execute(_make_req(prompt="тест retry"))

    assert result["status"] == "completed"
    assert call_count >= 2  # был хотя бы один retry


# ---------------------------------------------------------------------------
# Тест 4: невалидный JSON в ответе → статус failed, не исключение
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_malformed_response():
    """Gemini возвращает 200, но без candidates → статус failed на этой модели."""
    executor = GeminiExecutor()

    # Ответ без candidates — пустой список
    empty_response = _make_empty_gemini_response()
    # Fallback тоже не поможет — мок возвращает то же самое
    error_fallback = _make_error_response(503)

    call_count = 0

    async def fake_post(url, **kwargs):
        nonlocal call_count
        call_count += 1
        # Первый вызов DEFAULT_MODEL → пустые candidates
        # Дальнейшие попытки (FALLBACK_MODEL + retry) → 503 → exhausted
        if call_count == 1:
            return empty_response
        return error_fallback

    with (
        patch("worker.gemini_executor._get_gemini_api_key", new=AsyncMock(return_value="fake-key")),
        PATCH_BUILD_CONTEXT,
        PATCH_BUILD_PROMPT,
        PATCH_EXTRACT_IMAGES,
        PATCH_EXISTING_DIR,
        patch("asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_client_cls.return_value = mock_client

        result = await executor.execute(_make_req(prompt="тест malformed"))

    # Должны вернуть failed, не бросить исключение
    assert result["status"] == "failed"
    assert result["result"] == ""
    assert result["error"]  # сообщение об ошибке непустое


# ---------------------------------------------------------------------------
# Тест 5: отсутствие GEMINI_API_KEY → понятная ошибка
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_key_missing():
    """Нет ключа ни в env, ни в secrets, ни в backend → status='failed' с понятным сообщением."""
    executor = GeminiExecutor()

    with (
        # _get_gemini_api_key возвращает пустую строку — ключа нет
        patch("worker.gemini_executor._get_gemini_api_key", new=AsyncMock(return_value="")),
        PATCH_BUILD_CONTEXT,
        PATCH_BUILD_PROMPT,
        PATCH_EXTRACT_IMAGES,
        PATCH_EXISTING_DIR,
    ):
        result = await executor.execute(_make_req(prompt="любой запрос"))

    assert result["status"] == "failed"
    assert result["result"] == ""
    # Сообщение должно объяснять причину — не KeyError
    assert "GEMINI_API_KEY" in result["error"] or "gemini" in result["error"].lower()


# ---------------------------------------------------------------------------
# Тест 6: cancel() во время retry → прерывает выполнение
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_stops_execution():
    """cancel() вызванный между retry-попытками → результат 'cancelled'.

    Важно: execute() сбрасывает _cancelled=False в начале → cancel до вызова
    не работает. Нужно вызывать cancel во время asyncio.sleep между попытками.
    """
    executor = GeminiExecutor()

    # 503 на первой попытке → executor пойдёт на retry с asyncio.sleep
    # В момент sleep — вызываем cancel()
    error_503 = _make_error_response(503)

    async def fake_sleep(delay):
        """Симулируем pause и отмену во время ожидания retry."""
        executor.cancel()  # отменяем во время sleep

    async def fake_post(url, **kwargs):
        return error_503

    with (
        patch("worker.gemini_executor._get_gemini_api_key", new=AsyncMock(return_value="fake-key")),
        PATCH_BUILD_CONTEXT,
        PATCH_BUILD_PROMPT,
        PATCH_EXTRACT_IMAGES,
        PATCH_EXISTING_DIR,
        patch("asyncio.sleep", side_effect=fake_sleep),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_client_cls.return_value = mock_client

        result = await executor.execute(_make_req(prompt="тест отмены"))

    assert result["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Тест 7: _read_secrets_file — пустой dict если файл не существует
# ---------------------------------------------------------------------------

def test_read_secrets_file_missing():
    """Если .secrets.json не существует → возвращается пустой dict."""
    with patch("pathlib.Path.is_file", return_value=False):
        secrets = _read_secrets_file()

    assert secrets == {}


# ---------------------------------------------------------------------------
# Тест 8: _get_gemini_api_key берёт ключ из env
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_gemini_api_key_from_env():
    """Ключ в env GEMINI_API_KEY → возвращается без обращения к backend."""
    with (
        patch("worker.gemini_executor._read_secrets_file", return_value={}),
        patch.dict(os.environ, {"GEMINI_API_KEY": "env-test-key"}),
    ):
        key = await _get_gemini_api_key()

    assert key == "env-test-key"


# ---------------------------------------------------------------------------
# Тест 9: невосстановимая ошибка (401) → немедленный failed без retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fatal_error_no_retry():
    """HTTP 401 (невосстановимая) → немедленно failed, retry не делается."""
    executor = GeminiExecutor()
    auth_error = _make_error_response(401, "Unauthorized")

    call_count = 0

    async def fake_post(url, **kwargs):
        nonlocal call_count
        call_count += 1
        return auth_error

    with (
        patch("worker.gemini_executor._get_gemini_api_key", new=AsyncMock(return_value="fake-key")),
        PATCH_BUILD_CONTEXT,
        PATCH_BUILD_PROMPT,
        PATCH_EXTRACT_IMAGES,
        PATCH_EXISTING_DIR,
        patch("asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_client_cls.return_value = mock_client

        result = await executor.execute(_make_req(prompt="тест 401"))

    assert result["status"] == "failed"
    assert "401" in result["error"]
    # 401 — немедленный выход, post должен быть вызван только один раз
    assert call_count == 1


# ---------------------------------------------------------------------------
# Тест 10: on_chunk вызывается при успешном ответе
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_chunk_called_on_success():
    """Callback on_chunk вызывается с текстом результата."""
    executor = GeminiExecutor()
    mock_response = _make_gemini_response("Ответ для стриминга")

    chunks = []

    async def collect_chunk(text: str):
        chunks.append(text)

    with (
        patch("worker.gemini_executor._get_gemini_api_key", new=AsyncMock(return_value="fake-key")),
        PATCH_BUILD_CONTEXT,
        PATCH_BUILD_PROMPT,
        PATCH_EXTRACT_IMAGES,
        PATCH_EXISTING_DIR,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        req = _make_req(prompt="тест чанков")
        req.on_chunk = collect_chunk
        result = await executor.execute(req)

    assert result["status"] == "completed"
    assert len(chunks) == 1
    assert "Ответ" in chunks[0]
