@echo off
chcp 65001 >nul
echo ============================================
echo   Novel Ad Factory - 服务器部署打包
echo ============================================
echo.

set PACK_NAME=NovelAdFactory_deploy_%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set PACK_NAME=%PACK_NAME: =0%
set PACK_FILE=%PACK_NAME%.zip

echo [1/3] 创建临时目录...
if exist _deploy_pack rmdir /s /q _deploy_pack
mkdir _deploy_pack

echo [2/3] 复制文件...
:: 核心源码
copy main.py _deploy_pack\ >nul
copy database.py _deploy_pack\ >nul
copy meta_api.py _deploy_pack\ >nul
copy scraper.py _deploy_pack\ >nul
copy analytics.py _deploy_pack\ >nul
copy delivery.py _deploy_pack\ >nul
copy test_meta_creds.py _deploy_pack\ >nul
copy config.json _deploy_pack\ >nul
copy requirements.txt _deploy_pack\ >nul 2>nul

:: 前端
xcopy /e /i /q static _deploy_pack\static\

:: 提示词
xcopy /e /i /q prompts _deploy_pack\prompts\

:: 字体 (仅复制 .ttf/.ttc，跳过 .fon)
xcopy /e /i /q ziti _deploy_pack\ziti\

:: 数据文件 (包含数据库)
xcopy /e /i /q data _deploy_pack\data\

:: 音乐
if exist 音乐 xcopy /e /i /q 音乐 _deploy_pack\音乐\ 2>nul

:: 脚本工具
if exist scripts xcopy /e /i /q scripts _deploy_pack\scripts\

:: 模板索引
if exist templates_index.json copy templates_index.json _deploy_pack\ >nul
if exist video_styles.json copy video_styles.json _deploy_pack\ >nul

:: 启动脚本
echo @echo off > _deploy_pack\一键启动.bat
echo chcp 65001 ^>nul >> _deploy_pack\一键启动.bat
echo echo 启动 Novel Ad Factory... >> _deploy_pack\一键启动.bat
echo pip install -r requirements.txt --quiet >> _deploy_pack\一键启动.bat
echo uvicorn main:app --host 0.0.0.0 --port 8000 >> _deploy_pack\一键启动.bat

echo [3/3] 打包 %PACK_FILE%...
powershell -Command "Compress-Archive -Path _deploy_pack\* -DestinationPath %PACK_FILE% -Force"

rmdir /s /q _deploy_pack

echo.
echo ============================================
echo   打包完成: %PACK_FILE%
echo   上传到服务器后解压，运行 一键启动.bat
echo ============================================
pause
