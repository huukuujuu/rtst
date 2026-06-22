@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=.venv\Scripts\python.exe"
set "CHROME_DEBUG_PORT=9222"
set "CHROME_PROFILE=%CD%\.chrome-rtst"

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

set "CHROME_EXE="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if "%CHROME_EXE%"=="" (
    echo [RTST] Could not find chrome.exe.
    echo [RTST] Install Chrome or launch a Chromium browser with --remote-debugging-port=%CHROME_DEBUG_PORT%.
    pause
    exit /b 1
)

echo [RTST] Launching Chrome for browser DOM subtitle mode...
start "" "%CHROME_EXE%" --remote-debugging-port=%CHROME_DEBUG_PORT% --remote-allow-origins=http://127.0.0.1:%CHROME_DEBUG_PORT% --user-data-dir="%CHROME_PROFILE%" --new-window "about:blank"

echo [RTST] Launching desktop app with OpenAI Codex OAuth login...
"%PYTHON_EXE%" main.py --codex-oauth-login --browser-dom

echo [RTST] Desktop app closed.
pause
