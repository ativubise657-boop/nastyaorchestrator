"""BaseExecutor — общая основа для всех LLM-executor'ов nastyaorc.

Вынесено из CodexExecutor в рамках Issue 2.3A: AITunnelExecutor/GeminiExecutor
не связаны с Codex CLI отношением `is-a`, но должны переиспользовать сборку
контекста, AGENTS.md, секции промпта, утилиты путей. До рефакторинга они
наследовались от CodexExecutor, что несло паразитный параметр `codex_binary`
в их `__init__`.

Реальные рантаймы (Codex CLI / HTTP API) наследуют от BaseExecutor и
добавляют свою специфику (subprocess, HTTP client, tools).

# Как включить debug prompt logging:
# - Windows (cmd): set NASTYAORC_LOG_PROMPT=1 && запусти приложение
# - Или в dev-gui-среде: добавить в env перед стартом worker
# После отправки сообщения файлы prompt-{task_id}.log появятся в рабочей папке worker
# (обычно там же где worker.log).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from backend.core.file_types import (
    IMAGE_EXTS as IMAGE_EXTENSIONS,  # alias для обратной совместимости (Issue 2.2A)
    NON_READABLE_BINARY_EXTS,
)

logger = logging.getLogger(__name__)


@dataclass
class ExecuteRequest:
    """Единый контракт вызова любого executor-а.

    Заменяет 13-15 отдельных параметров в сигнатуре execute(). Все executor-ы
    принимают один объект — нет риска рассинхронизации при добавлении полей.

    Обязательные поля: prompt, workspace, model, mode.
    Остальные — опциональны (не каждый executor использует всё).
    """

    # ── Обязательные ──────────────────────────────────────────────────────
    prompt: str                          # текст сообщения от Насти
    workspace: str                       # путь к рабочей папке
    model: str                           # gpt-5.4 / glm-4.7-flash / gemini-2.5-flash / ...
    mode: str                            # auto / ag+ / rev / solo

    # ── Контекст задачи ───────────────────────────────────────────────────
    session_id: str | None = None
    task_id: str | None = None
    chat_history: list[dict] | None = None
    project: dict | None = None
    documents: list[dict] | None = None
    doc_folders: list[str] | None = None
    completed_tasks: list[dict] | None = None
    github_context: str | None = None    # уже подтянутый контекст (если есть)
    crm_context: str | None = None       # уже подтянутый CRM контекст

    # ── Источники для _fetch_contexts_parallel ────────────────────────────
    git_url: str | None = None           # URL репо для GitHub-контекста
    all_projects: list[dict] | None = None  # все проекты (альтернатива git_url)

    # ── Специфика отдельных executor-ов ──────────────────────────────────
    documents_dir: str | None = None     # папка документов (только CodexExecutor)
    codex_sandbox: str | None = None     # sandbox-режим CLI (только CodexExecutor)

    # ── Callback стриминга ────────────────────────────────────────────────
    # Не dataclass-поле в стандартном смысле — Callable не сериализуется,
    # но удобно держать рядом с запросом. Default — None (нет стриминга).
    on_chunk: object = field(default=None, repr=False)


def build_execute_request(task: dict) -> "ExecuteRequest":
    """Строит ExecuteRequest из task-dict (то что приходит из /api/queue/next).

    Удобная фабрика — один вызов вместо 15+ keyword-аргументов в call site.
    Имена полей соответствуют ключам task-dict из backend/api/system.py.
    """
    return ExecuteRequest(
        prompt=task["prompt"],
        workspace=task.get("project_path", ""),
        model=task.get("model", "gpt-5.4"),
        mode=task.get("mode", "auto"),
        session_id=task.get("session_id"),
        task_id=task.get("id"),
        chat_history=task.get("chat_history"),
        project=task.get("project"),
        documents=task.get("documents"),
        doc_folders=task.get("doc_folders"),
        completed_tasks=task.get("completed_tasks"),
        git_url=task.get("git_url"),
        all_projects=task.get("all_projects"),
        documents_dir=task.get("documents_dir"),
        codex_sandbox=task.get("codex_sandbox"),
    )

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class BaseExecutor:
    """Общие утилиты и сборка промпта. Конкретные рантаймы наследуют и добавляют execute()."""

    # Минимальный системный промпт — общие правила для всех моделей.
    # Всё остальное (стиль, шорткаты, правила работы с vault) — в AGENTS.md
    # в корне workspace проекта. Он подгружается автоматически (_load_agents_md).
    SYSTEM_PROMPT = (
        "Ты — ассистент Насти. Отвечай на русском, обращайся к Насте по имени.\n"
        "Если не уверен — скажи честно, не выдумывай.\n"
        "Не начинай ответы с «Конечно», «Отлично», «Безусловно».\n\n"
        "СОЗДАНИЕ ФАЙЛА-РЕЗУЛЬТАТА:\n"
        "Если Настя просит оформить результат как документ, используй:\n"
        "  :::document:имя_файла.md\n"
        "  содержимое markdown\n"
        "  :::\n"
        "Для папки: :::document:имя_файла.md:Имя папки\n"
        "После блока коротко скажи, что создала. Без запроса файл не создавай."
    )

    # Максимум байт AGENTS.md для подмешивания в промпт (защита от гигантских файлов)
    _AGENTS_MD_MAX_BYTES = 32 * 1024

    def __init__(self, task_timeout: int = 600):
        self.task_timeout = task_timeout

    # ------------------------------------------------------------------
    # Утилиты — пути, обрезка текста, проверки
    # ------------------------------------------------------------------

    @classmethod
    def _load_agents_md(cls, workspace: str) -> str | None:
        """Читает AGENTS.md из корня workspace. Возвращает None если нет/ошибка."""
        if not workspace:
            return None
        path = Path(workspace) / "AGENTS.md"
        if not path.is_file():
            return None
        try:
            data = path.read_bytes()[: cls._AGENTS_MD_MAX_BYTES]
            return data.decode("utf-8", errors="replace").strip() or None
        except OSError as exc:
            logger.warning("Не удалось прочитать AGENTS.md: %s", exc)
            return None

    @staticmethod
    def _smart_truncate(text: str, max_len: int = 500) -> str:
        if len(text) <= max_len:
            return text
        head_len = max_len * 3 // 10
        tail_len = max_len - head_len - 30
        return text[:head_len] + "\n... [обрезано] ...\n" + text[-tail_len:]

    @staticmethod
    def _normalize_path_for_cli(path: str) -> str:
        if not path:
            return path
        return str(Path(path))

    @staticmethod
    def _existing_dir(path: str | None) -> str | None:
        if not path:
            return None
        return path if os.path.isdir(path) else None

    @staticmethod
    def _extract_image_paths(documents: list[dict] | None) -> list[str]:
        if not documents:
            return []
        paths: list[str] = []
        for doc in documents:
            file_path = doc.get("path", "")
            suffix = Path(doc.get("filename", "")).suffix.lower()
            if not file_path or suffix not in IMAGE_EXTENSIONS:
                continue
            if not doc.get("requested"):
                continue
            if Path(file_path).exists():
                paths.append(str(Path(file_path).resolve()))
        return paths

    @staticmethod
    def _collect_additional_dirs(
        *,
        workspace: str,
        documents: list[dict] | None,
        image_paths: list[str],
    ) -> list[str]:
        workspace_path = Path(workspace).resolve()
        dirs: set[str] = set()

        for path in image_paths:
            parent = Path(path).resolve().parent
            try:
                parent.relative_to(workspace_path)
            except ValueError:
                dirs.add(str(parent))

        if documents:
            for doc in documents:
                file_path = doc.get("path", "")
                if not file_path:
                    continue
                # 1.4A: бинарные форматы — Codex их всё равно не прочитает без внешних тулов
                fname = doc.get("filename", "")
                ext = Path(fname).suffix.lower()
                if ext in NON_READABLE_BINARY_EXTS:
                    continue
                # 1.4A: парсинг упал — не даём add-dir, чтобы модель не лезла в файл
                if doc.get("parse_status") == "failed":
                    continue
                parent = Path(file_path).resolve().parent
                try:
                    parent.relative_to(workspace_path)
                except ValueError:
                    dirs.add(str(parent))

        return sorted(dirs)

    # ------------------------------------------------------------------
    # Секции _build_context_prompt (Issue 2.4A — декомпозиция)
    # ------------------------------------------------------------------

    @classmethod
    def _section_agents_md(cls, workspace: str | None) -> str | None:
        """AGENTS.md из workspace — персона, шорткаты, правила (единые для всех executor'ов)."""
        if not workspace:
            return None
        agents = cls._load_agents_md(workspace)
        if not agents:
            return None
        return (
            "\n--- Инструкции ассистента (AGENTS.md) ---\n"
            f"{agents}\n"
            "--- Конец инструкций ---"
        )

    @staticmethod
    def _section_project(project: dict | None) -> str | None:
        if not project:
            return None
        name = project.get("name", "")
        desc = project.get("description", "")
        parts: list[str] = []
        if name:
            parts.append(f"[Проект: {name}]")
        if desc:
            parts.append(f"Описание проекта: {desc}")
        return "\n".join(parts) if parts else None

    @staticmethod
    def _section_crm(crm_context: str | None) -> str | None:
        return crm_context or None

    @staticmethod
    def _format_single_doc(doc: dict) -> str:
        """Форматирует одну запись документа в строку для промпта (Issue 1.1A + 2.1C).

        Ветки:
          - изображение + requested → "прикреплено к первому сообщению"
          - content есть           → inline code block
          - requested без content  → честное "не распарсилось, не пытайся читать с диска"
          - просто listing        → "#N имя (size байт)" + ⚠ если parse_status=failed
        """
        num = doc.get("num", "?")
        fname = doc.get("filename", "?")
        size = doc.get("size", 0)
        ext = Path(fname).suffix.lower()
        content = doc.get("content")
        parse_failed = doc.get("parse_status") == "failed"

        if ext in IMAGE_EXTENSIONS and doc.get("requested"):
            if content:
                # Gemini распарсил картинку → кладём текстовое описание в промпт.
                # Модель читает это без vision-режима. Если у CLI есть vision,
                # она также увидит саму картинку через --image флаг.
                return (
                    f"#{num} {fname} (изображение; текстовое описание ниже,"
                    f" сам файл также прикреплён):\n```\n{content}\n```"
                )
            else:
                return f"#{num} {fname} (изображение прикреплено к первому сообщению)"
        elif content:
            return f"#{num} {fname}:\n```\n{content}\n```"
        elif doc.get("requested"):
            return (
                f"#{num} {fname} ({size} байт) — файл приложен, но автоматически распарсить содержимое не удалось. "
                f"Не пытайся читать файл с диска — инструментов для парсинга этого формата нет. "
                f"Честно скажи Насте, что видишь имя и размер файла, но содержимого не видишь; "
                f"предложи прислать текстовую версию или описать содержимое своими словами."
            )
        elif parse_failed:
            # Plain listing, но с предупреждением что content не извлечён
            return (
                f"#{num} {fname} ({size} байт) ⚠ содержимое не извлечено — "
                f"если Настя попросит его разобрать, сразу скажи что прочитать не получится"
            )
        else:
            return f"#{num} {fname} ({size} байт)"

    @staticmethod
    def _section_documents(documents: list[dict] | None) -> str | None:
        """Документы с разметкой под модель — две подсекции: чат и проект (Issue 1.1A + 2.1C + chat-sessions).

        Нумерация сквозная — поле doc["num"] проставляется выше по стеку, здесь используем как есть.

        Разделение по полю scope:
          - "session" → документы текущего чата (clipboard-картинки и т.п.)
          - "project"  → загруженные через UI PDF/TZ/прайсы (видны во всех чатах)
          - отсутствует → legacy payload, все идут в project (fallback, не падаем)
        """
        if not documents:
            return None

        # Разделяем по scope; legacy (нет поля) → всё в project
        session_docs = [d for d in documents if d.get("scope") == "session"]
        project_docs = [d for d in documents if d.get("scope") != "session"]

        # Обе группы пусты — такого быть не должно при непустом documents,
        # но на всякий случай возвращаем None
        if not session_docs and not project_docs:
            return None

        result_parts: list[str] = []

        # --- Секция документов чата ---
        if session_docs:
            result_parts.append("\n--- Документы этого чата (обращайся по #номеру) ---")
            for doc in session_docs:
                result_parts.append(BaseExecutor._format_single_doc(doc))

        # --- Секция документов проекта ---
        if project_docs:
            result_parts.append("\n--- Документы проекта (видны во всех чатах, обращайся по #номеру) ---")
            for doc in project_docs:
                result_parts.append(BaseExecutor._format_single_doc(doc))

        # Подсказка про listing — один раз в конце, независимо от количества секций
        result_parts.append(
            "\n--- (если применимо) ---\n"
            "Если Настя не прикрепила документ явно и не назвала его в сообщении — "
            "ты видишь только listing (имя+размер+статус). Спроси какой именно нужен."
        )
        result_parts.append("--- Конец документов ---")
        return "\n".join(result_parts)

    @staticmethod
    def _section_doc_folders(doc_folders: list[str] | None) -> str | None:
        if not doc_folders:
            return None
        return f"\nСуществующие папки документов: {', '.join(doc_folders)}"

    @staticmethod
    def _section_github(github_context: str | None) -> str | None:
        if not github_context:
            return None
        return (
            f"\n--- Контекст проекта из GitHub ---\n{github_context}\n--- Конец контекста ---"
        )

    @classmethod
    def _section_completed_tasks(cls, completed_tasks: list[dict] | None) -> str | None:
        if not completed_tasks:
            return None
        parts = ["\n--- Контекст предыдущих задач ---"]
        for task in completed_tasks:
            q = cls._smart_truncate(task.get("prompt", ""), 200)
            a = cls._smart_truncate(task.get("result", ""), 800)
            parts.append(f"Вопрос: {q}\nОтвет: {a}")
        parts.append("--- Конец контекста задач ---")
        return "\n".join(parts)

    @classmethod
    def _section_chat_history(cls, chat_history: list[dict] | None) -> str | None:
        """История без последнего сообщения (оно уже в `prompt`)."""
        if not chat_history:
            return None
        history = chat_history[:-1]
        if not history:
            return None
        parts = ["\n--- История разговора ---"]
        for msg in history:
            role_label = "Настя" if msg["role"] == "user" else "Ассистент"
            content = cls._smart_truncate(msg["content"], 500)
            parts.append(f"{role_label}: {content}")
        parts.append("--- Конец истории ---\n")
        return "\n".join(parts)

    async def _build_context_prompt(
        self,
        prompt: str,
        chat_history: list[dict] | None = None,
        project: dict | None = None,
        github_context: str | None = None,
        documents: list[dict] | None = None,
        crm_context: str | None = None,
        doc_folders: list[str] | None = None,
        completed_tasks: list[dict] | None = None,
        workspace: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Оркестратор — порядок секций фиксирован, None-секции скипаются."""
        sections: list[str | None] = [
            self.SYSTEM_PROMPT,
            self._section_agents_md(workspace),
            self._section_project(project),
            self._section_crm(crm_context),
            self._section_documents(documents),
            self._section_doc_folders(doc_folders),
            self._section_github(github_context),
            self._section_completed_tasks(completed_tasks),
            self._section_chat_history(chat_history),
            f"Настя: {prompt}",
        ]
        final_prompt = "\n".join(s for s in sections if s)

        # DEBUG: логируем финальный prompt чтобы видеть что реально идёт в LLM.
        # Активируется через env NASTYAORC_LOG_PROMPT=1 (не захламляем prod-логи по умолчанию).
        # Пишем в отдельный файл prompt-{task_id}.log рядом с worker.log.
        if os.environ.get("NASTYAORC_LOG_PROMPT") == "1":
            try:
                log_dir = Path(os.environ.get("WORKER_LOG_DIR", "."))
                log_dir.mkdir(parents=True, exist_ok=True)
                tid = task_id or "unknown"
                log_path = log_dir / f"prompt-{tid}.log"
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(final_prompt)
                logger.info("DEBUG prompt логирован в %s (%d байт)", log_path, len(final_prompt))
            except Exception as exc:
                logger.warning("prompt logging failed: %s", exc)

        return final_prompt

    # ------------------------------------------------------------------
    # Параллельная подтяжка внешних контекстов (Fix 4.4A)
    # ------------------------------------------------------------------

    async def _fetch_contexts_parallel(
        self,
        *,
        git_url: str | None,
        all_projects: list[dict] | None,
        prompt: str,
    ) -> tuple[str | None, str | None]:
        """GitHub и CRM контексты качаются одновременно вместо последовательно.

        До этого fix — два `await` подряд = ~3 секунд на каждый запрос. Теперь
        ~макс из двух (обычно 1-2 сек). Ошибки изолированы — каждый контекст
        падает независимо, не ломая другой.
        """

        async def _gh() -> str | None:
            if git_url:
                try:
                    from worker.github_client import build_project_context
                    ctx = await build_project_context(git_url)
                    if ctx:
                        logger.info("GitHub контекст подтянут: %d символов", len(ctx))
                    return ctx
                except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
                    logger.warning("Не удалось подтянуть GitHub контекст: %s", exc)
                    return None
            elif all_projects:
                try:
                    from worker.github_client import build_all_projects_context
                    ctx = await build_all_projects_context(all_projects)
                    if ctx:
                        logger.info("GitHub контекст всех проектов: %d символов", len(ctx))
                    return ctx
                except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
                    logger.warning("Не удалось подтянуть контекст всех проектов: %s", exc)
                    return None
            return None

        async def _crm() -> str | None:
            try:
                from worker.bitrix_client import build_crm_context
                ctx = await build_crm_context(prompt)
                if ctx:
                    logger.info("CRM контекст подтянут: %d символов", len(ctx))
                return ctx
            except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
                logger.warning("Не удалось получить CRM контекст: %s", exc)
                return None

        github_ctx, crm_ctx = await asyncio.gather(_gh(), _crm())
        return github_ctx, crm_ctx

    @staticmethod
    def _build_prompt(prompt: str, mode: str) -> str:
        """Оборачивание промпта под mode (rev/ag+). Специфично ни к одному рантайму."""
        if mode == "rev":
            return (
                "Режим: code review.\n"
                "Сначала перечисли конкретные проблемы и риски, затем вопросы и только потом краткий итог.\n\n"
                f"{prompt}"
            )
        if mode == "ag+":
            return (
                "Режим: широкий инженерный проход.\n"
                "Сначала быстро спланируй работу, затем внеси изменения end-to-end. "
                "Если реально помогает, используй параллельную работу и декомпозицию.\n\n"
                f"{prompt}"
            )
        return prompt
