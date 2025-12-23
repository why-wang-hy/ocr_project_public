# -*- coding: utf-8 -*-
"""
OCR & Translation App Backend
功能：处理PDF上传、Mistral OCR识别、GitHub图床/存储、DeepSeek翻译
"""

import os
import time
import base64
import re
import traceback
import tempfile
import urllib.parse
import datetime
import requests
from flask import Flask, render_template, request, jsonify, url_for, Response
from mistralai import Mistral
from openai import OpenAI
from pypdf import PdfWriter, PdfReader
import threading # 🟢 新增：用于后台异步拉取
# 🟢 必须添加这一行，否则会报“未定义 ThreadPoolExecutor”
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

app = Flask(__name__)

# 加载 .env 文件中的变量
load_dotenv()

# 从环境变量中安全读取 Key
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# ==============================================================================
# 🟢 第一部分：全局配置区域 (Configuration)
# ==============================================================================

# --- GitHub 配置 (用于云端存储和历史记录) ---
GITHUB_USER = "why-wang-hy"
GITHUB_REPO = "ocr-team-docs"
GITHUB_BRANCH = "main"

# GitHub API 构造
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents"
GH_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

# --- App 运行参数 ---
PAGE_CHUNK_SIZE = 5  # PDF 处理分块大小（每5页一组）
BASE_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

# --- 用户身份映射 ---
USERS = {
    's1': '王浩懿',
    's2': '王牧虹',
    's3': '陈妤何',
    's4': '同伴 D',
    's5': '同伴 E',
    's6': '同伴 F',
    's7': '同伴 G',
    's8': '默认'
}

#🟢 新增：全局缓存容器
# 结构: { 's1': [...列表数据...], 's2': [...] }
HISTORY_CACHE = {}

# ==============================================================================
# 🟢 第二部分：GitHub 工具模块 (GitHub Utils)
# ==============================================================================

def upload_to_github(file_path, target_path, commit_message):
    """
    功能：将本地文件上传到 GitHub 指定仓库路径。
    
    :param file_path: 本地文件路径
    :param target_path: GitHub 仓库内的目标路径
    :param commit_message: 提交信息
    :return: Boolean (成功为 True)
    """
    try:
        # 1. 读取文件并转换为 Base64
        with open(file_path, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")
        
        # 2. 构造 API URL (处理路径中的特殊字符)
        url = f"{GITHUB_API_BASE}/{urllib.parse.quote(target_path)}"
        
        # 3. 构造请求体
        data = {
            "message": commit_message,
            "content": content,
            "branch": GITHUB_BRANCH
        }
        
        # 4. 发送 PUT 请求
        resp = requests.put(url, json=data, headers=GH_HEADERS)
        
        if resp.status_code in [200, 201]:
            return True
        else:
            print(f"GitHub Upload Failed: {resp.text}")
            return False
    except Exception as e:
        print(f"Upload Error: {e}")
        return False
    
# ==================== 🟢 提取：独立的 GitHub 获取函数 ====================
# 这个函数负责干脏活累活，不直接处理 HTTP 请求，方便被各种路由调用
def _fetch_github_data(user_id):
    """
    功能：连接 GitHub API 获取原始数据，计算时间戳，返回处理后的列表。
    注意：这是一个耗时操作 (1-3秒)。
    """
    contents_url = f"{GITHUB_API_BASE}/{user_id}"
    print(f"🔄 [Cache Worker] 正在后台拉取 {user_id} 的数据...")
    
    try:
        # 1. 获取文件列表
        resp = requests.get(contents_url, headers=GH_HEADERS)
        if resp.status_code != 200: 
            print(f"⚠️ [Cache Worker] 获取列表失败: {resp.status_code}")
            return []

        items = resp.json()
        if not isinstance(items, list): return []

        # 1. 第一步：先扫描所有文件，按原始 PDF 名称归类
        # 结构：{ "文件名": { "pdf": path, "mds": [{"name": "显示名", "path": path}], "time": 0 } }
        files_groups = {}

        for item in items:
            if item['type'] != 'file': continue
            full_name = item['name']
            path = item['path']
            base_name, ext = os.path.splitext(full_name)
            ext = ext.lower()

            # 判断是否是双语版
            is_dual = base_name.endswith('_dual')
            # 统一找回原始 PDF 的 base_name (去掉 _dual)
            origin_base = base_name.replace('_dual', '') if is_dual else base_name

            if origin_base not in files_groups:
                files_groups[origin_base] = {'pdf': None, 'mds': [], 'timestamp': 0}

            if ext in ['.pdf', '.jpg', '.png']:
                files_groups[origin_base]['pdf'] = path
            elif ext == '.md':
                display_name = f"{origin_base} (双语)" if is_dual else origin_base
                files_groups[origin_base]['mds'].append({
                    'display_name': display_name,
                    'path': path
                })

        # --- 🟢 核心修改部分：仅请求前 7 个记录的时间戳 ---
        # 1. 获取所有有 PDF 的组名
        group_keys = [k for k, v in files_groups.items() if v['pdf']]
        
        # 2. 这里的 group_keys 顺序通常是 GitHub 返回的顺序（通常按名称排序）
        # 我们取前 7 个进行时间戳请求
        for i, origin_base in enumerate(group_keys):
            if i >= 7: break # 超过 7 个则跳过请求，保持 timestamp 为 0
            
            data = files_groups[origin_base]
            try:
                commit_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/commits"
                c_resp = requests.get(commit_url, 
                                    params={'path': data['pdf'], 'per_page': 1},
                                    headers=GH_HEADERS)
                if c_resp.status_code == 200 and c_resp.json():
                    date_str = c_resp.json()[0]['commit']['committer']['date']
                    data['timestamp'] = datetime.datetime.fromisoformat(date_str.replace('Z', '+00:00')).timestamp()
            except Exception as e:
                print(f"⚠️ 获取时间戳失败 ({origin_base}): {e}")

        # 3. 展平并构建最终列表
        history_items = []
        for origin_base, data in files_groups.items():
            if data['pdf'] and data['mds']:
                for md_info in data['mds']:
                    history_items.append({
                        'name': md_info['display_name'],
                        'pdf_path': data['pdf'],
                        'md_path': md_info['path'],
                        'timestamp': data['timestamp']
                    })
        
        # 按时间戳降序排序（最近的在前）
        history_items.sort(key=lambda x: x['timestamp'], reverse=True)
        
        # 更新缓存
        history_manager.set(user_id, history_items)
        print(f"✅ [Cache Worker] {user_id} 缓存已更新，请求了前 7 项时间戳")
        return history_items

    except Exception as e:
        print(f"❌ [Cache Worker] Error: {e}")
        return []
    
# ==================== 🟢 新增：后台刷新任务 ====================
def background_refresh_task(user_id):
    """线程入口函数"""
    with app.app_context(): # 确保有 Flask 上下文（虽然这里主要用 requests）
        _fetch_github_data(user_id)

# 🟢 修改第一部分：改进缓存逻辑
# 使用一个带锁的类来管理缓存，防止多线程竞争，并增加简单的本地持久化（可选）
class HistoryManager:
    def __init__(self):
        self.cache = {}
        self.last_sync = {}
        self.lock = threading.Lock()

    def get(self, user_id):
        with self.lock:
            return self.cache.get(user_id)

    def set(self, user_id, data):
        with self.lock:
            self.cache[user_id] = data
            self.last_sync[user_id] = time.time()

history_manager = HistoryManager()

# ==============================================================================
# 🟢 第三部分：翻译引擎模块 (Translation Engine - Advanced Isolation)
# ==============================================================================

class ContentIsolator:
    """
    功能：专门负责内容的 提取(Protect) 与 还原(Restore)
    策略：维护一个有序的替换列表，确保嵌套结构被正确处理
    """
    def __init__(self):
        self.vault = {} # 存储原始内容: {'key': 'content'}
        self.counter = 0
    
    def _get_key(self, prefix):
        """生成唯一的占位符 Key"""
        key = f"[[__{prefix}_{self.counter}__]]"
        self.counter += 1
        return key

    def protect(self, text, pattern, prefix):
        """
        通用保护函数
        :param text: 文本
        :param pattern: 正则表达式
        :param prefix: 占位符前缀 (如 IMG, EQ, TBL)
        """
        def replacer(match):
            content = match.group(0)
            key = self._get_key(prefix)
            self.vault[key] = content
            return key
        
        return re.sub(pattern, replacer, text, flags=re.MULTILINE | re.DOTALL)

    def restore(self, text):
        """将占位符还原为原始内容"""
        # 为了防止偶发的嵌套替换问题，建议按 Key 的长度逆序还原，或者直接遍历
        # 这里由于 Key 格式固定，直接遍历即可
        for key, content in self.vault.items():
            # 使用 replace 而非 re.sub，防止 content 中包含正则敏感字符导致崩溃
            text = text.replace(key, content)
        return text

class SafeTranslator:
    """
    功能：学术翻译引擎 (Pro 版)
    特点：彻底隔离图片、代码、公式、表格，只翻译纯文本
    """
    def __init__(self):
        self.client = OpenAI(
            api_key=DEEPSEEK_API_KEY, 
            base_url="https://api.deepseek.com"
        )

    def translate_bilingual(self, markdown_text):
        if not markdown_text.strip():
            return ""

        # 实例化隔离器 (每个 Chunk 独立)
        isolator = ContentIsolator()
        processed_text = markdown_text

        # ========== 🛡️ 隔离阶段 (顺序很重要!) ==========
        
        # 1. 保护代码块 (```...```) - 优先级最高
        # 说明：防止代码里的数学符号或图片标记被误识别
        processed_text = isolator.protect(
            processed_text, 
            r'```[\s\S]*?```', 
            "CODE"
        )

        # 2. 保护图片 (![...](...)) 
        # 说明：防止 Base64 干扰翻译，同时防止模型修改图片路径
        processed_text = isolator.protect(
            processed_text, 
            r'!\[.*?\]\(.*?\)', 
            "IMG"
        )

        # 3. 保护 Markdown 表格
        # 特征：匹配连续的以 | 开头的行。这能防止表格结构被翻译打乱。
        # 注意：这意味着表格内的文字将不会被翻译（通常OCR的表格翻译后格式极难控制，建议保留原文）
        processed_text = isolator.protect(
            processed_text,
            r'(?:^\|.*?\|\s*$\n?)+',
            "TBL"
        )

        # 4. 保护 Block 公式 ($$ ... $$)
        processed_text = isolator.protect(
            processed_text,
            r'\$\$[\s\S]*?\$\$',
            "EQ_BLOCK"
        )

        # 5. 保护 Inline 公式 ($ ... $)
        # 说明：使用负向预查 (?<!\\) 防止匹配转义的 \$
        processed_text = isolator.protect(
            processed_text,
            r'(?<!\\)\$(?!\s).*?(?<!\s)(?<!\\)\$',
            "EQ_INLINE"
        )

        # 构造 System Prompt (针对新占位符优化)
        system_prompt = r"""
            你是一位精通数学建模与科学研究的学术翻译专家。你负责将复杂的学术 Markdown 文档从英文翻译为中文，并保持文档的严谨性与排版完整性。

            ### 📝 翻译规范与格式要求 (必须遵守)：
            1. **双语对照格式**：采用“逐段对照”原则。输出每一段原文后，紧跟其对应的中文翻译段落。
            2. **译文引用标识**：所有的中文翻译段落必须且只能包裹在 Markdown 引用块内，即以 `> ` 开头。
            3. **术语准确性**：使用地道的中国学术语用习惯（如“本文”、“显著性”、“鲁棒性”等）。
            4. **占位符保留**：
               - 文本中包含类似 `[[__IMG_n__]]` (图片)、`[[__TBL_n__]]` (表格)、`[[__EQ_BLOCK_n__]]` (块级公式)、`[[__EQ_INLINE_n__]]` (行内公式) 以及 `[[__PB_n__]]` (换页符) 的占位符。
               - 这些占位符在译文中必须**原样保留**，位置应符合中文语序。

            ### 🚫 绝对禁令 (违者将导致解析崩溃)：
            1. **严禁修改占位符结构**：
               - 严禁翻译占位符内部的英文（如把 IMG 翻译成“图片”）。
               - 严禁在占位符的大括号内部添加任何空格。
               - ✅ 正确：`> 该模型如 [[__IMG_0__]] 所示。`
               - ❌ 错误：`> 该模型如 [[ __图片_0__ ]] 所示。`

            2. **严禁在译文中使用公式定界符**：
               - 严禁在 `> ` 开头的译文中输出 `$$`、`\[`、`\]`、`\begin{...}` 或 `\end{...}`。所有公式必须通过对应的 `[[__EQ_...__]]` 占位符体现。

            3. **禁止翻译纯组件行**：
               - 如果原文段落只包含占位符（如只有 `[[__EQ_BLOCK_0__]]`）而无文字内容，**严禁**输出对应的 `> ` 译文行，直接跳过并处理下一段。

            4. **禁止翻译孤立噪声**：
               - 遇到单独的页码数字（如 '1'）、年份（如 '2025'）或 OCR 产生的伪影数字，请直接忽略，不要输出翻译。

            5. **保护 Markdown 语法元字符**：
               - 严禁修改原文中的标题级数（`#`）、列表符号（`-`、`1.`）或加粗符号（`**`）。

            ### 💡 示例展示：
            输入：
            # 1. Introduction
            The growth of fungi is modeled by [[__EQ_INLINE_0__]].
            [[__EQ_BLOCK_1__]]

            输出：
            # 1. Introduction
            > # 1. 绪论

            The growth of fungi is modeled by [[__EQ_INLINE_0__]].
            > 真菌的生长通过 [[__EQ_INLINE_0__]] 进行建模。

            [[__EQ_BLOCK_1__]]
            (此处不输出译文，因为该段仅包含块级公式占位符)
            """

        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": processed_text}
                ],
                stream=False,
                temperature=0.1 # 降低随机性，确保占位符不乱跑
            )
            translated_text = response.choices[0].message.content
        except Exception as e:
            print(f"❌ API Error: {e}")
            return f"{markdown_text}\n\n> ⚠️ 翻译服务暂时不可用: {e}"

        # ========== 🔄 还原阶段 (改进版) ==========
        
        # 1. 分离原文和译文块 (假设 AI 遵循了 > 引用块格式)
        # 我们需要分别处理：原文保留所有占位符，译文删掉非文字占位符
        lines = translated_text.split('\n')
        final_lines = []
        
        for line in lines:
            if line.strip().startswith('>'):
                # 这是译文行：我们要在这里删掉组件占位符
                # 剔除图片、表格、块级公式占位符，只保留文字
                clean_line = line
                # 删掉图片
                clean_line = re.sub(r'\[\[__IMG_\d+__\]\]', '', clean_line)
                # 删掉表格
                clean_line = re.sub(r'\[\[__TBL_\d+__\]\]', '', clean_line)
                # 删掉块级公式 (可选：如果你希望译文里也不要行内公式，可以一并删掉)
                clean_line = re.sub(r'\[\[__EQ_BLOCK_\d+__\]\]', '', clean_line)
                
                # 还原剩下的文字占位符 (如果有的话)
                final_lines.append(isolator.restore(clean_line))
            else:
                # 这是原文行：完全还原，保留所有组件
                final_lines.append(isolator.restore(line))
        
        return '\n'.join(final_lines)
        
def is_likely_toc(text):
    """检测是否为目录页：寻找‘标题...数字’特征"""
    toc_lines = re.findall(r'^[^\n]{5,}\s+\d+$', text, re.MULTILINE)
    return len(toc_lines) >= 3 #
        
# ==================== 🟢 核心修复：后端文本清洗 ====================
def backend_smart_clean(content):
    if not content: return ""

    # 1. 🟢 图片“保险箱”隔离：防止巨大的 Base64 字符串被下方的正则误删或导致卡顿
    imgs = []
    def _hide(m):
        imgs.append(m.group(0))
        return f"__IMG_TMP_{len(imgs)-1}__"
    
    # 匹配所有的 Markdown 图片标签 (含 Base64)
    content = re.sub(r'!\[.*?\]\(data:image\/.*?;base64,.*?\)', _hide, content)
    
    # 2. 🟢 终极公式修复：暴力还原 HTML 实体
    # 这里使用顺序替换，先处理二次转义，再处理标准转义
    content = content.replace('&amp;lt;', '<').replace('&lt;', '<')
    content = content.replace('&amp;gt;', '>').replace('&gt;', '>')
    content = content.replace('&amp;le;', r'\le').replace('&le;', r'\le')
    content = content.replace('&amp;ge;', r'\ge').replace('&ge;', r'\ge')
    content = content.replace('&amp;plusmn;', r'\pm').replace('&plusmn;', r'\pm')

    # 3. 🟢 修复矩阵语法 (移除 \begin{array}[] 这种非标标记)
    # 使用 re.DOTALL 确保能跨过换行符匹配方括号
    content = re.sub(r'\\begin\{array\}\s*\[.*?\]', r'\\begin{array}', content, flags=re.DOTALL)
    content = content.replace('[]{cccccc}', '{cccccc}')

    # 4. 🟢 移除 OCR 垃圾信息 (同步前端逻辑)
    ad_keywords = [
        '获取更多资讯', '优质更多资讯', '國立臺灣大學','数字模型', '数学模型','I would like to get more information.', 
        '上海', '天津', '文汇', '云江', '太江', '云计', '交往','文江','資訊','大江','关注数学'
    ]
    ad_regex = r'^.*(' + '|'.join(ad_keywords) + r').*$'
    content = re.sub(ad_regex, '', content, flags=re.MULTILINE)
    
    # 移除 Team 标记与 Page 页码
    content = re.sub(r'^Team\s*[#]?\s*\d+\s*.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^Page\s+\d+(?:\s+of\s+\d+)?\s*.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'[↪\u21aa]', '', content)

    # 5. 🟢 目录页码对齐
    # 将被 OCR 切断的页码数字拉回上一行
    content = re.sub(r'(\d+\.[\d\.]*.*)\n+(\d+)$', r'\1 \2', content, flags=re.MULTILINE)
    # 处理目录点号：Title .... 12 -> Title 12
    content = re.sub(r'\.{3,}\s*(\d+)', r' \1', content)
    
    # 6. 🟢 结构压缩
    content = re.sub(r'\n{3,}', '\n\n', content)

    # 7. 🟢 还原图片
    for i, raw in enumerate(imgs):
        content = content.replace(f"__IMG_TMP_{i}__", raw)
    
    return content.strip()

# ==================== 🟢 核心修复：智能文本切分器 ====================
def smart_chunk_text(text, max_chars=2000):
    """
    优先按双换行(\n\n)切分段落。
    如果段落太长，再按单换行(\n)切分。
    尽最大努力保持语义完整性。
    """
    # 1. 先按“双换行”切分成大段落 (这是最自然的语义边界)
    paragraphs = text.split('\n\n')
    
    batches = []
    current_batch = []
    current_length = 0

    # 🟢 真正调用函数：检测当前块是否属于目录模式
    is_toc_mode = is_likely_toc(text)
    
    for para in paragraphs:
        # 如果这是一个图片行 (![...])，尽量让它单独成段或者跟随上一段
        # 但不要把它硬生生切到下一批次如果还能放得下
        
        para_len = len(para)

        # 🟢 目录优化：如果是目录模式，且遇到章节标题（如 "1 Introduction"）
        # 则强制开启新块，避免把目录的不同章节混在一起翻译导致散乱
        if is_toc_mode and re.match(r'^\d+\s+[A-Z\u4e00-\u9fa5]', para.strip()):
            if current_batch:
                batches.append("\n\n".join(current_batch))
                current_batch = []
                current_length = 0
        
        # 情况 A: 当前段落本身就超长 (例如 > 2000字符的大长篇 OCR 结果)
        # 需要内部再切分 (按单换行切)
        if para_len > max_chars:
            # 先把之前攒的存起来
            if current_batch:
                batches.append("\n\n".join(current_batch))
                current_batch = []
                current_length = 0
            
            # 内部切分逻辑
            lines = para.split('\n')
            temp_chunk = []
            temp_len = 0
            for line in lines:
                if temp_len + len(line) > max_chars and temp_chunk:
                    batches.append("\n".join(temp_chunk))
                    temp_chunk = [line]
                    temp_len = len(line)
                else:
                    temp_chunk.append(line)
                    temp_len += len(line)
            if temp_chunk:
                batches.append("\n".join(temp_chunk))
                
        # 情况 B: 当前段落不超长，但加上去会超过 Batch 限制
        elif current_length + para_len > max_chars and current_batch:
            batches.append("\n\n".join(current_batch))
            current_batch = [para]
            current_length = para_len
            
        # 情况 C: 安全，加入当前 Batch
        else:
            current_batch.append(para)
            current_length += para_len
            
    # 处理剩余部分
    if current_batch:
        batches.append("\n\n".join(current_batch))
        
    return batches

# ==================== 🟢 核心修复：独立翻译辅助函数 ====================
def translate_chunk(text_chunk):
    """
    这是一个全局函数，确保 ThreadPoolExecutor 可以稳定调用。
    """
    if not text_chunk.strip():
        return ""
    try:
        # 实例化新的 SafeTranslator (它现在包含 Advanced Isolation 逻辑)
        local_translator = SafeTranslator()
        return local_translator.translate_bilingual(text_chunk)
    except Exception as e:
        print(f"❌ 批次翻译失败: {e}")
        # 如果翻译挂了，至少返回原文，不要让用户看到报错堆栈
        return text_chunk

# ==============================================================================
# 🟢 第四部分：OCR 引擎模块 (Mistral OCR)
# ==============================================================================

def get_mistral_client():
    """获取配置好的 Mistral 客户端"""
    if not MISTRAL_API_KEY or "您的" in MISTRAL_API_KEY:
        raise ValueError("请在 app.py 中填写有效的 Mistral API Key")
    return Mistral(api_key=MISTRAL_API_KEY)

def process_chunk_with_mistral(file_content_bytes, mime_type, filename_base):
    """
    功能：调用 Mistral OCR API 处理单个 PDF/图片块。
    
    :param file_content_bytes: 文件二进制数据
    :param mime_type: 文件类型 (application/pdf 或 image/...)
    :param filename_base: 文件名（用于日志）
    :return: 包含 Base64 图片的 Markdown 字符串
    """
    try:
        # 1. 编码为 Base64 Data URI
        base64_encoded = base64.b64encode(file_content_bytes).decode('utf-8')
        data_uri = f"data:{mime_type};base64,{base64_encoded}"

        client = get_mistral_client()
        
        # 2. 调用 API
        ocr_response = client.ocr.process(
            model="mistral-ocr-latest",
            document={
                "type": "document_url",
                "document_url": data_uri
            },
            include_image_base64=True
        )
        
        full_markdown = ""
        image_map = {}
        
        # 3. 解析结果，提取 Markdown 和图片
        for page in ocr_response.pages:
            for img in page.images:
                image_map[img.id] = img.image_base64
            
            # 🟢 添加自定义分页标记，用于前端同步滚动
            full_markdown += f"\n\n[[PAGE_BREAK]]\n\n{page.markdown}"

        # 4. 将 Markdown 中的图片 ID 替换为 Base64
        def replace_img_ref(match):
            img_id = match.group(1)
            if img_id in image_map:
                b64_data = image_map[img_id]
                if not b64_data.startswith("data:"):
                    b64_data = f"data:image/jpeg;base64,{b64_data}"
                return f"![image]({b64_data})"
            return match.group(0)

        final_markdown = re.sub(r'!\[.*?\]\((.*?)\)', replace_img_ref, full_markdown)
        return final_markdown
        
    except Exception as e:
        print(f"❌ Mistral 处理 {filename_base} 失败: {e}")
        return f"# ❌ 解析失败: {str(e)}\n\n"

# ==============================================================================
# 🟢 第五部分：Flask 路由控制器 (Routes)
# ==============================================================================

@app.route('/')
def index():
    """渲染主页"""
    return render_template('index.html')

@app.route('/gh_proxy')
def gh_proxy():
    """
    GitHub 文件代理接口
    功能：前端直接请求 GitHub 会有跨域和鉴权问题，通过此接口中转。
    参数：path (GitHub文件路径), download (true/false)
    """
    path = request.args.get('path')
    should_download = request.args.get('download', 'false').lower() == 'true'
    
    if not path: return "No path specified", 400
    
    url = f"{GITHUB_API_BASE}/{urllib.parse.quote(path)}"
    
    try:
        # 1. 获取文件元数据 (含 download_url)
        meta_resp = requests.get(url, headers=GH_HEADERS)
        if meta_resp.status_code != 200: 
            return f"File not found on GitHub: {meta_resp.text}", 404
        
        # 2. 下载实际文件内容
        download_url = meta_resp.json().get('download_url')
        file_resp = requests.get(download_url, headers=GH_HEADERS)
        
        # 3. 构造响应类型
        mimetype = 'text/plain'
        if path.endswith('.pdf'): mimetype = 'application/pdf'
        elif path.endswith('.md'): mimetype = 'text/markdown'
        elif path.endswith(('.jpg', '.png')): mimetype = 'image/jpeg'
        
        response = Response(file_resp.content, mimetype=mimetype)

        # 4. 如果请求下载，添加附件头
        if should_download:
            filename = os.path.basename(path)
            encoded_filename = urllib.parse.quote(filename)
            response.headers["Content-Disposition"] = f"attachment; filename*=utf-8''{encoded_filename}"
        
        return response

    except Exception as e:
        traceback.print_exc()
        return f"Proxy Error: {e}", 500

@app.route('/history/preload', methods=['POST'])
def preload_history():
    """
    🟢 新增接口：预加载历史记录
    前端选择身份后立即调用此接口，后端开启线程去 GitHub 拉取数据。
    """
    user_id = request.json.get('user', 's1')
    
    # 开启线程进行后台更新，立即返回，不阻塞前端
    thread = threading.Thread(target=background_refresh_task, args=(user_id,))
    thread.start()
    
    return jsonify({'status': 'started', 'message': f'Background fetch started for {user_id}'})
@app.route('/history/list', methods=['GET'])
def get_history_list():
    """
    修改后的列表接口：优先读缓存
    """
    user_id = request.args.get('user', 's1')
    
    # 优先从管理器读取
    items = history_manager.get(user_id)
    
    if items:
        print(f"⚡ [Cache Hit] 命中持久化缓存: {user_id}")
    else:
        print(f"🐢 [Cache Miss] 缓存失效，正在同步...")
        items = _fetch_github_data(user_id)
        history_manager.set(user_id, items)
    
    # 3. 补全 URL (url_for 需要在请求上下文中运行)
    # 因为缓存里存的是 path，这里动态生成最终 URL
    final_items = []
    for item in items:
        # 浅拷贝一下，避免修改缓存里的原始数据
        new_item = item.copy() 
        new_item['pdf_url'] = url_for('gh_proxy', path=item['pdf_path'])
        new_item['md_url'] = url_for('gh_proxy', path=item['md_path'])
        final_items.append(new_item)

    return jsonify(final_items)

@app.route('/upload', methods=['POST'])
def upload_file():
    """
    核心接口：上传与 OCR 处理
    流程：上传 -> 临时存储 -> PDF拆分 -> 循环OCR -> 合并Markdown -> 上传GitHub -> 清理
    """
    # 1. 验证请求
    user_id = request.form.get('user', 's1')
    if user_id not in USERS: return jsonify({'error': 'Invalid user'}), 403
    if 'file' not in request.files: return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    filename = file.filename
    file_extension = filename.rsplit('.', 1)[-1].lower()
    
    # 2. 准备路径与 ID
    timestamp = int(time.time())
    filename_base = os.path.splitext(filename)[0]
    task_id = f"{filename_base}_{timestamp}"
    
    # GitHub 存储路径
    gh_pdf_path = f"{user_id}/{task_id}.{file_extension}"
    gh_md_path = f"{user_id}/{task_id}.md"

    temp_filepath = None
    temp_md_path = None
    all_markdown_chunks = []

    try:
        # 3. 保存上传文件到临时目录
        temp_dir = tempfile.gettempdir()
        temp_filepath = os.path.join(temp_dir, f"{task_id}.{file_extension}")
        file.save(temp_filepath)
        
        final_markdown = ""

        # 4. 根据文件类型处理
        if file_extension == 'pdf':
            # --- PDF 处理流程 (分块) ---
            reader = PdfReader(temp_filepath)
            total_pages = len(reader.pages)
            
            for start_page in range(0, total_pages, PAGE_CHUNK_SIZE):
                end_page = min(start_page + PAGE_CHUNK_SIZE, total_pages)
                page_range_str = f"P{start_page+1}-P{end_page}"
                
                # 创建临时分块文件
                writer = PdfWriter()
                for i in range(start_page, end_page):
                    writer.add_page(reader.pages[i])
                
                temp_chunk_path = os.path.join(temp_dir, f"{task_id}_{page_range_str}.pdf")
                with open(temp_chunk_path, "wb") as output_stream:
                    writer.write(output_stream)
                
                # 读取分块并调用 OCR
                with open(temp_chunk_path, "rb") as chunk_file:
                    chunk_bytes = chunk_file.read()
                
                print(f"🔄 Processing chunk: {page_range_str}")
                markdown_chunk = process_chunk_with_mistral(
                    chunk_bytes, "application/pdf", f"{task_id}_{page_range_str}"
                )
                
                all_markdown_chunks.append(markdown_chunk)
                os.remove(temp_chunk_path) # 清理分块
            
            # 合并结果，使用同步标记
            final_markdown = "\n----------\n".join(all_markdown_chunks)
        
        elif file_extension in ['jpg', 'jpeg', 'png']:
            # --- 图片处理流程 ---
            with open(temp_filepath, "rb") as image_file:
                chunk_bytes = image_file.read()
            final_markdown = process_chunk_with_mistral(
                chunk_bytes, f"image/{file_extension}", task_id
            )
        
        if not final_markdown: final_markdown = "# ⚠️ 识别内容为空"

        if final_markdown:
            # 🟢 必须在这里调用清洗函数，修复上传后的原始 MD
            final_markdown = backend_smart_clean(final_markdown)

        # 5. 上传结果到 GitHub
        # 5.1 上传源文件 (PDF/Image)
        print(f"☁️ Uploading source to {gh_pdf_path}...")
        if not upload_to_github(temp_filepath, gh_pdf_path, f"Add source: {filename}"):
             raise Exception("Failed to upload source file.")
        
        # 5.2 上传 Markdown
        temp_md_path = os.path.join(temp_dir, f"{task_id}.md")
        with open(temp_md_path, "w", encoding="utf-8") as f:
            f.write(final_markdown)
        
        print(f"☁️ Uploading markdown to {gh_md_path}...")
        if not upload_to_github(temp_md_path, gh_md_path, f"Add markdown: {filename}"):
             raise Exception("Failed to upload markdown.")
        
        # 🟢 核心修改：上传成功后，让缓存失效或立即刷新
        # 方案：开启一个线程，稍后刷新该用户的缓存
        print(f"♻️ 上传成功，触发后台缓存刷新: {user_id}")
        refresh_thread = threading.Thread(target=background_refresh_task, args=(user_id,))
        refresh_thread.start()
        
        # 6. 返回结果
        return jsonify({
            'markdown': final_markdown,
            'download_url': url_for('gh_proxy', path=gh_md_path, download='true'),
            'pdf_url': url_for('gh_proxy', path=gh_pdf_path),
            'gh_path': gh_md_path
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': f"Processing Error: {str(e)}"}), 500
        
    finally:
        # 7. 清理临时文件
        if temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)
        if temp_md_path and os.path.exists(temp_md_path):
            os.remove(temp_md_path)

@app.route('/translate', methods=['POST'])
def translate_file():
    """
    翻译接口
    逻辑：检查是否已有翻译缓存 -> 若无，下载原MD -> 分段翻译 -> 上传新MD
    """
    data = request.get_json()
    if not data or 'path' not in data:
        return jsonify({"error": "Missing path parameter"}), 400
    
    gh_path = data.get('path') 
    dual_path = gh_path.replace('.md', '_dual.md')
    
    try:
        # 1. 检查 GitHub 是否已有翻译缓存
        check_url = f"{GITHUB_API_BASE}/{urllib.parse.quote(dual_path)}"
        if requests.get(check_url, headers=GH_HEADERS).status_code == 200:
            print("✅ Cache hit for translation.")
            download_url = requests.get(check_url, headers=GH_HEADERS).json().get('download_url')
            return jsonify({
                'content': requests.get(download_url, headers=GH_HEADERS).text, 
                'status': 'cached',
                'dual_url': url_for('gh_proxy', path=dual_path, download='true')
            })

        # 2. 下载原始 Markdown
        original_meta_url = f"{GITHUB_API_BASE}/{urllib.parse.quote(gh_path)}"
        meta_resp = requests.get(original_meta_url, headers=GH_HEADERS)
        if meta_resp.status_code != 200: return jsonify({'error': 'Original file not found'}), 404
        
        original_content = requests.get(meta_resp.json().get('download_url'), headers=GH_HEADERS).text
        
        # 1. 后端清洗 (同步之前前端的清洗逻辑)
        clean_content = backend_smart_clean(original_content)
        
        # 2. 智能分块 (按语义/长度切分)
        batches = smart_chunk_text(clean_content, max_chars=2000)
        
        print(f"🚀 开始并发翻译，共 {len(batches)} 个批次...")

        # 3. 使用并发执行全局辅助函数
        with ThreadPoolExecutor(max_workers=8) as executor:
            # 使用全局函数 translate_chunk 避免闭包引用错误
            dual_chunks = list(executor.map(translate_chunk, batches))

        dual_content = "\n\n".join(dual_chunks)

        # 重新组合
        dual_content = "\n\n".join(dual_chunks)
        
        # 4. 上传翻译结果
        temp_dual_path = os.path.join(tempfile.gettempdir(), "temp_dual.md")
        with open(temp_dual_path, "w", encoding="utf-8") as f:
            f.write(dual_content)
            
        print(f"☁️ Uploading translation to {dual_path}...")
        upload_to_github(temp_dual_path, dual_path, "Add AI Translation")
        os.remove(temp_dual_path)
        
        return jsonify({
            'content': dual_content, 
            'status': 'translated',
            'dual_url': url_for('gh_proxy', path=dual_path, download='true')
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    
@app.route('/history/delete', methods=['POST'])
def delete_history():
    data = request.json
    user_id = data.get('user')
    pdf_path = data.get('pdf_path')
    md_path = data.get('md_path')
    
    if not user_id or not pdf_path or not md_path:
        return jsonify({'error': '参数不完整'}), 400

    try:
        # 1. 定义需要尝试删除的文件路径列表
        # 包含 PDF、原始 MD 和可能存在的 双语版 MD
        files_to_delete = [
            pdf_path, 
            md_path, 
            md_path.replace('.md', '_dual.md')
        ]
        
        results = []
        for path in files_to_delete:
            # 2. 获取文件的 SHA 值（GitHub 删除文件必须提供 SHA）
            url = f"{GITHUB_API_BASE}/{urllib.parse.quote(path)}"
            resp = requests.get(url, headers=GH_HEADERS)
            
            if resp.status_code == 200:
                sha = resp.json().get('sha')
                
                # 3. 执行删除操作
                del_payload = {
                    "message": f"🗑️ 彻底删除文档: {path}",
                    "sha": sha,
                    "branch": GITHUB_BRANCH
                }
                del_resp = requests.delete(url, json=del_payload, headers=GH_HEADERS)
                results.append(f"{path}: {del_resp.status_code}")
            else:
                results.append(f"{path}: 跳过 (文件不存在)")

        # 4. 关键：删除后必须强制刷新本地缓存
        # 这样下次前端请求列表时，看到的就是更新后的数据
        print(f"♻️ 文件删除成功，正在刷新 {user_id} 的缓存...")
        _fetch_github_data(user_id)
        
        return jsonify({
            'status': 'success', 
            'details': results,
            'message': '文件已从 GitHub 物理删除并同步缓存'
        })
        
    except Exception as e:
        print(f"❌ 删除失败: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
