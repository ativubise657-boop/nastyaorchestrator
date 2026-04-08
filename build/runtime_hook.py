"""
PyInstaller runtime hook — выполняется ДО импорта приложения внутри frozen .exe.

Задачи:
  1. Установить рабочую директорию рядом с .exe (а не во временный _MEIPASS).
     Это нужно чтобы относительные пути (data/, documents/, .env) резолвились
     в директорию, где лежит сам .exe — там, где Tauri их положит.
  2. Если рядом с .exe лежит frontend/dist — включить SERVE_STATIC=true,
     чтобы backend сам раздавал статику (без nginx).
  3. Прокинуть .env рядом с .exe в окружение через python-dotenv (на всякий
     случай — config.py тоже его читает, но порядок импортов в frozen режиме
     может отличаться).
"""
import os
import sys
from pathlib import Path

# При frozen — sys.executable указывает на .exe (не на питон)
if getattr(sys, "frozen", False):
    exe_dir = Path(sys.executable).resolve().parent

    # 1. CWD рядом с .exe — чтобы относительные пути работали как ожидается
    try:
        os.chdir(exe_dir)
    except OSError:
        pass

    # 2. SERVE_STATIC во frozen включаем безусловно — фронт вшит в _MEIPASS
    # через datas в backend.spec. main.py найдёт его через __file__.parent.parent.
    os.environ.setdefault("SERVE_STATIC", "true")

    # 3. Подгрузим .env если есть (config.py тоже это сделает, но раньше — лучше)
    env_file = exe_dir / ".env"
    if env_file.is_file():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except Exception:
            pass
