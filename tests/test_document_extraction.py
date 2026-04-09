"""Smoke-тесты для worker.document_extractor.extract_documents."""
import pytest

from worker.document_extractor import extract_documents


def test_plain_text_no_blocks():
    text = "Hello world, no documents here.\nJust text."
    cleaned, docs = extract_documents(text)
    assert cleaned == text
    assert docs == []


def test_single_document_block():
    text = (
        "Вот документ:\n"
        ":::document:readme.md\n"
        "Hello content\n"
        ":::\n"
        "Готово."
    )
    cleaned, docs = extract_documents(text)
    assert len(docs) == 1
    assert docs[0]["filename"] == "readme.md"
    assert docs[0]["content"] == "Hello content"
    assert docs[0]["folder"] is None
    assert ":::document:" not in cleaned
    assert "Вот документ:" in cleaned
    assert "Готово." in cleaned


def test_document_block_with_folder():
    text = (
        ":::document:spec.md:Architecture\n"
        "Spec body\n"
        ":::"
    )
    cleaned, docs = extract_documents(text)
    assert len(docs) == 1
    assert docs[0]["filename"] == "spec.md"
    assert docs[0]["folder"] == "Architecture"
    assert docs[0]["content"] == "Spec body"


def test_multiple_document_blocks():
    text = (
        "Intro\n"
        ":::document:a.md\n"
        "Content A\n"
        ":::\n"
        "Middle\n"
        ":::document:b.md:Folder B\n"
        "Content B\n"
        ":::\n"
        "Outro"
    )
    cleaned, docs = extract_documents(text)
    assert len(docs) == 2
    assert docs[0]["filename"] == "a.md"
    assert docs[0]["content"] == "Content A"
    assert docs[0]["folder"] is None
    assert docs[1]["filename"] == "b.md"
    assert docs[1]["content"] == "Content B"
    assert docs[1]["folder"] == "Folder B"
    assert "Intro" in cleaned
    assert "Middle" in cleaned
    assert "Outro" in cleaned
    assert ":::document:" not in cleaned


def test_malformed_block_no_closing_fence_is_ignored():
    """Блок без закрывающего ::: — регексп не матчит, текст остаётся как есть."""
    text = (
        "Before\n"
        ":::document:broken.md\n"
        "Content without closing fence\n"
        "And more text"
    )
    cleaned, docs = extract_documents(text)
    # Текущая реализация: нет совпадения → возвращается исходный текст, пустой список
    assert docs == []
    assert cleaned == text


@pytest.mark.xfail(
    reason="Известное ограничение: non-greedy regex останавливается на первом ::: "
    "внутри code fence, путая границы блока документа."
)
def test_triple_colon_inside_code_fence_not_confused():
    """Содержимое документа содержит ::: внутри примера кода."""
    text = (
        ":::document:guide.md\n"
        "Example:\n"
        "```\n"
        ":::\n"
        "```\n"
        "End of guide\n"
        ":::"
    )
    cleaned, docs = extract_documents(text)
    assert len(docs) == 1
    assert "End of guide" in docs[0]["content"]
