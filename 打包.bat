@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

set "OUTPUT=NovelAdFactory_部署包.zip"

echo ==========================================
echo   打包部署包: %OUTPUT%
echo ==========================================
echo.

if exist "%OUTPUT%" del /q "%OUTPUT%"

:: 使用 PowerShell 打包
powershell -Command "Compress-Archive -Path 'main.py','database.py','scraper.py','analytics.py','requirements.txt','config.json','video_styles.json','templates_index.json','CLAUDE.md','一键启动.bat','打包.bat','static','prompts','ziti','scripts' -DestinationPath '%OUTPUT%' -Force"

if !errorlevel! neq 0 (
    echo [ERROR] 打包失败
    pause
    exit /b 1
)

for %%F in ("%OUTPUT%") do echo [OK] %%F  (%%~zF bytes)

echo.
echo 部署步骤:
echo   1. 解压 %OUTPUT% 到目标电脑
echo   2. 编辑 config.json，填入 api_key
echo   3. 双击 一键启动.bat
echo.
pause
