@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on this computer.
    echo Install it from https://www.python.org/downloads/
    echo IMPORTANT: on the first setup screen, check "Add Python to PATH".
    pause
    exit /b 1
)

echo Checking for the Flask package (installs it if missing)...
python -m pip install --quiet --disable-pip-version-check flask

python app.py %*
pause
