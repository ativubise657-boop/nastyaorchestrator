@echo off
set "ROOT=%~dp0"
set "PATH=%ROOT%runtime\git\bin;%ROOT%runtime\git\usr\bin;%ROOT%runtime\python;%ROOT%runtime\python\Scripts;%ROOT%runtime\node;%ROOT%runtime\node\node_modules\.bin;%PATH%"
set "CLAUDE_CODE_GIT_BASH_PATH=%ROOT%runtime\git\bin\bash.exe"
cd /d "%ROOT%"
"%ROOT%runtime\python\python.exe" -c "import sys; sys.path.insert(0, '.'); from worker.main import main; import asyncio; asyncio.run(main())"
