@echo off
REM ============================================================================
REM Full local build pipeline for Nastya Orchestrator Tauri app.
REM Requires MSVC + Rust already active (run from dev-shell.bat).
REM
REM Steps:
REM   1. Build frontend (npm run build in frontend/)
REM   2. Build backend.exe and worker.exe via PyInstaller
REM   3. Copy .exe files to src-tauri/binaries with MSVC triple suffix
REM   4. Run cargo tauri build
REM ============================================================================
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

REM Check that we're inside dev-shell (cl and cargo must be present)
where cargo >nul 2>&1
if errorlevel 1 (
    echo [local-build] ERROR: cargo not in PATH. Run this from dev-shell.bat
    pause
    exit /b 1
)

REM -------------------------------------------------------------------------
echo.
echo [local-build] Step 1/4: Building frontend...
cd /d "%ROOT%frontend"
if not exist "node_modules" (
    echo [local-build] node_modules missing, running npm install...
    call npm install
    if errorlevel 1 (
        echo [local-build] ERROR: npm install failed
        pause
        exit /b 1
    )
)
call npm run build
if errorlevel 1 (
    echo [local-build] ERROR: npm run build failed
    pause
    exit /b 1
)

REM -------------------------------------------------------------------------
echo.
echo [local-build] Step 2/4: Building backend.exe and worker.exe via PyInstaller...
cd /d "%ROOT%"
call "%ROOT%build\build.bat"
if errorlevel 1 (
    echo [local-build] ERROR: PyInstaller build failed
    pause
    exit /b 1
)

REM -------------------------------------------------------------------------
echo.
echo [local-build] Step 3/4: Copying sidecar binaries...
if not exist "%ROOT%src-tauri\binaries" mkdir "%ROOT%src-tauri\binaries"
copy /Y "%ROOT%dist\nastya-backend.exe" "%ROOT%src-tauri\binaries\nastya-backend-x86_64-pc-windows-msvc.exe" >nul
if errorlevel 1 (
    echo [local-build] ERROR: backend.exe not found in dist/
    pause
    exit /b 1
)
copy /Y "%ROOT%dist\nastya-worker.exe" "%ROOT%src-tauri\binaries\nastya-worker-x86_64-pc-windows-msvc.exe" >nul
if errorlevel 1 (
    echo [local-build] ERROR: worker.exe not found in dist/
    pause
    exit /b 1
)

REM -------------------------------------------------------------------------
echo.
echo [local-build] Step 4/4: Running cargo tauri build...
cd /d "%ROOT%"
cargo tauri build
if errorlevel 1 (
    echo [local-build] ERROR: cargo tauri build failed
    pause
    exit /b 1
)

echo.
echo [local-build] ============================================================
echo [local-build] DONE. Installer:
echo [local-build]   %ROOT%src-tauri\target\release\bundle\nsis\
echo [local-build] ============================================================
echo.
pause
