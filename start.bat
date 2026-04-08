@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

title Nastya Orchestrator

set "ROOT=%~dp0"
set "PORT=8781"
set "ENV_FILE=%ROOT%.env"
set "LOCAL_STATE=%LOCALAPPDATA%\NastyaOrchestrator"
set "VENV=%LOCAL_STATE%\venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "VENV_MARKER=%VENV%\installed.marker"
set "BOOTSTRAP_PY=python"
set "BOOTSTRAP_PY_ARGS="
set "DATA=%ROOT%data"
set "DIST=%ROOT%frontend\dist"
set "STACK_RUNNER=%ROOT%tools\run_local_stack.py"

echo.
echo ==========================================
echo   Nastya Orchestrator
echo   Single-window backend + worker start
echo ==========================================
echo.

where py >nul 2>&1
if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "BOOTSTRAP_PY=py"
        set "BOOTSTRAP_PY_ARGS=-3.12"
    ) else (
        py -3.13 -c "import sys" >nul 2>&1
        if not errorlevel 1 (
            set "BOOTSTRAP_PY=py"
            set "BOOTSTRAP_PY_ARGS=-3.13"
        )
    )
)

if "%BOOTSTRAP_PY%"=="python" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python was not found in PATH.
        echo [ERROR] Install Python 3.12 or 3.13 and try again.
        pause
        exit /b 1
    )
)

"%BOOTSTRAP_PY%" %BOOTSTRAP_PY_ARGS% -c "import sys; raise SystemExit(0 if sys.version_info[:2] < (3,14) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.14 is not supported by current pinned dependencies.
    echo [ERROR] Install Python 3.12 ^(recommended^) or 3.13.
    echo [ERROR] If py launcher is installed, start.bat will pick it automatically.
    pause
    exit /b 1
)

echo [INFO] Bootstrap interpreter: %BOOTSTRAP_PY% %BOOTSTRAP_PY_ARGS%

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

if not exist "%LOCAL_STATE%" (
    mkdir "%LOCAL_STATE%" >nul 2>&1
)
echo [INFO] Python environment: %VENV%

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
    echo [SETUP] This may take 1-2 minutes on first run.
    "%BOOTSTRAP_PY%" %BOOTSTRAP_PY_ARGS% -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Could not create virtual environment.
        pause
        exit /b 1
    )
    echo [SETUP] Virtual environment created.
)

if not exist "%VENV_PY%" (
    echo [SETUP] Virtual environment looks incomplete. Recreating...
    rmdir /s /q "%VENV%" >nul 2>&1
    echo [SETUP] Creating fresh environment...
    echo [SETUP] This may take 1-2 minutes on first run.
    "%BOOTSTRAP_PY%" %BOOTSTRAP_PY_ARGS% -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Could not recreate virtual environment.
        pause
        exit /b 1
    )
    echo [SETUP] Virtual environment recreated.
)

"%VENV_PY%" -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [SETUP] Virtual environment is not runnable. Recreating...
    rmdir /s /q "%VENV%" >nul 2>&1
    echo [SETUP] Creating fresh environment...
    echo [SETUP] This may take 1-2 minutes on first run.
    "%BOOTSTRAP_PY%" %BOOTSTRAP_PY_ARGS% -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Could not recreate virtual environment.
        pause
        exit /b 1
    )
    echo [SETUP] Virtual environment recreated.
)

set "NEED_PIP_INSTALL=0"
if not exist "%VENV_MARKER%" set "NEED_PIP_INSTALL=1"

"%VENV_PY%" -c "import uvicorn" >nul 2>&1
if errorlevel 1 set "NEED_PIP_INSTALL=1"

"%VENV_PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [SETUP] pip is missing in virtual environment. Bootstrapping...
    "%VENV_PY%" -m ensurepip --upgrade
    if errorlevel 1 (
        echo [ERROR] Could not bootstrap pip in virtual environment.
        pause
        exit /b 1
    )
    set "NEED_PIP_INSTALL=1"
)

if "%NEED_PIP_INSTALL%"=="1" (
    echo [SETUP] Installing Python dependencies ^(first run may take 3-10 minutes^)...
    "%VENV_PY%" -m pip install --disable-pip-version-check -r "%ROOT%requirements.txt"
    if errorlevel 1 (
        echo [ERROR] pip install failed.
        pause
        exit /b 1
    )
    echo.>"%VENV_MARKER%"
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
    for /f "delims=" %%t in ('"%VENV_PY%" -c "import secrets; print(secrets.token_hex(32))"') do set "TOKEN=%%t"
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
"%VENV_PY%" "%STACK_RUNNER%" --port %PORT%
set "STACK_EXIT=%errorlevel%"
if not "%STACK_EXIT%"=="0" (
    echo.
    echo [ERROR] Backend/worker exited with code %STACK_EXIT%.
    echo [INFO] Keep this window open and send me the last 20 lines.
    pause
)
exit /b %STACK_EXIT%
