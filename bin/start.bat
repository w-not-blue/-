@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0.."
title Campus DataCenter Scheduler

set "PY="
py -3.12 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3.12"

if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo [错误] 未检测到 Python，请先安装 Python 3.11 及以上版本并加入 PATH。
    pause
    exit /b 1
)

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [错误] 当前 Python 版本过低，需要 Python 3.10 及以上。
    echo         请先安装 Python 3.12，或确认 py -3.12 可用。
    pause
    exit /b 1
)

%PY% -c "import flask, pandas, yaml, numpy, requests" >nul 2>&1
if errorlevel 1 (
    echo [提示] 当前 Python 缺少依赖，正在安装 requirements.txt...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后手动运行:
        echo     %PY% -m pip install -r requirements.txt
        pause
        exit /b 1
    )
)

echo 使用解释器: %PY%
echo 服务地址: http://localhost:5000
echo 浏览器将在服务就绪后自动打开，关闭本窗口即停止服务。
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://localhost:5000'"
%PY% -m src.api.app
pause
