@echo off
chcp 65001 >nul 2>&1
setlocal

set "ROOT=%~dp0"
set "TARGET=%ROOT%start.bat"
set "PS1=%TEMP%\nastya-shortcut-%RANDOM%.ps1"

if not exist "%TARGET%" (
    echo [ERROR] Could not find start.bat in:
    echo         %TARGET%
    pause
    exit /b 1
)

> "%PS1%" echo $target = [IO.Path]::GetFullPath('%TARGET%')
>> "%PS1%" echo $workdir = [IO.Path]::GetFullPath('%ROOT%')
>> "%PS1%" echo $desktop = [Environment]::GetFolderPath('Desktop')
>> "%PS1%" echo if (-not $desktop) { throw 'Desktop path not found' }
>> "%PS1%" echo $shortcutPath = Join-Path $desktop 'Nastya Orchestrator.lnk'
>> "%PS1%" echo $shell = New-Object -ComObject WScript.Shell
>> "%PS1%" echo $shortcut = $shell.CreateShortcut($shortcutPath)
>> "%PS1%" echo $shortcut.TargetPath = $target
>> "%PS1%" echo $shortcut.WorkingDirectory = $workdir
>> "%PS1%" echo $shortcut.Description = 'Launch Nastya Orchestrator'
>> "%PS1%" echo $shortcut.IconLocation = $env:SystemRoot + '\System32\SHELL32.dll,220'
>> "%PS1%" echo $shortcut.Save()
>> "%PS1%" echo Write-Output $shortcutPath

for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"`) do set "SHORTCUT=%%i"
set "PS_EXIT=%ERRORLEVEL%"
del "%PS1%" >nul 2>&1

if not "%PS_EXIT%"=="0" (
    echo [ERROR] Failed to create desktop shortcut.
    pause
    exit /b 1
)

echo [OK] Shortcut created:
echo      %SHORTCUT%
exit /b 0
