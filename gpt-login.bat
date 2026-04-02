@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
call "%ROOT%auth.bat" login
echo.
pause
