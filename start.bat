@echo off
setlocal
REM ============================================
REM 本地调试启动（Windows）：同时启动 HTTP 与手机扫码 HTTPS 入口
REM 线上部署不经过本脚本（见 deploy/deploy.sh）
REM ============================================
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
title RoboMaster Progress Tracker (Local Dev)
pushd "%~dp0"

echo.
echo ============================================================
echo   RoboMaster XunTian YuFeng -- Progress Tracker V1.0
echo ============================================================
if not defined PORT set "PORT=8520"
echo   Local HTTP: http://127.0.0.1:%PORT%
echo   Phone HTTPS address and certificate will be printed below.
echo   Android: install root-ca.cer as a CA certificate once.
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3 first.
    echo          https://www.python.org/downloads/
    pause
    set "RESULT=1"
    goto finish
)

python -c "import flask, cryptography, werkzeug" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        set "RESULT=1"
        goto finish
    )
)

echo [INFO] Starting HTTP and HTTPS together. Press Ctrl+C to stop both.
echo [INFO] Optional: start.bat --ip YOUR_LAN_IP --port 8521
echo.
python deploy\local_https.py --with-app %*
set "RESULT=%ERRORLEVEL%"

echo.
echo [INFO] Local services stopped.
pause
:finish
popd
exit /b %RESULT%
