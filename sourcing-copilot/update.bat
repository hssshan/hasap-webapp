@echo off
REM Sourcing Copilot - one-click update. Double-click this file.
REM Stops the running app, downloads the latest version from GitHub, keeps your
REM data folder, .env and installed packages, then starts the app again.
setlocal
cd /d "%~dp0"
set "BRANCH=claude/sourcing-copilot-resale-xjcr4h"
set "ZIPURL=https://codeload.github.com/hssshan/hasap-webapp/zip/refs/heads/%BRANCH%"
set "TMP_DIR=%TEMP%\copilot-update"

echo Stopping the running app (if any)...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

echo Downloading the latest version...
if exist "%TMP_DIR%" rmdir /s /q "%TMP_DIR%"
mkdir "%TMP_DIR%"
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%ZIPURL%' -OutFile '%TMP_DIR%\latest.zip'; Expand-Archive -Path '%TMP_DIR%\latest.zip' -DestinationPath '%TMP_DIR%' -Force" || goto :fail

set "SRC="
for /d %%D in ("%TMP_DIR%\hasap-webapp-*") do set "SRC=%%D\sourcing-copilot"
if not defined SRC goto :fail
if not exist "%SRC%\app\main.py" goto :fail

echo Installing the update (your data and .env are kept)...
robocopy "%SRC%" "%~dp0." /E /XD data .venv /XF .env /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :fail
rmdir /s /q "%TMP_DIR%"

echo Updated. Starting the app...
call "%~dp0run.bat"
goto :eof

:fail
echo.
echo The update failed (see the messages above). Your app and data were not changed.
pause
exit /b 1
