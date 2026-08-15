@echo off
setlocal enabledelayedexpansion
title Novel Ad Factory
cd /d "%~dp0"

echo ==========================================
echo     Novel Ad Factory
echo ==========================================
echo.

:: 1. Locate Python (python -> py launcher -> python3)
set "PY=python"
python --version >nul 2>&1
if errorlevel 1 (
    set "PY=py -3"
    py -3 --version >nul 2>&1
    if errorlevel 1 (
        set "PY=python3"
        python3 --version >nul 2>&1
        if errorlevel 1 (
            echo [ERROR] Python 3.10+ not found.
            echo Install from https://www.python.org/downloads/
            echo Make sure to check "Add Python to PATH" during install.
            pause
            exit /b 1
        )
    )
)
echo [OK] Python:
!PY! --version 2>&1
echo.

:: 2. Check pip
!PY! -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip not available. Reinstall Python with pip enabled.
    pause
    exit /b 1
)
echo [OK] pip available
echo.

:: 3. Check / install dependencies
echo [CHECK] Checking dependencies...
!PY! -c "import fastapi, uvicorn, requests, PIL, moviepy, numpy, multipart, sse_starlette, imageio_ffmpeg, apscheduler, bs4, lxml" >nul 2>&1
if errorlevel 1 (
    echo [MISSING] Installing dependencies, first run takes 1-3 minutes...
    !PY! -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [RETRY] Falling back to Tsinghua mirror...
        !PY! -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        if errorlevel 1 (
            echo [ERROR] Dependency install failed. Run manually: pip install -r requirements.txt
            pause
            exit /b 1
        )
    )
    echo [OK] Dependencies installed.
) else (
    echo [OK] Dependencies ready.
)
echo.

:: 4. Check config.json
if not exist "config.json" (
    echo {"api_key": "FILL_API_KEY", "api_url": "https://api.geeknow.top/v1", "chat_model_name": "gemini-3.1-pro-preview", "image_model_name": "gpt-image-2", "analysis_prompt": "", "concurrency": 4, "meta": {"app_id": "", "app_secret": "", "default_access_token": "", "proxy": "", "api_version": "v25.0", "sync_interval_seconds": 300, "rate_limit_per_second": 4}} > config.json
    echo [WARN] Created config.json. Fill api_key and Meta config, then re-run.
    start notepad config.json
    pause
    exit /b 0
)
echo [OK] config.json exists

:: 5. Free port 8000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " 2^>nul ^| findstr "LISTENING" 2^>nul') do (
    echo [WARN] Port 8000 in use by PID %%a, releasing...
    taskkill /F /PID %%a 2>nul
    timeout /t 1 /nobreak >nul
)

:: 6. Start server
echo.
echo ==========================================
echo  Started! Open: http://127.0.0.1:8000/static/index.html
echo  Press Ctrl+C to stop
echo ==========================================
start "" http://127.0.0.1:8000/static/index.html
!PY! -m uvicorn main:app --host 0.0.0.0 --port 8000

pause