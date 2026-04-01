@echo off
chcp 65001 >nul 2>&1

set "ROOT=%~dp0"
set "CLI=%ROOT%runtime\node\node_modules\.bin\codex.cmd"
set "PATH=%ROOT%runtime\git\bin;%ROOT%runtime\git\usr\bin;%ROOT%runtime\node;%PATH%"

if not exist "%CLI%" (
    echo [ERROR] Codex CLI not found
    pause
    exit /b 1
)

echo.
echo === Codex CLI Authentication ===
echo.
echo Sign in with your ChatGPT account or API key.
echo Browser will open automatically.
echo.

call "%CLI%" login

echo.
if errorlevel 1 (
    echo [ERROR] Auth failed. Try again.
) else (
    echo [OK] Auth done! Now run start.bat
)
echo.
pause
