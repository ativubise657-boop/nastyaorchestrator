"""
Nastya Orchestrator — dev GUI (customtkinter).

Современный dev-tool для локальных билдов и диагностики.
Запуск: dev-gui.bat (подхватывает MSVC/Rust env) либо
.venv-build\\Scripts\\pythonw.exe dev-gui.pyw
"""
from __future__ import annotations

import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from urllib import error, request

import customtkinter as ctk

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "dev-gui.log"

# ── Палитра (VSCode Dark+ inspired, проверена на контраст WCAG AA+) ──
C_BG = "#1e1e1e"
C_PANEL = "#252526"
C_PANEL_ALT = "#2d2d30"
C_BORDER = "#3c3c3c"
C_TEXT = "#ffffff"
C_TEXT_DIM = "#cccccc"
C_TEXT_MUTED = "#858585"

# Кнопки (на тёмном фоне):
#   - светлые (зелёный, красный, жёлтый) → тёмный текст (#1e1e1e)
#   - тёмные (серый, синий) → белый текст (#ffffff)
C_BTN_GRAY = "#3c3c3c"
C_BTN_GRAY_HOVER = "#505050"
C_BTN_GRAY_TEXT = C_TEXT

C_BTN_BLUE = "#0e639c"
C_BTN_BLUE_HOVER = "#1177bb"
C_BTN_BLUE_TEXT = C_TEXT

C_BTN_GREEN = "#4ec9b0"  # mint
C_BTN_GREEN_HOVER = "#6fddc3"
C_BTN_GREEN_TEXT = "#0a1f1b"

C_BTN_ORANGE = "#e5a957"
C_BTN_ORANGE_HOVER = "#f0bf7a"
C_BTN_ORANGE_TEXT = "#1e1100"

C_BTN_RED = "#f48771"
C_BTN_RED_HOVER = "#ff9a80"
C_BTN_RED_TEXT = "#1e0900"

# Индикаторы
C_DOT_GREEN = "#4ec9b0"
C_DOT_RED = "#f48771"
C_DOT_GRAY = "#555555"

# Лог
C_LOG_BG = "#0a0a0a"
C_LOG_FG = "#d4d4d4"
C_LOG_CMD = "#569cd6"
C_LOG_OK = "#4ec9b0"
C_LOG_ERR = "#f48771"
C_LOG_WARN = "#dcdcaa"

# Шрифты
F_APP_TITLE = ("Segoe UI", 18, "bold")
F_SECTION = ("Segoe UI", 13, "bold")
F_LABEL_BOLD = ("Segoe UI", 11, "bold")
F_LABEL = ("Segoe UI", 11)
F_BTN = ("Segoe UI", 11, "bold")
F_BTN_HERO = ("Segoe UI", 14, "bold")
F_LOG = ("Consolas", 12)
F_LOG_BOLD = ("Consolas", 12, "bold")
F_TOOLTIP = ("Segoe UI", 10)


# ============================================================================
# Tooltip — самописный, без зависимостей
# ============================================================================


class Tooltip:
    """Простой tooltip при наведении на виджет."""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 500) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tipwindow: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._unschedule()
        self._after_id = self.widget.after(self.delay, self._show)

    def _unschedule(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self.tipwindow or not self.text:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)
        tw.configure(bg="#111111")
        label = tk.Label(
            tw,
            text=self.text,
            justify="left",
            bg="#111111",
            fg="#eeeeee",
            font=F_TOOLTIP,
            padx=10,
            pady=6,
            borderwidth=1,
            relief="solid",
            wraplength=440,
        )
        label.pack()
        tw.update_idletasks()
        # Центрируем относительно кнопки
        tw_w = tw.winfo_width()
        tw.geometry(f"+{x - tw_w // 2}+{y}")
        self.tipwindow = tw

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._unschedule()
        if self.tipwindow:
            try:
                self.tipwindow.destroy()
            except Exception:
                pass
            self.tipwindow = None


# ============================================================================
# Команды (возвращают список аргументов для subprocess)
# ============================================================================


def cmd_check_env() -> list[str]:
    return [
        "cmd", "/c",
        "echo === cl === & where cl & "
        "echo === rustc === & rustc --version & "
        "echo === cargo === & cargo --version & "
        "echo === tauri === & cargo tauri --version & "
        "echo === node === & node --version & "
        "echo === npm === & npm --version & "
        "echo === codex === & where codex & "
        "echo === venv === & if exist .venv-build\\Scripts\\python.exe (echo OK) else (echo MISSING) & "
        "echo === signing key === & if exist %USERPROFILE%\\.tauri\\nastya.key (echo OK) else (echo MISSING)",
    ]


def cmd_frontend_install() -> list[str]:
    return ["cmd", "/c", "cd /d frontend && npm install"]


def cmd_frontend_build() -> list[str]:
    return ["cmd", "/c", "cd /d frontend && npm run build"]


def cmd_pyinstaller_backend() -> list[str]:
    return [
        "cmd", "/c",
        ".venv-build\\Scripts\\pyinstaller.exe --noconfirm --clean "
        "--distpath build\\dist --workpath build\\work build\\backend.spec",
    ]


def cmd_pyinstaller_worker() -> list[str]:
    return [
        "cmd", "/c",
        ".venv-build\\Scripts\\pyinstaller.exe --noconfirm --clean "
        "--distpath build\\dist --workpath build\\work build\\worker.spec",
    ]


def cmd_copy_sidecars() -> list[str]:
    return [
        "cmd", "/c",
        "if not exist src-tauri\\binaries mkdir src-tauri\\binaries && "
        "copy /Y build\\dist\\nastya-backend.exe "
        "src-tauri\\binaries\\nastya-backend-x86_64-pc-windows-msvc.exe && "
        "copy /Y build\\dist\\nastya-worker.exe "
        "src-tauri\\binaries\\nastya-worker-x86_64-pc-windows-msvc.exe",
    ]


def cmd_tauri_build() -> list[str]:
    return ["cmd", "/c", "cargo tauri build"]


def cmd_tauri_dev() -> list[str]:
    return ["cmd", "/c", "cargo tauri dev"]


def cmd_full_build() -> list[str]:
    return ["cmd", "/c", "local-build.bat"]


def cmd_debug_worker() -> list[str]:
    return ["cmd", "/k", "debug-worker.bat"]


def cmd_kill_processes() -> list[str]:
    return [
        "cmd", "/c",
        "taskkill /F /IM nastya-backend.exe /IM nastya-worker.exe "
        "/IM nastya-orchestrator.exe "
        "/IM opera-proxy.exe /IM opera-proxy-x86_64-pc-windows-msvc.exe 2>nul & "
        "echo done",
    ]


def cmd_open_installer_folder() -> list[str]:
    return ["cmd", "/c", "explorer src-tauri\\target\\release\\bundle\\nsis"]


def cmd_open_project_folder() -> list[str]:
    return ["cmd", "/c", "explorer ."]


def _install_paths() -> list[str]:
    """Возможные пути установки Nastya Orchestrator (per-user NSIS)."""
    user_profile = os.environ.get("USERPROFILE", "C:\\Users\\Default")
    local_app = os.environ.get("LOCALAPPDATA", user_profile + "\\AppData\\Local")
    return [
        f"{local_app}\\Programs\\Nastya Orchestrator",
        f"{user_profile}\\Desktop\\Nastya Orchestrator",
        "D:\\Programs\\Nastya Orchestrator",
    ]


def cmd_restart_app() -> list[str]:
    """Sentinel — реальная логика в DevGui._python_action_restart_app()."""
    return ["__python__", "restart_app"]


def cmd_uninstall_app() -> list[str]:
    """Sentinel — реальная логика в DevGui._python_action_uninstall_app()."""
    return ["__python__", "uninstall_app"]


def cmd_clean_data() -> list[str]:
    """Sentinel — удаляет data/ из всех install-папок. Чистый старт."""
    return ["__python__", "clean_data"]


def cmd_remote_config_show() -> list[str]:
    return ["__python__", "remote_config_show"]


def cmd_remote_config_edit() -> list[str]:
    return ["__python__", "remote_config_edit"]


def cmd_remote_config_push() -> list[str]:
    return ["__python__", "remote_config_push"]


def cmd_remote_config_refresh() -> list[str]:
    return ["__python__", "remote_config_refresh"]


# ── Release (CI) ────────────────────────────────────────────────────────────
def cmd_release_full() -> list[str]:
    return ["__python__", "release_full"]


def cmd_push_code_only() -> list[str]:
    return ["__python__", "push_code_only"]


def cmd_open_actions() -> list[str]:
    return ["__python__", "open_actions"]


def cmd_open_releases() -> list[str]:
    return ["__python__", "open_releases"]


# ============================================================================
# App
# ============================================================================

# URL репо для релизного workflow — меняется если репо transfer'нется
RELEASE_REPO_URL = "https://github.com/ativubise657-boop/nastyaorchestrator"
RELEASE_REPO_SLUG = "ativubise657-boop/nastyaorchestrator"


class DevGui:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title("Nastya Orchestrator — dev GUI")
        self.root.minsize(1200, 720)

        # Initial geometry — точно под screen size минус taskbar.
        # state("zoomed") не работает в CTk из __init__ (окно ещё не готово) —
        # вызываем через after(50, ...) когда tk mainloop уже стартует.
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            # Взрослый прямоугольник на 90% экрана — потом maximize
            gw = max(1200, sw - 80)
            gh = max(720, sh - 120)
            self.root.geometry(f"{gw}x{gh}+20+20")
        except Exception:
            self.root.geometry("1800x980")

        # Отложенный maximize — CTk нужно время на первую отрисовку
        def _maximize_later():
            try:
                self.root.state("zoomed")
            except Exception:
                # Fallback для не-Windows или если state недоступен
                try:
                    self.root.attributes("-zoomed", True)
                except Exception:
                    pass
        self.root.after(100, _maximize_later)

        self.current_proc: subprocess.Popen | None = None
        self.output_queue: queue.Queue[str] = queue.Queue()
        self._stop_flag = threading.Event()

        self._build_ui()
        self._poll_output()
        self._refresh_status()

    # ───── UI ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.root.configure(fg_color=C_BG)
        # Layout: кнопки слева + лог справа с пропорциональным раcпределением.
        # Weights 2/3 — при ресайзе окна колонки сохраняют 40/60 соотношение,
        # лог никогда не обрезается. Minsize для левой = кнопки не сжимаются
        # ниже читаемого размера, но могут расширяться если окно большое.
        self.root.grid_columnconfigure(0, weight=2, minsize=780)
        self.root.grid_columnconfigure(1, weight=3, minsize=480)
        # Row 0 = header (columnspan=2)
        # Rows 1-4 = левая колонка панелей кнопок
        # Row 5 = spacer чтобы заполнить пустоту снизу (прижать панели вверх)
        # Row 6 = status bar (columnspan=2)
        self.root.grid_rowconfigure(5, weight=1)

        # Состояние лога — для zoom и wrap toggle
        self._log_font_size = 12
        self._log_wrap = "none"  # "none" или "word"

        # ╔══════════════════════════════════════════════╗
        # ║  HEADER — заголовок + индикаторы             ║
        # ╚══════════════════════════════════════════════╝
        header = ctk.CTkFrame(
            self.root, corner_radius=12, fg_color=C_PANEL, border_width=1, border_color=C_BORDER
        )
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(14, 8))
        header.grid_columnconfigure(5, weight=1)

        ctk.CTkLabel(
            header, text="Nastya Orchestrator", font=F_APP_TITLE, text_color=C_TEXT
        ).grid(row=0, column=0, padx=(18, 6), pady=(14, 0), sticky="w")
        ctk.CTkLabel(
            header, text="dev GUI", font=F_LABEL, text_color=C_TEXT_MUTED
        ).grid(row=0, column=1, padx=(0, 6), pady=(22, 0), sticky="w")

        # Индикаторы (вторая строка в header)
        dots_row = ctk.CTkFrame(header, fg_color="transparent")
        dots_row.grid(row=1, column=0, columnspan=6, sticky="ew", padx=18, pady=(4, 14))

        ctk.CTkLabel(
            dots_row, text="backend", font=F_LABEL_BOLD, text_color=C_TEXT
        ).pack(side="left", padx=(0, 6))
        self.backend_dot = ctk.CTkLabel(
            dots_row, text=" ", width=18, height=18, corner_radius=9, fg_color=C_DOT_GRAY
        )
        self.backend_dot.pack(side="left", padx=(0, 18))
        Tooltip(
            self.backend_dot,
            "Backend API (FastAPI на http://127.0.0.1:8781)\n"
            "Зелёный = /api/system/health отвечает 200\n"
            "Красный = процесс не запущен или порт недоступен",
        )

        ctk.CTkLabel(
            dots_row, text="opera-proxy", font=F_LABEL_BOLD, text_color=C_TEXT
        ).pack(side="left", padx=(0, 6))
        self.opera_dot = ctk.CTkLabel(
            dots_row, text=" ", width=18, height=18, corner_radius=9, fg_color=C_DOT_GRAY
        )
        self.opera_dot.pack(side="left", padx=(0, 18))
        Tooltip(
            self.opera_dot,
            "Встроенный Opera VPN proxy (127.0.0.1:18080)\n"
            "Туннелирует через Opera EU сервер — обход Cloudflare\n"
            "для доступа к OpenAI API с российского IP.\n"
            "Зелёный = туннель работает (OpenAI API отвечает)",
        )

        self.status_text = ctk.CTkLabel(
            dots_row, text="checking…", font=F_LABEL, text_color=C_TEXT_MUTED
        )
        self.status_text.pack(side="left", padx=(0, 8))

        refresh_btn = ctk.CTkButton(
            dots_row,
            text="↻ Refresh",
            width=110,
            height=34,
            font=F_BTN,
            fg_color=C_BTN_GRAY,
            hover_color=C_BTN_GRAY_HOVER,
            text_color=C_BTN_GRAY_TEXT,
            command=self._refresh_status,
        )
        refresh_btn.pack(side="right", padx=(0, 6))
        Tooltip(refresh_btn, "Перепроверить состояние backend и opera-proxy")

        # ℹ — ликбез "как устанавливать билд Насте"
        info_btn = ctk.CTkButton(
            dots_row,
            text="ⓘ",
            width=40,
            height=34,
            font=("Segoe UI", 16, "bold"),
            fg_color=C_BTN_BLUE,
            hover_color=C_BTN_BLUE_HOVER,
            text_color=C_BTN_BLUE_TEXT,
            corner_radius=17,
            command=self._open_install_guide,
        )
        info_btn.pack(side="right", padx=(0, 6))
        Tooltip(
            info_btn,
            "Как передать и установить билд Насте.\n"
            "Клик — полная инструкция в модальном окне.",
        )

        # ╔══════════════════════════════════════════════╗
        # ║  SECTION 1 — MAIN ACTIONS (hero)             ║
        # ╚══════════════════════════════════════════════╝
        main_panel = ctk.CTkFrame(
            self.root, corner_radius=12, fg_color=C_PANEL, border_width=1, border_color=C_BORDER
        )
        main_panel.grid(row=1, column=0, sticky="ew", padx=14, pady=6)

        self._section_title(main_panel, "⚡  Основное")
        main_row = ctk.CTkFrame(main_panel, fg_color="transparent")
        main_row.pack(fill="x", padx=14, pady=(0, 14))

        self._mkbtn(
            main_row,
            "⚡  FULL BUILD  (F5)",
            cmd_full_build,
            variant="green",
            hero=True,
            tooltip=(
                "Полная локальная сборка приложения.\n"
                "Хоткей: F5\n\n"
                "Выполняет последовательно:\n"
                "  1. npm run build (frontend → dist)\n"
                "  2. PyInstaller backend.exe (~3 мин)\n"
                "  3. PyInstaller worker.exe (~2 мин)\n"
                "  4. Copy sidecars в src-tauri/binaries/\n"
                "  5. cargo tauri build → NSIS installer\n\n"
                "Время: ~5-6 минут с прогретым кешем.\n"
                "Итог: installer в src-tauri/target/release/bundle/nsis/"
            ),
        )
        self._mkbtn(
            main_row,
            "📂  Открыть installer",
            cmd_open_installer_folder,
            variant="blue",
            tooltip=(
                "Открыть проводник в папке с собранным NSIS installer-ом:\n"
                "src-tauri\\target\\release\\bundle\\nsis\\\n\n"
                "Двойной клик по .exe → установка поверх старой версии"
            ),
        )
        self._mkbtn(
            main_row,
            "🚀  Tauri dev  (F6)",
            cmd_tauri_dev,
            variant="blue",
            tooltip=(
                "Запустить приложение в dev-режиме с hot reload.\n"
                "Хоткей: F6\n\n"
                "Правки .tsx файлов применяются мгновенно в окне\n"
                "без пересборки Tauri. Идеально для доработки UI.\n\n"
                "Первый запуск ~15 минут (компиляция Rust deps),\n"
                "последующие — 10-20 сек на старт."
            ),
        )
        self._mkbtn(
            main_row,
            "🔄  Restart app",
            cmd_restart_app,
            variant="orange",
            tooltip=(
                "Перезапустить установленное приложение:\n"
                "  1. taskkill всех nastya-* + opera-proxy\n"
                "  2. timeout 2 сек (пока OS освободит handles)\n"
                "  3. Запуск nastya-orchestrator.exe из папки установки\n\n"
                "Ищет приложение в:\n"
                "  • %LOCALAPPDATA%\\Programs\\Nastya Orchestrator\n"
                "  • %USERPROFILE%\\Desktop\\Nastya Orchestrator\n"
                "  • D:\\Programs\\Nastya Orchestrator"
            ),
        )
        self._mkbtn(
            main_row,
            "⬛  Kill all  (F8)",
            cmd_kill_processes,
            variant="red",
            tooltip=(
                "Принудительно убивает все процессы приложения.\n"
                "Хоткей: F8\n\n"
                "Убивает:\n"
                "  • nastya-backend.exe\n"
                "  • nastya-worker.exe\n"
                "  • nastya-orchestrator.exe (Tauri main)\n"
                "  • opera-proxy-*.exe\n\n"
                "Используй ПЕРЕД новым FULL BUILD если приложение\n"
                "установлено — файлы .exe заняты запущенными процессами\n"
                "и билд не сможет их перезаписать."
            ),
        )

        # ╔══════════════════════════════════════════════╗
        # ║  SECTION 2 — BUILD STEPS (для отладки)       ║
        # ╚══════════════════════════════════════════════╝
        steps_panel = ctk.CTkFrame(
            self.root, corner_radius=12, fg_color=C_PANEL, border_width=1, border_color=C_BORDER
        )
        steps_panel.grid(row=2, column=0, sticky="ew", padx=14, pady=6)

        self._section_title(steps_panel, "🔧  По шагам (для отладки если Full Build падает)")
        steps_row = ctk.CTkFrame(steps_panel, fg_color="transparent")
        steps_row.pack(fill="x", padx=14, pady=(0, 14))

        self._mkbtn(
            steps_row, "Check env", cmd_check_env, variant="gray",
            tooltip=(
                "Проверить что установлены все инструменты для сборки:\n"
                "MSVC (cl), rustc, cargo, tauri-cli, node, npm, codex,\n"
                ".venv-build, Tauri signing key.\n\n"
                "Запускай первым если что-то не работает."
            ),
        )
        self._mkbtn(
            steps_row, "Frontend install", cmd_frontend_install, variant="gray",
            tooltip="npm install в frontend/. Один раз после git clone\nили при изменении package.json",
        )
        self._mkbtn(
            steps_row, "Frontend build", cmd_frontend_build, variant="gray",
            tooltip="npm run build во frontend/ → frontend/dist\n(Vite production build React)",
        )
        self._mkbtn(
            steps_row, "PyInstaller backend", cmd_pyinstaller_backend, variant="gray",
            tooltip=(
                "Собрать backend.exe через PyInstaller (~3 мин).\n"
                "Результат: build/dist/nastya-backend.exe (~75 МБ)\n"
                "Включает: FastAPI, markitdown, SQLite, встроенный frontend/dist"
            ),
        )
        self._mkbtn(
            steps_row, "PyInstaller worker", cmd_pyinstaller_worker, variant="gray",
            tooltip=(
                "Собрать worker.exe через PyInstaller (~2 мин).\n"
                "Результат: build/dist/nastya-worker.exe (~70 МБ)\n"
                "Включает: httpx poller, tools/codex-npx.cmd wrapper"
            ),
        )
        self._mkbtn(
            steps_row, "Copy sidecars", cmd_copy_sidecars, variant="gray",
            tooltip=(
                "Копирует build/dist/nastya-backend.exe и worker.exe\n"
                "в src-tauri/binaries/ с platform-суффиксом\n"
                "(-x86_64-pc-windows-msvc), как того требует Tauri sidecar API"
            ),
        )
        self._mkbtn(
            steps_row, "Tauri build", cmd_tauri_build, variant="gray",
            tooltip=(
                "cargo tauri build — только Tauri часть.\n"
                "Использует готовые backend/worker из src-tauri/binaries/.\n"
                "Быстрее Full Build в 2-3 раза (~2-3 мин с кешем).\n\n"
                "Используй когда менял только Rust код в src-tauri/src/\n"
                "или tauri.conf.json, а backend/worker не трогал."
            ),
        )

        # ╔══════════════════════════════════════════════╗
        # ║  SECTION 3 — DIAGNOSTIC + UTILS              ║
        # ╚══════════════════════════════════════════════╝
        diag_panel = ctk.CTkFrame(
            self.root, corner_radius=12, fg_color=C_PANEL, border_width=1, border_color=C_BORDER
        )
        diag_panel.grid(row=3, column=0, sticky="new", padx=14, pady=(6, 0))

        # ╔══════════════════════════════════════════════╗
        # ║  SECTION 4 — REMOTE UPDATES (GitHub)         ║
        # ╚══════════════════════════════════════════════╝
        remote_panel = ctk.CTkFrame(
            self.root, corner_radius=12, fg_color=C_PANEL,
            border_width=1, border_color=C_BORDER,
        )
        remote_panel.grid(row=4, column=0, sticky="new", padx=14, pady=(6, 0))

        self._section_title(remote_panel, "🌐  Remote updates (GitHub config)")
        remote_row = ctk.CTkFrame(remote_panel, fg_color="transparent")
        remote_row.pack(fill="x", padx=14, pady=(0, 14))

        self._mkbtn(
            remote_row, "📝  Edit config", cmd_remote_config_edit, variant="blue",
            tooltip=(
                "Открыть редактор для remote-config.json.\n"
                "Содержимое: header_emoji, версия, notification_message.\n\n"
                "После правки жми 'Save & push' — изменения\n"
                "автоматически коммитятся и пушатся в master,\n"
                "Настя получит обновление при следующем запуске\n"
                "или в течение 5 минут (backend background refresh)."
            ),
        )
        self._mkbtn(
            remote_row, "🚀  Push config", cmd_remote_config_push, variant="green",
            tooltip=(
                "Git commit + push remote-config.json в master.\n\n"
                "Выполняет:\n"
                "  git add remote-config.json\n"
                "  git commit -m 'chore: update remote config'\n"
                "  git push origin master\n\n"
                "После push у Насти в течение 5 минут\n"
                "backend подтянет новую версию → SSE → всплывашка."
            ),
        )
        self._mkbtn(
            remote_row, "🔄  Force refresh", cmd_remote_config_refresh, variant="blue",
            tooltip=(
                "Принудительно заставить локальный backend\n"
                "перечитать remote-config с GitHub сейчас.\n\n"
                "POST /api/system/remote-config/refresh\n\n"
                "Использовать когда Дима уже запушил новый config\n"
                "и хочет проверить что он прилетел без ожидания\n"
                "5 минут фонового refresh."
            ),
        )
        self._mkbtn(
            remote_row, "👁  Show current", cmd_remote_config_show, variant="gray",
            tooltip=(
                "Показать в логе текущий remote-config который\n"
                "backend видит прямо сейчас.\n\n"
                "GET /api/system/remote-config\n\n"
                "Полезно проверить что приложение подхватило\n"
                "последнюю версию."
            ),
        )

        # ╔══════════════════════════════════════════════╗
        # ║  SECTION 5 — RELEASE (CI — signed updates)   ║
        # ╚══════════════════════════════════════════════╝
        release_panel = ctk.CTkFrame(
            self.root, corner_radius=12, fg_color=C_PANEL,
            border_width=1, border_color=C_BORDER,
        )
        release_panel.grid(row=5, column=0, sticky="new", padx=14, pady=(6, 0))

        self._section_title(
            release_panel,
            "🚀  Release (CI — signed updates для Насти через Tauri Updater)",
        )
        release_row = ctk.CTkFrame(release_panel, fg_color="transparent")
        release_row.pack(fill="x", padx=14, pady=(0, 14))

        self._mkbtn(
            release_row, "🚀  Release", cmd_release_full, variant="green",
            tooltip=(
                "Полный релизный workflow:\n"
                "  1. Диалог ввода новой версии (предложит +1 patch)\n"
                "  2. Прогон pytest — если красное, релиз отменяется\n"
                "  3. Bump версии в Cargo.toml, tauri.conf.json, backend/core/config.py\n"
                "  4. git add -A + commit -m 'release: vX.Y.Z'\n"
                "  5. git tag vX.Y.Z\n"
                "  6. git push master + push tag → триггерит CI\n"
                "  7. Открывает GitHub Actions в браузере\n\n"
                "Требуется env GITHUB_PAT (contents:write) — ставится в dev-gui.env.bat.\n\n"
                "После зелёного ✓ CI публикует Release, Tauri Updater у Насти\n"
                "автоматически подхватит обновление в течение часа."
            ),
        )
        self._mkbtn(
            release_row, "📤  Push code", cmd_push_code_only, variant="blue",
            tooltip=(
                "Только git push origin master (без bump/tag/CI).\n\n"
                "Используй когда есть локальные коммиты которые\n"
                "нужно отправить в remote, но релизить рано —\n"
                "например промежуточный WIP коммит.\n\n"
                "Требуется env GITHUB_PAT (contents:write)."
            ),
        )
        self._mkbtn(
            release_row, "👁  Watch CI", cmd_open_actions, variant="gray",
            tooltip=(
                "Открывает страницу GitHub Actions репо в браузере.\n"
                "Там можно посмотреть статус текущего/последнего билда:\n"
                "  🟢 зелёный — CI собрал installer, Release опубликован\n"
                "  🔴 красный — смотри лог, чиним, перезапускаем tag\n\n"
                "URL: github.com/.../actions"
            ),
        )
        self._mkbtn(
            release_row, "📦  Releases", cmd_open_releases, variant="gray",
            tooltip=(
                "Открывает страницу GitHub Releases.\n"
                "Там лежат все опубликованные версии с артефактами:\n"
                "  • *.exe (installer)\n"
                "  • *.nsis.zip + *.nsis.zip.sig (для Tauri Updater)\n"
                "  • latest.json (манифест для updater endpoint)\n"
                "  • SHA256SUMS.txt (контрольные суммы)\n\n"
                "URL: github.com/.../releases"
            ),
        )

        self._section_title(diag_panel, "🔍  Диагностика и утилиты")
        diag_row = ctk.CTkFrame(diag_panel, fg_color="transparent")
        diag_row.pack(fill="x", padx=14, pady=(0, 14))

        self._mkbtn(
            diag_row, "🐛  Debug worker", cmd_debug_worker, variant="orange",
            tooltip=(
                "Запускает worker НЕ из frozen .exe, а из исходников Python\n"
                "с полным stdout логом (PYTHONUNBUFFERED=1).\n\n"
                "Используй когда frozen worker молча падает —\n"
                "debug worker покажет полный traceback в cmd окне\n"
                "и запишет лог в worker-debug.log."
            ),
        )
        self._mkbtn(
            diag_row, "📁  Открыть проект", cmd_open_project_folder, variant="gray",
            tooltip="Открыть проводник в D:\\Share\\nastyaorc",
        )
        self._mkbtn(
            diag_row, "🗑  Uninstall", cmd_uninstall_app, variant="orange",
            tooltip=(
                "Запустить uninstall.exe из папки установки.\n\n"
                "Сначала убивает все nastya-* процессы (иначе\n"
                "uninstaller не сможет удалить занятые файлы),\n"
                "затем запускает NSIS uninstaller из первого найденного:\n"
                "  • %LOCALAPPDATA%\\Programs\\Nastya Orchestrator\n"
                "  • %USERPROFILE%\\Desktop\\Nastya Orchestrator\n"
                "  • D:\\Programs\\Nastya Orchestrator\n\n"
                "Полезно перед тестом чистой установки свежего билда.\n\n"
                "⚠️ NSIS НЕ удаляет папку data/ — используй Clean data отдельно."
            ),
        )
        self._mkbtn(
            diag_row, "🧹  Clean data", cmd_clean_data, variant="orange",
            tooltip=(
                "Удалить папку data/ из всех install-путей.\n\n"
                "Содержимое:\n"
                "  • data/nastya.db — SQLite БД (проекты, задачи, чат, app_settings)\n"
                "  • data/documents/ — загруженные PDF/Excel/документы\n\n"
                "NSIS uninstall НЕ трогает data/ — эта папка создаётся backend-ом\n"
                "при первом запуске и содержит пользовательские данные.\n\n"
                "Используй для ЧИСТОГО старта (как у Насти впервые):\n"
                "  1. Убивает все процессы (SQLite файл залочен при работе)\n"
                "  2. Удаляет data/ целиком\n"
                "  3. При следующем запуске backend создаст новую БД с дефолтами\n\n"
                "⚠️ Необратимо. Все тестовые проекты/чаты/документы потеряются."
            ),
        )

        # Clear log перенесён в toolbar над log panel

        self.stop_btn = ctk.CTkButton(
            diag_row,
            text="⬛  STOP running",
            height=38,
            width=160,
            font=F_BTN,
            fg_color=C_BTN_RED,
            hover_color=C_BTN_RED_HOVER,
            text_color=C_BTN_RED_TEXT,
            corner_radius=8,
            state="disabled",
            command=self._stop_current,
        )
        self.stop_btn.pack(side="left", padx=5)
        Tooltip(
            self.stop_btn,
            "Убить текущий запущенный subprocess (если команда зависла).\n"
            "Активна только пока что-то выполняется.",
        )

        # ╔══════════════════════════════════════════════╗
        # ║  LOG — большой textbox                       ║
        # ╚══════════════════════════════════════════════╝
        # Лог справа: row=1..3 через rowspan, занимает всю вертикаль
        log_panel = ctk.CTkFrame(
            self.root, corner_radius=12, fg_color=C_PANEL, border_width=1, border_color=C_BORDER
        )
        log_panel.grid(row=1, column=1, rowspan=6, sticky="nsew", padx=(0, 14), pady=6)
        log_panel.grid_rowconfigure(1, weight=1)
        log_panel.grid_columnconfigure(0, weight=1)

        header_log = ctk.CTkFrame(log_panel, fg_color="transparent")
        header_log.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        ctk.CTkLabel(
            header_log, text="📋  Вывод команд", font=F_SECTION, text_color=C_TEXT
        ).pack(side="left")

        # Toolbar на правой стороне log-header: zoom, wrap, copy-all, clear
        toolbar = ctk.CTkFrame(header_log, fg_color="transparent")
        toolbar.pack(side="right")

        def _mk_tb(txt: str, cmd, tooltip: str, width: int = 40) -> ctk.CTkButton:
            b = ctk.CTkButton(
                toolbar,
                text=txt,
                width=width,
                height=30,
                font=("Segoe UI", 11, "bold"),
                fg_color=C_BTN_GRAY,
                hover_color=C_BTN_GRAY_HOVER,
                text_color=C_BTN_GRAY_TEXT,
                corner_radius=6,
                command=cmd,
            )
            b.pack(side="left", padx=2)
            Tooltip(b, tooltip)
            return b

        _mk_tb("🔍", self._open_find_dialog, "Поиск в логе (Ctrl+F)\n  • Enter/F3 = next\n  • Shift+F3 = prev\n  • Esc = закрыть")
        _mk_tb("A−", self._log_zoom_out, "Уменьшить шрифт лога (Ctrl+minus, Ctrl+wheel)")
        _mk_tb("A+", self._log_zoom_in, "Увеличить шрифт лога (Ctrl+plus, Ctrl+wheel)")
        _mk_tb("A⟲", self._log_zoom_reset, "Сбросить размер шрифта (Ctrl+0)")
        self._wrap_btn = _mk_tb(
            "↵ Wrap", self._log_toggle_wrap,
            "Переключить word wrap (перенос длинных строк)",
            width=80,
        )
        _mk_tb(
            "📋 All",
            self._log_copy_all,
            "Скопировать весь лог в буфер обмена",
            width=70,
        )
        _mk_tb(
            "🧹",
            self._clear_log,
            "Очистить лог (и файл dev-gui.log)",
        )

        self.log = ctk.CTkTextbox(
            log_panel,
            font=("Consolas", self._log_font_size),
            fg_color=C_LOG_BG,
            text_color=C_LOG_FG,
            border_width=1,
            border_color=C_BORDER,
            corner_radius=8,
            wrap=self._log_wrap,
        )
        self.log.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        inner = self.log._textbox  # type: ignore[attr-defined]
        inner.tag_config("info", foreground=C_LOG_FG)
        inner.tag_config("cmd", foreground=C_LOG_CMD, font=F_LOG_BOLD)
        inner.tag_config("ok", foreground=C_LOG_OK, font=F_LOG_BOLD)
        inner.tag_config("err", foreground=C_LOG_ERR, font=F_LOG_BOLD)
        inner.tag_config("warn", foreground=C_LOG_WARN)

        # Read-only режим — блокируем все клавиши кроме копирования и навигации.
        # Поддерживаем И латиницу И кириллицу (Ctrl+С в русской раскладке).
        # Когда раскладка русская, keysym приходит как Cyrillic_XX. Маппинг:
        #   C → Cyrillic_es   (с)
        #   A → Cyrillic_ef   (ф)
        #   V → Cyrillic_em   (м)
        #   X → Cyrillic_che  (ч)
        COPY_KEYSYMS = {"c", "C", "Cyrillic_es", "Cyrillic_ES"}
        SELECT_ALL_KEYSYMS = {"a", "A", "Cyrillic_ef", "Cyrillic_EF"}

        def _readonly_keys(event: tk.Event) -> str | None:
            ctrl = (event.state & 0x4) != 0
            if ctrl:
                if event.keysym in COPY_KEYSYMS or event.keysym in SELECT_ALL_KEYSYMS:
                    return None
                if event.keysym in ("Insert",):
                    return None
            # Разрешаем навигацию
            nav = {
                "Up", "Down", "Left", "Right", "Home", "End",
                "Prior", "Next", "Shift_L", "Shift_R", "Control_L",
                "Control_R",
            }
            if event.keysym in nav:
                return None
            return "break"

        inner.bind("<Key>", _readonly_keys)

        # Правый клик — контекстное меню с Copy / Select All / Clear
        ctx_menu = tk.Menu(self.root, tearoff=0, bg="#1e1e1e", fg="#e0e0e0",
                           activebackground=C_BTN_BLUE, activeforeground="#ffffff",
                           font=F_LABEL)
        ctx_menu.add_command(
            label="Copy  (Ctrl+C)",
            command=lambda: inner.event_generate("<<Copy>>"),
        )
        ctx_menu.add_command(
            label="Select All  (Ctrl+A)",
            command=lambda: (inner.tag_add("sel", "1.0", "end"), "break"),
        )
        ctx_menu.add_separator()
        ctx_menu.add_command(label="Clear log", command=self._clear_log)

        def _show_ctx(event: tk.Event) -> None:
            try:
                ctx_menu.tk_popup(event.x_root, event.y_root)
            finally:
                ctx_menu.grab_release()

        inner.bind("<Button-3>", _show_ctx)

        # Ctrl+A — select all (перехватываем до read-only-handler)
        def _select_all(event: tk.Event) -> str:
            inner.tag_add("sel", "1.0", "end-1c")
            inner.mark_set("insert", "1.0")
            inner.see("insert")
            return "break"

        inner.bind("<Control-a>", _select_all)
        inner.bind("<Control-A>", _select_all)
        # Кириллический Ctrl+Ф (keysym Cyrillic_ef) → тоже select all
        inner.bind("<Control-KeyPress-Cyrillic_ef>", _select_all)
        inner.bind("<Control-KeyPress-Cyrillic_EF>", _select_all)

        # Ctrl+C — copy (Ctrl+С, кириллическая, работает через generic handler,
        # но явно дублируем событие для надёжности)
        def _copy_ru(_event: tk.Event) -> str:
            inner.event_generate("<<Copy>>")
            return "break"

        inner.bind("<Control-KeyPress-Cyrillic_es>", _copy_ru)
        inner.bind("<Control-KeyPress-Cyrillic_ES>", _copy_ru)

        # Zoom shortcuts (Ctrl+plus/minus/0/wheel) для лога
        self._bind_log_shortcuts()

        # Ctrl+F — поиск в логе (диалог Find)
        inner.bind("<Control-f>", lambda e: (self._open_find_dialog(), "break")[1])
        inner.bind("<Control-F>", lambda e: (self._open_find_dialog(), "break")[1])
        # Кириллическая А на клавише F (keysym Cyrillic_a)
        inner.bind("<Control-KeyPress-Cyrillic_a>", lambda e: (self._open_find_dialog(), "break")[1])

        # Глобальные hero-хоткеи (F5/F6/F8/Ctrl+L)
        self.root.bind(
            "<F5>", lambda e: self._run_command("F5: ⚡ FULL BUILD", cmd_full_build())
        )
        self.root.bind(
            "<F6>", lambda e: self._run_command("F6: 🚀 Tauri dev", cmd_tauri_dev())
        )
        self.root.bind(
            "<F8>", lambda e: self._run_command("F8: ⬛ Kill all", cmd_kill_processes())
        )
        self.root.bind("<Control-l>", lambda e: (self._clear_log(), "break")[1])
        self.root.bind("<Control-L>", lambda e: (self._clear_log(), "break")[1])

        # ╔══════════════════════════════════════════════╗
        # ║  STATUS BAR снизу                            ║
        # ╚══════════════════════════════════════════════╝
        self.busy_label = ctk.CTkLabel(
            self.root,
            text="● Idle",
            anchor="w",
            font=F_LABEL_BOLD,
            text_color=C_TEXT_MUTED,
            fg_color=C_PANEL_ALT,
            corner_radius=8,
            height=30,
        )
        self.busy_label.grid(
            row=6, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 14)
        )

    def _section_title(self, parent: ctk.CTkFrame, text: str) -> None:
        ctk.CTkLabel(
            parent, text=text, font=F_SECTION, text_color=C_TEXT, anchor="w"
        ).pack(anchor="w", padx=18, pady=(12, 8), fill="x")

    def _mkbtn(
        self,
        parent: ctk.CTkBaseClass,
        text: str,
        cmd_factory,
        variant: str = "gray",
        hero: bool = False,
        tooltip: str | None = None,
    ) -> ctk.CTkButton:
        palette = {
            "gray": (C_BTN_GRAY, C_BTN_GRAY_HOVER, C_BTN_GRAY_TEXT),
            "blue": (C_BTN_BLUE, C_BTN_BLUE_HOVER, C_BTN_BLUE_TEXT),
            "green": (C_BTN_GREEN, C_BTN_GREEN_HOVER, C_BTN_GREEN_TEXT),
            "orange": (C_BTN_ORANGE, C_BTN_ORANGE_HOVER, C_BTN_ORANGE_TEXT),
            "red": (C_BTN_RED, C_BTN_RED_HOVER, C_BTN_RED_TEXT),
        }
        fg, hover, text_c = palette[variant]

        btn = ctk.CTkButton(
            parent,
            text=text,
            height=48 if hero else 38,
            width=230 if hero else 170,
            font=F_BTN_HERO if hero else F_BTN,
            fg_color=fg,
            hover_color=hover,
            text_color=text_c,
            corner_radius=10 if hero else 8,
            command=lambda: self._run_command(text.strip(), cmd_factory()),
        )
        btn.pack(side="left", padx=(0, 8), pady=2)
        if tooltip:
            Tooltip(btn, tooltip)
        return btn

    # ───── Команды ────────────────────────────────────────────────────

    def _run_command(self, label: str, cmd: list[str]) -> None:
        if self.current_proc and self.current_proc.poll() is None:
            self._write_log(
                f"[dev-gui] Busy: '{label}' skipped (another task running)\n", "warn"
            )
            return
        self._stop_flag.clear()
        self._write_log(f"\n━━━  {label}  ━━━\n", "cmd")

        # Sentinel: действие целиком на чистом Python, без cmd/ps escape
        if cmd and cmd[0] == "__python__":
            action = cmd[1] if len(cmd) > 1 else ""
            self.busy_label.configure(text=f"▸ Running: {label}", text_color=C_LOG_OK)
            self.stop_btn.configure(state="normal")
            threading.Thread(
                target=self._run_python_action,
                args=(label, action),
                daemon=True,
            ).start()
            return

        quoted = " ".join(shlex.quote(c) if " " in c else c for c in cmd)
        self._write_log(f"$ {quoted}\n", "cmd")
        self.busy_label.configure(text=f"▸ Running: {label}", text_color=C_LOG_OK)
        self.stop_btn.configure(state="normal")

        threading.Thread(
            target=self._run_in_thread, args=(label, cmd), daemon=True
        ).start()

    # ───── Python-based actions (без cmd/powershell escape hell) ─────

    def _run_python_action(self, label: str, action: str) -> None:
        start = time.time()
        try:
            if action == "restart_app":
                self._python_action_restart_app()
            elif action == "uninstall_app":
                self._python_action_uninstall_app()
            elif action == "clean_data":
                self._python_action_clean_data()
            elif action == "remote_config_show":
                self._python_action_remote_config_show()
            elif action == "remote_config_edit":
                self._python_action_remote_config_edit()
            elif action == "remote_config_push":
                self._python_action_remote_config_push()
            elif action == "remote_config_refresh":
                self._python_action_remote_config_refresh()
            elif action == "release_full":
                self._python_action_release_full()
            elif action == "push_code_only":
                self._python_action_push_code_only()
            elif action == "open_actions":
                self._python_action_open_actions()
            elif action == "open_releases":
                self._python_action_open_releases()
            else:
                self.output_queue.put(
                    f"[dev-gui] unknown python action: {action}\n"
                )
        except Exception as exc:
            self.output_queue.put(f"[dev-gui] ERROR in {action}: {exc}\n")
        finally:
            elapsed = time.time() - start
            self.output_queue.put(
                f"[dev-gui] {label}: exit 0 in {elapsed:.1f}s\n\x00ok"
            )
            self.output_queue.put("__DONE__")

    def _taskkill_all_known(self) -> None:
        """Убить все процессы приложения. Вывод в output_queue."""
        names = [
            "nastya-backend.exe",
            "nastya-worker.exe",
            "nastya-orchestrator.exe",
            "opera-proxy.exe",
            "opera-proxy-x86_64-pc-windows-msvc.exe",
        ]
        self.output_queue.put("[action] killing processes...\n")
        args = ["taskkill", "/F"]
        for n in names:
            args.extend(["/IM", n])
        try:
            r = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="oem",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            all_out = (r.stdout or "") + (r.stderr or "")
            for line in all_out.splitlines():
                line = line.strip()
                if not line:
                    continue
                low = line.lower()
                # Скрываем "процесс не найден" — не ошибка
                if "не найден" in low or "not found" in low or "не запущ" in low:
                    continue
                self.output_queue.put(f"  {line}\n")
        except Exception as exc:
            self.output_queue.put(f"[action] taskkill error: {exc}\n")
        # Даём OS секунду чтобы освободить file handles
        time.sleep(1.2)

    def _find_installed_exe(self, exe_name: str) -> Path | None:
        for p in _install_paths():
            candidate = Path(p) / exe_name
            if candidate.is_file():
                return candidate
        return None

    def _launch_detached(self, exe: Path) -> None:
        """Запуск exe в отдельном процессе, без наследования stdin/stdout."""
        flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
        subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )

    def _python_action_restart_app(self) -> None:
        self._taskkill_all_known()
        target = self._find_installed_exe("nastya-orchestrator.exe")
        if target is None:
            self.output_queue.put(
                "[restart] ERROR: nastya-orchestrator.exe not found in any install path\n"
            )
            return
        try:
            self._launch_detached(target)
            self.output_queue.put(f"[restart] launched: {target}\n")
        except Exception as exc:
            self.output_queue.put(f"[restart] launch failed: {exc}\n")

    def _python_action_uninstall_app(self) -> None:
        self._taskkill_all_known()
        target = self._find_installed_exe("uninstall.exe")
        if target is None:
            self.output_queue.put(
                "[uninstall] ERROR: uninstall.exe not found in any install path\n"
            )
            return
        try:
            self._launch_detached(target)
            self.output_queue.put(f"[uninstall] launched: {target}\n")
        except Exception as exc:
            self.output_queue.put(f"[uninstall] launch failed: {exc}\n")

    # ───── Remote config actions ────────────────────────────────────

    REMOTE_CONFIG_FILE = "remote-config.json"

    def _python_action_remote_config_show(self) -> None:
        """Запрашивает у backend текущее состояние remote config."""
        import json as _json
        import urllib.request
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8781/api/system/remote-config", timeout=5
            ) as r:
                data = _json.loads(r.read().decode("utf-8"))
            pretty = _json.dumps(data, indent=2, ensure_ascii=False)
            self.output_queue.put("[remote] current backend state:\n")
            for line in pretty.splitlines():
                self.output_queue.put(f"  {line}\n")
            if not data:
                self.output_queue.put(
                    "[remote] WARN: empty — GITHUB_PAT не задан или fetch упал\n"
                )
        except Exception as exc:
            self.output_queue.put(f"[remote] GET failed: {exc}\n")

    def _python_action_remote_config_refresh(self) -> None:
        """Форсирует backend перечитать remote config прямо сейчас."""
        import json as _json
        import urllib.request

        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8781/api/system/remote-config/refresh",
                method="POST",
                data=b"",
                headers={"Content-Length": "0"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = _json.loads(r.read().decode("utf-8"))
            if data.get("ok"):
                changed = data.get("changed", False)
                self.output_queue.put(
                    f"[remote] refresh OK — {'CHANGED' if changed else 'no changes'}\n"
                )
                if changed:
                    cfg = data.get("config", {})
                    self.output_queue.put(
                        f"  new version: {cfg.get('version', '?')}\n"
                    )
                    self.output_queue.put(
                        f"  emoji: {cfg.get('header_emoji', '-')}\n"
                    )
            else:
                self.output_queue.put(f"[remote] refresh failed: {data}\n")
        except Exception as exc:
            self.output_queue.put(f"[remote] POST refresh failed: {exc}\n")

    # ───── Release workflow (CI + Tauri Updater) ──────────────────────

    def _release_run(self, cmd: list[str], *, sensitive: str = "") -> tuple[int, str]:
        """Запустить subprocess в корне репо, вывод в output_queue.

        sensitive — строка которую нужно маскировать в логе (токен).
        Возвращает (returncode, combined_stdout_stderr).
        """
        try:
            r = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="oem",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=120,
            )
            combined = (r.stdout or "") + (r.stderr or "")
            safe = combined
            if sensitive:
                safe = safe.replace(sensitive, "<TOKEN>")
            for line in safe.splitlines():
                if line.strip():
                    self.output_queue.put(f"  {line}\n")
            return r.returncode, combined
        except subprocess.TimeoutExpired:
            self.output_queue.put(f"[release] timeout: {' '.join(cmd[:3])}...\n")
            return -1, "timeout"
        except Exception as exc:
            self.output_queue.put(f"[release] error: {exc}\n")
            return -1, str(exc)

    def _get_current_tauri_version(self) -> str | None:
        """Читает версию из src-tauri/tauri.conf.json (источник правды для Tauri Updater)."""
        conf = ROOT / "src-tauri" / "tauri.conf.json"
        if not conf.is_file():
            return None
        try:
            import json as _json
            data = _json.loads(conf.read_text(encoding="utf-8"))
            return data.get("version")
        except Exception:
            return None

    def _suggest_next_version(self, current: str) -> str:
        """Bump patch: 0.1.5 → 0.1.6. Если parse failed — возвращает 0.0.1."""
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", current.strip())
        if not m:
            return "0.0.1"
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{major}.{minor}.{patch + 1}"

    def _bump_versions_in_files(self, new_ver: str) -> list[str]:
        """Обновляет версию в 3 файлах. Возвращает список изменённых файлов."""
        changed: list[str] = []

        # 1. src-tauri/Cargo.toml — строка `version = "X.Y.Z"`
        cargo = ROOT / "src-tauri" / "Cargo.toml"
        if cargo.is_file():
            text = cargo.read_text(encoding="utf-8")
            new_text = re.sub(
                r'(^version\s*=\s*")([^"]+)(")',
                rf'\g<1>{new_ver}\g<3>',
                text,
                count=1,
                flags=re.MULTILINE,
            )
            if new_text != text:
                cargo.write_text(new_text, encoding="utf-8")
                changed.append("src-tauri/Cargo.toml")

        # 2. src-tauri/tauri.conf.json — "version": "X.Y.Z"
        tauri_conf = ROOT / "src-tauri" / "tauri.conf.json"
        if tauri_conf.is_file():
            text = tauri_conf.read_text(encoding="utf-8")
            new_text = re.sub(
                r'("version"\s*:\s*")([^"]+)(")',
                rf'\g<1>{new_ver}\g<3>',
                text,
                count=1,
            )
            if new_text != text:
                tauri_conf.write_text(new_text, encoding="utf-8")
                changed.append("src-tauri/tauri.conf.json")

        # 3. backend/core/config.py — APP_VERSION: str = "X.Y.Z"
        cfg_py = ROOT / "backend" / "core" / "config.py"
        if cfg_py.is_file():
            text = cfg_py.read_text(encoding="utf-8")
            new_text = re.sub(
                r'(APP_VERSION\s*:\s*str\s*=\s*")([^"]+)(")',
                rf'\g<1>{new_ver}\g<3>',
                text,
                count=1,
            )
            if new_text != text:
                cfg_py.write_text(new_text, encoding="utf-8")
                changed.append("backend/core/config.py")

        return changed

    def _python_action_release_full(self) -> None:
        """Полный release workflow: bump → pytest → commit → tag → push → open Actions."""
        self.output_queue.put("[release] ═══ Полный релизный workflow ═══\n")

        # 1. Определить текущую версию
        current = self._get_current_tauri_version()
        if not current:
            self.output_queue.put(
                "[release] ERROR: не могу прочитать версию из src-tauri/tauri.conf.json\n"
            )
            return
        suggested = self._suggest_next_version(current)
        self.output_queue.put(
            f"[release] текущая версия: {current} → предлагаемая: {suggested}\n"
        )

        # 2. Диалог ввода новой версии — в main thread через self.root.after
        new_ver_holder: dict[str, str | None] = {"value": None}
        ready = threading.Event()

        def _ask():
            try:
                dlg = ctk.CTkInputDialog(
                    text=f"Текущая версия: {current}\nНовая версия (semver, без 'v'):",
                    title="🚀 Release — выбор версии",
                )
                # Предустановить предложенную версию через внутренний entry
                try:
                    for child in dlg.winfo_children():
                        for sub in child.winfo_children():
                            if isinstance(sub, (ctk.CTkEntry, tk.Entry)):
                                sub.insert(0, suggested)
                                break
                except Exception:
                    pass
                new_ver_holder["value"] = dlg.get_input()
            except Exception as exc:
                self.output_queue.put(f"[release] dialog error: {exc}\n")
            finally:
                ready.set()

        self.root.after(0, _ask)
        ready.wait(timeout=300)  # макс 5 минут на ввод
        new_ver = (new_ver_holder["value"] or "").strip()

        if not new_ver:
            self.output_queue.put("[release] отменено пользователем\n")
            return

        # Валидация формата
        if not re.match(r"^\d+\.\d+\.\d+$", new_ver):
            self.output_queue.put(
                f"[release] ERROR: версия '{new_ver}' не в формате X.Y.Z\n"
            )
            return

        self.output_queue.put(f"[release] выбрана версия v{new_ver}\n")

        # 3. Проверка токена ДО всех правок
        token = self._get_github_token()
        if not token:
            self.output_queue.put(
                "[release] ERROR: env GITHUB_PAT не задан.\n"
                "  Создай dev-gui.env.bat с `set GITHUB_PAT=...` (contents:write)\n"
                "  и перезапусти dev-gui через dev-gui.bat.\n"
            )
            return

        # 4. Прогон pytest (gate — если красное, релиз не едет)
        self.output_queue.put("[release] ─── pytest (smoke) ─────────────\n")
        py = sys.executable or "python"
        rc, _ = self._release_run([py, "-m", "pytest", "-q", "--tb=line"])
        if rc != 0:
            self.output_queue.put(
                "[release] ✗ pytest failed — релиз отменён. Почини тесты и попробуй снова.\n"
            )
            return
        self.output_queue.put("[release] ✓ pytest passed\n")

        # 5. Bump версии
        self.output_queue.put(f"[release] ─── bump версии в {new_ver} ────\n")
        changed_files = self._bump_versions_in_files(new_ver)
        if not changed_files:
            self.output_queue.put(
                f"[release] предупреждение: ни один файл не изменился "
                f"(версия {new_ver} уже везде установлена?)\n"
            )
        else:
            for f in changed_files:
                self.output_queue.put(f"  bumped: {f}\n")

        # 6. git add -A
        self.output_queue.put("[release] ─── git add -A ────────────────\n")
        rc, _ = self._release_run(["git", "add", "-A"])
        if rc != 0:
            self.output_queue.put("[release] ✗ git add failed — stop\n")
            return

        # 7. git commit (может быть 0 изменений если Дима уже всё закоммитил)
        self.output_queue.put("[release] ─── git commit ─────────────────\n")
        commit_msg = f"release: v{new_ver}"
        rc, out = self._release_run(["git", "commit", "-m", commit_msg])
        if rc != 0 and "nothing to commit" not in out:
            self.output_queue.put("[release] ✗ git commit failed — stop\n")
            return
        if "nothing to commit" in out:
            self.output_queue.put("[release] ничего коммитить — уже чисто\n")

        # 8. git tag
        self.output_queue.put(f"[release] ─── git tag v{new_ver} ────────\n")
        tag_name = f"v{new_ver}"
        rc, out = self._release_run(["git", "tag", tag_name])
        if rc != 0 and "already exists" not in out:
            self.output_queue.put(
                f"[release] ✗ git tag failed — возможно тег уже существует. "
                f"Используй другую версию.\n"
            )
            return

        # 9. git push master + push tag через HTTPS+token
        repo_url = f"https://{token}@github.com/{RELEASE_REPO_SLUG}.git"
        self.output_queue.put("[release] ─── git push origin master ─────\n")
        rc, _ = self._release_run(
            ["git", "push", repo_url, "master"], sensitive=token
        )
        if rc != 0:
            self.output_queue.put("[release] ✗ git push master failed — stop\n")
            return

        self.output_queue.put(f"[release] ─── git push tag {tag_name} ────\n")
        rc, _ = self._release_run(
            ["git", "push", repo_url, tag_name], sensitive=token
        )
        if rc != 0:
            self.output_queue.put("[release] ✗ git push tag failed — stop\n")
            return

        # 10. Открыть Actions в браузере
        self.output_queue.put(
            f"[release] ✓ v{new_ver} запушен — CI стартует\n"
            f"[release] открываю GitHub Actions...\n"
        )
        try:
            webbrowser.open(f"{RELEASE_REPO_URL}/actions")
        except Exception as exc:
            self.output_queue.put(f"[release] webbrowser.open failed: {exc}\n")

        self.output_queue.put(
            "[release] ═══ Готово. Жди ~10 минут, потом кликни Releases. ═══\n"
        )

    def _python_action_push_code_only(self) -> None:
        """Просто git push origin master (без bump/tag/CI)."""
        token = self._get_github_token()
        if not token:
            self.output_queue.put(
                "[push] ERROR: env GITHUB_PAT не задан. "
                "Создай dev-gui.env.bat и перезапусти dev-gui.\n"
            )
            return

        self.output_queue.put("[push] git push origin master...\n")
        repo_url = f"https://{token}@github.com/{RELEASE_REPO_SLUG}.git"
        rc, _ = self._release_run(
            ["git", "push", repo_url, "master"], sensitive=token
        )
        if rc == 0:
            self.output_queue.put("[push] ✓ pushed\n")
        else:
            self.output_queue.put("[push] ✗ push failed (см. лог выше)\n")

    def _python_action_open_actions(self) -> None:
        """Открывает https://github.com/.../actions в браузере."""
        url = f"{RELEASE_REPO_URL}/actions"
        self.output_queue.put(f"[ci] open {url}\n")
        try:
            webbrowser.open(url)
        except Exception as exc:
            self.output_queue.put(f"[ci] webbrowser.open failed: {exc}\n")

    def _python_action_open_releases(self) -> None:
        """Открывает https://github.com/.../releases в браузере."""
        url = f"{RELEASE_REPO_URL}/releases"
        self.output_queue.put(f"[ci] open {url}\n")
        try:
            webbrowser.open(url)
        except Exception as exc:
            self.output_queue.put(f"[ci] webbrowser.open failed: {exc}\n")

    def _python_action_remote_config_push(self) -> None:
        """git add + commit + push remote-config.json в master."""
        cfg_path = ROOT / self.REMOTE_CONFIG_FILE
        if not cfg_path.is_file():
            self.output_queue.put(
                f"[remote] ERROR: {self.REMOTE_CONFIG_FILE} not found at {cfg_path}\n"
            )
            return

        # 1. git status
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain", self.REMOTE_CONFIG_FILE],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="oem",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if not status.stdout.strip():
                self.output_queue.put(
                    f"[remote] no changes in {self.REMOTE_CONFIG_FILE}, "
                    f"nothing to push\n"
                )
                return
            self.output_queue.put(f"[remote] status: {status.stdout.strip()}\n")
        except Exception as exc:
            self.output_queue.put(f"[remote] git status error: {exc}\n")
            return

        # 2. git add
        try:
            r = subprocess.run(
                ["git", "add", self.REMOTE_CONFIG_FILE],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="oem",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if r.returncode != 0:
                self.output_queue.put(f"[remote] git add failed: {r.stderr}\n")
                return
        except Exception as exc:
            self.output_queue.put(f"[remote] git add error: {exc}\n")
            return

        # 3. git commit
        from datetime import datetime
        msg = f"chore: update remote config ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
        try:
            r = subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="oem",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in (r.stdout + r.stderr).splitlines():
                if line.strip():
                    self.output_queue.put(f"  {line}\n")
            if r.returncode != 0:
                self.output_queue.put(f"[remote] git commit failed\n")
                return
        except Exception as exc:
            self.output_queue.put(f"[remote] git commit error: {exc}\n")
            return

        # 4. git push через HTTPS+token (SSH-ключ для bot-аккаунта
        # ativubise657-boop не настроен в Windows, поэтому берём токен
        # из env GITHUB_PAT и пушим напрямую).
        # Нужен fine-grained PAT с contents:write на репо nastyaorchestrator.
        self.output_queue.put("[remote] pushing to origin/master via HTTPS+token...\n")
        token = self._get_github_token()
        if not token:
            self.output_queue.put(
                "[remote] ERROR: env GITHUB_PAT не задан. "
                "Создай fine-grained PAT (contents:write) и установи "
                "`set GITHUB_PAT=...` перед запуском dev-gui.bat\n"
            )
            return
        # Явный URL с токеном — не меняем origin, одноразовый push
        repo_https_url = (
            f"https://{token}@github.com/"
            "ativubise657-boop/nastyaorchestrator.git"
        )
        try:
            r = subprocess.run(
                ["git", "push", repo_https_url, "master"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="oem",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=60,
            )
            # Вывод git скрываем токен если он вдруг попал в stderr
            safe_lines = (
                (r.stdout + r.stderr)
                .replace(token, "<TOKEN>")
                .replace(repo_https_url, "https://<TOKEN>@github.com/.../nastyaorchestrator.git")
            )
            for line in safe_lines.splitlines():
                if line.strip():
                    self.output_queue.put(f"  {line}\n")
            if r.returncode == 0:
                self.output_queue.put("[remote] ✓ pushed to master\n")
                self.output_queue.put(
                    "[remote] Настя получит обновление в течение 5 минут "
                    "(или после перезапуска приложения)\n"
                )
            else:
                self.output_queue.put(f"[remote] git push failed\n")
        except subprocess.TimeoutExpired:
            self.output_queue.put(
                "[remote] git push timeout — проверь прокси и токен\n"
            )
        except Exception as exc:
            self.output_queue.put(f"[remote] git push error: {exc}\n")

    def _get_github_token(self) -> str:
        """Получаем токен из env GITHUB_PAT (для Push config — нужен contents:write)."""
        return os.environ.get("GITHUB_PAT", "")

    def _python_action_remote_config_edit(self) -> None:
        """Открывает модальное окно с редактором JSON."""
        # UI должен запускаться в main thread — используем root.after
        self.root.after(0, self._open_remote_config_editor)

    def _open_remote_config_editor(self) -> None:
        """Модальное окно редактирования remote-config.json."""
        cfg_path = ROOT / self.REMOTE_CONFIG_FILE
        try:
            content = cfg_path.read_text(encoding="utf-8") if cfg_path.is_file() else "{\n  \n}"
        except Exception as exc:
            self._write_log(f"[remote] cannot read config: {exc}\n", "err")
            return

        win = ctk.CTkToplevel(self.root)
        win.title("Edit remote-config.json")
        win.geometry("720x600")
        win.transient(self.root)
        try:
            win.grab_set()
        except Exception:
            pass
        win.configure(fg_color=C_BG)

        # Header
        header = ctk.CTkFrame(win, fg_color=C_PANEL, corner_radius=10)
        header.pack(fill="x", padx=14, pady=(14, 6))
        ctk.CTkLabel(
            header,
            text=f"📝  {self.REMOTE_CONFIG_FILE}",
            font=F_SECTION,
            text_color=C_TEXT,
        ).pack(side="left", padx=14, pady=10)
        ctk.CTkLabel(
            header,
            text=str(cfg_path),
            font=F_LABEL,
            text_color=C_TEXT_MUTED,
        ).pack(side="left", padx=(0, 14), pady=10)

        # Editor textbox
        editor = ctk.CTkTextbox(
            win,
            font=("Consolas", 13),
            fg_color=C_LOG_BG,
            text_color=C_LOG_FG,
            wrap="none",
            corner_radius=8,
        )
        editor.pack(fill="both", expand=True, padx=14, pady=6)
        editor.insert("1.0", content)

        # Status label for JSON validation
        status = ctk.CTkLabel(
            win, text="", font=F_LABEL, text_color=C_TEXT_MUTED, anchor="w"
        )
        status.pack(fill="x", padx=18, pady=(0, 4))

        # Action buttons
        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(6, 14))

        def validate() -> bool:
            import json as _json
            try:
                _json.loads(editor.get("1.0", "end-1c"))
                status.configure(text="✓ Valid JSON", text_color=C_LOG_OK)
                return True
            except _json.JSONDecodeError as exc:
                status.configure(text=f"✗ JSON error: {exc}", text_color=C_LOG_ERR)
                return False

        def on_save() -> None:
            if not validate():
                return
            try:
                cfg_path.write_text(
                    editor.get("1.0", "end-1c"), encoding="utf-8"
                )
                self._write_log(
                    f"[remote] saved {self.REMOTE_CONFIG_FILE}\n", "ok"
                )
                win.destroy()
            except Exception as exc:
                self._write_log(f"[remote] save failed: {exc}\n", "err")

        def on_save_and_push() -> None:
            if not validate():
                return
            try:
                cfg_path.write_text(
                    editor.get("1.0", "end-1c"), encoding="utf-8"
                )
            except Exception as exc:
                self._write_log(f"[remote] save failed: {exc}\n", "err")
                return
            win.destroy()
            # Запускаем push в background thread (как обычные команды)
            self._run_command(
                "🚀  Save & push config", cmd_remote_config_push()
            )

        ctk.CTkButton(
            footer, text="Validate JSON", width=130, height=36,
            font=F_BTN, fg_color=C_BTN_GRAY, hover_color=C_BTN_GRAY_HOVER,
            text_color=C_BTN_GRAY_TEXT, command=validate,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            footer, text="💾  Save", width=120, height=36,
            font=F_BTN, fg_color=C_BTN_BLUE, hover_color=C_BTN_BLUE_HOVER,
            text_color=C_BTN_BLUE_TEXT, command=on_save,
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            footer, text="🚀  Save & push", width=160, height=36,
            font=F_BTN, fg_color=C_BTN_GREEN, hover_color=C_BTN_GREEN_HOVER,
            text_color=C_BTN_GREEN_TEXT, command=on_save_and_push,
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            footer, text="Cancel", width=100, height=36,
            font=F_BTN, fg_color=C_BTN_GRAY, hover_color=C_BTN_GRAY_HOVER,
            text_color=C_BTN_GRAY_TEXT, command=win.destroy,
        ).pack(side="right", padx=4)

        # Валидация на каждое изменение (с debounce через after)
        inner_editor = editor._textbox  # type: ignore[attr-defined]
        validate_after_id = [None]

        def on_change(_event=None):
            if validate_after_id[0]:
                win.after_cancel(validate_after_id[0])
            validate_after_id[0] = win.after(400, validate)

        inner_editor.bind("<KeyRelease>", on_change)

        validate()  # Первичная валидация
        editor.focus_set()

    def _python_action_clean_data(self) -> None:
        """Удалить папку data/ из всех install-путей (чистый старт)."""
        import shutil

        # Обязательно сначала убить процессы — иначе SQLite файл залочен
        self._taskkill_all_known()

        removed_any = False
        for p in _install_paths():
            data_dir = Path(p) / "data"
            if not data_dir.exists():
                continue

            # Подсчитаем размер для информации
            total_size = 0
            file_count = 0
            try:
                for f in data_dir.rglob("*"):
                    if f.is_file():
                        total_size += f.stat().st_size
                        file_count += 1
            except Exception:
                pass
            size_mb = total_size / 1024 / 1024

            self.output_queue.put(
                f"[clean-data] removing: {data_dir} "
                f"({file_count} files, {size_mb:.1f} MB)\n"
            )
            try:
                shutil.rmtree(data_dir)
                removed_any = True
                self.output_queue.put(f"  → removed ✓\n")
            except Exception as exc:
                self.output_queue.put(f"  → FAILED: {exc}\n")

        if not removed_any:
            self.output_queue.put("[clean-data] no data/ directories found in known paths\n")
        else:
            self.output_queue.put("[clean-data] Done. Next app launch = clean БД + default proxy settings\n")

    def _run_in_thread(self, label: str, cmd: list[str]) -> None:
        start = time.time()
        try:
            env = dict(os.environ)
            env.setdefault("PYTHONIOENCODING", "utf-8")
            env.setdefault("PYTHONUNBUFFERED", "1")
            # Выбор кодировки по команде:
            #   • debug-worker.bat, local-build.bat (npm/pip/python внутри) → UTF-8
            #   • taskkill, copy, tasklist, net и другие cmd-утилиты → OEM (cp866)
            # Python 3.11+ поддерживает encoding="oem" для OEM codepage.
            joined_cmd = " ".join(cmd).lower()
            utf8_markers = (
                "debug-worker.bat",
                "local-build.bat",
                "pyinstaller",
                "npm ",
                "cargo ",
                "python",
                "tauri",
            )
            if any(m in joined_cmd for m in utf8_markers):
                use_encoding = "utf-8"
            else:
                use_encoding = "oem"
            self.current_proc = subprocess.Popen(
                cmd, cwd=str(ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding=use_encoding, errors="replace",
                bufsize=1, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            assert self.current_proc.stdout is not None
            for line in self.current_proc.stdout:
                if self._stop_flag.is_set():
                    break
                self.output_queue.put(line)
            self.current_proc.wait(timeout=5)
        except Exception as exc:
            self.output_queue.put(f"[dev-gui] ERROR: {exc}\n")
        finally:
            elapsed = time.time() - start
            code = self.current_proc.returncode if self.current_proc else "?"
            status = "ok" if code == 0 else "err"
            self.output_queue.put(
                f"[dev-gui] {label}: exit {code} in {elapsed:.1f}s\n\x00{status}"
            )
            self.current_proc = None
            self.output_queue.put("__DONE__")

    def _stop_current(self) -> None:
        if self.current_proc and self.current_proc.poll() is None:
            try:
                self.current_proc.kill()
                self._write_log("[dev-gui] Killed current process\n", "warn")
            except Exception as exc:
                self._write_log(f"[dev-gui] Kill failed: {exc}\n", "err")
        self._stop_flag.set()

    # ───── Polling ─────────────────────────────────────────────────────

    def _poll_output(self) -> None:
        try:
            while True:
                line = self.output_queue.get_nowait()
                if line == "__DONE__":
                    self.busy_label.configure(text="● Idle", text_color=C_TEXT_MUTED)
                    self.stop_btn.configure(state="disabled")
                    self._refresh_status()
                    continue
                tag = "info"
                if "\x00" in line:
                    line, tag = line.rsplit("\x00", 1)
                else:
                    low = line.lower()
                    if "error" in low or "failed" in low or "fatal" in low:
                        tag = "err"
                    elif "warn" in low:
                        tag = "warn"
                    elif (
                        " ok" in low or "success" in low
                        or "finished" in low or "built application" in low
                    ):
                        tag = "ok"
                self._write_log(line, tag)
        except queue.Empty:
            pass
        finally:
            self.root.after(80, self._poll_output)

    def _write_log(self, text: str, tag: str = "info") -> None:
        inner = self.log._textbox  # type: ignore[attr-defined]
        inner.insert("end", text, tag)
        inner.see("end")
        try:
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def _clear_log(self) -> None:
        inner = self.log._textbox  # type: ignore[attr-defined]
        inner.delete("1.0", "end")
        try:
            LOG_FILE.write_text("", encoding="utf-8")
        except Exception:
            pass

    # ───── Install guide modal ──────────────────────────────────────

    def _open_install_guide(self) -> None:
        """Модалка с пошаговой инструкцией как установить билд Насте."""
        guide = """📦  Установка Nastya Orchestrator на ПК Насти
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ШАГ 0 — Требования у Насти
  • Windows 10/11 ✓
  • Node.js 20+ (нужен для codex CLI через npx)
  • Codex CLI установлен:
      npm install -g @openai/codex
  • Доступ к корп-прокси 94.103.191.13:3528 (уже есть)
  • НЕ нужно: Python, Git, Rust — всё вшито в .exe

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ШАГ 1 — Передача installer
  Файл (~146 МБ):
    D:\\Share\\nastyaorc\\src-tauri\\target\\release\\
      bundle\\nsis\\Nastya Orchestrator_0.1.0_x64-setup.exe

  Способы:
  • Telegram (лучший — файл проходит без сжатия)
  • Network share / OneDrive / Яндекс.Диск
  • USB флешка

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ШАГ 2 — Установка у Насти
  1. Двойной клик на .exe
  2. SmartScreen → "Подробнее" → "Выполнить в любом случае"
     (installer не подписан известным издателем — это норма
      для selfbuilt. Уберётся когда включим signed releases через CI)
  3. NSIS диалог → выбрать путь установки:
       По умолчанию: %LOCALAPPDATA%\\Programs\\Nastya Orchestrator
       Альтернатива: D:\\Programs\\Nastya Orchestrator (если места мало на C:)
     БЕЗ АДМИНА — всё в user-профиль
  4. Нажать Install
  5. Если нет WebView2 Runtime — скачается автоматически (~120 МБ)
  6. Finish → в Пуске появится "Nastya Orchestrator"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ШАГ 3 — Первый запуск
  1. Клик на "Nastya Orchestrator" в Пуске
  2. Windows Firewall → "Разрешить nastya-backend" (один раз)
  3. Открывается окно:
     • В шапке: Nastya Orchestrator 🚀 (emoji из remote-config)
     • Worker статус: через 3-5 сек становится зелёный "Worker онлайн"
     • Список проектов: пустой
  4. Настя: + → создать проект "Чат" без git_url → выбрать
  5. Внизу выбрана модель GPT-5 (новый дефолт)
  6. Написать "привет" → Enter → Codex отвечает через 15-30 сек

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ШАГ 4 — Проверка что работает
  ✓ В шапке эmoji 🚀 (remote-config сработал)
  ✓ Worker онлайн (зелёная точка)
  ✓ tasklist: nastya-orchestrator, nastya-backend,
               nastya-worker, opera-proxy
  ✓ Test: "привет" → GPT-5 → ответ за 15-30 сек
  ✓ ⚙ Настройки → Прокси: 127.0.0.1:18080
  ✓ Крестик → окно в трей, процессы живут
  ✓ Правый клик трей → Quit → все процессы умирают

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ШАГ 5 — Обновления в будущем

КАНАЛ А (мгновенно, для настроек и конфигов):
  Дима: dev-gui → 📝 Edit config → Save & push
      ↓
  Настя: backend через 5 минут видит новый config
      ↓
  Всплывашка в правом верхнем углу
      ↓
  Emoji в шапке меняется

КАНАЛ Б (через CI, для нового кода ~10-15 мин):
  Дима: git commit → git tag v0.1.X → git push origin v0.1.X
      ↓
  GitHub Actions собирает signed installer
      ↓
  Publishes GitHub Release
      ↓
  Настя при следующем запуске: диалог "Доступно обновление"
      ↓
  Нажимает Скачать → installer через opera-proxy
      ↓
  Silent install → перезапуск → уже новая версия

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ШАГ 6 — Troubleshooting

Приложение не запускается:
  → tasklist | findstr nastya
  → taskkill /F /IM nastya-*.exe
  → запустить от имени пользователя

Worker офлайн:
  → cd "C:\\Users\\<имя>\\AppData\\Local\\Programs\\Nastya Orchestrator"
  → nastya-worker.exe  (увидишь traceback)
  → Если codex not found: npm install -g @openai/codex
  → Если proxy connection: Настройки → Прокси → 127.0.0.1:18080

Codex возвращает Cloudflare 403:
  → opera-proxy не стартовал — перезапустить приложение
  → или opera-proxy не достучался до Opera API

БД сломалась:
  → Quit через трей
  → Удалить data\\nastya.db в папке установки
  → Запустить — создаст пустую БД

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🚨 КРИТИЧНОЕ ДЛЯ ПЕРВОЙ УСТАНОВКИ:

  1. Убедись что у Насти установлен @openai/codex
     (npm install -g @openai/codex в cmd)

  2. Убедись что у неё авторизован codex
     (codex login в cmd — откроет браузер на auth)

  3. После установки и первого запуска — ОСТАВЬ
     Настю на связи на 10 минут для теста.
     Нужно проверить что Worker онлайн и codex отвечает.

  4. Сохрани первый скрин работающего приложения
     у Насти — для истории и портфолио 😄"""

        win = ctk.CTkToplevel(self.root)
        win.title("ⓘ  Как установить Nastya Orchestrator Насте")
        win.geometry("820x680")
        win.transient(self.root)
        try:
            win.grab_set()
        except Exception:
            pass
        win.configure(fg_color=C_BG)

        # Header
        header = ctk.CTkFrame(win, fg_color=C_PANEL, corner_radius=10)
        header.pack(fill="x", padx=14, pady=(14, 6))
        ctk.CTkLabel(
            header,
            text="ⓘ  Инструкция по установке",
            font=F_SECTION,
            text_color=C_TEXT,
        ).pack(side="left", padx=14, pady=10)
        ctk.CTkLabel(
            header,
            text="scroll или клавиши ↑↓ PgUp PgDn",
            font=F_LABEL,
            text_color=C_TEXT_MUTED,
        ).pack(side="left", padx=(0, 14), pady=10)

        # Textbox с гайдом (read-only через bind)
        guide_box = ctk.CTkTextbox(
            win,
            font=("Consolas", 12),
            fg_color=C_LOG_BG,
            text_color=C_LOG_FG,
            wrap="word",
            corner_radius=8,
        )
        guide_box.pack(fill="both", expand=True, padx=14, pady=6)
        guide_box.insert("1.0", guide)

        # Read-only — блокируем ввод, разрешаем только копирование и навигацию
        inner_guide = guide_box._textbox  # type: ignore[attr-defined]

        def _readonly_guide(event):
            ctrl = (event.state & 0x4) != 0
            if ctrl and event.keysym.lower() in ("c", "a"):
                return None
            if event.keysym in ("Up", "Down", "Left", "Right",
                                "Home", "End", "Prior", "Next"):
                return None
            return "break"

        inner_guide.bind("<Key>", _readonly_guide)

        # Footer buttons
        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(6, 14))

        def copy_guide():
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(guide)
                self.root.update()
                # Visual feedback
                copy_btn.configure(text="✓ Скопировано")
                self.root.after(1500, lambda: copy_btn.configure(text="📋  Copy guide"))
            except Exception:
                pass

        copy_btn = ctk.CTkButton(
            footer, text="📋  Copy guide", width=180, height=36,
            font=F_BTN, fg_color=C_BTN_BLUE, hover_color=C_BTN_BLUE_HOVER,
            text_color=C_BTN_BLUE_TEXT, command=copy_guide,
        )
        copy_btn.pack(side="left", padx=4)

        ctk.CTkButton(
            footer, text="📂  Open installer folder", width=220, height=36,
            font=F_BTN, fg_color=C_BTN_GRAY, hover_color=C_BTN_GRAY_HOVER,
            text_color=C_BTN_GRAY_TEXT,
            command=lambda: self._run_command(
                "📂 Open installer", cmd_open_installer_folder()
            ),
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            footer, text="Close", width=100, height=36,
            font=F_BTN, fg_color=C_BTN_GRAY, hover_color=C_BTN_GRAY_HOVER,
            text_color=C_BTN_GRAY_TEXT, command=win.destroy,
        ).pack(side="right", padx=4)

    # ───── Find in log ──────────────────────────────────────────────

    def _open_find_dialog(self) -> None:
        """Ctrl+F — маленькое окно поиска по логу с Next/Prev."""
        # Если уже открыт — фокус на него
        existing = getattr(self, "_find_win", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            try:
                self._find_entry.focus_set()
                self._find_entry.select_range(0, "end")
            except Exception:
                pass
            return

        win = ctk.CTkToplevel(self.root)
        win.title("Find in log")
        win.geometry("460x110")
        win.transient(self.root)
        win.configure(fg_color=C_BG)
        win.attributes("-topmost", True)

        frame = ctk.CTkFrame(win, fg_color=C_PANEL, corner_radius=8)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(
            frame, text="Search in log:", font=F_LABEL, text_color=C_TEXT_MUTED,
            anchor="w",
        ).pack(side="top", fill="x", padx=12, pady=(10, 2))
        entry = ctk.CTkEntry(
            frame,
            font=F_LABEL, height=34, width=320,
            fg_color=C_LOG_BG, text_color=C_LOG_FG, border_color=C_BORDER,
        )
        entry.pack(side="top", fill="x", padx=10, pady=(0, 6))
        entry.focus_set()

        status_lbl = ctk.CTkLabel(
            frame, text="", font=("Segoe UI", 10),
            text_color=C_TEXT_MUTED, anchor="w",
        )
        status_lbl.pack(side="top", fill="x", padx=12)

        inner = self.log._textbox  # type: ignore[attr-defined]
        inner.tag_config("find_hit", background="#5a4a00", foreground="#ffffff")
        inner.tag_config("find_active", background="#b8860b", foreground="#000000")

        find_state = {"matches": [], "active": -1, "query": ""}

        def clear_tags():
            inner.tag_remove("find_hit", "1.0", "end")
            inner.tag_remove("find_active", "1.0", "end")

        def do_search():
            query = entry.get()
            if query == find_state["query"] and find_state["matches"]:
                return  # уже найдено
            find_state["query"] = query
            find_state["matches"] = []
            find_state["active"] = -1
            clear_tags()
            if not query:
                status_lbl.configure(text="")
                return
            start = "1.0"
            while True:
                idx = inner.search(query, start, stopindex="end", nocase=True)
                if not idx:
                    break
                end_idx = f"{idx}+{len(query)}c"
                find_state["matches"].append((idx, end_idx))
                inner.tag_add("find_hit", idx, end_idx)
                start = end_idx
            total = len(find_state["matches"])
            if total == 0:
                status_lbl.configure(text=f"No matches for '{query}'")
            else:
                status_lbl.configure(text=f"{total} matches — Enter/F3 = next, Shift+F3 = prev")
                go_to(0)

        def go_to(n: int):
            matches = find_state["matches"]
            if not matches:
                return
            n = n % len(matches)
            find_state["active"] = n
            inner.tag_remove("find_active", "1.0", "end")
            idx, end_idx = matches[n]
            inner.tag_add("find_active", idx, end_idx)
            inner.see(idx)
            status_lbl.configure(
                text=f"Match {n + 1}/{len(matches)} for '{find_state['query']}'"
            )

        def next_match():
            if not find_state["matches"]:
                do_search()
                return
            go_to(find_state["active"] + 1)

        def prev_match():
            if not find_state["matches"]:
                do_search()
                return
            go_to(find_state["active"] - 1)

        def on_close():
            clear_tags()
            win.destroy()
            self._find_win = None

        entry.bind("<Return>", lambda e: next_match())
        entry.bind("<KeyRelease>", lambda e: do_search() if e.keysym not in ("Return", "F3", "Escape") else None)
        win.bind("<F3>", lambda e: next_match())
        win.bind("<Shift-F3>", lambda e: prev_match())
        win.bind("<Escape>", lambda e: on_close())
        win.protocol("WM_DELETE_WINDOW", on_close)

        self._find_win = win
        self._find_entry = entry

    # ───── Log toolbar actions ───────────────────────────────────────

    def _log_copy_all(self) -> None:
        """Копировать весь текст лога в системный буфер обмена."""
        inner = self.log._textbox  # type: ignore[attr-defined]
        try:
            text = inner.get("1.0", "end-1c")
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()  # заставляет tk синхронизировать clipboard с OS
            self._write_log(
                f"[dev-gui] copied {len(text)} chars to clipboard\n", "ok"
            )
        except Exception as exc:
            self._write_log(f"[dev-gui] copy all failed: {exc}\n", "err")

    def _log_apply_font(self) -> None:
        """Применить текущий размер шрифта к textbox и всем тегам."""
        size = max(6, min(48, self._log_font_size))
        self._log_font_size = size
        try:
            inner = self.log._textbox  # type: ignore[attr-defined]
            inner.configure(font=("Consolas", size))
            inner.tag_config("info", font=("Consolas", size))
            inner.tag_config("cmd", font=("Consolas", size, "bold"))
            inner.tag_config("ok", font=("Consolas", size, "bold"))
            inner.tag_config("err", font=("Consolas", size, "bold"))
            inner.tag_config("warn", font=("Consolas", size))
        except Exception:
            pass

    def _log_zoom_in(self) -> None:
        self._log_font_size += 1
        self._log_apply_font()

    def _log_zoom_out(self) -> None:
        self._log_font_size -= 1
        self._log_apply_font()

    def _log_zoom_reset(self) -> None:
        self._log_font_size = 12
        self._log_apply_font()

    def _log_toggle_wrap(self) -> None:
        """Переключить word wrap в textbox."""
        self._log_wrap = "word" if self._log_wrap == "none" else "none"
        try:
            self.log.configure(wrap=self._log_wrap)
            # Обновить текст кнопки для визуальной индикации
            if hasattr(self, "_wrap_btn"):
                self._wrap_btn.configure(
                    text="↵ Wrap ✓" if self._log_wrap == "word" else "↵ Wrap"
                )
        except Exception:
            pass

    def _bind_log_shortcuts(self) -> None:
        """Global shortcuts для zoom/copy на textbox."""
        inner = self.log._textbox  # type: ignore[attr-defined]

        def _zoom_wheel(event: tk.Event) -> str | None:
            ctrl = (event.state & 0x4) != 0
            if not ctrl:
                return None
            if event.delta > 0:
                self._log_zoom_in()
            else:
                self._log_zoom_out()
            return "break"

        # Ctrl+MouseWheel — zoom
        inner.bind("<Control-MouseWheel>", _zoom_wheel)
        # Ctrl+plus / Ctrl+=
        inner.bind("<Control-equal>", lambda e: (self._log_zoom_in(), "break")[1])
        inner.bind("<Control-plus>", lambda e: (self._log_zoom_in(), "break")[1])
        inner.bind("<Control-KP_Add>", lambda e: (self._log_zoom_in(), "break")[1])
        # Ctrl+minus
        inner.bind("<Control-minus>", lambda e: (self._log_zoom_out(), "break")[1])
        inner.bind("<Control-KP_Subtract>", lambda e: (self._log_zoom_out(), "break")[1])
        # Ctrl+0 — reset
        inner.bind("<Control-Key-0>", lambda e: (self._log_zoom_reset(), "break")[1])
        inner.bind("<Control-KP_0>", lambda e: (self._log_zoom_reset(), "break")[1])

    # ───── Status проверки ────────────────────────────────────────────

    def _refresh_status(self) -> None:
        def check() -> None:
            backend_ok = self._check_url("http://127.0.0.1:8781/api/system/health")
            opera_ok = self._check_url(
                "https://api.openai.com/v1/models",
                proxy="http://127.0.0.1:18080",
                expected_codes=(200, 401, 403),
            )

            def apply() -> None:
                self.backend_dot.configure(fg_color=C_DOT_GREEN if backend_ok else C_DOT_RED)
                self.opera_dot.configure(fg_color=C_DOT_GREEN if opera_ok else C_DOT_RED)
                parts = []
                parts.append("backend :8781 alive" if backend_ok else "backend DOWN")
                parts.append("opera-proxy :18080 OK" if opera_ok else "opera-proxy DOWN")
                self.status_text.configure(text="  ·  " + "  ·  ".join(parts))

            self.root.after(0, apply)

        threading.Thread(target=check, daemon=True).start()

    @staticmethod
    def _check_url(
        url: str,
        proxy: str | None = None,
        expected_codes: tuple[int, ...] = (200,),
    ) -> bool:
        try:
            handlers = []
            if proxy:
                handlers.append(
                    request.ProxyHandler({"https": proxy, "http": proxy})
                )
            opener = (
                request.build_opener(*handlers) if handlers
                else request.build_opener()
            )
            req = request.Request(url, headers={"User-Agent": "nastyaorc-devgui"})
            with opener.open(req, timeout=4) as resp:
                return resp.status in expected_codes
        except error.HTTPError as e:
            return e.code in expected_codes
        except Exception:
            return False


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    root = ctk.CTk()
    DevGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
