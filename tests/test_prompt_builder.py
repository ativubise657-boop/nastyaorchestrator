"""Unit-тесты для секций сборки промпта (worker.executor.CodexExecutor._section_*).

Покрывают core-логику сборки контекста. Вводится в рамках Issue 2.4A (декомпозиция
монолитного _build_context_prompt) — чтобы любое изменение промпта было защищено
тестом, а не разъезжалось в рантайме.
"""
from __future__ import annotations

import pytest

from worker.executor import CodexExecutor


# ------------------------------------------------------------------
# _section_project
# ------------------------------------------------------------------

def test_section_project_none_or_empty():
    assert CodexExecutor._section_project(None) is None
    assert CodexExecutor._section_project({}) is None


def test_section_project_name_only():
    out = CodexExecutor._section_project({"name": "Мой Проект"})
    assert out == "[Проект: Мой Проект]"


def test_section_project_name_and_desc():
    out = CodexExecutor._section_project({"name": "X", "description": "Описание"})
    assert "[Проект: X]" in out
    assert "Описание проекта: Описание" in out


def test_section_project_desc_only():
    out = CodexExecutor._section_project({"description": "Только описание"})
    assert out == "Описание проекта: Только описание"


# ------------------------------------------------------------------
# _section_crm
# ------------------------------------------------------------------

def test_section_crm_none():
    assert CodexExecutor._section_crm(None) is None
    assert CodexExecutor._section_crm("") is None


def test_section_crm_passthrough():
    out = CodexExecutor._section_crm("CRM block text")
    assert out == "CRM block text"


# ------------------------------------------------------------------
# _section_documents — 4 ветки
# ------------------------------------------------------------------

def test_section_documents_none_or_empty():
    assert CodexExecutor._section_documents(None) is None
    assert CodexExecutor._section_documents([]) is None


def test_section_documents_with_content():
    docs = [{"num": 1, "filename": "readme.md", "size": 100, "content": "Hello"}]
    out = CodexExecutor._section_documents(docs)
    assert "#1 readme.md:" in out
    assert "Hello" in out
    assert "--- Документы проекта" in out
    assert "--- Конец документов ---" in out


def test_section_documents_image_requested():
    docs = [{"num": 1, "filename": "img.png", "size": 500, "requested": True}]
    out = CodexExecutor._section_documents(docs)
    assert "img.png" in out
    assert "изображение прикреплено к первому сообщению" in out


def test_section_documents_image_not_requested_is_plain_listing():
    """Картинка без requested → обычный listing (без спец-метки)."""
    docs = [{"num": 1, "filename": "img.png", "size": 500}]
    out = CodexExecutor._section_documents(docs)
    assert "#1 img.png (500 байт)" in out
    assert "прикреплено" not in out


def test_section_documents_requested_no_content_honest_message():
    """Fix 1.1A: модели чётко сказано НЕ пытаться читать с диска."""
    docs = [{"num": 3, "filename": "scan.pdf", "size": 9000, "requested": True}]
    out = CodexExecutor._section_documents(docs)
    assert "#3 scan.pdf" in out
    assert "Не пытайся читать файл с диска" in out
    assert "автоматически распарсить содержимое не удалось" in out


def test_section_documents_plain_listing_no_request_no_content():
    docs = [{"num": 1, "filename": "notes.pdf", "size": 200}]
    out = CodexExecutor._section_documents(docs)
    assert "#1 notes.pdf (200 байт)" in out
    assert "Не пытайся" not in out
    assert "```" not in out


def test_section_documents_failed_parse_gets_warning_marker():
    """Fix 2.1C: документ с parse_status=failed, но не запрошенный — показывается
    в listing с маркером ⚠ и подсказкой модели что если спросят — ответить честно."""
    docs = [{"num": 2, "filename": "scan.pdf", "size": 500, "parse_status": "failed"}]
    out = CodexExecutor._section_documents(docs)
    assert "#2 scan.pdf (500 байт)" in out
    assert "⚠" in out
    assert "содержимое не извлечено" in out


def test_section_documents_parsed_status_no_warning():
    """parse_status=parsed без requested — обычный listing, без ⚠."""
    docs = [{"num": 1, "filename": "ok.pdf", "size": 100, "parse_status": "parsed"}]
    out = CodexExecutor._section_documents(docs)
    assert "#1 ok.pdf (100 байт)" in out
    assert "⚠" not in out


def test_section_documents_multiple_mixed():
    docs = [
        {"num": 1, "filename": "a.md", "size": 50, "content": "aaa"},
        {"num": 2, "filename": "b.pdf", "size": 200, "requested": True},
        {"num": 3, "filename": "c.png", "size": 300, "requested": True},
        {"num": 4, "filename": "d.txt", "size": 400},
    ]
    out = CodexExecutor._section_documents(docs)
    # #1 content block
    assert "#1 a.md:" in out and "aaa" in out
    # #2 honest failure
    assert "#2 b.pdf" in out and "Не пытайся" in out
    # #3 image marker
    assert "#3 c.png" in out and "прикреплено" in out
    # #4 plain listing
    assert "#4 d.txt (400 байт)" in out


# ------------------------------------------------------------------
# _section_doc_folders
# ------------------------------------------------------------------

def test_section_doc_folders_none_or_empty():
    assert CodexExecutor._section_doc_folders(None) is None
    assert CodexExecutor._section_doc_folders([]) is None


def test_section_doc_folders_multiple():
    out = CodexExecutor._section_doc_folders(["Проекты", "Отчёты", "Черновики"])
    assert "Проекты, Отчёты, Черновики" in out
    assert "Существующие папки документов" in out


# ------------------------------------------------------------------
# _section_github
# ------------------------------------------------------------------

def test_section_github_none_or_empty():
    assert CodexExecutor._section_github(None) is None
    assert CodexExecutor._section_github("") is None


def test_section_github_with_content():
    out = CodexExecutor._section_github("GH block content")
    assert "GH block content" in out
    assert "--- Контекст проекта из GitHub ---" in out
    assert "--- Конец контекста ---" in out


# ------------------------------------------------------------------
# _section_completed_tasks
# ------------------------------------------------------------------

def test_section_completed_tasks_none_or_empty():
    assert CodexExecutor._section_completed_tasks(None) is None
    assert CodexExecutor._section_completed_tasks([]) is None


def test_section_completed_tasks_basic():
    tasks = [
        {"prompt": "Что такое X?", "result": "X это Y"},
        {"prompt": "А Z?", "result": "Z это W"},
    ]
    out = CodexExecutor._section_completed_tasks(tasks)
    assert "Вопрос: Что такое X?" in out
    assert "Ответ: X это Y" in out
    assert "Вопрос: А Z?" in out
    assert "Ответ: Z это W" in out
    assert "--- Контекст предыдущих задач ---" in out


def test_section_completed_tasks_truncates_long_result():
    long_text = "x" * 2000
    tasks = [{"prompt": "q", "result": long_text}]
    out = CodexExecutor._section_completed_tasks(tasks)
    assert "[обрезано]" in out
    assert len(out) < 2000 + 500  # заметно короче исходных 2000


# ------------------------------------------------------------------
# _section_chat_history
# ------------------------------------------------------------------

def test_section_chat_history_none_or_empty():
    assert CodexExecutor._section_chat_history(None) is None
    assert CodexExecutor._section_chat_history([]) is None


def test_section_chat_history_single_message_is_dropped():
    """chat_history[:-1] — последнее сообщение уже в `prompt`, секция его выкидывает."""
    msgs = [{"role": "user", "content": "Единственное сообщение"}]
    assert CodexExecutor._section_chat_history(msgs) is None


def test_section_chat_history_full():
    msgs = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуй, Настя"},
        {"role": "user", "content": "этот дропается"},
    ]
    out = CodexExecutor._section_chat_history(msgs)
    assert "Настя: Привет" in out
    assert "Ассистент: Здравствуй, Настя" in out
    assert "этот дропается" not in out


# ------------------------------------------------------------------
# _section_agents_md
# ------------------------------------------------------------------

def test_section_agents_md_none_workspace():
    assert CodexExecutor._section_agents_md(None) is None
    assert CodexExecutor._section_agents_md("") is None


def test_section_agents_md_with_file(tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# MyAgent\nInstructions", encoding="utf-8")
    out = CodexExecutor._section_agents_md(str(tmp_path))
    assert "# MyAgent" in out
    assert "--- Инструкции ассистента (AGENTS.md) ---" in out
    assert "--- Конец инструкций ---" in out


def test_section_agents_md_missing_file(tmp_path):
    # tmp_path без AGENTS.md
    assert CodexExecutor._section_agents_md(str(tmp_path)) is None
