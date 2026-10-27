@echo off
setlocal
cd /d "%~dp0"
if not defined UV_PROJECT_ENVIRONMENT set "UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\PaintByNumbers\venv-1.4"
uv --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Install uv from https://astral.sh/uv/install/windows/
    pause
    exit /b 1
)
echo Starting Paint-by-Numbers at http://127.0.0.1:8000
uv run --locked --no-dev server.py
if errorlevel 1 (
    echo [ERROR] Startup failed. Keep Windows security enabled.
    echo See README troubleshooting for a permitted Python runtime and a fresh local environment.
    pause
)
