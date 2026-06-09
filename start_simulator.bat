@echo off
chcp 65001 >nul
echo ========================================
echo   Profinet 模拟器启动
echo ========================================
echo.

cd /d "%~dp0backend"

echo [1/2] 检查Python环境...
python --version
if errorlevel 1 (
    echo 错误: 未找到Python
    pause
    exit /b 1
)

echo.
echo [2/2] 启动Profinet模拟器...
echo 模拟10台冻干机，每10秒上报一次数据
echo.
echo 按 Ctrl+C 停止模拟器
echo.

python profinet_simulator.py

pause
