@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: ═══════════════════════════════════════════════════════════════
::  Nastya Orchestrator — Portable Start
::  Один скрипт: setup + запуск backend + worker + браузер
:: ═══════════════════════════════════════════════════════════════

title Nastya Orchestrator

set "ROOT=%~dp0"
set "PORT=8781"
set "ENV_FILE=%ROOT%.env"
set "VENV=%ROOT%.venv"
set "DATA=%ROOT%data"
set "DIST=%ROOT%frontend\dist"

echo.
echo  ╔══════════════════════════════════════╗
echo  ║     Nastya Orchestrator v0.1         ║
echo  ╚══════════════════════════════════════╝
echo.

:: ─── Проверка Python ────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не найден. Установите Python 3.10+ с python.org
    echo          Не забудьте поставить галочку "Add Python to PATH"
    pause
    exit /b 1
)

:: Проверяем версию Python (нужна 3.10+)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PY_VER=%%v"
echo [OK] Python %PY_VER%

:: ─── Проверка Node.js ───────────────────────────────────────
where node >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Node.js не найден. Установите Node.js 18+ с nodejs.org
    pause
    exit /b 1
)

for /f "tokens=1 delims= " %%v in ('node --version 2^>^&1') do set "NODE_VER=%%v"
echo [OK] Node.js %NODE_VER%

:: ─── Проверка собранного фронтенда ──────────────────────────
if not exist "%DIST%\index.html" (
    echo.
    echo [!] Frontend не собран. Собираю...
    cd /d "%ROOT%frontend"
    if not exist "node_modules" (
        echo     npm install...
        call npm install --silent
    )
    echo     npm run build...
    call npm run build
    if errorlevel 1 (
        echo [ОШИБКА] Сборка frontend не удалась
        pause
        exit /b 1
    )
    echo [OK] Frontend собран
    cd /d "%ROOT%"
)

:: ─── Создание виртуального окружения ────────────────────────
if not exist "%VENV%\Scripts\activate.bat" (
    echo.
    echo [SETUP] Создаю виртуальное окружение Python...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать venv
        pause
        exit /b 1
    )
    echo [OK] venv создан
)

:: Активируем venv
call "%VENV%\Scripts\activate.bat"

:: ─── Установка Python-зависимостей ──────────────────────────
if not exist "%VENV%\installed.marker" (
    echo.
    echo [SETUP] Устанавливаю Python-зависимости...
    pip install --quiet --disable-pip-version-check -r "%ROOT%requirements.txt"
    if errorlevel 1 (
        echo [ОШИБКА] pip install не удался
        pause
        exit /b 1
    )
    echo. > "%VENV%\installed.marker"
    echo [OK] Зависимости установлены
)

:: ─── Проверка Claude CLI ────────────────────────────────────
where claude >nul 2>&1
if errorlevel 1 (
    echo.
    echo [SETUP] Устанавливаю Claude CLI...
    call npm install -g @anthropic-ai/claude-code
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось установить Claude CLI
        echo          Попробуйте вручную: npm install -g @anthropic-ai/claude-code
        pause
        exit /b 1
    )
    echo [OK] Claude CLI установлен
    echo.
    echo [!] Нужно авторизоваться в Claude CLI.
    echo     Сейчас откроется окно авторизации...
    echo.
    call claude auth login
)

echo [OK] Claude CLI найден

:: ─── Создание .env ──────────────────────────────────────────
if not exist "%ENV_FILE%" (
    echo.
    echo [SETUP] Создаю .env файл...

    :: Генерируем токен
    for /f "delims=" %%t in ('python -c "import secrets; print(secrets.token_hex(32))"') do set "TOKEN=%%t"

    (
        echo # Nastya Orchestrator — конфигурация
        echo.
        echo # Токен авторизации worker ^(сгенерирован автоматически^)
        echo WORKER_TOKEN=!TOKEN!
        echo.
        echo # URL оркестратора ^(для worker — подключается к локальному серверу^)
        echo ORCH_SERVER_URL=http://localhost:%PORT%
        echo.
        echo # Standalone режим — backend раздаёт фронтенд
        echo SERVE_STATIC=true
        echo.
        echo # Bitrix24 CRM webhook ^(опционально^)
        echo # BITRIX_WEBHOOK_URL=https://your-portal.bitrix24.ru/rest/USER_ID/TOKEN/
        echo.
        echo # GitHub PAT для приватных репо ^(опционально^)
        echo # GITHUB_PAT=ghp_your_token
    ) > "%ENV_FILE%"

    echo [OK] .env создан с токеном: !TOKEN:~0,8!...
)

:: ─── Создание директории данных ─────────────────────────────
if not exist "%DATA%" mkdir "%DATA%"
if not exist "%DATA%\documents" mkdir "%DATA%\documents"

:: ─── Останавливаем старые процессы ──────────────────────────
echo.
echo [...] Останавливаю старые процессы...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)

:: ─── Запуск Backend ─────────────────────────────────────────
echo.
echo [START] Backend на порту %PORT%...
start "Nastya-Backend" /min cmd /c "cd /d "%ROOT%" && call "%VENV%\Scripts\activate.bat" && uvicorn backend.main:app --host 127.0.0.1 --port %PORT% --workers 1 --log-level info"

:: Ждём пока backend поднимется
echo         Жду запуск сервера...
set "RETRIES=0"
:wait_backend
timeout /t 1 /nobreak >nul
set /a RETRIES+=1
if %RETRIES% gtr 15 (
    echo [ОШИБКА] Backend не запустился за 15 секунд
    pause
    exit /b 1
)
curl -s http://localhost:%PORT%/api/system/health >nul 2>&1
if errorlevel 1 goto wait_backend
echo [OK] Backend запущен

:: ─── Запуск Worker ──────────────────────────────────────────
echo [START] Worker...
start "Nastya-Worker" /min cmd /c "cd /d "%ROOT%" && call "%VENV%\Scripts\activate.bat" && python -m worker.main"
echo [OK] Worker запущен

:: ─── Открытие браузера ──────────────────────────────────────
timeout /t 2 /nobreak >nul
echo.
echo  ╔══════════════════════════════════════╗
echo  ║  Готово! Открываю браузер...         ║
echo  ║                                      ║
echo  ║  http://localhost:%PORT%              ║
echo  ║                                      ║
echo  ║  Для остановки — закройте это окно   ║
echo  ║  или нажмите Ctrl+C                  ║
echo  ╚══════════════════════════════════════╝
echo.

start http://localhost:%PORT%

:: Ждём нажатия для выхода
echo Нажмите любую клавишу для остановки сервера...
pause >nul

:: ─── Остановка ──────────────────────────────────────────────
echo.
echo [STOP] Останавливаю процессы...
taskkill /FI "WINDOWTITLE eq Nastya-Backend*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Nastya-Worker*" /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)
echo [OK] Остановлено
