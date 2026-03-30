#!/usr/bin/env python3
"""
双语逐句阅读器 - 后端服务
Bilingual Sentence Reader - Backend Server

使用 Edge TTS (微软自然人声) + DeepL/MyMemory 翻译
启动方式: python server.py
然后打开浏览器访问 http://localhost:8765
"""

import asyncio
import json
import os
import re
import sys
import hashlib
import tempfile
import urllib.request
import urllib.parse
import traceback
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============ 配置 ============
PORT = int(os.environ.get("PORT", 8765))  # 云平台会自动设置 PORT 环境变量
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "")  # 可通过环境变量或直接填写
AUDIO_CACHE_DIR = os.path.join(tempfile.gettempdir(), "bilingual_reader_audio")
os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)

# 用户书库目录
BOOKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_books")
os.makedirs(BOOKS_DIR, exist_ok=True)
import uuid

# Edge TTS 推荐语音
VOICES = {
    "zh_female": "zh-CN-XiaoxiaoNeural",
    "zh_male": "zh-CN-YunxiNeural",
    "zh_female_2": "zh-CN-XiaoyiNeural",
    "zh_male_2": "zh-CN-YunjianNeural",
    "en_female": "en-US-JennyNeural",
    "en_male": "en-US-GuyNeural",
    "en_female_2": "en-US-AriaNeural",
    "en_male_2": "en-US-ChristopherNeural",
}

# ============ 检查 edge-tts 是否安装 ============
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
    print("✅ edge-tts 已安装")
except ImportError:
    EDGE_TTS_AVAILABLE = False
    print("❌ edge-tts 未安装！请运行: pip install edge-tts")
    print("   安装后重新启动 server.py")

# ============ TTS 引擎 ============
async def generate_tts(text, voice, output_path):
    """使用 edge-tts 生成语音文件"""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)

def tts_sync(text, voice):
    """同步版 TTS，返回音频文件路径（带缓存）"""
    if not EDGE_TTS_AVAILABLE:
        raise RuntimeError("edge-tts 未安装")

    cache_key = hashlib.md5(f"{text}:{voice}".encode()).hexdigest()
    cache_path = os.path.join(AUDIO_CACHE_DIR, f"{cache_key}.mp3")

    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        return cache_path

    print(f"  🔊 生成语音: [{voice}] {text[:30]}...")

    # 使用新的事件循环
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(generate_tts(text, voice, cache_path))
        loop.close()
    except Exception as e:
        print(f"  ❌ TTS 生成失败: {e}")
        traceback.print_exc()
        # 清理失败的文件
        if os.path.exists(cache_path):
            os.remove(cache_path)
        raise

    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        print(f"  ✅ 语音生成成功: {os.path.getsize(cache_path)} bytes")
        return cache_path
    else:
        raise RuntimeError("生成的音频文件为空")

# ============ 翻译引擎 ============
def translate_deepl(text, source_lang, target_lang):
    """使用 DeepL API 翻译"""
    url = "https://api-free.deepl.com/v2/translate"
    data = urllib.parse.urlencode({
        "auth_key": DEEPL_API_KEY,
        "text": text,
        "source_lang": source_lang.upper(),
        "target_lang": target_lang.upper(),
    }).encode()

    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
        return result["translations"][0]["text"]

def translate_mymemory(text, source_lang, target_lang):
    """使用 MyMemory 免费 API 翻译"""
    lang_map = {"zh": "zh-CN", "en": "en-GB"}
    langpair = f"{lang_map.get(source_lang, source_lang)}|{lang_map.get(target_lang, target_lang)}"
    url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(text)}&langpair={langpair}"

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
        if result.get("responseStatus") == 200:
            return result["responseData"]["translatedText"]
        return None

def translate(text, source_lang, target_lang):
    """翻译文本，优先使用 DeepL"""
    try:
        if DEEPL_API_KEY:
            return translate_deepl(text, source_lang, target_lang)
        else:
            return translate_mymemory(text, source_lang, target_lang)
    except Exception as e:
        print(f"  ❌ 翻译出错: {e}")
        return None

# ============ 分句 ============
def split_sentences(text):
    """智能分句，支持中英文"""
    text = text.replace('\r\n', '\n')
    parts = re.split(r'(?<=[。！？.!?\n])\s*', text)
    return [s.strip() for s in parts if s.strip()]

# ============ 批量翻译（核心优化） ============
def translate_batch(sentences, source_lang, target_lang):
    """
    批量翻译：先尝试整段翻译再拆句（1次请求），
    失败则用并发逐句翻译（N次请求但并行）
    """
    if not sentences:
        return []

    print(f"  📝 批量翻译 {len(sentences)} 句...")
    import time
    start = time.time()

    # === 策略1: 整段翻译后智能对齐（最快，1次API请求） ===
    try:
        # 用特殊分隔符拼接，翻译后再拆分
        separator = " ||| "
        combined = separator.join(sentences)

        translated_combined = translate(combined, source_lang, target_lang)
        if translated_combined:
            # 尝试用分隔符拆回
            translated_parts = translated_combined.split("|||")
            # 清理空白
            translated_parts = [p.strip() for p in translated_parts if p.strip()]

            if len(translated_parts) == len(sentences):
                elapsed = time.time() - start
                print(f"  ✅ 整段翻译成功！{len(sentences)} 句，耗时 {elapsed:.1f}s")
                return translated_parts
            else:
                print(f"  ⚠️ 整段翻译句数不匹配 ({len(translated_parts)} vs {len(sentences)})，改用并发翻译")
    except Exception as e:
        print(f"  ⚠️ 整段翻译失败: {e}，改用并发翻译")

    # === 策略2: 并发翻译（比逐句快3-5倍） ===
    results = [None] * len(sentences)
    max_workers = min(8, len(sentences))  # 最多8个并发

    def translate_one(index, text):
        result = translate(text, source_lang, target_lang)
        return index, result

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(translate_one, i, s) for i, s in enumerate(sentences)]
        for future in as_completed(futures):
            try:
                idx, result = future.result()
                results[idx] = result if result else f"[翻译失败 / Translation failed]"
            except Exception as e:
                print(f"  ❌ 并发翻译出错: {e}")

    elapsed = time.time() - start
    print(f"  ✅ 并发翻译完成！{len(sentences)} 句，耗时 {elapsed:.1f}s")
    return results

# ============ 用户书库管理 ============
def get_user_dir(user_id):
    """获取用户书库目录（安全检查）"""
    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', user_id)
    if not safe_id:
        return None
    user_dir = os.path.join(BOOKS_DIR, safe_id)
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def save_book(user_id, title, content, lang="zh"):
    """保存书籍到用户书库"""
    user_dir = get_user_dir(user_id)
    if not user_dir:
        return False
    book_id = hashlib.md5(f"{title}:{content[:100]}".encode()).hexdigest()[:12]
    book_data = {
        "id": book_id,
        "title": title,
        "lang": lang,
        "content": content,
        "sentences": split_sentences(content),
        "created": __import__('time').strftime("%Y-%m-%d %H:%M")
    }
    filepath = os.path.join(user_dir, f"{book_id}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(book_data, f, ensure_ascii=False)
    print(f"  📚 保存书籍: {title} ({len(book_data['sentences'])} 句) → {user_id}")
    return book_id

def list_books(user_id):
    """列出用户的所有书籍（不返回内容，只返回元数据）"""
    user_dir = get_user_dir(user_id)
    if not user_dir:
        return []
    books = []
    for fname in sorted(os.listdir(user_dir)):
        if fname.endswith(".json"):
            filepath = os.path.join(user_dir, fname)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                books.append({
                    "id": data["id"],
                    "title": data["title"],
                    "lang": data.get("lang", "zh"),
                    "sentence_count": len(data.get("sentences", [])),
                    "created": data.get("created", "")
                })
            except:
                pass
    return books

def get_book_content(user_id, book_id):
    """获取书籍内容（逐句返回，不返回原始全文）"""
    user_dir = get_user_dir(user_id)
    if not user_dir:
        return None
    filepath = os.path.join(user_dir, f"{book_id}.json")
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    # 只返回逐句内容，不返回可下载的原始全文
    return {
        "id": data["id"],
        "title": data["title"],
        "lang": data.get("lang", "zh"),
        "sentences": data.get("sentences", []),
    }

def delete_book(user_id, book_id):
    """删除书籍"""
    user_dir = get_user_dir(user_id)
    if not user_dir:
        return False
    safe_id = re.sub(r'[^a-zA-Z0-9]', '', book_id)
    filepath = os.path.join(user_dir, f"{safe_id}.json")
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False

# ============ HTTP 服务器 ============
class BilingualHandler(SimpleHTTPRequestHandler):

    def send_cors_headers(self):
        """发送 CORS 头部"""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_file("index.html")
        elif self.path.startswith("/audio/"):
            self.serve_audio()
        elif self.path == "/voices":
            self.send_json(VOICES)
        elif self.path == "/health":
            self.send_json({"status": "ok", "edge_tts": EDGE_TTS_AVAILABLE})
        else:
            super().do_GET()

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            if self.path == "/translate":
                self.handle_translate(data)
            elif self.path == "/translate_batch":
                self.handle_translate_batch(data)
            elif self.path == "/tts":
                self.handle_tts(data)
            elif self.path == "/books/save":
                self.handle_book_save(data)
            elif self.path == "/books/list":
                self.handle_book_list(data)
            elif self.path == "/books/read":
                self.handle_book_read(data)
            elif self.path == "/books/delete":
                self.handle_book_delete(data)
            elif self.path == "/user/create":
                self.handle_user_create(data)
            else:
                self.send_error(404)
        except Exception as e:
            print(f"  ❌ POST 请求错误: {e}")
            traceback.print_exc()
            self.send_json({"success": False, "error": str(e)})

    def handle_translate(self, data):
        text = data.get("text", "")
        source = data.get("source", "zh")
        target = data.get("target", "en")

        result = translate(text, source, target)
        if result:
            self.send_json({"success": True, "text": result})
        else:
            self.send_json({"success": False, "error": "Translation failed"})

    def handle_translate_batch(self, data):
        """批量翻译：接收完整文本，分句+翻译一步完成"""
        text = data.get("text", "")
        source = data.get("source", "zh")
        target = data.get("target", "en")

        if not text.strip():
            self.send_json({"success": False, "error": "No text"})
            return

        try:
            # 分句
            sentences = split_sentences(text)
            if not sentences:
                self.send_json({"success": False, "error": "No sentences found"})
                return

            # 批量翻译
            translations = translate_batch(sentences, source, target)

            # 组装结果
            pairs = []
            for orig, trans in zip(sentences, translations):
                if source == "zh":
                    pairs.append({"zh": orig, "en": trans or "[Translation failed]"})
                else:
                    pairs.append({"zh": trans or "[翻译失败]", "en": orig})

            self.send_json({"success": True, "pairs": pairs, "count": len(pairs)})
        except Exception as e:
            print(f"  ❌ 批量翻译错误: {e}")
            traceback.print_exc()
            self.send_json({"success": False, "error": str(e)})

    def handle_user_create(self, data):
        """创建新用户阅读码"""
        user_id = str(uuid.uuid4())[:8]
        get_user_dir(user_id)  # 创建目录
        self.send_json({"success": True, "user_id": user_id})

    def handle_book_save(self, data):
        """保存书籍"""
        user_id = data.get("user_id", "")
        title = data.get("title", "未命名")
        content = data.get("content", "")
        lang = data.get("lang", "zh")
        if not user_id or not content.strip():
            self.send_json({"success": False, "error": "缺少用户ID或内容"})
            return
        book_id = save_book(user_id, title, content, lang)
        if book_id:
            self.send_json({"success": True, "book_id": book_id})
        else:
            self.send_json({"success": False, "error": "保存失败"})

    def handle_book_list(self, data):
        """列出用户书库"""
        user_id = data.get("user_id", "")
        if not user_id:
            self.send_json({"success": False, "error": "缺少用户ID"})
            return
        books = list_books(user_id)
        self.send_json({"success": True, "books": books})

    def handle_book_read(self, data):
        """读取书籍内容（逐句，不可下载）"""
        user_id = data.get("user_id", "")
        book_id = data.get("book_id", "")
        if not user_id or not book_id:
            self.send_json({"success": False, "error": "缺少参数"})
            return
        book = get_book_content(user_id, book_id)
        if book:
            self.send_json({"success": True, "book": book})
        else:
            self.send_json({"success": False, "error": "书籍不存在"})

    def handle_book_delete(self, data):
        """删除书籍"""
        user_id = data.get("user_id", "")
        book_id = data.get("book_id", "")
        if delete_book(user_id, book_id):
            self.send_json({"success": True})
        else:
            self.send_json({"success": False, "error": "删除失败"})

    def handle_tts(self, data):
        text = data.get("text", "")
        voice = data.get("voice", VOICES["zh_female"])

        if not text:
            self.send_json({"success": False, "error": "No text provided"})
            return

        try:
            audio_path = tts_sync(text, voice)
            cache_key = hashlib.md5(f"{text}:{voice}".encode()).hexdigest()
            self.send_json({"success": True, "audio_url": f"/audio/{cache_key}.mp3"})
        except Exception as e:
            print(f"  ❌ TTS 接口错误: {e}")
            traceback.print_exc()
            self.send_json({"success": False, "error": str(e)})

    def serve_audio(self):
        filename = self.path.split("/audio/")[-1]
        # 安全检查
        if ".." in filename or "/" in filename:
            self.send_error(403)
            return
        filepath = os.path.join(AUDIO_CACHE_DIR, filename)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", os.path.getsize(filepath))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_cors_headers()
            self.end_headers()
            with open(filepath, "rb") as f:
                self.wfile.write(f.read())
        else:
            print(f"  ⚠️ 音频文件不存在: {filepath}")
            self.send_error(404)

    def send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, filename):
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        if os.path.exists(filepath):
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404, f"File not found: {filename}")

    def log_message(self, format, *args):
        msg = str(args[0]) if args else ""
        if "/audio/" not in msg and "favicon" not in msg:
            print(f"  📡 {msg}")

def main():
    print("=" * 50)
    print("📖 双语逐句阅读器 Bilingual Reader")
    print("=" * 50)
    print()

    if DEEPL_API_KEY:
        print("✅ 翻译引擎: DeepL (高质量)")
    else:
        print("ℹ️  翻译引擎: MyMemory (免费)")
        print("   如需更好翻译，请在 server.py 中填入 DEEPL_API_KEY")

    if EDGE_TTS_AVAILABLE:
        print("✅ 语音引擎: Edge TTS (微软自然人声)")
    else:
        print("❌ 语音引擎: 未安装！运行 pip install edge-tts")

    print(f"\n🌐 请在浏览器中打开: http://localhost:{PORT}")
    print("   按 Ctrl+C 停止服务器")
    print(f"\n📁 音频缓存目录: {AUDIO_CACHE_DIR}")
    print()

    server = HTTPServer(("0.0.0.0", PORT), BilingualHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.server_close()

if __name__ == "__main__":
    main()
