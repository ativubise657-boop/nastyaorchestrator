@echo off
REM Switch console code page to UTF-8 so Python's UTF-8 output (русский в логах)
REM renders correctly instead of cp866 mojibake (╨С╨Ф вместо БД).
chcp 65001 >nul 2>&1
REM ============================================================================
REM Debug worker from sources (not frozen .exe) with full logs + unbuffered.
REM
REM Purpose: reproduce codex subprocess crash with a full Python traceback
REM that the frozen nastya-worker.exe silently loses on crash.
REM
REM Requirements:
REM   - Installed Nastya Orchestrator app running (Tauri + backend + opera-proxy)
REM   - .venv-build exists in this folder (created by build\build.bat)
REM
REM Usage: double-click or run from dev-shell.bat
REM ============================================================================
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

title Nastya Worker (debug, from sources)

echo.
echo [debug-worker] Root: %ROOT%
echo.

REM Check venv exists
if not exist ".venv-build\Scripts\python.exe" (
    echo [debug-worker] ERROR: .venv-build\Scripts\python.exe not found.
    echo [debug-worker] Run build\build.bat once to create the venv.
    echo.
    pause
    exit /b 1
)

REM Check backend is alive on 8781
echo [debug-worker] Checking backend on http://127.0.0.1:8781 ...
curl.exe -s -o nul -w "%%{http_code}" --max-time 3 http://127.0.0.1:8781/api/system/health > "%TEMP%\nastyaorc-bcheck.txt" 2>&1
set /p BCODE=<"%TEMP%\nastyaorc-bcheck.txt"
del "%TEMP%\nastyaorc-bcheck.txt" >nul 2>&1
if not "%BCODE%"=="200" (
    echo [debug-worker] WARNING: backend not responding on :8781 ^(got '%BCODE%'^).
    echo [debug-worker] Make sure Nastya Orchestrator is running before starting the worker.
    echo.
    choice /C YN /M "Continue anyway"
    if errorlevel 2 exit /b 1
) else (
    echo [debug-worker] Backend OK ^(HTTP 200^)
)

REM Check opera-proxy is alive on 18080
echo [debug-worker] Checking opera-proxy on http://127.0.0.1:18080 ...
curl.exe -s -o nul -w "%%{http_code}" --max-time 3 -x http://127.0.0.1:18080 https://api.openai.com/v1/models > "%TEMP%\nastyaorc-pcheck.txt" 2>&1
set /p PCODE=<"%TEMP%\nastyaorc-pcheck.txt"
del "%TEMP%\nastyaorc-pcheck.txt" >nul 2>&1
if "%PCODE%"=="401" (
    echo [debug-worker] opera-proxy OK ^(401 from OpenAI = no-auth, tunnel works^)
) else if "%PCODE%"=="200" (
    echo [debug-worker] opera-proxy OK ^(200^)
) else (
    echo [debug-worker] WARNING: opera-proxy unusual response '%PCODE%'
)

REM -------------------------------------------------------------------------
REM Environment for worker
REM -------------------------------------------------------------------------
set "HTTPS_PROXY=http://127.0.0.1:18080"
set "HTTP_PROXY=http://127.0.0.1:18080"
set "NO_PROXY=localhost,127.0.0.1,::1"
set "ORCH_SERVER_URL=http://127.0.0.1:8781"
set "WORKER_TOKEN=nastya-local-dev"
set "PYTHONUNBUFFERED=1"
set "PYTHONIOENCODING=utf-8"

echo.
echo [debug-worker] Environment:
echo   HTTPS_PROXY      = %HTTPS_PROXY%
echo   ORCH_SERVER_URL  = %ORCH_SERVER_URL%
echo   WORKER_TOKEN     = %WORKER_TOKEN%
echo   PYTHONUNBUFFERED = %PYTHONUNBUFFERED%
echo.

REM -------------------------------------------------------------------------
REM Log file (fixed name, overwritten each run)
REM -------------------------------------------------------------------------
set "LOG=%ROOT%worker-debug.log"
REM Clear previous log so we start with a fresh file each run
if exist "%LOG%" del /q "%LOG%"

echo [debug-worker] Log file: %LOG%
echo [debug-worker] Starting worker from sources ^(debug mode^)...
echo [debug-worker] Press Ctrl+C to stop. Log saved even on crash.
echo.
echo ================================================================================

REM Run worker, tee output to both console and log file via PowerShell.
REM Force UTF-8 for both console output and the log file to avoid mojibake
REM (PowerShell 5 Tee-Object defaults to UTF-16, our Python writes UTF-8).
powershell -NoProfile -Command "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8; & '.\.venv-build\Scripts\python.exe' -m worker.main --debug 2>&1 | ForEach-Object { $_ | Out-File -FilePath 'worker-debug.log' -Encoding utf8 -Append; Write-Host $_ }"

set "EXITCODE=%ERRORLEVEL%"
echo.
echo ================================================================================
echo [debug-worker] Worker exited with code %EXITCODE%
echo [debug-worker] Full log: %LOG%
echo.
pause
