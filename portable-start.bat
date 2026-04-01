@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

title Nastya Orchestrator

set "ROOT=%~dp0"
set "PORT=8781"
set "PYTHON=%ROOT%runtime\python\python.exe"
set "NODE=%ROOT%runtime\node\node.exe"
set "CODEX_CLI=%ROOT%runtime\node\node_modules\.bin\codex.cmd"
set "ENV_FILE=%ROOT%.env"
set "DATA=%ROOT%data"

:: Runtime PATH
set "PATH=%ROOT%runtime\git\bin;%ROOT%runtime\git\usr\bin;%ROOT%runtime\python;%ROOT%runtime\python\Scripts;%ROOT%runtime\node;%ROOT%runtime\node\node_modules\.bin;%PATH%"

echo.
echo === Nastya Orchestrator - Portable ===
echo.

if not exist "%PYTHON%" (
    echo [ERROR] Python not found in runtime\python\
    pause
    exit /b 1
)
echo [OK] Python portable

if not exist "%NODE%" (
    echo [ERROR] Node.js not found in runtime\node\
    pause
    exit /b 1
)
echo [OK] Node.js portable

if not exist "%CODEX_CLI%" (
    echo [ERROR] Codex CLI not found in runtime\node\node_modules\.bin\
    pause
    exit /b 1
)
echo [OK] Codex CLI portable

:: Codex auth check (non-blocking)
call "%CODEX_CLI%" login status >nul 2>&1
if errorlevel 1 (
    echo [WARN] Codex CLI not authenticated
    echo        Run auth.bat first for full functionality
) else (
    echo [OK] Codex CLI auth
)

:: Create .env via Python (avoids CMD escaping issues)
if not exist "%ENV_FILE%" (
    echo [SETUP] Creating .env...
    "%PYTHON%" -c "import secrets; open(r'%ENV_FILE%','w',encoding='utf-8').write('WORKER_TOKEN='+secrets.token_hex(32)+'\nORCH_SERVER_URL=http://localhost:%PORT%\nSERVE_STATIC=true\nNOTES_PATH='+r'%ROOT%data\notes'+'\nCODEX_BINARY='+r'%ROOT%runtime\node\node_modules\.bin\codex.cmd'+'\n')"
    echo [OK] .env created
)

if not exist "%DATA%" mkdir "%DATA%"
if not exist "%DATA%\documents" mkdir "%DATA%\documents"

echo.
echo Stopping old processes...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)

echo.
echo [START] Backend port %PORT%...
start "Nastya-Backend" /min "%ROOT%run-backend.bat"

echo         Waiting for server...
set "RETRIES=0"
:wait_backend
ping -n 2 127.0.0.1 >nul
set /a RETRIES+=1
if %RETRIES% gtr 30 (
    echo [ERROR] Backend did not start in 30s
    pause
    exit /b 1
)
curl -s http://localhost:%PORT%/api/system/health >nul 2>&1
if errorlevel 1 goto wait_backend
echo [OK] Backend running

echo [START] Worker...
start "Nastya-Worker" /min "%ROOT%run-worker.bat"
echo [OK] Worker running

ping -n 3 127.0.0.1 >nul
echo.
echo === Ready! Opening browser ===
echo http://localhost:%PORT%
echo.
echo Close this window to stop the server.
echo.

start http://localhost:%PORT%

echo Press any key to stop...
pause >nul

echo.
echo [STOP] Stopping...
taskkill /FI "WINDOWTITLE eq Nastya-Backend*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Nastya-Worker*" /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)
echo [OK] Stopped
