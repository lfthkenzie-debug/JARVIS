@echo off
rem Launch JARVIS on Windows:  run.bat [--ui | --text | --selftest | --oneshot "open notepad"]
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python -m jarvis %*
