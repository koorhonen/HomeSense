@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m homesense.server
  goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
  python -m homesense.server
  goto finished
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m homesense.server
  goto finished
)
echo Python was not found. Install a supported Python 3.11+ release from https://www.python.org/downloads/windows/
:finished
pause
