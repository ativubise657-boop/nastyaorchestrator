@echo off
REM ============================================================================
REM Nastya Orchestrator — dev GUI launcher.
REM Loads MSVC + Rust environment via dev-shell env script, then runs tkinter GUI.
REM Double-click to open the GUI window.
REM ============================================================================
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

REM Load MSVC env (if PortableBuildTools installed)
if exist "D:\BuildTools\devcmd.bat" (
    call "D:\BuildTools\devcmd.bat"
)

REM Load signing password if present (optional)
if exist "D:\Share\Rust_MSVC\sign-password.bat" (
    call "D:\Share\Rust_MSVC\sign-password.bat"
    set "TAURI_SIGNING_PRIVATE_KEY=%USERPROFILE%\.tauri\nastya.key"
)

REM Load local dev env (GITHUB_PAT для Push config и т.п.) — файл в .gitignore
if exist "%ROOT%dev-gui.env.bat" (
    call "%ROOT%dev-gui.env.bat"
)

REM Venv check
if not exist ".venv-build\Scripts\pythonw.exe" (
    echo ERROR: .venv-build\Scripts\pythonw.exe not found.
    echo Run build\build.bat once to create the venv.
    pause
    exit /b 1
)

REM Launch GUI with pythonw (no console window)
start "" "%ROOT%.venv-build\Scripts\pythonw.exe" "%ROOT%dev-gui.pyw"
