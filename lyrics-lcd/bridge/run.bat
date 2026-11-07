@echo off
REM Start the bridge on Windows (double-click this file). First run creates the virtual environment.
REM Extra arguments are passed on, e.g.  run.bat --port COM3
cd /d "%~dp0"

if not exist .venv (
  echo Creating virtual environment ^(first run only^) ...
  py -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)

.venv\Scripts\python bridge.py %*
pause
