"""HTTP-contract тесты для backend.core.aitunnel_pdf (Issue 3.4A).

Моки на уровне httpx.MockTransport — проверяем что:
  - корректный URL/headers/body летит на AITunnel (Bearer, модель, base64 PDF)
  - success → markdown
  - 429 на 2.5-flash → fallback на 2.0-flash
  - empty choices → None
  - HTTP error → None
  - content как list-of-parts → склейка в строку
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from backend.core import aitunnel_pdf


# ============================================================================
# Хелперы
# ============================================================================

def _make_pdf(tmp_path: Path, content: bytes = b"%PDF-1.4 fake") -> Path:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(content)
    return pdf


def _mock_client(handler):
    """Подменяет httpx.Client так, что все запросы идут через MockTransport(handler)."""
    transport = httpx.MockTransport(handler)

    class _Wrapped(httpx.Client):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    return patch.object(aitunnel_pdf.httpx, "Client", _Wrapped)


# ============================================================================
# Success path
# ============================================================================

def test_success_returns_markdown(tmp_path):
    pdf = _make_pdf(tmp_path, b"%PDF-1.4 realbytes")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "# Hello\ntext from pdf"}}],
            },
        )

    with _mock_client(handler):
        out = aitunnel_pdf.parse_pdf(pdf, api_key="test-key", base_url="https://api.aitunnel.ru/v1")

    assert out == "# Hello\ntext from pdf"
    assert "chat/completions" in captured["url"]
    assert captured["auth"] == "Bearer test-key"
    body = captured["body"]
    assert body["model"] == "gemini-2.5-flash"
    messages = body["messages"]
    assert messages[0]["role"] == "user"
    parts = messages[0]["content"]
    assert any(p.get("type") == "text" for p in parts)
    img = next(p for p in parts if p.get("type") == "image_url")
    url = img["image_url"]["url"]
    # base64-encoded PDF встроен в data URL
    assert url.startswith("data:application/pdf;base64,")
    decoded = base64.b64decode(url.split(",", 1)[1])
    assert decoded == b"%PDF-1.4 realbytes"


def test_content_as_list_of_parts(tmp_path):
    """Некоторые провайдеры возвращают content как list-of-parts → склеиваем."""
    pdf = _make_pdf(tmp_path)

    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "content": [
                            {"type": "text", "text": "part1 "},
                            {"type": "text", "text": "part2"},
                        ],
                    },
                }],
            },
        )

    with _mock_client(handler):
        out = aitunnel_pdf.parse_pdf(pdf, api_key="k")

    assert out == "part1 part2"


# ============================================================================
# Fallback / retry
# ============================================================================

def test_429_on_25_falls_back_to_20(tmp_path):
    pdf = _make_pdf(tmp_path)
    calls: list[str] = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "gemini-2.5-flash":
            return httpx.Response(429, json={"error": "rate limit"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "from 2.0"}}]})

    with _mock_client(handler):
        out = aitunnel_pdf.parse_pdf(pdf, api_key="k")

    assert out == "from 2.0"
    assert calls == ["gemini-2.5-flash", "gemini-2.0-flash"]


def test_all_http_errors_return_none(tmp_path):
    pdf = _make_pdf(tmp_path)

    def handler(request):
        return httpx.Response(500, text="internal")

    with _mock_client(handler):
        out = aitunnel_pdf.parse_pdf(pdf, api_key="k")

    assert out is None


def test_empty_choices_returns_none(tmp_path):
    pdf = _make_pdf(tmp_path)

    def handler(request):
        return httpx.Response(200, json={"choices": []})

    with _mock_client(handler):
        out = aitunnel_pdf.parse_pdf(pdf, api_key="k")

    assert out is None


def test_empty_content_returns_none(tmp_path):
    pdf = _make_pdf(tmp_path)

    def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": ""}}]},
        )

    with _mock_client(handler):
        out = aitunnel_pdf.parse_pdf(pdf, api_key="k")

    assert out is None


# ============================================================================
# Edge cases
# ============================================================================

def test_no_api_key_returns_none_fast(tmp_path, monkeypatch):
    monkeypatch.delenv("AITUNNEL_API_KEY", raising=False)
    pdf = _make_pdf(tmp_path)
    # Без ключа функция возвращает None до HTTP-вызова
    out = aitunnel_pdf.parse_pdf(pdf)
    assert out is None


def test_too_large_pdf_returns_none_without_http(tmp_path):
    """PDF > 20 МБ — отказ сразу, без вызова AITunnel."""
    big_pdf = tmp_path / "big.pdf"
    big_pdf.write_bytes(b"\0" * (aitunnel_pdf.MAX_INLINE_BYTES + 1))
    called = []

    def handler(request):
        called.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    with _mock_client(handler):
        out = aitunnel_pdf.parse_pdf(big_pdf, api_key="k")

    assert out is None
    assert called == []  # HTTP не вызывался


def test_env_api_key_used_when_not_passed(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    monkeypatch.setenv("AITUNNEL_API_KEY", "from-env")
    captured: dict = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    with _mock_client(handler):
        out = aitunnel_pdf.parse_pdf(pdf)  # api_key не передан

    assert out == "ok"
    assert captured["auth"] == "Bearer from-env"


def test_parse_image_uses_image_mime(tmp_path):
    """parse_image отправляет картинку с корректным image/<ext> mime."""
    img = tmp_path / "screen.png"
    img.write_bytes(b"fake-png-bytes")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "На скриншоте кнопка Sign up"}}]},
        )

    with _mock_client(handler):
        out = aitunnel_pdf.parse_image(img, api_key="k")

    assert out == "На скриншоте кнопка Sign up"
    parts = captured["body"]["messages"][0]["content"]
    img_part = next(p for p in parts if p.get("type") == "image_url")
    assert img_part["image_url"]["url"].startswith("data:image/png;base64,")
    text_part = next(p for p in parts if p.get("type") == "text")
    # Prompt для image — просит описание картинки, не «преобразуй PDF»
    assert "изображ" in text_part["text"].lower() or "скриншот" in text_part["text"].lower()


def test_prompt_text_in_request(tmp_path):
    """В body должен быть PROMPT — инструкция 'преобразуй в markdown'."""
    pdf = _make_pdf(tmp_path)
    captured: dict = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    with _mock_client(handler):
        aitunnel_pdf.parse_pdf(pdf, api_key="k")

    text_parts = [p for p in captured["body"]["messages"][0]["content"] if p.get("type") == "text"]
    assert len(text_parts) == 1
    assert "Markdown" in text_parts[0]["text"] or "markdown" in text_parts[0]["text"]
