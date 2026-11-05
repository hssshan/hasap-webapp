@echo off
REM Sourcing Copilot - one command to run on Windows.
REM Double-click this file, or run it from Command Prompt. Then open http://localhost:8000
setlocal
cd /d "%~dp0"

REM Find Python (python.exe or the "py" launcher)
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (where py >nul 2>nul && set "PY=py -3")
if not defined PY (
  echo.
  echo Python was not found.
  echo Install Python 3.12 from https://www.python.org/downloads/
  echo IMPORTANT: on the first installer screen tick "Add python.exe to PATH".
  echo Then close this window and run run.bat again.
  echo.
  pause
  exit /b 1
)

if not exist .env copy .env.example .env >nul

if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  %PY% -m venv .venv || goto :fail
)

echo Installing packages (first time takes a few minutes)...
.venv\Scripts\python.exe -m pip install -q --upgrade pip setuptools wheel || goto :fail
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || goto :fail

echo.
echo  Sourcing Copilot is starting...
echo  Open http://localhost:8000 in your browser.
echo  On your phone (same Wi-Fi): http://YOUR-PC-IP:8000  (find the IP with: ipconfig)
echo  Press Ctrl+C to stop.
echo.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
goto :eof

:fail
echo.
echo Something went wrong (see the messages above).
pause
exit /b 1
