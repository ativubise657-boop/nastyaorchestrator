@echo off
chcp 65001 >nul 2>&1

set "ROOT=%~dp0"
set "NODE=%ROOT%runtime\node\node.exe"
set "CLI=%ROOT%runtime\node\node_modules\@anthropic-ai\claude-code\cli.js"
set "PATH=%ROOT%runtime\git\bin;%ROOT%runtime\git\usr\bin;%ROOT%runtime\node;%PATH%"
set "CLAUDE_CODE_GIT_BASH_PATH=%ROOT%runtime\git\bin\bash.exe"

if not exist "%NODE%" (
    echo [ERROR] Node.js not found in runtime\node\
    pause
    exit /b 1
)

if not exist "%CLI%" (
    echo [ERROR] Claude CLI not found
    pause
    exit /b 1
)

echo.
echo === Claude CLI Authentication ===
echo.
echo Sign in with your Claude.ai account (Pro/Max required).
echo Browser will open automatically.
echo.

"%NODE%" "%CLI%" auth login

echo.
if errorlevel 1 (
    echo [ERROR] Auth failed. Try again.
) else (
    echo [OK] Auth done! Now run start.bat
)
echo.
pause
