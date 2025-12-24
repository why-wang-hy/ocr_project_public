---

# 📚 Academic Paper OCR & Bilingual Reader

### 一个专为学术论文翻译阅读设计的「OCR + AI 翻译 + 云端存档」沉浸式阅读器

---

## 🌟 项目简介

**OCR Project Public** 是一款专为学习者打造的高效率文档翻译阅读工具。它打破了传统 OCR 工具仅仅“识别文字”的局限，通过集成 **DeepSeek / Mistral ** 顶尖 AI 模型，实现了从 PDF 上传到双语对照阅读的全流程闭环。

项目特别注重“阅读体验”，前端采取了很多美化处理，让原本枯燥的文献阅读变得优雅且高效。

---

## ✨ 核心特性

### 1. 📂 智能云端管理

* **GitHub 档案库同步**：通过后端 `gh_proxy` 代理，实现文档自动上传至 GitHub 私有/公共仓库，永久保存你的阅读痕迹。
* **按序历史追溯**：历史记录根据文件名内置的时间戳精准排序，确保最近阅读的 7 篇核心论文的上传时间了如指掌。
* **物理抹除功能**：支持在网页端直接删除 GitHub 仓库中的文件，实现真正的云端控制。

### 2. 🤖 深度 AI 翻译集成

* **双语对照阅读**：支持 AI 翻译开关，一键生成中英双语对照的 Markdown 文本。
* **预设模式**：支持“连续阅读”预设，开启后新上传的文档将自动执行翻译流程。
* **复杂公式解析**：完美支持 LaTeX 格式的数学公式渲染。

### 3. 🖱️ 极致交互体验

* **智能同步滚动**：当右侧 Markdown 标志线滚动至视口 **30%（中上部）** 时，左侧 PDF 自动跟进翻页，符合人类视觉焦点。
* **拖拽上传**：支持将 PDF 文件直接拖入浏览器进行处理。
* **视觉定制**：支持自定义 UI 背景壁纸，定制你的专属学术空间。

### 界面预览
<img width="2398" height="1431" alt="屏幕截图 2025-12-24 202849" src="https://github.com/user-attachments/assets/72e61559-bb37-433b-964a-35c9ec7c2d5c" />

---

## 🛠️ 技术栈

* **Frontend**: HTML5, CSS3 (Glassmorphism), Vanilla JavaScript
* **Backend**: Flask (Python)
* **Libraries**:
* [PDF.js](https://mozilla.github.io/pdf.js/) (PDF 解析与渲染)
* [requests](https://requests.readthedocs.io/) (API 通讯)
* [python-dotenv](https://saurabh-kumar.com/python-dotenv/) (环境配置)


* **Services**: GitHub API (数据持久化), DeepSeek/OpenAI API (智能翻译)

---

## 🚀 快速开始

## 提示：🗄️ 文档存储机制 (Storage Mechanism)

本项目采用 **GitHub 作为云端后端存储**，实现了本地零占用与多端同步：

1. **解耦设计**：
   - **程序库 (This Repo)**：存放 Flask 后端逻辑与前端界面。
   - **档案库 (Archive Repo)**：专门用于存放 OCR 处理后的 PDF 原件与 Markdown 译文。建议创建一个专门的仓库（如 `ocr-archive`）用于存储，以保持主项目的整洁。

2. **存储路径规范**：
   文件将按照用户 ID 自动分类存储在档案库中，命名规则如下：
   - PDF：`用户ID/文件名_时间戳.pdf`
   - Markdown：`用户ID/文件名_时间戳.md`
   - 双语对照版：`用户ID/文件名_时间戳_dual.md`

3. **配置要求**：
   请确保 `.env` 中的 `GITHUB_REPO` 指向你的**档案库名称**，且 `GITHUB_TOKEN` 拥有对该仓库的 `repo` 读写权限。

### 1. 克隆仓库

```bash
git clone https://github.com/why-wang-hy/ocr_project_public.git
cd ocr_project_public

```

### 2. 环境配置

在项目根目录创建 `.env` 文件，并填入以下必要参数：

```text
GITHUB_TOKEN=your_personal_access_token
GITHUB_USER=your_username
GITHUB_REPO=your_repo_name
GITHUB_BRANCH=your_branch_name
DEEPSEEK_API_KEY=your_key
MISTRAL_API_KEY=your_key
```

### 3. 安装依赖

```bash
pip install -r requirements.txt

```

### 4. 运行

```bash
python app.py

```

访问 `http://127.0.0.1:5000` 即可开始使用。

---

## ☁️ 部署在 PythonAnywhere

本项目已针对 **PythonAnywhere** 进行了深度优化：

1. **WSGI 适配**：代码已处理 `chdir` 与环境变量手动加载逻辑。
2. **静态文件映射**：
* URL: `/static/`
* Directory: `/home/yourname/ocr_project_public/static`


3. **安全性**：所有对 GitHub 的敏感请求均通过后端 `/gh_proxy` 代理，不暴露任何 Token 到前端。

---

## 📜 许可证

本项目采用 [MIT License](https://www.google.com/search?q=LICENSE) 许可协议。

---

## 🤝 鸣谢

本项目集成了多项优秀的开源技术，在此向以下项目及其社区表示由衷的感谢：

核心解析与渲染

PDF.js：由 Mozilla 维护的强大 PDF 解析库，支撑了项目的文档预览功能。

Marked.js：极速的 Markdown 解析器，用于将 OCR 结果实时转换为可视化的 HTML。

MathJax：为项目提供了近乎完美的 LaTeX 数学公式渲染支持。

AI 与 API 支撑

DeepSeek API / Mistral AI：为本项目提供了高性能的学术级大语言模型支持。

GitHub REST API：支撑了文档的远程持久化存储与历史记录管理。

---

> **"Read smarter, not harder."** —— 致力于提升每一位学习者的阅读体验。
