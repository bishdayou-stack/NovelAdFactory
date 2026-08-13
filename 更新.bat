@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Novel Ad Factory - 代码更新
cd /d "%~dp0"

echo ============================================
echo   Novel Ad Factory - 从 GitHub 更新代码
echo ============================================
echo.

:: 1. 停止正在运行的服务（释放 8000 端口）
echo [1/3] 停止服务...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " 2^>nul ^| findstr "LISTENING" 2^>nul') do (
    echo   释放端口 8000 (PID %%a)
    taskkill /F /PID %%a 2>nul
    timeout /t 1 /nobreak >nul
)

:: 2. 备份本地 config.json（防止 pull 覆盖服务器上的代理等配置）
echo [2/3] 备份本地配置...
if exist config.json copy /y config.json config.local.json >nul

:: 3. 拉取最新代码
echo [3/3] 从 GitHub 拉取最新代码...
git pull origin master

:: 4. 如果 config.json 被更新覆盖，恢复本地配置（保留服务器 proxy 设置）
if exist config.local.json (
    python -c "import json,os; bak=json.load(open('config.local.json',encoding='utf-8')); cur=json.load(open('config.json',encoding='utf-8')); cur['meta']=bak.get('meta',{}); json.dump(cur,open('config.json','w',encoding='utf-8'),ensure_ascii=False,indent=2)" 2>nul
    del config.local.json 2>nul
)

echo.
echo ============================================
echo   更新完成！请重新运行 一键启动.bat
echo ============================================
pause
