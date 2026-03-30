@echo off
chcp 65001 >nul 2>&1
echo Stopping Nastya Orchestrator...

taskkill /FI "WINDOWTITLE eq Nastya-Backend*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Nastya-Worker*" /F >nul 2>&1

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8781 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)

wmic process where "commandline like '%%portablenastyaorc%%uvicorn%%'" call terminate >nul 2>&1
wmic process where "commandline like '%%portablenastyaorc%%worker.main%%'" call terminate >nul 2>&1

echo [OK] Stopped
ping -n 3 127.0.0.1 >nul
