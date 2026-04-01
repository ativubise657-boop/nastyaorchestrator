@echo off
setlocal enabledelayedexpansion

set "ROOT=%~dp0.."
set "PORT=8781"
set "VENV=%ROOT%\.venv"

timeout /t 2 /nobreak >nul

taskkill /FI "WINDOWTITLE eq Nastya-Backend*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Nastya-Worker*" /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)

start "Nastya-Backend" /min cmd /c "cd /d "%ROOT%" && call "%VENV%\Scripts\activate.bat" && uvicorn backend.main:app --host 127.0.0.1 --port %PORT% --workers 1 --log-level info"
timeout /t 5 /nobreak >nul
start "Nastya-Worker" /min cmd /c "cd /d "%ROOT%" && call "%VENV%\Scripts\activate.bat" && python -m worker.main"
