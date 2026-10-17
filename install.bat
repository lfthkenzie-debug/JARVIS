@echo off
rem JARVIS installer for Windows 10/11 — run from an Explorer double-click
rem or:  install.bat
setlocal
cd /d "%~dp0"
title JARVIS installer

where py >nul 2>nul
if errorlevel 1 (
    echo X Python launcher "py" not found.
    echo   Install Python 3.10+ from python.org and tick "Add python.exe to PATH".
    exit /b 1
)

echo [>] Creating virtualenv .venv
py -3 -m venv .venv
if errorlevel 1 exit /b 1
call .venv\Scripts\activate.bat

echo [>] Upgrading pip
python -m pip install -q --upgrade pip setuptools wheel

echo [>] Installing JARVIS core + web tiers
python -m pip install -r requirements-core.txt -r requirements-web.txt

echo [>] Training intent models
python -c "from jarvis.main import _boot; _boot()"

echo.
echo ----------------------------------------------------------
echo  JARVIS is installed. Try:
echo     run.bat --ui          holographic control panel
echo     run.bat --text        typed conversation
echo     run.bat               full voice mode (needs a microphone)
echo  Windows native TTS uses PowerShell voice; for the Fish Audio
echo  "JARVIS" voice put your key in jarvis_data\fish_api_key.txt.
echo ----------------------------------------------------------
pause
