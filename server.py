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
import base64
import io
import cgi
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

# ============ PDF 支持 ============
try:
    from PyPDF2 import PdfReader
    PDF_AVAILABLE = True
    print("✅ PyPDF2 已安装，支持 PDF 上传")
except ImportError:
    PDF_AVAILABLE = False
    print("⚠️ PyPDF2 未安装，PDF 上传不可用。运行: pip install PyPDF2")

def extract_text_from_pdf(pdf_bytes):
    """从 PDF 文件提取文本"""
    if not PDF_AVAILABLE:
        raise RuntimeError("PyPDF2 未安装")
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text_parts = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text_parts.append(page_text.strip())
    full_text = "\n".join(text_parts)
    if not full_text.strip():
        raise ValueError("PDF 文件中没有可提取的文本（可能是扫描件/图片 PDF）")
    return full_text

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

# ============ 语言自动识别 ============
def detect_language(text):
    """通过中文字符比例自动识别语言"""
    sample = text[:2000]
    chinese_count = len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', sample))
    total = len(re.sub(r'\s', '', sample))
    if total == 0:
        return 'zh'
    return 'zh' if (chinese_count / total) > 0.15 else 'en'

# ============ 分句 ============
def split_sentences(text):
    """智能分句，支持中英文"""
    text = text.replace('\r\n', '\n')
    parts = re.split(r'(?<=[。！？.!?\n])\s*', text)
    return [s.strip() for s in parts if s.strip()]

# ============ 章节检测 ============
CHAPTER_PATTERNS = [
    # 中文章节
    r'^第[一二三四五六七八九十百千\d]+[章回节篇卷]',
    r'^[一二三四五六七八九十]+[、.．]',
    # 英文章节
    r'^Chapter\s+\d+',
    r'^CHAPTER\s+\d+',
    r'^Part\s+\d+',
    r'^PART\s+\d+',
    r'^Section\s+\d+',
    # 数字章节
    r'^\d+[、.．]\s*\S',
]
CHAPTER_RE = re.compile('|'.join(CHAPTER_PATTERNS), re.MULTILINE)

def split_chapters(text):
    """将文本按章节拆分，返回 [{"title": "...", "content": "..."}]"""
    lines = text.replace('\r\n', '\n').split('\n')
    chapters = []
    current_title = "开头 / Introduction"
    current_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped and CHAPTER_RE.match(stripped):
            # 保存前一章
            if current_lines:
                content = '\n'.join(current_lines).strip()
                if content:
                    chapters.append({"title": current_title, "content": content})
            current_title = stripped
            current_lines = []
        else:
            current_lines.append(line)

    # 保存最后一章
    if current_lines:
        content = '\n'.join(current_lines).strip()
        if content:
            chapters.append({"title": current_title, "content": content})

    # 如果只检测到一章，按固定大小分段（每段约50句）
    if len(chapters) <= 1:
        all_sentences = split_sentences(text)
        if len(all_sentences) > 60:
            chunk_size = 50
            chapters = []
            for i in range(0, len(all_sentences), chunk_size):
                chunk = all_sentences[i:i+chunk_size]
                part_num = i // chunk_size + 1
                chapters.append({
                    "title": f"第 {part_num} 部分 / Part {part_num}",
                    "content": '\n'.join(chunk)
                })
        # else: 短文本不需要分章

    return chapters if chapters else [{"title": "全文", "content": text}]

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
    """保存书籍到用户书库（自动分章）"""
    user_dir = get_user_dir(user_id)
    if not user_dir:
        return False
    book_id = hashlib.md5(f"{title}:{content[:100]}".encode()).hexdigest()[:12]

    # 自动分章
    chapters = split_chapters(content)
    chapter_data = []
    total_sentences = 0
    for ch in chapters:
        sents = split_sentences(ch["content"])
        chapter_data.append({"title": ch["title"], "sentences": sents})
        total_sentences += len(sents)

    book_data = {
        "id": book_id,
        "title": title,
        "lang": lang,
        "content": content,  # 保留原文用于下载
        "chapters": chapter_data,
        "sentences": split_sentences(content),  # 兼容旧格式
        "created": __import__('time').strftime("%Y-%m-%d %H:%M")
    }
    filepath = os.path.join(user_dir, f"{book_id}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(book_data, f, ensure_ascii=False)
    print(f"  📚 保存书籍: {title} ({len(chapters)} 章, {total_sentences} 句) → {user_id}")
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
                chapters = data.get("chapters", [])
                books.append({
                    "id": data["id"],
                    "title": data["title"],
                    "lang": data.get("lang", "zh"),
                    "sentence_count": len(data.get("sentences", [])),
                    "chapter_count": len(chapters) if chapters else 1,
                    "created": data.get("created", "")
                })
            except:
                pass
    return books

def get_book_content(user_id, book_id, chapter_index=None):
    """获取书籍内容（支持按章节读取）"""
    user_dir = get_user_dir(user_id)
    if not user_dir:
        return None
    filepath = os.path.join(user_dir, f"{book_id}.json")
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    chapters = data.get("chapters", [])
    # 兼容旧格式（无章节的书）
    if not chapters:
        chapters = [{"title": "全文", "sentences": data.get("sentences", [])}]

    chapter_list = [{"title": ch["title"], "sentence_count": len(ch["sentences"])} for ch in chapters]

    result = {
        "id": data["id"],
        "title": data["title"],
        "lang": data.get("lang", "zh"),
        "chapter_count": len(chapters),
        "chapters": chapter_list,
    }

    # 如果指定了章节索引，只返回该章节的句子
    if chapter_index is not None and 0 <= chapter_index < len(chapters):
        result["current_chapter"] = chapter_index
        result["current_chapter_title"] = chapters[chapter_index]["title"]
        result["sentences"] = chapters[chapter_index]["sentences"]
    else:
        # 默认返回第一章
        result["current_chapter"] = 0
        result["current_chapter_title"] = chapters[0]["title"]
        result["sentences"] = chapters[0]["sentences"]

    return result

def get_book_raw(user_id, book_id):
    """获取书籍原始全文（用于下载自己上传的内容）"""
    user_dir = get_user_dir(user_id)
    if not user_dir:
        return None
    filepath = os.path.join(user_dir, f"{book_id}.json")
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"title": data["title"], "content": data.get("content", "")}

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
            # PDF 上传用 multipart/form-data（二进制直传，不经过 base64）
            if self.path == "/books/upload_pdf":
                self.handle_pdf_upload()
                return

            content_length = int(self.headers.get("Content-Length", 0))
            # 限制最大 50MB（文本内容）
            if content_length > 50 * 1024 * 1024:
                self.send_json({"success": False, "error": "请求过大（最大 50MB）"})
                return
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
            elif self.path == "/books/download":
                self.handle_book_download(data)
            elif self.path == "/extract_pdf":
                self.handle_extract_pdf(data)
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

    def handle_pdf_upload(self):
        """处理 PDF 文件上传（multipart/form-data）
        一步完成：接收 PDF → 提取文本 → 分章 → 保存到书库
        """
        if not PDF_AVAILABLE:
            self.send_json({"success": False, "error": "服务器未安装 PyPDF2，无法处理 PDF"})
            return
        try:
            content_type = self.headers.get("Content-Type", "")
            content_length = int(self.headers.get("Content-Length", 0))

            # 限制 PDF 大小 100MB
            if content_length > 100 * 1024 * 1024:
                self.send_json({"success": False, "error": "PDF 文件过大（最大 100MB）"})
                return

            print(f"  📄 接收 PDF 上传: {content_length / 1024 / 1024:.1f} MB")

            # 解析 multipart form data
            environ = {
                'REQUEST_METHOD': 'POST',
                'CONTENT_TYPE': content_type,
                'CONTENT_LENGTH': str(content_length),
            }
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ=environ
            )

            # 获取表单字段
            pdf_field = form['file']
            user_id = form.getvalue('user_id', '')
            title = form.getvalue('title', '未命名')
            lang = form.getvalue('lang', 'zh')

            if not user_id:
                self.send_json({"success": False, "error": "缺少用户ID"})
                return

            # 读取 PDF 数据
            pdf_bytes = pdf_field.file.read()
            print(f"  📄 PDF 大小: {len(pdf_bytes) / 1024:.0f} KB")

            # 提取文本
            text = extract_text_from_pdf(pdf_bytes)
            print(f"  📄 提取文本: {len(text)} 字符")

            # 自动识别语言
            if lang == 'auto':
                lang = detect_language(text)
                print(f"  🔍 自动识别语言: {lang}")

            # 保存到书库（save_book 会自动分章）
            book_id = save_book(user_id, title, text, lang)
            if book_id:
                # 获取保存后的章节信息
                book = get_book_content(user_id, book_id)
                self.send_json({
                    "success": True,
                    "book_id": book_id,
                    "title": title,
                    "chapter_count": book.get("chapter_count", 1) if book else 1,
                    "sentence_count": len(split_sentences(text)),
                    "text_length": len(text)
                })
            else:
                self.send_json({"success": False, "error": "保存失败"})
        except KeyError as e:
            self.send_json({"success": False, "error": f"缺少字段: {e}"})
        except Exception as e:
            print(f"  ❌ PDF 上传处理失败: {e}")
            traceback.print_exc()
            self.send_json({"success": False, "error": str(e)})

    def handle_extract_pdf(self, data):
        """从 base64 编码的 PDF 中提取文本（保留旧接口兼容）"""
        pdf_base64 = data.get("pdf_data", "")
        if not pdf_base64:
            self.send_json({"success": False, "error": "缺少 PDF 数据"})
            return
        if not PDF_AVAILABLE:
            self.send_json({"success": False, "error": "服务器未安装 PyPDF2，无法处理 PDF"})
            return
        try:
            pdf_bytes = base64.b64decode(pdf_base64)
            text = extract_text_from_pdf(pdf_bytes)
            print(f"  📄 PDF 提取成功: {len(text)} 字符")
            self.send_json({"success": True, "text": text})
        except Exception as e:
            print(f"  ❌ PDF 提取失败: {e}")
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
        if lang == 'auto':
            lang = detect_language(content)
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
        """读取书籍内容（按章节返回）"""
        user_id = data.get("user_id", "")
        book_id = data.get("book_id", "")
        chapter_index = data.get("chapter", None)
        if not user_id or not book_id:
            self.send_json({"success": False, "error": "缺少参数"})
            return
        if chapter_index is not None:
            chapter_index = int(chapter_index)
        book = get_book_content(user_id, book_id, chapter_index)
        if book:
            self.send_json({"success": True, "book": book})
        else:
            self.send_json({"success": False, "error": "书籍不存在"})

    def handle_book_download(self, data):
        """下载用户自己上传的书籍原文"""
        user_id = data.get("user_id", "")
        book_id = data.get("book_id", "")
        if not user_id or not book_id:
            self.send_json({"success": False, "error": "缺少参数"})
            return
        book = get_book_raw(user_id, book_id)
        if book and book["content"]:
            self.send_json({"success": True, "title": book["title"], "content": book["content"]})
        else:
            self.send_json({"success": False, "error": "书籍不存在或内容为空"})

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
