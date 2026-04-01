@echo off
setlocal

REM WindowsApps codex.exe can be inaccessible from subprocess on some systems.
REM This wrapper runs the npm-published Codex CLI through npx instead.
chcp 65001 >nul 2>&1
npx.cmd -y @openai/codex %*
