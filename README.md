# 📖 双语逐句阅读器 Bilingual Sentence Reader

**先中文，再英文，逐句精读** — 一款专为中文母语者设计的英语学习工具。

## 🌐 在线使用

👉 **[点击这里直接使用](https://bilingual-reader.onrender.com)**（无需安装，打开即用）

> 首次打开可能需要等待 30-50 秒唤醒服务器（Render 免费版特性）

## ✨ 功能特点

- **多种输入方式**：粘贴文本、上传 .txt 文件、内置演示内容
- **自动翻译**：输入中文或英文，自动翻译成另一种语言
- **逐句交替朗读（TTS）**：使用微软 Edge TTS 自然人声，先读中文再读英文
- **语音选择**：支持多种中英文语音（晓晓、云希、Jenny、Guy 等）
- **播放控制**：连续播放、暂停、上/下一句、句间间隔调节
- **实时高亮**：朗读时自动高亮当前句子，进度实时显示

## 🚀 本地运行

```bash
# 1. 克隆仓库
git clone https://github.com/ziyingzhang93/bilingual-reader.git
cd bilingual-reader

# 2. 安装依赖
pip install edge-tts aiohttp "aiosignal==1.3.1"

# 3. 启动服务器
python server.py

# 4. 打开浏览器访问
# http://localhost:8765
```

## 🛠 技术栈

- **后端**：Python + Edge TTS（微软自然人声）
- **前端**：原生 HTML/CSS/JS
- **翻译**：MyMemory API（免费）/ DeepL API（可选，更高质量）
- **部署**：Docker + Render

## 📝 配置 DeepL（可选）

如需更高质量的翻译，在 `server.py` 中设置环境变量：

```bash
export DEEPL_API_KEY="your-api-key-here"
```

免费注册：[deepl.com/pro-api](https://www.deepl.com/pro-api)
