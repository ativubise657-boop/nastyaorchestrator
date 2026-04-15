"""Тесты для executor argv и _collect_additional_dirs (Issue 3.3A).

Покрывают:
  - CodexExecutor._build_command — argv для `codex exec` (sandbox, add-dir, image, model)
  - BaseExecutor._collect_additional_dirs — фильтры binary/failed-parse/workspace-inside
  - BaseExecutor._extract_image_paths — фильтрация по requested + IMAGE_EXTS

Без реального Codex CLI и без network: чистый unit на сборку argv и list<->list.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from worker.base_executor import BaseExecutor
from worker.executor import CodexExecutor


# ============================================================================
# _collect_additional_dirs
# ============================================================================

def test_collect_empty_inputs(tmp_path):
    out = BaseExecutor._collect_additional_dirs(
        workspace=str(tmp_path),
        documents=None,
        image_paths=[],
    )
    assert out == []


def test_collect_doc_inside_workspace_skipped(tmp_path):
    """Файл внутри workspace — не добавляется (Codex его и так видит)."""
    inside = tmp_path / "subdir"
    inside.mkdir()
    docs = [{"filename": "a.md", "path": str(inside / "a.md"), "parse_status": "parsed"}]
    out = BaseExecutor._collect_additional_dirs(
        workspace=str(tmp_path),
        documents=docs,
        image_paths=[],
    )
    assert out == []


def test_collect_doc_outside_workspace_added(tmp_path):
    """Файл вне workspace — родитель добавляется в add-dir."""
    external = tmp_path / "external"
    external.mkdir()
    docs = [{"filename": "a.md", "path": str(external / "a.md"), "parse_status": "parsed"}]
    workspace = tmp_path / "ws"
    workspace.mkdir()
    out = BaseExecutor._collect_additional_dirs(
        workspace=str(workspace),
        documents=docs,
        image_paths=[],
    )
    assert len(out) == 1
    assert str(external.resolve()) in out[0]


def test_collect_binary_ext_skipped_even_outside(tmp_path):
    """Fix 1.4A: бинарные форматы (.pdf/.docx/...) — skip, их всё равно не прочитать."""
    external = tmp_path / "external"
    external.mkdir()
    docs = [
        {"filename": "scan.pdf", "path": str(external / "scan.pdf"), "parse_status": "parsed"},
        {"filename": "spec.docx", "path": str(external / "spec.docx"), "parse_status": "parsed"},
    ]
    workspace = tmp_path / "ws"
    workspace.mkdir()
    out = BaseExecutor._collect_additional_dirs(
        workspace=str(workspace),
        documents=docs,
        image_paths=[],
    )
    assert out == []


def test_collect_failed_parse_skipped(tmp_path):
    """Fix 1.4A: parse_status=failed — не даём add-dir, чтобы модель не лезла в файл."""
    external = tmp_path / "external"
    external.mkdir()
    docs = [{"filename": "readme.md", "path": str(external / "readme.md"), "parse_status": "failed"}]
    workspace = tmp_path / "ws"
    workspace.mkdir()
    out = BaseExecutor._collect_additional_dirs(
        workspace=str(workspace),
        documents=docs,
        image_paths=[],
    )
    assert out == []


def test_collect_image_paths_outside_added(tmp_path):
    """Пути картинок — их родители добавляются если вне workspace."""
    external = tmp_path / "external"
    external.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    image = external / "pic.png"
    image.write_bytes(b"fake")
    out = BaseExecutor._collect_additional_dirs(
        workspace=str(workspace),
        documents=None,
        image_paths=[str(image)],
    )
    assert len(out) == 1
    assert str(external.resolve()) in out[0]


def test_collect_dedup_same_parent(tmp_path):
    """Два файла в одной папке → один add-dir."""
    external = tmp_path / "external"
    external.mkdir()
    docs = [
        {"filename": "a.md", "path": str(external / "a.md"), "parse_status": "parsed"},
        {"filename": "b.md", "path": str(external / "b.md"), "parse_status": "parsed"},
    ]
    workspace = tmp_path / "ws"
    workspace.mkdir()
    out = BaseExecutor._collect_additional_dirs(
        workspace=str(workspace),
        documents=docs,
        image_paths=[],
    )
    assert len(out) == 1


# ============================================================================
# _extract_image_paths
# ============================================================================

def test_extract_images_no_documents():
    assert BaseExecutor._extract_image_paths(None) == []
    assert BaseExecutor._extract_image_paths([]) == []


def test_extract_images_only_requested(tmp_path):
    """Только requested + существующие картинки попадают в список."""
    img1 = tmp_path / "a.png"
    img1.write_bytes(b"1")
    img2 = tmp_path / "b.jpg"
    img2.write_bytes(b"2")
    docs = [
        {"filename": "a.png", "path": str(img1), "requested": True},
        {"filename": "b.jpg", "path": str(img2)},  # не requested → skip
        {"filename": "c.pdf", "path": str(tmp_path / "c.pdf"), "requested": True},  # не image
    ]
    out = BaseExecutor._extract_image_paths(docs)
    assert len(out) == 1
    assert out[0].endswith("a.png")


def test_extract_images_nonexistent_skipped(tmp_path):
    """Несуществующий image файл — skip (не кидаем пустой путь в Codex)."""
    docs = [{"filename": "missing.png", "path": str(tmp_path / "missing.png"), "requested": True}]
    out = BaseExecutor._extract_image_paths(docs)
    assert out == []


# ============================================================================
# CodexExecutor._build_command (argv)
# ============================================================================

def _cmd(exe, **kwargs):
    defaults = {
        "model": "gpt-5.4",
        "workspace": "/tmp/ws",
        "image_paths": [],
        "add_dirs": [],
    }
    defaults.update(kwargs)
    return exe._build_command(**defaults)


def test_build_command_contains_base_flags():
    exe = CodexExecutor(codex_binary="codex-bin")
    cmd = _cmd(exe)
    assert cmd[0] == "codex-bin"
    assert "--ask-for-approval" in cmd
    i = cmd.index("--ask-for-approval")
    assert cmd[i + 1] == "never"
    assert "--sandbox" in cmd
    assert "--cd" in cmd
    assert "exec" in cmd
    assert "--json" in cmd
    assert "--skip-git-repo-check" in cmd
    assert cmd[-1] == "-"


def test_build_command_default_sandbox_danger_full_access():
    """Fix sandbox Дима → дефолт danger-full-access (fallback если None)."""
    exe = CodexExecutor()
    cmd = _cmd(exe, sandbox=None)
    i = cmd.index("--sandbox")
    assert cmd[i + 1] == "danger-full-access"


@pytest.mark.parametrize("sandbox", ["workspace-write", "read-only", "danger-full-access"])
def test_build_command_sandbox_passed_through(sandbox):
    exe = CodexExecutor()
    cmd = _cmd(exe, sandbox=sandbox)
    i = cmd.index("--sandbox")
    assert cmd[i + 1] == sandbox


def test_build_command_add_dirs(tmp_path):
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    exe = CodexExecutor()
    cmd = _cmd(exe, add_dirs=[str(d1), str(d2)])
    occurrences = [i for i, v in enumerate(cmd) if v == "--add-dir"]
    assert len(occurrences) == 2
    values = [cmd[i + 1] for i in occurrences]
    assert str(Path(d1)) in values
    assert str(Path(d2)) in values


def test_build_command_image_paths(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"p")
    exe = CodexExecutor()
    cmd = _cmd(exe, image_paths=[str(img)])
    assert "--image" in cmd
    i = cmd.index("--image")
    assert cmd[i + 1] == str(Path(img))


def test_build_command_reasoning_effort_for_known_model():
    """gpt-5.4 → reasoning effort 'high' (см. MODEL_REASONING_EFFORTS)."""
    exe = CodexExecutor()
    cmd = _cmd(exe, model="gpt-5.4")
    # Проверяем что есть -c флаг с model_reasoning_effort
    dash_c_indices = [i for i, v in enumerate(cmd) if v == "-c"]
    values = [cmd[i + 1] for i in dash_c_indices]
    assert any("model_reasoning_effort" in v and "high" in v for v in values)


def test_build_command_gpt5_codex_xhigh():
    """gpt-5.3-codex → xhigh."""
    exe = CodexExecutor()
    cmd = _cmd(exe, model="gpt-5.3-codex")
    dash_c_indices = [i for i, v in enumerate(cmd) if v == "-c"]
    values = [cmd[i + 1] for i in dash_c_indices]
    assert any("xhigh" in v for v in values)


def test_build_command_workspace_cd():
    exe = CodexExecutor()
    cmd = _cmd(exe, workspace="/custom/ws")
    i = cmd.index("--cd")
    assert cmd[i + 1] == str(Path("/custom/ws"))
