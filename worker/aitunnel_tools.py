"""AI Tunnel tools for project-aware function calling inside the worker."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_FILE_LIMIT = 120_000
_DEFAULT_LIST_LIMIT = 500
_DEFAULT_SEARCH_LIMIT = 300


AVAILABLE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file from the current project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file inside the project.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write UTF-8 text to a file inside the project. Creates parent folders if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file inside the project.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full file content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and folders in a project directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path. Use '.' for the project root.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "When true, list recursively.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search project files by glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern like '*.py', 'src/**/*.ts', or '*test*'.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative search root inside the project.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Run a shell command inside the project and return stdout, stderr, and exit code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Relative working directory inside the project.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds. Default: 60.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_info",
            "description": "Get basic information about the current project and git status when available.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


def get_tool_definitions() -> list[dict[str, Any]]:
    return AVAILABLE_TOOLS


class AITunnelToolRunner:
    """Executes AI Tunnel function calls inside the current project."""

    def __init__(self, work_dir: str):
        self.work_dir = Path(work_dir).resolve()
        self._current_process: asyncio.subprocess.Process | None = None

    def cancel(self) -> None:
        proc = self._current_process
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        self._current_process = None

    def _resolve_path(
        self,
        raw_path: str | None,
        *,
        must_exist: bool = False,
        expect_dir: bool | None = None,
    ) -> Path:
        candidate = Path(raw_path or ".")
        resolved = (candidate if candidate.is_absolute() else self.work_dir / candidate).resolve()
        try:
            resolved.relative_to(self.work_dir)
        except ValueError as exc:
            raise ValueError(f"Path escapes project directory: {raw_path}") from exc

        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"Path not found: {raw_path}")
        if expect_dir is True and not resolved.is_dir():
            raise ValueError(f"Not a directory: {raw_path}")
        if expect_dir is False and not resolved.is_file():
            raise ValueError(f"Not a file: {raw_path}")
        return resolved

    async def run(self, tool_name: str, arguments: dict[str, Any]) -> str:
        handler = getattr(self, f"_handle_{tool_name}", None)
        if handler is None:
            return json.dumps({"ok": False, "error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)

        try:
            result = await handler(arguments)
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        except Exception as exc:
            logger.warning("AI Tunnel tool %s failed: %s", tool_name, exc)
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    async def _handle_read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("path is required")

        full_path = self._resolve_path(path, must_exist=True, expect_dir=False)
        content = full_path.read_text(encoding="utf-8", errors="replace")
        truncated = False
        if len(content) > _DEFAULT_FILE_LIMIT:
            content = content[:_DEFAULT_FILE_LIMIT]
            truncated = True

        return {
            "path": str(full_path.relative_to(self.work_dir)),
            "truncated": truncated,
            "content": content,
        }

    async def _handle_write_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("path is required")

        content = args.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")

        full_path = self._resolve_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

        return {
            "path": str(full_path.relative_to(self.work_dir)),
            "bytes_written": len(content.encode("utf-8")),
        }

    async def _handle_list_directory(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", ".")).strip() or "."
        recursive = bool(args.get("recursive", False))
        full_path = self._resolve_path(path, must_exist=True, expect_dir=True)

        items: list[dict[str, Any]] = []
        iterator = full_path.rglob("*") if recursive else full_path.iterdir()
        for item in iterator:
            rel_path = str(item.relative_to(self.work_dir))
            items.append(
                {
                    "path": rel_path,
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                }
            )
            if len(items) >= _DEFAULT_LIST_LIMIT:
                break

        return {
            "path": str(full_path.relative_to(self.work_dir)),
            "recursive": recursive,
            "truncated": len(items) >= _DEFAULT_LIST_LIMIT,
            "items": items,
        }

    async def _handle_search_files(self, args: dict[str, Any]) -> dict[str, Any]:
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("pattern is required")

        path = str(args.get("path", ".")).strip() or "."
        root = self._resolve_path(path, must_exist=True, expect_dir=True)

        matches: list[str] = []
        for item in root.rglob("*"):
            rel_path = str(item.relative_to(self.work_dir))
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(item.name, pattern):
                matches.append(rel_path)
                if len(matches) >= _DEFAULT_SEARCH_LIMIT:
                    break

        return {
            "pattern": pattern,
            "path": str(root.relative_to(self.work_dir)),
            "truncated": len(matches) >= _DEFAULT_SEARCH_LIMIT,
            "matches": matches,
        }

    async def _handle_execute_command(self, args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command", "")).strip()
        if not command:
            raise ValueError("command is required")

        cwd_arg = str(args.get("cwd", ".")).strip() or "."
        timeout = int(args.get("timeout", 60))
        cwd = self._resolve_path(cwd_arg, must_exist=True, expect_dir=True)

        env = dict(os.environ)
        env.pop("CLAUDECODE", None)

        self._current_process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(self._current_process.communicate(), timeout=timeout)
            return {
                "cwd": str(cwd.relative_to(self.work_dir)),
                "return_code": self._current_process.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
            }
        except asyncio.TimeoutError:
            self.cancel()
            return {
                "cwd": str(cwd.relative_to(self.work_dir)),
                "return_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds",
            }
        finally:
            self._current_process = None

    async def _handle_get_project_info(self, args: dict[str, Any]) -> dict[str, Any]:
        del args
        info: dict[str, Any] = {
            "work_dir": str(self.work_dir),
            "exists": self.work_dir.exists(),
        }

        git_dir = self.work_dir / ".git"
        info["is_git_repo"] = git_dir.exists()

        if info["is_git_repo"]:
            info["git_branch"] = self._run_git(["branch", "--show-current"])
            info["git_status"] = self._run_git(["status", "--short"])

        return info

    def _run_git(self, args: list[str]) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(self.work_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
            )
        except Exception as exc:
            return f"git error: {exc}"

        text = (result.stdout or result.stderr).strip()
        return text[:4000]
