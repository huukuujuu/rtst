@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [RTST] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [RTST] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

if not exist ".env" (
    echo [RTST] .env was not found. Creating it from .env.example...
    copy ".env.example" ".env" >nul
)

echo [RTST] Checking Python dependencies...
"%PYTHON_EXE%" -c "import PySide6, mss, PIL, pytesseract, requests, websocket, dotenv, winocr, fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo [RTST] Installing dependencies...
    "%PYTHON_EXE%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [RTST] Dependency installation failed.
        pause
        exit /b 1
    )
)

echo [RTST] Launching desktop app with OpenAI Codex OAuth login...
"%PYTHON_EXE%" main.py --codex-oauth-login

echo [RTST] Desktop app closed.
pause
