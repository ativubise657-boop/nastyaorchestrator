@echo off
chcp 65001 >nul 2>&1
echo Останавливаю Nastya Orchestrator...

:: Убиваем окна с заголовками
taskkill /FI "WINDOWTITLE eq Nastya-Backend*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Nastya-Worker*" /F >nul 2>&1

:: Убиваем по порту
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8781 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)

echo [OK] Остановлено
timeout /t 2 >nul
