"""Unit-тесты для каскада парсинга документов (Issue 3.1A).

Покрывают:
  - _try_markitdown / _try_pdfminer / _try_aitunnel_pdf (каждый уровень каскада)
  - _convert_to_text (последовательность фолбэков markitdown → pdfminer → AITunnel)
  - _parse_and_status (states: parsed / failed / skipped)
  - _get_text_content (чтение .md кеша и текстовых файлов)

Моки вместо реальных PDF/API — тесты быстрые, детерминированные,
проверяют именно логику каскада, а не поведение markitdown/pdfminer/Gemini.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.api import documents as docs_mod


@pytest.fixture(autouse=True)
def _disable_parse_cache(monkeypatch):
    """Fix 4.2A добавил SHA256-кеш. Для тестов каскада — отключаем, чтобы
    межтестовые hit'ы не маскировали падения парсеров."""
    from backend.core import parse_cache
    monkeypatch.setattr(parse_cache, "get", lambda *a, **kw: None)
    monkeypatch.setattr(parse_cache, "put", lambda *a, **kw: None)


# ============================================================================
# _get_text_content — чтение .md кеша и текстовых файлов
# ============================================================================

def test_get_text_content_reads_md_cache(tmp_path):
    """Если рядом с файлом лежит .md — возвращается его содержимое."""
    pdf = tmp_path / "notes.pdf"
    pdf.write_bytes(b"%PDF fake")
    md = tmp_path / "notes.md"
    md.write_text("# Markdown from cache", encoding="utf-8")

    out = docs_mod._get_text_content(str(pdf), "notes.pdf")
    assert out == "# Markdown from cache"


def test_get_text_content_reads_plain_text(tmp_path):
    """Текстовые файлы (.txt/.md/.csv) — читаются напрямую."""
    txt = tmp_path / "hello.txt"
    txt.write_text("plain text content", encoding="utf-8")
    out = docs_mod._get_text_content(str(txt), "hello.txt")
    assert out == "plain text content"


def test_get_text_content_skips_binary_without_md(tmp_path):
    """Бинарник без .md рядом — None."""
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"\x00\x01\x02binary")
    out = docs_mod._get_text_content(str(pdf), "scan.pdf")
    assert out is None


def test_get_text_content_skips_large_text(tmp_path):
    """Текстовый файл >500KB — skip (защита от раздувания промпта)."""
    big = tmp_path / "huge.txt"
    big.write_bytes(b"x" * 600_000)
    out = docs_mod._get_text_content(str(big), "huge.txt")
    assert out is None


# ============================================================================
# Каскад: _try_markitdown / _try_pdfminer / _try_aitunnel_pdf
# markitdown/pdfminer в requirements.txt, но в WSL-dev могут не стоять —
# используем skipif. На Windows CI тесты идут.
# ============================================================================

try:
    import markitdown  # noqa: F401
    _HAS_MARKITDOWN = True
except ImportError:
    _HAS_MARKITDOWN = False
try:
    import pdfminer  # noqa: F401
    _HAS_PDFMINER = True
except ImportError:
    _HAS_PDFMINER = False


@pytest.mark.skipif(not _HAS_MARKITDOWN, reason="markitdown не установлен в dev-окружении")
def test_try_markitdown_returns_text(tmp_path):
    """markitdown вернул непустой текст — возвращаем его."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF fake")

    class FakeResult:
        text_content = "extracted from markitdown"

    class FakeMD:
        def convert(self, path):
            return FakeResult()

    with patch("markitdown.MarkItDown", FakeMD):
        out = docs_mod._try_markitdown(pdf, "a.pdf")

    assert out == "extracted from markitdown"


@pytest.mark.skipif(not _HAS_MARKITDOWN, reason="markitdown не установлен в dev-окружении")
def test_try_markitdown_empty_result_returns_none(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")

    class FakeResult:
        text_content = "   "

    class FakeMD:
        def convert(self, path):
            return FakeResult()

    with patch("markitdown.MarkItDown", FakeMD):
        out = docs_mod._try_markitdown(pdf, "a.pdf")

    assert out is None


@pytest.mark.skipif(not _HAS_MARKITDOWN, reason="markitdown не установлен в dev-окружении")
def test_try_markitdown_exception_returns_none(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")

    class FakeMD:
        def convert(self, path):
            raise RuntimeError("markitdown boom")

    with patch("markitdown.MarkItDown", FakeMD):
        out = docs_mod._try_markitdown(pdf, "a.pdf")

    assert out is None


@pytest.mark.skipif(not _HAS_PDFMINER, reason="pdfminer не установлен в dev-окружении")
def test_try_pdfminer_success(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")

    with patch("pdfminer.high_level.extract_text", return_value="pdfminer text"):
        out = docs_mod._try_pdfminer(pdf, "a.pdf")

    assert out == "pdfminer text"


@pytest.mark.skipif(not _HAS_PDFMINER, reason="pdfminer не установлен в dev-окружении")
def test_try_pdfminer_empty_returns_none(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")

    with patch("pdfminer.high_level.extract_text", return_value="  \n  "):
        out = docs_mod._try_pdfminer(pdf, "a.pdf")

    assert out is None


@pytest.mark.skipif(not _HAS_PDFMINER, reason="pdfminer не установлен в dev-окружении")
def test_try_pdfminer_exception_returns_none(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")

    with patch("pdfminer.high_level.extract_text", side_effect=RuntimeError("boom")):
        out = docs_mod._try_pdfminer(pdf, "a.pdf")

    assert out is None


def test_try_aitunnel_success(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")

    with patch("backend.core.aitunnel_pdf.parse_pdf", return_value="ai markdown"):
        out = docs_mod._try_aitunnel_pdf(pdf, "a.pdf")

    assert out == "ai markdown"


def test_try_aitunnel_none_passes_through(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")

    with patch("backend.core.aitunnel_pdf.parse_pdf", return_value=None):
        out = docs_mod._try_aitunnel_pdf(pdf, "a.pdf")

    assert out is None


# ============================================================================
# _convert_to_text — оркестрация каскада
# ============================================================================

def test_convert_markitdown_wins_if_returns_text(tmp_path):
    """Если markitdown вернул текст — остальные не вызываются, .md создаётся."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    pdfminer_called = []
    aitunnel_called = []

    with patch.object(docs_mod, "_try_markitdown", return_value="from md"):
        with patch.object(docs_mod, "_try_pdfminer", side_effect=lambda *a: pdfminer_called.append(1)):
            with patch.object(docs_mod, "_try_aitunnel_pdf", side_effect=lambda *a: aitunnel_called.append(1)):
                out = docs_mod._convert_to_text(pdf, "a.pdf")

    assert out is not None
    assert out.read_text(encoding="utf-8") == "from md"
    assert pdfminer_called == [] and aitunnel_called == []


def test_convert_fallback_to_pdfminer_when_md_fails(tmp_path):
    """markitdown вернул None → pdfminer → .md сохранён из pdfminer."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    aitunnel_called = []

    with patch.object(docs_mod, "_try_markitdown", return_value=None):
        with patch.object(docs_mod, "_try_pdfminer", return_value="pdfminer text"):
            with patch.object(docs_mod, "_try_aitunnel_pdf", side_effect=lambda *a: aitunnel_called.append(1)):
                out = docs_mod._convert_to_text(pdf, "a.pdf")

    assert out is not None
    assert out.read_text(encoding="utf-8") == "pdfminer text"
    assert aitunnel_called == []


def test_convert_fallback_to_aitunnel_when_both_fail(tmp_path):
    """markitdown и pdfminer упали → AITunnel."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")

    with patch.object(docs_mod, "_try_markitdown", return_value=None):
        with patch.object(docs_mod, "_try_pdfminer", return_value=None):
            with patch.object(docs_mod, "_try_aitunnel_pdf", return_value="ai ocr text"):
                out = docs_mod._convert_to_text(pdf, "a.pdf")

    assert out is not None
    assert out.read_text(encoding="utf-8") == "ai ocr text"


def test_convert_all_fail_returns_none(tmp_path):
    """Все 3 уровня упали — None, .md не создаётся."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")

    with patch.object(docs_mod, "_try_markitdown", return_value=None):
        with patch.object(docs_mod, "_try_pdfminer", return_value=None):
            with patch.object(docs_mod, "_try_aitunnel_pdf", return_value=None):
                out = docs_mod._convert_to_text(pdf, "a.pdf")

    assert out is None
    assert not (tmp_path / "a.md").exists()


def test_convert_docx_no_pdfminer_fallback(tmp_path):
    """DOCX: markitdown не сработал — pdfminer/AITunnel НЕ вызываются (они только для PDF)."""
    docx = tmp_path / "doc.docx"
    docx.write_bytes(b"x")
    pdfminer_called = []
    aitunnel_called = []

    with patch.object(docs_mod, "_try_markitdown", return_value=None):
        with patch.object(docs_mod, "_try_pdfminer", side_effect=lambda *a: pdfminer_called.append(1)):
            with patch.object(docs_mod, "_try_aitunnel_pdf", side_effect=lambda *a: aitunnel_called.append(1)):
                out = docs_mod._convert_to_text(docx, "doc.docx")

    assert out is None
    assert pdfminer_called == [] and aitunnel_called == []


def test_convert_unsupported_ext_returns_none(tmp_path):
    """Не конвертируемый формат (.zip) — возвращает None без попыток."""
    z = tmp_path / "x.zip"
    z.write_bytes(b"PK")
    out = docs_mod._convert_to_text(z, "x.zip")
    assert out is None


# ============================================================================
# _parse_and_status — 3 состояния
# ============================================================================

def test_parse_status_parsed(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    with patch.object(docs_mod, "_try_markitdown", return_value="ok"):
        md_path, status, error = docs_mod._parse_and_status(pdf, "a.pdf")

    assert status == "parsed"
    assert error == ""
    assert md_path is not None and md_path.exists()


def test_parse_status_failed(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    with patch.object(docs_mod, "_try_markitdown", return_value=None):
        with patch.object(docs_mod, "_try_pdfminer", return_value=None):
            with patch.object(docs_mod, "_try_aitunnel_pdf", return_value=None):
                md_path, status, error = docs_mod._parse_and_status(pdf, "a.pdf")

    assert status == "failed"
    assert "markitdown" in error.lower() or "парсер" in error.lower()
    assert md_path is None


def test_parse_status_skipped(tmp_path):
    """Формат не поддерживается (.zip, .png) → skipped, без попытки."""
    z = tmp_path / "x.zip"
    z.write_bytes(b"PK")
    md_path, status, error = docs_mod._parse_and_status(z, "x.zip")
    assert status == "skipped"
    assert error == ""
    assert md_path is None


def test_parse_status_failed_on_unexpected_exception(tmp_path):
    """Внутри _convert_to_text упало необработанное исключение — failed + error фиксится."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    with patch.object(docs_mod, "_convert_to_text", side_effect=RuntimeError("surprise")):
        md_path, status, error = docs_mod._parse_and_status(pdf, "a.pdf")

    assert status == "failed"
    assert "RuntimeError" in error
    assert "surprise" in error
    assert md_path is None
