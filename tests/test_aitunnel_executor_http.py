"""HTTP-contract тест для AITunnelExecutor._call_api (Issue 3.4A).

Фиксируем что body содержит правильные ключи (model, messages, tools, temperature),
что url собирается из base_url + /chat/completions, headers содержат Bearer.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import httpx

from worker.aitunnel_executor import AITunnelExecutor


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _async_mock_client(handler):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def test_call_api_body_and_headers():
    """_call_api формирует корректный POST с model/messages/tools и Bearer."""
    exe = AITunnelExecutor(api_key="test-key", base_url="https://api.aitunnel.ru/v1")
    captured: dict = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def run():
        async with _async_mock_client(handler) as client:
            return await exe._call_api(
                client=client,
                model="gpt-5.4",
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "read_file"}}],
            )

    result = _run(run())

    assert result["choices"][0]["message"]["content"] == "ok"
    assert captured["url"] == "https://api.aitunnel.ru/v1/chat/completions"
    assert captured["auth"] == "Bearer test-key"
    body = captured["body"]
    assert body["model"] == "gpt-5.4"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["tool_choice"] == "auto"
    assert body["tools"][0]["function"]["name"] == "read_file"
    assert "temperature" in body


def test_call_api_raises_on_http_error():
    exe = AITunnelExecutor(api_key="k")

    def handler(request):
        return httpx.Response(500, text="boom")

    async def run():
        async with _async_mock_client(handler) as client:
            await exe._call_api(client=client, model="x", messages=[], tools=[])

    import pytest
    with pytest.raises(httpx.HTTPStatusError):
        _run(run())
