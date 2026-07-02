@echo off
title Innovedex 2026 Sorter - Launcher
cd /d "%~dp0"

echo ==================================================
echo   Innovedex 2026 Sorter - Launching System
echo ==================================================
echo.

:: Check if .venv exists, if so, run launch_all.py directly to start instantly
if exist ".venv\Scripts\python.exe" (
    echo [OK] Found .venv. Launching nodes immediately...
    .venv\Scripts\python.exe launch_all.py
) else if exist "venv\Scripts\python.exe" (
    echo [OK] Found venv. Launching nodes immediately...
    venv\Scripts\python.exe launch_all.py
) else (
    echo [WARN] Virtual environment (.venv/venv) not found!
    echo Running full setup script...
    powershell -ExecutionPolicy Bypass -File .\setup.ps1
)

echo.
echo ==================================================
echo   System stopped. Press any key to exit.
echo ==================================================
pause
