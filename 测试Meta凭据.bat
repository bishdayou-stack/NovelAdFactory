@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Meta 凭据验证
echo ==========================================
echo   Meta 凭据验证工具
echo ==========================================
echo.
python 测试Meta凭据.py
pause
