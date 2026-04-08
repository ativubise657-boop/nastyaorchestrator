@echo off
REM ============================================================================
REM Локальная сборка nastya-backend.exe и nastya-worker.exe (Windows).
REM Используется для отладки спеков. На CI билдится через GitHub Actions.
REM ============================================================================
setlocal

REM Корпоративный прокси (Win10 без админа, наружу только через него)
if "%HTTPS_PROXY%"=="" set HTTPS_PROXY=http://user393678:a6g7ln@94.103.191.13:3528
if "%HTTP_PROXY%"==""  set HTTP_PROXY=http://user393678:a6g7ln@94.103.191.13:3528
set NO_PROXY=localhost,127.0.0.1,::1

cd /d "%~dp0\.."

echo [build] Создаю venv (если нет)...
if not exist .venv-build (
    python -m venv .venv-build
)

call .venv-build\Scripts\activate.bat

echo [build] Обновляю pip и ставлю зависимости...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 goto :error

echo [build] Сборка backend...
pyinstaller --noconfirm --clean build\backend.spec
if errorlevel 1 goto :error

echo [build] Сборка worker...
pyinstaller --noconfirm --clean build\worker.spec
if errorlevel 1 goto :error

echo.
echo [build] Готово! Артефакты в dist\
dir dist\*.exe
exit /b 0

:error
echo [build] ОШИБКА сборки
exit /b 1
