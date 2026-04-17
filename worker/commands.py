"""Встроенные команды worker-а.

Перехватывают специальные промпты (начинающиеся с /) и выполняют
без вызова Codex CLI.
"""
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Пути проекта
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Хранилище precompact-файлов — из env или fallback на data/notes/memory
_default_storage = str(PROJECT_ROOT / "data" / "notes")
STORAGE_PATH = Path(os.getenv("NOTES_PATH", _default_storage))
MEMORY_PATH = STORAGE_PATH / "memory"

# Известные встроенные команды (остальные пойдут в Codex CLI)
BUILTIN_COMMANDS = {"/pre", "/post"}


def is_command(prompt: str) -> bool:
    """Проверяет, является ли промпт встроенной командой."""
    cmd = prompt.strip().split()[0].lower() if prompt.strip() else ""
    return cmd in BUILTIN_COMMANDS


def get_command_name(prompt: str) -> str:
    """Извлекает имя команды из промпта."""
    return prompt.strip().split()[0].lower()


async def handle_command(
    prompt: str,
    project: dict | None = None,
    chat_history: list[dict] | None = None,
) -> dict:
    """Выполняет встроенную команду.

    Returns:
        {"status": "completed"|"failed", "result": str, "error": str|None}
    """
    cmd = get_command_name(prompt)

    if cmd == "/pre":
        return await _cmd_pre(chat_history, project)
    elif cmd == "/post":
        return await _cmd_post()
    else:
        return {
            "status": "failed",
            "result": "",
            "error": f"Неизвестная команда: {cmd}",
        }


# ---------------------------------------------------------------------------
# /pre — Скомпоновать чат (precompact)
# ---------------------------------------------------------------------------

async def _cmd_pre(
    chat_history: list[dict] | None = None,
    project: dict | None = None,
) -> dict:
    """Сохраняет контекст текущей сессии в precompact файл."""
    pid = os.getpid()
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")
    now_file = now.strftime("%Y%m%d-%H%M%S")

    # Убедимся что папка memory существует
    MEMORY_PATH.mkdir(parents=True, exist_ok=True)

    filename = f"precompact-{pid}.md"
    filepath = MEMORY_PATH / filename

    # Собираем контекст
    lines = [
        "---",
        f"session_pid: {pid}",
        "project: nastyaorchestrator",
        f"date: {now.strftime('%Y-%m-%d')}",
        f"time: {now_str}",
        "---",
        "",
        "# Precompact — Nastya Orchestrator",
        "",
    ]

    # Проект
    if project:
        lines.append(f"## Проект: {project.get('name', '?')}")
        if project.get("description"):
            lines.append(f"{project['description']}")
        lines.append("")

    # История чата (последние сообщения)
    if chat_history:
        lines.append("## История чата")
        lines.append("")
        for msg in chat_history:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            # Обрезаем длинные сообщения для компактности
            if len(content) > 500:
                content = content[:500] + "..."
            prefix = "**Настя:**" if role == "user" else "**Codex:**"
            lines.append(f"{prefix} {content}")
            lines.append("")

    # Git diff (краткая сводка изменений)
    try:
        diff_stat = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        if diff_stat.stdout.strip():
            lines.append("## Изменённые файлы (git diff)")
            lines.append("```")
            lines.append(diff_stat.stdout.strip())
            lines.append("```")
            lines.append("")
    except Exception as exc:
        logger.warning("precompact: git diff --stat упал, git diff секция будет пропущена: %s", exc, exc_info=True)

    # Незакоммиченные файлы
    try:
        status = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        if status.stdout.strip():
            lines.append("## Git status")
            lines.append("```")
            lines.append(status.stdout.strip())
            lines.append("```")
            lines.append("")
    except Exception as exc:
        logger.warning("precompact: git status упал, статус будет пропущен: %s", exc, exc_info=True)

    content = "\n".join(lines)

    try:
        filepath.write_text(content, encoding="utf-8")
        logger.info("pre: контекст сохранён в %s", filepath)
        return {
            "status": "completed",
            "result": f"**Контекст скомпонован** ({now_str})\n\n"
                      f"📁 Файл: `{filename}`\n"
                      f"📍 Путь: `{filepath}`\n"
                      f"💬 Сообщений в истории: {len(chat_history) if chat_history else 0}",
            "error": None,
        }
    except Exception as e:
        logger.exception("pre: ошибка записи %s", filepath)
        return {
            "status": "failed",
            "result": "",
            "error": f"Ошибка записи precompact: {e}",
        }


# ---------------------------------------------------------------------------
# /post — Раскомпоновать чат (postcompact)
# ---------------------------------------------------------------------------

async def _cmd_post() -> dict:
    """Находит и показывает последний precompact, затем удаляет файл."""
    if not MEMORY_PATH.exists():
        return {
            "status": "completed",
            "result": "📭 Нет сохранённых precompact-файлов.",
            "error": None,
        }

    # Ищем все precompact файлы
    files = sorted(MEMORY_PATH.glob("precompact-*.md"), key=lambda f: f.stat().st_mtime, reverse=True)

    if not files:
        return {
            "status": "completed",
            "result": "📭 Нет сохранённых precompact-файлов.",
            "error": None,
        }

    # Берём самый свежий
    latest = files[0]
    try:
        content = latest.read_text(encoding="utf-8")
    except Exception as e:
        return {"status": "failed", "result": "", "error": f"Ошибка чтения {latest.name}: {e}"}

    # Удаляем прочитанный файл
    try:
        latest.unlink()
        logger.info("post: прочитан и удалён %s", latest)
    except Exception as exc:
        logger.debug("post: не удалось удалить %s (возможно уже удалён): %s", latest.name, exc)

    # Удаляем старые precompact (>24ч)
    for f in files[1:]:
        try:
            age_hours = (datetime.now().timestamp() - f.stat().st_mtime) / 3600
            if age_hours > 24:
                f.unlink()
                logger.info("post: удалён старый %s (%.1fч)", f.name, age_hours)
        except Exception as exc:
            logger.debug("post: не удалось удалить старый precompact %s: %s", f.name, exc)

    now_str = datetime.now().strftime("%H:%M")
    return {
        "status": "completed",
        "result": f"**Контекст раскомпонован** ({now_str})\n"
                  f"📁 Из файла: `{latest.name}`\n\n"
                  f"---\n\n{content}",
        "error": None,
    }


