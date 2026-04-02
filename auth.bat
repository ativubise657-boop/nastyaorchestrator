@echo off
setlocal

set "ROOT=%~dp0"
set "CLI=%ROOT%tools\codex-npx.cmd"
set "AUTO_MODE="

cd /d "%ROOT%"

if not exist "%CLI%" (
    echo [ERROR] Codex CLI wrapper not found:
    echo         %CLI%
    echo.
    pause
    exit /b 1
)

if not "%~1"=="" set "AUTO_MODE=1"

if /I "%~1"=="login" goto do_login
if /I "%~1"=="login-browser" goto do_login_browser
if /I "%~1"=="logout" goto do_logout
if /I "%~1"=="status" goto do_status

echo.
echo ==========================================
echo   GPT Auth (Codex CLI)
echo ==========================================
echo.
echo 1. Login (device auth, recommended)
echo 2. Login (browser callback)
echo 3. Logout
echo 4. Status
echo 5. Cancel
echo.
set /p "CHOICE=Select action (1-5): "

if "%CHOICE%"=="1" goto do_login
if "%CHOICE%"=="2" goto do_login_browser
if "%CHOICE%"=="3" goto do_logout
if "%CHOICE%"=="4" goto do_status
goto done

:do_login
echo.
echo [AUTH] Starting device auth login flow...
call "%CLI%" login --device-auth
goto show_status

:do_login_browser
echo.
echo [AUTH] Starting browser login flow...
call "%CLI%" login

:show_status
echo.
echo [AUTH] Current status:
call "%CLI%" login status
if errorlevel 1 (
    echo [AUTH] Not authorized yet.
) else (
    echo [AUTH] Authorized.
)
goto done

:do_logout
echo.
echo [AUTH] Logging out...
call "%CLI%" logout
echo.
echo [AUTH] Current status:
call "%CLI%" login status
goto done

:do_status
echo.
echo [AUTH] Checking status...
call "%CLI%" login status
goto done

:done
echo.
if defined AUTO_MODE exit /b
pause
