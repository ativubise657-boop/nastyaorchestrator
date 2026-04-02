@echo off
setlocal

set "ROOT=%~dp0"
set "CLI=%ROOT%tools\codex-npx.cmd"

cd /d "%ROOT%"

if not exist "%CLI%" (
    echo [ERROR] Codex CLI wrapper not found:
    echo         %CLI%
    echo.
    pause
    exit /b 1
)

if /I "%~1"=="login" goto do_login
if /I "%~1"=="logout" goto do_logout
if /I "%~1"=="status" goto do_status

echo.
echo ==========================================
echo   GPT Auth (Codex CLI)
echo ==========================================
echo.
echo 1. Login
echo 2. Logout
echo 3. Status
echo 4. Cancel
echo.
set /p "CHOICE=Select action (1-4): "

if "%CHOICE%"=="1" goto do_login
if "%CHOICE%"=="2" goto do_logout
if "%CHOICE%"=="3" goto do_status
goto done

:do_login
set "AUTO_MODE=1"
echo.
echo [AUTH] Starting login flow...
call "%CLI%" login
echo.
echo [AUTH] Current status:
call "%CLI%" login status
goto done

:do_logout
set "AUTO_MODE=1"
echo.
echo [AUTH] Logging out...
call "%CLI%" logout
echo.
echo [AUTH] Current status:
call "%CLI%" login status
goto done

:do_status
set "AUTO_MODE=1"
echo.
echo [AUTH] Checking status...
call "%CLI%" login status
goto done

:done
echo.
if defined AUTO_MODE exit /b
pause
