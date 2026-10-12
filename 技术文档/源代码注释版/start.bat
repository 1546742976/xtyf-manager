@echo off
REM ============================================
REM 本地调试启动（Windows）：双击运行，自动装依赖并启动 Flask 开发服务器
REM 线上部署不经过本脚本（见 deploy/deploy.sh）
REM ============================================
chcp 936 >nul 2>&1
title RoboMaster Progress Tracker (Local Dev)
pushd "%~dp0"

echo.
echo ============================================================
echo   RoboMaster XunTian YuFeng -- Progress Tracker V1.0
echo ============================================================
echo   Local:  http://127.0.0.1:8520
echo   First login: admin / admin123
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3 first.
    echo          https://www.python.org/downloads/
    pause
    exit /b
)

pip show flask >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    pip install flask
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b
    )
)

echo [INFO] Starting server, press Ctrl+C to stop...
echo.
python app.py

echo.
echo [INFO] Server stopped.
pause
