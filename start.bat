@echo off
chcp 65001 >nul
echo 📖 双语逐句阅读器 Bilingual Reader
echo ==================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到 Python，请先安装 Python
    pause
    exit /b 1
)

REM 安装依赖
echo 📦 检查依赖...
pip install edge-tts aiohttp --quiet 2>nul

echo ✅ 依赖已就绪
echo.
echo 🌐 正在启动服务器...
echo    启动后请在浏览器打开: http://localhost:8765
echo.

REM 启动服务器
cd /d "%~dp0"
python server.py
pause
