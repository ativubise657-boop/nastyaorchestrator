@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

title Portable Build - Nastya Orchestrator

set "SRC=%~dp0"
set "OUT=%SRC%portablenastyaorc"
set "RUNTIME=%OUT%\runtime"
set "TEMP_DL=%SRC%_download_temp"

set "PY_VER=3.12.8"
set "PY_ZIP=python-%PY_VER%-embed-amd64.zip"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/%PY_ZIP%"

set "NODE_VER=20.18.1"
set "NODE_ZIP=node-v%NODE_VER%-win-x64.zip"
set "NODE_URL=https://nodejs.org/dist/v%NODE_VER%/%NODE_ZIP%"
set "NODE_DIR=node-v%NODE_VER%-win-x64"

set "GIT_VER=2.47.1"
set "GIT_ZIP=PortableGit-%GIT_VER%-64-bit.7z.exe"
set "GIT_URL=https://github.com/git-for-windows/git/releases/download/v%GIT_VER%.windows.1/%GIT_ZIP%"

echo.
echo === Portable Build: Nastya Orchestrator ===
echo     Python %PY_VER% + Node.js %NODE_VER% + Git %GIT_VER%
echo.

if not exist "%RUNTIME%" mkdir "%RUNTIME%"
if not exist "%TEMP_DL%" mkdir "%TEMP_DL%"
if not exist "%OUT%\data" mkdir "%OUT%\data"
if not exist "%OUT%\data\documents" mkdir "%OUT%\data\documents"

:: === 1. PYTHON EMBEDDED ===

if exist "%RUNTIME%\python\python.exe" (
    echo [OK] Python already installed
    goto :py_done
)

echo [1/7] Python %PY_VER%...
if not exist "%TEMP_DL%\%PY_ZIP%" (
    curl -L -o "%TEMP_DL%\%PY_ZIP%" "%PY_URL%"
    if errorlevel 1 ( echo [ERROR] Download Python failed & pause & exit /b 1 )
)

echo       Extracting...
if not exist "%RUNTIME%\python" mkdir "%RUNTIME%\python"
powershell -Command "Expand-Archive -Force '%TEMP_DL%\%PY_ZIP%' '%RUNTIME%\python'"

set "PTH=%RUNTIME%\python\python312._pth"
if exist "%PTH%" (
    powershell -Command "(Get-Content '%PTH%') -replace '#import site','import site' | Set-Content '%PTH%'"
    echo .>> "%PTH%"
)

echo       Installing pip...
curl -sL -o "%TEMP_DL%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
"%RUNTIME%\python\python.exe" "%TEMP_DL%\get-pip.py" --quiet --no-warn-script-location
echo [OK] Python %PY_VER%

:py_done

:: === 2. PYTHON DEPS ===

if exist "%RUNTIME%\python\Lib\site-packages\fastapi" (
    echo [OK] Python deps
    goto :pydeps_done
)

echo [2/7] Python dependencies...
"%RUNTIME%\python\python.exe" -m pip install --quiet --no-warn-script-location -r "%SRC%requirements.txt"
if errorlevel 1 ( echo [ERROR] pip install failed & pause & exit /b 1 )
echo [OK] Dependencies installed

:pydeps_done

:: === 3. NODE.JS PORTABLE ===

if exist "%RUNTIME%\node\node.exe" (
    echo [OK] Node.js
    goto :node_done
)

echo [3/7] Node.js %NODE_VER%...
if not exist "%TEMP_DL%\%NODE_ZIP%" (
    curl -L -o "%TEMP_DL%\%NODE_ZIP%" "%NODE_URL%"
    if errorlevel 1 ( echo [ERROR] Download Node.js failed & pause & exit /b 1 )
)

echo       Extracting...
powershell -Command "Expand-Archive -Force '%TEMP_DL%\%NODE_ZIP%' '%TEMP_DL%'"
if exist "%RUNTIME%\node" rmdir /s /q "%RUNTIME%\node"
move "%TEMP_DL%\%NODE_DIR%" "%RUNTIME%\node" >nul
echo [OK] Node.js %NODE_VER%

:node_done

:: === 4. CLAUDE CLI ===

if exist "%RUNTIME%\node\node_modules\@anthropic-ai\claude-code" (
    echo [OK] Claude CLI
    goto :claude_done
)

echo [4/7] Claude CLI...
set "PATH=%RUNTIME%\node;%PATH%"
call "%RUNTIME%\node\npm.cmd" install --prefix "%RUNTIME%\node" @anthropic-ai/claude-code --silent 2>nul
echo [OK] Claude CLI

:claude_done

:: === 5. GIT PORTABLE ===

if exist "%RUNTIME%\git\bin\bash.exe" (
    echo [OK] Git portable
    goto :git_done
)

echo [5/7] Git portable %GIT_VER%...
if not exist "%TEMP_DL%\%GIT_ZIP%" (
    curl -L -o "%TEMP_DL%\%GIT_ZIP%" "%GIT_URL%"
    if errorlevel 1 (
        echo [WARN] Could not download Git portable
        goto :git_done
    )
)

echo       Extracting (self-extracting archive)...
if not exist "%RUNTIME%\git" mkdir "%RUNTIME%\git"
"%TEMP_DL%\%GIT_ZIP%" -o"%RUNTIME%\git" -y >nul 2>&1
if exist "%RUNTIME%\git\bin\bash.exe" (
    echo [OK] Git %GIT_VER%
) else (
    echo [WARN] Git extraction may have failed
)

:git_done

:: === 6. CODE + NOTES ===

echo [6/7] Copying code...

xcopy /E /Y /I /Q "%SRC%backend" "%OUT%\backend" >nul 2>&1
xcopy /E /Y /I /Q "%SRC%worker" "%OUT%\worker" >nul 2>&1
xcopy /E /Y /I /Q "%SRC%config" "%OUT%\config" >nul 2>&1

if exist "%SRC%frontend\dist\index.html" (
    xcopy /E /Y /I /Q "%SRC%frontend\dist" "%OUT%\frontend\dist" >nul 2>&1
) else (
    echo [!] frontend/dist not found! Run: cd frontend ^& npm run build
)

copy /Y "%SRC%requirements.txt" "%OUT%\" >nul

:: Notes (Obsidian vault) — путь нужно будет настроить для Насти
set "NOTES_SRC="
if defined NOTES_SRC (
    if exist "%NOTES_SRC%" (
        echo       Copying notes from vault...
        xcopy /E /Y /I /Q "%NOTES_SRC%" "%OUT%\data\notes" >nul 2>&1
    )
) else (
    echo [!] Notes vault path not configured - creating empty
    if not exist "%OUT%\data\notes" mkdir "%OUT%\data\notes"
)

if not exist "%OUT%\data\notes\.obsidian" mkdir "%OUT%\data\notes\.obsidian"

:: Obsidian installer
if not exist "%OUT%\tools" mkdir "%OUT%\tools"
if not exist "%OUT%\tools\Obsidian-installer.exe" (
    echo       Downloading Obsidian...
    curl -L -o "%OUT%\tools\Obsidian-installer.exe" "https://github.com/obsidianmd/obsidian-releases/releases/download/v1.8.9/Obsidian.1.8.9.exe" 2>nul
)

echo [OK] Code + notes copied

:: === 7. SCRIPTS ===

echo [7/7] Scripts...
copy /Y "%SRC%portable-start.bat" "%OUT%\start.bat" >nul
copy /Y "%SRC%portable-stop.bat" "%OUT%\stop.bat" >nul
copy /Y "%SRC%portable-auth.bat" "%OUT%\auth.bat" >nul
copy /Y "%SRC%portable-run-backend.bat" "%OUT%\run-backend.bat" >nul
copy /Y "%SRC%portable-run-worker.bat" "%OUT%\run-worker.bat" >nul
copy /Y "%SRC%portable-open-notes.bat" "%OUT%\open-notes.bat" >nul
echo [OK] All scripts copied

:: === CLEANUP ===

echo.
echo Cleanup...
if exist "%TEMP_DL%" rmdir /s /q "%TEMP_DL%"

echo.
echo =============================================
echo  DONE! Portable version ready:
echo.
echo  %OUT%\
echo    runtime\python\   Python %PY_VER%
echo    runtime\node\     Node.js %NODE_VER% + Claude CLI
echo    runtime\git\      Git %GIT_VER% (for Claude CLI)
echo    data\notes\       Obsidian vault
echo    tools\            Obsidian installer
echo    backend\          FastAPI server
echo    worker\           Claude CLI worker
echo    frontend\dist\    Built UI
echo.
echo  Scripts:
echo    start.bat         Start app (double-click)
echo    stop.bat          Stop app
echo    auth.bat          Claude CLI authentication
echo    open-notes.bat    Open Obsidian vault
echo.
echo  First run: auth.bat then start.bat
echo =============================================
echo.
pause
