#!/bin/bash
# 双语逐句阅读器 - 一键启动脚本
# Bilingual Reader - Quick Start Script

echo "📖 双语逐句阅读器 Bilingual Reader"
echo "=================================="
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 Python3，请先安装 Python"
    exit 1
fi

# 安装依赖
echo "📦 检查依赖..."
pip3 install edge-tts aiohttp --quiet 2>/dev/null || pip install edge-tts aiohttp --quiet 2>/dev/null

echo "✅ 依赖已就绪"
echo ""
echo "🌐 正在启动服务器..."
echo "   启动后请在浏览器打开: http://localhost:8765"
echo ""

# 启动服务器
cd "$(dirname "$0")"
python3 server.py
