@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Novel Ad Factory
cd /d "%~dp0"

echo ==========================================
echo     Novel Ad Factory
echo ==========================================
echo.

:: 1. Check Python
python --version >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] Python 未安装或不在 PATH 中！
    echo 请安装 Python 3.10+ https://www.python.org/downloads/
    echo 安装时务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)
echo [OK] Python:
python --version 2>&1
echo.

:: 2. Check pip
python -m pip --version >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] pip 不可用，请确保 Python 安装时勾选了 pip
    pause
    exit /b 1
)

:: 3. Check / install dependencies
python -c "import fastapi, uvicorn, requests, PIL, apscheduler" >nul 2>&1
if !errorlevel! neq 0 (
    echo [依赖缺失] 正在安装，请稍候...
    python -m pip install -r requirements.txt -q
    if !errorlevel! neq 0 (
        echo [ERROR] 依赖安装失败，请手动运行: pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo [OK] 依赖安装完成。
) else (
    echo [OK] 依赖已就绪。
)
echo.

:: 4. Check config
if not exist "config.json" (
    echo {"api_key": "请填入API_KEY", "api_url": "https://api.geeknow.top/v1", "chat_model_name": "gemini-3.1-pro-preview", "image_model_name": "gpt-image-2", "analysis_prompt": "", "concurrency": 4, "meta": {"app_id": "", "app_secret": "", "default_access_token": "", "api_version": "v25.0", "sync_interval_seconds": 300, "rate_limit_per_second": 4}} > config.json
    echo [WARN] 已创建 config.json，请编辑填入 api_key 和 Meta 配置后重新运行！
    start notepad config.json
    pause
    exit /b 0
)
echo [OK] config.json 存在

:: 5. Kill old process on port 8000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " 2^>nul ^| findstr "LISTENING" 2^>nul') do (
    echo [WARN] 端口 8000 被 PID %%a 占用，正在释放...
    taskkill /F /PID %%a 2>nul
    timeout /t 1 /nobreak >nul
)

:: 6. Start server
echo.
echo ==========================================
echo  启动成功！
echo  地址: http://127.0.0.1:8000/static/index.html
echo  按 Ctrl+C 停止服务
echo ==========================================
start "" http://127.0.0.1:8000/static/index.html
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

pause
