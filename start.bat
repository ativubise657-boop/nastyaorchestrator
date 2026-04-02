@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

title Nastya Orchestrator

set "ROOT=%~dp0"
set "PORT=8781"
set "ENV_FILE=%ROOT%.env"
set "VENV=%ROOT%.venv"
set "DATA=%ROOT%data"
set "DIST=%ROOT%frontend\dist"
set "STACK_RUNNER=%ROOT%tools\run_local_stack.py"

echo.
echo ==========================================
echo   Nastya Orchestrator
echo   Single-window backend + worker start
echo ==========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    pause
    exit /b 1
)

where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js was not found in PATH.
    pause
    exit /b 1
)

where npx.cmd >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npx.cmd was not found in PATH.
    pause
    exit /b 1
)

if not exist "%STACK_RUNNER%" (
    echo [ERROR] Missing runner: %STACK_RUNNER%
    pause
    exit /b 1
)

if not exist "%DIST%\index.html" (
    echo [SETUP] Frontend build was not found. Building...
    pushd "%ROOT%frontend"
    if not exist "node_modules" (
        call npm install --silent
        if errorlevel 1 (
            popd
            echo [ERROR] npm install failed.
            pause
            exit /b 1
        )
    )
    call npm run build
    if errorlevel 1 (
        popd
        echo [ERROR] Frontend build failed.
        pause
        exit /b 1
    )
    popd
)

if not exist "%VENV%\Scripts\activate.bat" (
    echo [SETUP] Creating Python virtual environment...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Could not create virtual environment.
        pause
        exit /b 1
    )
)

call "%VENV%\Scripts\activate.bat"

if not exist "%VENV%\installed.marker" (
    echo [SETUP] Installing Python dependencies...
    pip install --quiet --disable-pip-version-check -r "%ROOT%requirements.txt"
    if errorlevel 1 (
        echo [ERROR] pip install failed.
        pause
        exit /b 1
    )
    echo.>"%VENV%\installed.marker"
)

call "%ROOT%tools\codex-npx.cmd" login status >nul 2>&1
if errorlevel 1 (
    echo [WARN] Codex CLI is not authenticated.
    echo [WARN] GPT-5 CLI models will not work until authorization is done.
    echo [WARN] Run:
    echo        auth.bat
    echo.
)

if not exist "%ENV_FILE%" (
    echo [SETUP] Creating .env file...
    for /f "delims=" %%t in ('python -c "import secrets; print(secrets.token_hex(32))"') do set "TOKEN=%%t"
    (
        echo WORKER_TOKEN=!TOKEN!
        echo ORCH_SERVER_URL=http://127.0.0.1:%PORT%
        echo SERVE_STATIC=true
        echo CODEX_BINARY=tools\codex-npx.cmd
    ) > "%ENV_FILE%"
)

if not exist "%DATA%" mkdir "%DATA%"
if not exist "%DATA%\documents" mkdir "%DATA%\documents"

echo [STOP] Stopping previous backend on port %PORT%...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)

echo [STOP] Stopping previous worker processes for this project...
powershell -NoProfile -Command ^
  "$root = [Regex]::Escape((Resolve-Path '%ROOT%').Path); " ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $root -and $_.CommandLine -match 'worker\.main' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

echo.
echo [START] Launching backend and worker in this window...
echo [INFO] Press Ctrl+C to stop both processes.
echo.
python "%STACK_RUNNER%" --port %PORT%
exit /b %errorlevel%
