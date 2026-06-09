@echo off
chcp 65001 >nul
echo ========================================
echo   生物制药冻干机监控系统 - 前端启动
echo ========================================
echo.

cd /d "%~dp0frontend"

echo [1/3] 检查Node.js环境...
node --version
if errorlevel 1 (
    echo 错误: 未找到Node.js，请先安装Node.js 18+
    pause
    exit /b 1
)

echo.
echo [2/3] 安装依赖...
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force ; npm install
if errorlevel 1 (
    echo 警告: 依赖安装可能存在问题，尝试继续启动...
)

echo.
echo [3/3] 启动开发服务器...
echo 前端地址: http://localhost:5173
echo.
echo 按 Ctrl+C 停止服务
echo.

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force ; npm run dev

pause
