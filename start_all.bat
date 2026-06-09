@echo off
chcp 65001 >nul
title 生物制药冻干机监控系统 - 全栈启动

echo ========================================
echo   生物制药冻干机监控系统
echo   一键启动所有服务
echo ========================================
echo.
echo 正在启动以下服务：
echo   1. FastAPI 后端 (端口: 8000)
echo   2. React 前端 (端口: 5173)
echo   3. Profinet 模拟器
echo.

cd /d "%~dp0"

echo [1/3] 启动后端服务...
start "后端服务" cmd /k call start_backend.bat

timeout /t 3 /nobreak >nul

echo.
echo [2/3] 启动前端服务...
start "前端服务" cmd /k call start_frontend.bat

timeout /t 5 /nobreak >nul

echo.
echo [3/3] 启动Profinet模拟器...
start "Profinet模拟器" cmd /k call start_simulator.bat

echo.
echo ========================================
echo   所有服务已启动！
echo ========================================
echo.
echo 访问地址：
echo   前端: http://localhost:5173
echo   后端API: http://localhost:8000
echo   API文档: http://localhost:8000/docs
echo.
echo 请在各窗口中查看服务运行日志
echo 关闭各窗口即可停止对应服务
echo.
pause
