@echo off
chcp 65001 >nul 2>&1

set "ROOT=%~dp0"
set "VAULT=%ROOT%data\notes"
set "INSTALLER=%ROOT%tools\Obsidian-installer.exe"

where obsidian >nul 2>&1
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Obsidian\Obsidian.exe" goto :open_vault

    echo Obsidian not installed.
    if exist "%INSTALLER%" (
        echo Installing Obsidian...
        start "" "%INSTALLER%"
        echo After installation, run this script again.
        pause
        exit /b 0
    ) else (
        echo Download from https://obsidian.md and install.
        pause
        exit /b 1
    )
)

:open_vault
echo Opening vault: %VAULT%
if exist "%LOCALAPPDATA%\Obsidian\Obsidian.exe" (
    start "" "%LOCALAPPDATA%\Obsidian\Obsidian.exe" "obsidian://open?path=%VAULT%"
) else (
    start "" obsidian "obsidian://open?path=%VAULT%"
)
