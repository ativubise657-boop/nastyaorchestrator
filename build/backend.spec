# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec для backend (FastAPI + uvicorn).
Сборка:
    pyinstaller --noconfirm build/backend.spec
Результат: dist/nastya-backend.exe (onefile, console).

Запускается из CWD рядом с .exe — там же должны лежать data/, documents/,
опционально frontend/dist/ и .env.
"""
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Корень репо (build/backend.spec → ..)
ROOT = Path(SPECPATH).resolve().parent

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
# uvicorn — динамически грузит loop/protocol по строке имени
hiddenimports = [
    # uvicorn loops/protocols/lifespan
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "h11",
    "wsproto",
    # FastAPI / pydantic
    "fastapi",
    "pydantic",
    "pydantic.deprecated.decorator",
    "pydantic_core",
    "starlette",
    "starlette.routing",
    # multipart / aiofiles
    "multipart",
    "aiofiles",
    # stdlib
    "sqlite3",
    "email.mime.multipart",
    "email.mime.text",
    # markitdown и его конвертеры
    "markitdown",
    "markitdown.converters",
    # PDF
    "pdfminer",
    "pdfminer.high_level",
    "pdfminer.layout",
    "pdfminer.pdfparser",
    "pdfminer.pdfinterp",
    # docx
    "docx",
    "lxml",
    "lxml.etree",
    "lxml._elementpath",
    # xlsx
    "openpyxl",
    "openpyxl.cell._writer",
    # pptx
    "pptx",
    # вспомогательное для markitdown
    "charset_normalizer",
    "bs4",
    "magika",
    "mammoth",
]

# Подтянуть все submodules markitdown — на всякий
hiddenimports += collect_submodules("markitdown")

# ---------------------------------------------------------------------------
# Data files (НЕ runtime data — только то, что нужно встроить в .exe:
# ресурсы пакетов, такие как magika models, pdfminer cmap-таблицы и т.п.)
# ---------------------------------------------------------------------------
datas = []
# React-фронт — вшиваем внутрь .exe чтобы backend мог раздавать через SERVE_STATIC
# внутри Tauri-окна (окно грузит http://127.0.0.1:8781/ напрямую).
# Собранный фронт должен существовать до запуска pyinstaller (CI делает npm run build раньше).
_frontend_dist = ROOT / "frontend" / "dist"
if _frontend_dist.is_dir():
    datas += [(str(_frontend_dist), "frontend/dist")]
datas += collect_data_files("markitdown")
datas += collect_data_files("pdfminer")
datas += collect_data_files("magika")  # модели для определения типа файла
datas += collect_data_files("docx")
datas += collect_data_files("pptx")
datas += collect_data_files("openpyxl")

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(ROOT / "build" / "backend_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "build" / "runtime_hook.py")],
    excludes=[
        # Тяжёлое и не нужное
        "tkinter",
        "matplotlib",
        "numpy.tests",
        "pytest",
        "PIL.ImageQt",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "IPython",
        "jupyter",
        "notebook",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# TODO: подложить build/icon.ico для брендирования .exe
ICON_PATH = ROOT / "build" / "icon.ico"
icon = str(ICON_PATH) if ICON_PATH.is_file() else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="nastya-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX часто триггерит антивирусы на Win10 — отключаем
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,         # console=True — нужны логи uvicorn в окне Tauri sidecar
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)
