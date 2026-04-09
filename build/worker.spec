# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec для worker (фоновый процесс, отдельный .exe).
Сборка:
    pyinstaller --noconfirm build/worker.spec
Результат: dist/nastya-worker.exe (onefile, console).
"""
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent

# ---------------------------------------------------------------------------
# Hidden imports — worker использует httpx + markitdown + sqlite
# ---------------------------------------------------------------------------
hiddenimports = [
    "httpx",
    "httpcore",
    "h11",
    "anyio",
    "sniffio",
    "certifi",
    "sqlite3",
    # markitdown и форматы
    "markitdown",
    "markitdown.converters",
    "pdfminer",
    "pdfminer.high_level",
    "pdfminer.layout",
    "pdfminer.pdfparser",
    "pdfminer.pdfinterp",
    "docx",
    "lxml",
    "lxml.etree",
    "lxml._elementpath",
    "openpyxl",
    "openpyxl.cell._writer",
    "pptx",
    "charset_normalizer",
    "bs4",
    "magika",
    "mammoth",
]

hiddenimports += collect_submodules("markitdown")

datas = []
datas += collect_data_files("markitdown")
datas += collect_data_files("pdfminer")
datas += collect_data_files("magika")
datas += collect_data_files("docx")
datas += collect_data_files("pptx")
datas += collect_data_files("openpyxl")
datas += collect_data_files("certifi")  # ca-bundle для httpx

# Локальный wrapper codex-npx.cmd — обходит баг WindowsApps codex.exe
# (системный codex из WindowsApps недоступен из subprocess на некоторых системах).
# Wrapper вызывает `npx @openai/codex` напрямую.
# Встраиваем в onefile, worker при frozen резолвит через sys._MEIPASS.
_codex_wrapper = ROOT / "tools" / "codex-npx.cmd"
if _codex_wrapper.is_file():
    datas += [(str(_codex_wrapper), "tools")]

a = Analysis(
    [str(ROOT / "worker" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "build" / "runtime_hook.py")],
    excludes=[
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
        # backend / fastapi worker'у не нужны
        "fastapi",
        "starlette",
        "uvicorn",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# TODO: подложить build/icon.ico для брендирования
ICON_PATH = ROOT / "build" / "icon.ico"
icon = str(ICON_PATH) if ICON_PATH.is_file() else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="nastya-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)
