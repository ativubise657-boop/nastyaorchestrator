"""GitHub API клиент — read-only доступ к репозиториям.

Используется для подтягивания контекста проектов без клонирования.
Настя может анализировать код, но не менять его.
"""
import base64
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# PAT из настроек (fine-grained, read-only)
_PAT = "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"
_API = "https://api.github.com"
_TIMEOUT = 15


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"token {_PAT}",
        "Accept": "application/vnd.github.v3+json",
    }


def _parse_git_url(git_url: str) -> tuple[str, str] | None:
    """Извлекает owner/repo из git_url.

    Примеры:
        https://github.com/Gypsea67/geniled.ru.git → (Gypsea67, geniled.ru)
        https://github.com/Gypsea67/sparta.git → (Gypsea67, sparta)
    """
    url = git_url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    parts = url.split("/")
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return None


async def get_repo_tree(git_url: str, max_entries: int = 200) -> str | None:
    """Получает дерево файлов репозитория (рекурсивно).

    Возвращает отформатированное дерево или None при ошибке.
    """
    parsed = _parse_git_url(git_url)
    if not parsed:
        return None

    owner, repo = parsed
    url = f"{_API}/repos/{owner}/{repo}/git/trees/master?recursive=1"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=_headers())

            if resp.status_code != 200:
                # Попробуем main вместо master
                url_main = f"{_API}/repos/{owner}/{repo}/git/trees/main?recursive=1"
                resp = await client.get(url_main, headers=_headers())
                if resp.status_code != 200:
                    logger.warning("GitHub tree: %d для %s/%s", resp.status_code, owner, repo)
                    return None

        data = resp.json()
        tree = data.get("tree", [])

        # Фильтруем: только файлы (blob), пропускаем бинарные и тяжёлые
        skip_dirs = {"node_modules", ".git", "vendor", "__pycache__", ".next", "dist", "build", ".venv"}
        lines = []
        for entry in tree[:max_entries]:
            path = entry.get("path", "")
            # Пропускаем файлы в игнорируемых директориях
            if any(part in skip_dirs for part in path.split("/")):
                continue
            if entry.get("type") == "blob":
                size = entry.get("size", 0)
                size_str = f" ({_human_size(size)})" if size > 0 else ""
                lines.append(f"  {path}{size_str}")

        if not lines:
            return None

        return f"Структура репозитория {owner}/{repo} ({len(lines)} файлов):\n" + "\n".join(lines)

    except Exception as e:
        logger.error("GitHub tree error: %s", e)
        return None


async def get_file_content(git_url: str, file_path: str) -> str | None:
    """Читает содержимое одного файла из репозитория.

    Возвращает текст файла или None при ошибке.
    """
    parsed = _parse_git_url(git_url)
    if not parsed:
        return None

    owner, repo = parsed
    url = f"{_API}/repos/{owner}/{repo}/contents/{file_path}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=_headers())

        if resp.status_code != 200:
            logger.warning("GitHub file %s: %d", file_path, resp.status_code)
            return None

        data = resp.json()
        encoding = data.get("encoding", "")
        content = data.get("content", "")

        if encoding == "base64" and content:
            try:
                return base64.b64decode(content).decode("utf-8", errors="replace")
            except Exception:
                return None

        return None

    except Exception as e:
        logger.error("GitHub file error: %s", e)
        return None


async def get_readme(git_url: str) -> str | None:
    """Получает README репозитория."""
    parsed = _parse_git_url(git_url)
    if not parsed:
        return None

    owner, repo = parsed
    url = f"{_API}/repos/{owner}/{repo}/readme"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=_headers())

        if resp.status_code != 200:
            return None

        data = resp.json()
        content = data.get("content", "")
        if content:
            try:
                return base64.b64decode(content).decode("utf-8", errors="replace")
            except Exception:
                return None

        return None

    except Exception as e:
        logger.error("GitHub README error: %s", e)
        return None


async def get_recent_commits(git_url: str, count: int = 5) -> str | None:
    """Получает последние коммиты."""
    parsed = _parse_git_url(git_url)
    if not parsed:
        return None

    owner, repo = parsed
    url = f"{_API}/repos/{owner}/{repo}/commits?per_page={count}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=_headers())

        if resp.status_code != 200:
            return None

        commits = resp.json()
        lines = []
        for c in commits:
            sha = c.get("sha", "")[:7]
            msg = c.get("commit", {}).get("message", "").split("\n")[0]
            date = c.get("commit", {}).get("committer", {}).get("date", "")[:10]
            author = c.get("commit", {}).get("author", {}).get("name", "")
            lines.append(f"  {sha} {date} [{author}] {msg}")

        return f"Последние коммиты ({len(lines)}):\n" + "\n".join(lines) if lines else None

    except Exception as e:
        logger.error("GitHub commits error: %s", e)
        return None


async def _get_repo_instructions(git_url: str) -> tuple[str | None, str | None]:
    """Получает инструкции проекта: AGENTS.md в приоритете, fallback на CLAUDE.md."""
    import asyncio

    agents_md_task = asyncio.create_task(get_file_content(git_url, "AGENTS.md"))
    claude_md_task = asyncio.create_task(get_file_content(git_url, "CLAUDE.md"))
    agents_md, claude_md = await asyncio.gather(agents_md_task, claude_md_task)

    if agents_md:
        return "AGENTS.md", agents_md
    if claude_md:
        return "CLAUDE.md", claude_md
    return None, None


async def build_project_context(git_url: str) -> str:
    """Собирает контекст проекта для промпта Codex.

    Подтягивает параллельно: структуру файлов, AGENTS.md/CLAUDE.md, README, коммиты.
    """
    import asyncio

    # Запускаем все запросы параллельно
    tree_task = asyncio.create_task(get_repo_tree(git_url))
    instructions_task = asyncio.create_task(_get_repo_instructions(git_url))
    readme_task = asyncio.create_task(get_readme(git_url))
    commits_task = asyncio.create_task(get_recent_commits(git_url))

    tree, instructions, readme, commits = await asyncio.gather(
        tree_task, instructions_task, readme_task, commits_task
    )
    instructions_name, instructions_text = instructions

    parts = []

    if tree:
        parts.append(tree)

    # AGENTS.md/CLAUDE.md (приоритет) или README
    if instructions_text and instructions_name:
        if len(instructions_text) > 3000:
            instructions_text = instructions_text[:3000] + "\n... (обрезано)"
        parts.append(f"{instructions_name} проекта:\n{instructions_text}")
    elif readme:
        if len(readme) > 2000:
            readme = readme[:2000] + "\n... (обрезано)"
        parts.append(f"README:\n{readme}")

    if commits:
        parts.append(commits)

    return "\n\n".join(parts)


async def build_all_projects_context(projects: list[dict]) -> str:
    """Собирает краткий контекст ВСЕХ проектов параллельно.

    Для каждого проекта подтягивает: структуру + AGENTS.md/CLAUDE.md (обрезанный).
    """
    import asyncio

    async def _one_project(p: dict) -> str:
        name = p.get("name", "?")
        desc = p.get("description", "")
        git_url = p.get("git_url", "")
        parts = [f"### {name}", f"_{desc}_"]

        if git_url:
            # Параллельно: дерево + AGENTS.md/CLAUDE.md
            tree_task = asyncio.create_task(get_repo_tree(git_url, max_entries=80))
            instructions_task = asyncio.create_task(_get_repo_instructions(git_url))
            tree, instructions = await asyncio.gather(tree_task, instructions_task)
            instructions_name, instructions_text = instructions

            if tree:
                # Только первые 30 строк дерева для краткости
                tree_lines = tree.split("\n")
                if len(tree_lines) > 32:
                    tree = "\n".join(tree_lines[:32]) + f"\n  ... и ещё {len(tree_lines) - 32} файлов"
                parts.append(tree)

            if instructions_text and instructions_name:
                # Краткий обзор — первые 1000 символов
                if len(instructions_text) > 1000:
                    instructions_text = instructions_text[:1000] + "\n... (обрезано)"
                parts.append(f"{instructions_name}:\n{instructions_text}")

        return "\n".join(parts)

    # Запускаем все проекты параллельно
    tasks = [asyncio.create_task(_one_project(p)) for p in projects]
    results = await asyncio.gather(*tasks)

    header = f"Все проекты компании ({len(projects)}):\n"
    return header + "\n\n---\n\n".join(results)


def _human_size(size: int) -> str:
    """Форматирует размер файла."""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size // 1024}KB"
    else:
        return f"{size // (1024 * 1024)}MB"
