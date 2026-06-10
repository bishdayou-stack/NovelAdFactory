@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Meta 凭据验证
echo ==========================================
echo   Meta 凭据验证工具
echo ==========================================
echo.
python test_meta_creds.py
pause
