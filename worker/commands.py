"""Встроенные команды worker-а.

Перехватывают специальные промпты (начинающиеся с /) и выполняют
без вызова Claude CLI.
"""
import glob
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Пути проекта
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_INSTRUCTION_FILES = [
    PROJECT_ROOT / "AGENTS.md",
    PROJECT_ROOT / "CLAUDE.md",
]
PROJECT_INSTRUCTIONS = next(
    (path for path in PROJECT_INSTRUCTION_FILES if path.exists()),
    PROJECT_INSTRUCTION_FILES[0],
)

# Хранилище заметок — из env или fallback на data/notes внутри проекта
_default_storage = str(PROJECT_ROOT / "data" / "notes")
STORAGE_PATH = Path(os.getenv("NOTES_PATH", _default_storage))
MEMORY_PATH = STORAGE_PATH / "memory"
GLOBAL_INSTRUCTION_FILES = [
    Path.home() / ".codex" / "AGENTS.md",
    Path.home() / ".Codex" / "AGENTS.md",
    Path.home() / ".claude" / "CLAUDE.md",
]
GLOBAL_INSTRUCTIONS = next(
    (path for path in GLOBAL_INSTRUCTION_FILES if path.exists()),
    GLOBAL_INSTRUCTION_FILES[0],
)
GLOBAL_STORAGE = Path(
    os.getenv("GLOBAL_STORAGE_PATH", "/mnt/d/Bloknot/Reels/Work/Projects/Globalinit/AGENTS.md")
)

# Известные встроенные команды (остальные пойдут в Codex CLI)
BUILTIN_COMMANDS = {"/lai", "/pre", "/post", "/rev"}


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

    if cmd == "/lai":
        return await _cmd_lai()
    elif cmd == "/pre":
        return await _cmd_pre(chat_history, project)
    elif cmd == "/post":
        return await _cmd_post()
    elif cmd == "/rev":
        return await _cmd_rev()
    else:
        return {
            "status": "failed",
            "result": "",
            "error": f"Неизвестная команда: {cmd}",
        }


async def _cmd_lai() -> dict:
    """LAI = lessons + init: сохранить инструкции проекта в хранилище."""
    results = []
    errors = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # init: проектные инструкции -> хранилище
    try:
        if PROJECT_INSTRUCTIONS.exists() and STORAGE_PATH.exists():
            shutil.copy2(PROJECT_INSTRUCTIONS, STORAGE_PATH / PROJECT_INSTRUCTIONS.name)
            results.append(f"✅ Инструкции проекта ({PROJECT_INSTRUCTIONS.name}) → хранилище")
            logger.info("init: проектные инструкции скопированы в %s", STORAGE_PATH)
        elif not PROJECT_INSTRUCTIONS.exists():
            errors.append("⚠️ Инструкции проекта не найдены")
        elif not STORAGE_PATH.exists():
            errors.append(f"⚠️ Хранилище не найдено: {STORAGE_PATH}")
    except Exception as e:
        errors.append(f"❌ Ошибка копирования инструкций проекта: {e}")
        logger.exception("init: ошибка копирования инструкций проекта")

    # init: глобальные инструкции -> хранилище
    try:
        if GLOBAL_INSTRUCTIONS.exists() and GLOBAL_STORAGE.parent.exists():
            shutil.copy2(GLOBAL_INSTRUCTIONS, GLOBAL_STORAGE)
            results.append(f"✅ Глобальные инструкции ({GLOBAL_INSTRUCTIONS.name}) → хранилище")
            logger.info("init: глобальные инструкции скопированы в %s", GLOBAL_STORAGE)
        elif not GLOBAL_INSTRUCTIONS.exists():
            errors.append("⚠️ Глобальные инструкции не найдены")
    except Exception as e:
        errors.append(f"❌ Ошибка копирования глобальных инструкций: {e}")
        logger.exception("init: ошибка копирования глобальных инструкций")

    # Формируем отчёт
    report_parts = [f"**LAI выполнен** ({now})", ""]
    report_parts.extend(results)
    if errors:
        report_parts.append("")
        report_parts.extend(errors)

    status = "completed" if not errors else ("completed" if results else "failed")
    return {
        "status": status,
        "result": "\n".join(report_parts),
        "error": None if not errors else "; ".join(errors),
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
    except Exception:
        pass

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
    except Exception:
        pass

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
    except Exception:
        pass

    # Удаляем старые precompact (>24ч)
    for f in files[1:]:
        try:
            age_hours = (datetime.now().timestamp() - f.stat().st_mtime) / 3600
            if age_hours > 24:
                f.unlink()
                logger.info("post: удалён старый %s (%.1fч)", f.name, age_hours)
        except Exception:
            pass

    now_str = datetime.now().strftime("%H:%M")
    return {
        "status": "completed",
        "result": f"**Контекст раскомпонован** ({now_str})\n"
                  f"📁 Из файла: `{latest.name}`\n\n"
                  f"---\n\n{content}",
        "error": None,
    }


# ---------------------------------------------------------------------------
# /rev — Анализ и рекомендация типа ревью
# ---------------------------------------------------------------------------

async def _cmd_rev() -> dict:
    """Анализирует изменения и рекомендует тип ревью (big/small)."""
    now_str = datetime.now().strftime("%H:%M")

    files_changed = 0
    insertions = 0
    deletions = 0
    diff_stat_text = ""

    try:
        # git diff --stat (незакоммиченные)
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        diff_stat_text = result.stdout.strip()

        # git diff --numstat для подсчёта строк
        numstat = subprocess.run(
            ["git", "diff", "--numstat", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        for line in numstat.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                files_changed += 1
                try:
                    insertions += int(parts[0]) if parts[0] != "-" else 0
                    deletions += int(parts[1]) if parts[1] != "-" else 0
                except ValueError:
                    pass

        # Также учитываем untracked файлы
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        untracked_count = len([l for l in untracked.stdout.strip().splitlines() if l])

    except Exception as e:
        return {
            "status": "failed",
            "result": "",
            "error": f"Ошибка анализа git: {e}",
        }

    total_lines = insertions + deletions
    total_files = files_changed + untracked_count

    # Определяем рекомендацию
    if total_files <= 3 and total_lines <= 100:
        recommendation = "small"
        reason = "мало файлов и строк — быстрый ревью"
        emoji = "🟢"
    elif total_files <= 8 and total_lines <= 500:
        recommendation = "small"
        reason = "средний объём — достаточно краткого ревью"
        emoji = "🟡"
    else:
        recommendation = "big"
        reason = "много изменений — нужен полный ревью"
        emoji = "🔴"

    report = [
        f"**REV анализ** ({now_str})",
        "",
        f"📊 **Статистика изменений:**",
        f"  · Файлов изменено: **{files_changed}**" + (f" (+{untracked_count} новых)" if untracked_count else ""),
        f"  · Строк добавлено: **+{insertions}**",
        f"  · Строк удалено: **-{deletions}**",
        f"  · Всего строк: **{total_lines}**",
        "",
    ]

    if diff_stat_text:
        report.extend([
            "```",
            diff_stat_text,
            "```",
            "",
        ])

    report.extend([
        f"{emoji} **Рекомендация: rev {recommendation}**",
        f"  _{reason}_",
        "",
        f"Напишите **rev big** для полного ревью (архитектура → код → тесты → перформанс)",
        f"или **rev small** для быстрого (только критичное).",
    ])

    return {
        "status": "completed",
        "result": "\n".join(report),
        "error": None,
    }
