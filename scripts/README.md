# Web to Markdown Grabber / 网页转 Markdown 工具

[English](#english) | [中文](#chinese)

---

<a name="english"></a>
## English

### Overview

A lightweight Python tool that fetches web pages and converts them to clean Markdown format with local image assets. Uses only Python standard library HTML parser (no external dependencies like BeautifulSoup), making it suitable for offline or restricted environments.

### Features

- ✅ **Smart Content Extraction**: Prioritizes `<article>` → `<main>` → `<body>` to extract main content while filtering out navigation/footer noise
- ✅ **Pure Standard Library**: Uses `html.parser` only - no heavy dependencies
- ✅ **Comprehensive Image Support**: 
  - Handles `src`, `data-src`, `srcset`, `<picture>`, `<source>` elements
  - Supports relative URLs and auto-detects image formats
  - Filters out favicons and UI icons automatically
- ✅ **Rich Markdown Conversion**:
  - Headings, paragraphs, lists (ordered/unordered)
  - Tables (multi-line cells via `<br>`), blockquotes, code blocks (preserve whitespace + best-effort language fences)
  - Math formulas: extract TeX from MathJax/KaTeX when present, normalize `\(...\)`/`\[...\]` to `$...$`/`$$...$$`
  - Links, images, bold/italic text
- ✅ **YAML Frontmatter**: Generates standard metadata headers compatible with Obsidian/Hugo/Jekyll
- ✅ **Anti-Crawler Support**: Configurable User-Agent presets, Cookie/Header injection
- ✅ **Complex Table Handling**: Option to preserve HTML for tables with `colspan`/`rowspan` or nested tables
- ✅ **Manual Selector**: Specify target content area by ID or class when auto-extraction fails
- ✅ **SPA Detection**: Warns when extracted content is too short (possible dynamic rendering)
- ✅ **Robust Error Handling**: Automatic retries for network failures
- ✅ **Validation Tools**: Verify that all referenced images exist after conversion
- ✅ **Windows Path Safety**: Auto-truncates long filenames to avoid path length limits

### Installation

**Requirements**: Python 3.10+

```bash
# Install required package
pip install requests
```

### Usage

#### Basic Usage

```bash
python grab_web_to_md.py https://example.com/article
```

This will:
1. Download the web page
2. Extract main content
3. Create `example.com_article.md` with YAML frontmatter
4. Create `example.com_article.assets/` folder with images
5. Create `example.com_article.md.assets.json` mapping file

#### YAML Frontmatter (New!)

By default, the tool generates YAML frontmatter for better compatibility with note-taking apps. The visible title and source line are **always included** for readability:

```markdown
---
title: "Article Title"
source: "https://example.com/article"
date: "2026-01-18 13:30:28"
tags: ["ai", "agents"]
---

# Article Title

- Source: https://example.com/article

Content starts here...
```

```bash
# Add tags to frontmatter
python grab_web_to_md.py https://example.com/article --tags "ai,tutorial,tech"

# Disable frontmatter (keeps visible title and source line)
python grab_web_to_md.py https://example.com/article --no-frontmatter
```

#### Cookie & Header Support (New!)

For pages that require authentication or have anti-crawler protection:

```bash
# Pass cookie string directly
python grab_web_to_md.py https://example.com/private \
  --cookie "session=abc123; token=xyz789"

# Use Netscape cookies.txt file (exported from browser)
python grab_web_to_md.py https://example.com/private \
  --cookies-file cookies.txt

# Custom headers (JSON format)
python grab_web_to_md.py https://example.com/api-doc \
  --headers '{"Authorization": "Bearer xxx", "X-Custom": "value"}'

# Single header (can be repeated)
python grab_web_to_md.py https://example.com/article \
  --header "Authorization: Bearer xxx" \
  --header "X-Custom: value"
```

#### User-Agent Configuration (New!)

```bash
# Use preset User-Agent (default: chrome-win)
python grab_web_to_md.py https://example.com/article --ua-preset firefox-win

# Available presets: chrome-linux, chrome-mac, chrome-win, edge-win, firefox-win, safari-mac, tool

# Custom User-Agent (overrides preset)
python grab_web_to_md.py https://example.com/article \
  --user-agent "Mozilla/5.0 (custom UA string)"
```

#### Complex Table Handling (New!)

For tables with `colspan`/`rowspan` or nested tables that can't be properly converted to Markdown:

```bash
# Preserve complex tables as raw HTML in Markdown
python grab_web_to_md.py https://example.com/data-table --keep-html
```

#### Manual Content Selection (New!)

When auto-extraction fails (e.g., comment section longer than article):

```bash
# Specify content container by ID
python grab_web_to_md.py https://example.com/article --target-id "post-content"

# Specify content container by class
python grab_web_to_md.py https://example.com/article --target-class "article-body"
```

#### SPA Warning (New!)

The tool warns when extracted content is suspiciously short (possible SPA/dynamic rendering):

```bash
# Adjust warning threshold (default: 500 characters)
python grab_web_to_md.py https://spa-site.com/article --spa-warn-len 1000

# Disable SPA warning
python grab_web_to_md.py https://example.com/article --spa-warn-len 0
```

#### Other Options

```bash
# Specify custom output filename
python grab_web_to_md.py https://example.com/article --out my-article.md

# Specify custom assets directory
python grab_web_to_md.py https://example.com/article --assets-dir ./images

# Set custom title
python grab_web_to_md.py https://example.com/article --title "My Article Title"

# Overwrite existing files
python grab_web_to_md.py https://example.com/article --overwrite

# Run validation after conversion
python grab_web_to_md.py https://example.com/article --validate

# Generate PDF with the same basename (requires local Edge/Chrome)
python grab_web_to_md.py https://example.com/article --with-pdf

# Adjust timeout and retries
python grab_web_to_md.py https://example.com/article --timeout 120 --retries 5

# Best-effort image download (warn and skip on image failures)
python grab_web_to_md.py https://example.com/article --best-effort-images
```

#### Full Options Reference

| Option | Description | Default |
|--------|-------------|---------|
| `url` | Target webpage URL | (required) |
| `--out` | Output Markdown filename | Auto-generated from URL |
| `--assets-dir` | Image assets directory | `<output>.assets` |
| `--title` | Article title in Markdown | Extracted from `<title>` or `<h1>` |
| `--timeout` | Request timeout in seconds | 60 |
| `--retries` | Number of retry attempts | 3 |
| `--best-effort-images` | Warn and skip failed image downloads | False |
| `--overwrite` | Overwrite existing files | False |
| `--validate` | Validate output after conversion | False |
| `--with-pdf` | Also generate a same-name PDF (Edge/Chrome required) | False |
| **Frontmatter** | | |
| `--frontmatter` | Generate YAML frontmatter | True |
| `--no-frontmatter` | Disable YAML frontmatter | - |
| `--tags` | Tags for frontmatter (comma-separated) | None |
| **HTTP Request** | | |
| `--cookie` | Cookie string | None |
| `--cookies-file` | Netscape cookies.txt file path | None |
| `--headers` | Custom headers (JSON format) | None |
| `--header` | Single header (repeatable) | None |
| `--ua-preset` | User-Agent preset | `chrome-win` |
| `--user-agent`, `--ua` | Custom User-Agent | None |
| **Content Extraction** | | |
| `--keep-html` | Keep complex tables as raw HTML | False |
| `--target-id` | Extract content by element ID | None |
| `--target-class` | Extract content by element class | None |
| `--spa-warn-len` | SPA warning threshold (0 to disable) | 500 |

### Output Structure

```
example.com_article.md                # Markdown file (frontmatter + visible title/source + content)
example.com_article.assets/           # Image assets folder
  ├── 01-hero-image.png
  ├── 02-diagram.jpg
  └── 03-screenshot.webp
example.com_article.md.assets.json    # URL-to-local mapping
example.com_article.pdf               # Optional PDF (with --with-pdf)
```

### Examples

```bash
# Basic: Grab an Anthropic blog post
python grab_web_to_md.py https://www.anthropic.com/research/building-effective-agents

# With tags and validation
python grab_web_to_md.py https://lilianweng.github.io/posts/2023-06-23-agent/ \
  --tags "ai,agents,llm" --validate

# Authenticated page with custom headers
python grab_web_to_md.py https://private.example.com/doc \
  --cookie "session=xxx" \
  --header "Authorization: Bearer yyy" \
  --ua-preset edge-win

# Complex table page
python grab_web_to_md.py https://docs.example.com/api-reference \
  --keep-html --target-id "main-content"

# Legacy format (no frontmatter)
python grab_web_to_md.py https://example.com/article \
  --no-frontmatter --out legacy-format.md
```

### Technical Details

- **HTML Parsing**: Custom `HTMLParser` subclasses for image collection and Markdown conversion
- **Image Format Detection**: Supports PNG, JPEG, GIF, WebP, SVG, AVIF via content-type headers and binary sniffing
- **Noise Filtering**: Skips `<script>`, `<style>`, `<svg>`, `<video>`, buttons, and Ghost CMS UI elements
- **Table Conversion**: Converts HTML tables to Markdown pipe tables; complex/nested tables can be preserved as HTML
- **Nested Table Handling**: Inner tables are isolated from outer table parsing to prevent structure corruption
- **Self-Closing Tags**: Properly handles both `<img>` and `<img/>` formats
- **PDF Export**: Uses local Edge/Chrome headless `--print-to-pdf` (no extra Python deps required; optional python-markdown improves HTML fidelity)
- **Path Safety**: Auto-truncates filenames to avoid Windows 260-character path limit
- **Best-Effort Mode**: Optional graceful handling of image download failures

### Validation

When using `--validate`, the tool checks:
- Total image references in Markdown
- Local vs remote image references
- Number of files in assets directory
- Missing image files (returns exit code 3 if found)

### Tests

```bash
python -m unittest -q scripts/test_grab_web_to_md.py
```

---

<a name="chinese"></a>
## 中文

### 概述

一个轻量级 Python 工具，用于抓取网页并将其转换为干净的 Markdown 格式，同时下载图片到本地。仅使用 Python 标准库的 HTML 解析器（无需 BeautifulSoup 等外部依赖），适合离线或受限环境使用。

### 功能特性

- ✅ **智能内容提取**：优先提取 `<article>` → `<main>` → `<body>`，过滤导航/页脚等噪音内容
- ✅ **纯标准库实现**：仅使用 `html.parser` - 无重型依赖
- ✅ **全面的图片支持**：
  - 处理 `src`、`data-src`、`srcset`、`<picture>`、`<source>` 等元素
  - 支持相对 URL 和自动检测图片格式
  - 自动过滤网站图标和 UI 图标
- ✅ **丰富的 Markdown 转换**：
  - 标题、段落、列表（有序/无序）
  - 表格（单元格多行用 `<br>` 保留）、引用块、代码块（保留空白并尽力补充语言标识）
  - 数学公式：支持提取 MathJax/KaTeX 的 TeX 源，并把 `\(...\)`/`\[...\]` 归一为 `$...$`/`$$...$$`
  - 链接、图片、粗体/斜体文本
- ✅ **YAML Frontmatter**：生成兼容 Obsidian/Hugo/Jekyll 的标准元数据头
- ✅ **反爬支持**：可配置的 User-Agent 预设、Cookie/Header 注入
- ✅ **复杂表格处理**：对含 `colspan`/`rowspan` 或嵌套表格可选择保留原始 HTML
- ✅ **手动选择器**：自动提取失败时可通过 ID 或 class 指定目标区域
- ✅ **SPA 检测**：提取内容过短时发出警告（可能是动态渲染页面）
- ✅ **健壮的错误处理**：网络失败时自动重试
- ✅ **校验工具**：转换后验证所有引用的图片是否存在
- ✅ **Windows 路径安全**：自动截断过长文件名以避免路径长度限制

### 安装

**要求**：Python 3.10+

```bash
# 安装所需包
pip install requests
```

### 使用方法

#### 基础用法

```bash
python grab_web_to_md.py https://example.com/article
```

这将会：
1. 下载网页
2. 提取主要内容
3. 创建带 YAML frontmatter 的 `example.com_article.md` 文件
4. 创建 `example.com_article.assets/` 文件夹存放图片
5. 创建 `example.com_article.md.assets.json` 映射文件

#### YAML Frontmatter（新功能！）

默认情况下，工具会生成 YAML frontmatter 以更好地兼容笔记软件。为保持正文可读性，**始终包含**可见的标题和来源行：

```markdown
---
title: "文章标题"
source: "https://example.com/article"
date: "2026-01-18 13:30:28"
tags: ["ai", "agents"]
---

# 文章标题

- Source: https://example.com/article

正文内容...
```

```bash
# 添加标签到 frontmatter
python grab_web_to_md.py https://example.com/article --tags "ai,tutorial,tech"

# 禁用 frontmatter（仍保留可见标题和来源行）
python grab_web_to_md.py https://example.com/article --no-frontmatter
```

#### Cookie 和 Header 支持（新功能！）

用于需要登录或有反爬保护的页面：

```bash
# 直接传入 cookie 字符串
python grab_web_to_md.py https://example.com/private \
  --cookie "session=abc123; token=xyz789"

# 使用 Netscape 格式的 cookies.txt 文件（从浏览器导出）
python grab_web_to_md.py https://example.com/private \
  --cookies-file cookies.txt

# 自定义请求头（JSON 格式）
python grab_web_to_md.py https://example.com/api-doc \
  --headers '{"Authorization": "Bearer xxx", "X-Custom": "value"}'

# 单个请求头（可重复使用）
python grab_web_to_md.py https://example.com/article \
  --header "Authorization: Bearer xxx" \
  --header "X-Custom: value"
```

#### User-Agent 配置（新功能！）

```bash
# 使用预设 User-Agent（默认：chrome-win）
python grab_web_to_md.py https://example.com/article --ua-preset firefox-win

# 可用预设：chrome-linux, chrome-mac, chrome-win, edge-win, firefox-win, safari-mac, tool

# 自定义 User-Agent（覆盖预设）
python grab_web_to_md.py https://example.com/article \
  --user-agent "Mozilla/5.0 (自定义 UA 字符串)"
```

#### 复杂表格处理（新功能！）

对于含有 `colspan`/`rowspan` 或嵌套表格，无法正确转换为 Markdown 的情况：

```bash
# 在 Markdown 中保留复杂表格的原始 HTML
python grab_web_to_md.py https://example.com/data-table --keep-html
```

#### 手动内容选择（新功能！）

当自动提取失败时（如评论区比正文还长）：

```bash
# 通过 ID 指定内容容器
python grab_web_to_md.py https://example.com/article --target-id "post-content"

# 通过 class 指定内容容器
python grab_web_to_md.py https://example.com/article --target-class "article-body"
```

#### SPA 警告（新功能！）

当提取内容异常短时（可能是 SPA/动态渲染），工具会发出警告：

```bash
# 调整警告阈值（默认：500 字符）
python grab_web_to_md.py https://spa-site.com/article --spa-warn-len 1000

# 禁用 SPA 警告
python grab_web_to_md.py https://example.com/article --spa-warn-len 0
```

#### 其他选项

```bash
# 指定自定义输出文件名
python grab_web_to_md.py https://example.com/article --out my-article.md

# 指定自定义图片目录
python grab_web_to_md.py https://example.com/article --assets-dir ./images

# 设置自定义标题
python grab_web_to_md.py https://example.com/article --title "我的文章标题"

# 覆盖已存在的文件
python grab_web_to_md.py https://example.com/article --overwrite

# 转换后运行校验
python grab_web_to_md.py https://example.com/article --validate

# 生成同名 PDF（需要本机安装 Edge/Chrome）
python grab_web_to_md.py https://example.com/article --with-pdf

# 调整超时和重试次数
python grab_web_to_md.py https://example.com/article --timeout 120 --retries 5

# 图片下载尽力而为（失败时仅警告并跳过）
python grab_web_to_md.py https://example.com/article --best-effort-images
```

#### 完整选项参考

| 选项 | 说明 | 默认值 |
|------|------|--------|
| `url` | 目标网页 URL | （必需） |
| `--out` | 输出 Markdown 文件名 | 根据 URL 自动生成 |
| `--assets-dir` | 图片资源目录 | `<输出文件>.assets` |
| `--title` | Markdown 中的文章标题 | 从 `<title>` 或 `<h1>` 提取 |
| `--timeout` | 请求超时时间（秒） | 60 |
| `--retries` | 重试次数 | 3 |
| `--best-effort-images` | 图片下载失败时仅警告并跳过 | False |
| `--overwrite` | 覆盖已存在的文件 | False |
| `--validate` | 转换后校验输出 | False |
| `--with-pdf` | 同时生成同名 PDF（需要 Edge/Chrome） | False |
| **Frontmatter** | | |
| `--frontmatter` | 生成 YAML frontmatter | True |
| `--no-frontmatter` | 禁用 YAML frontmatter | - |
| `--tags` | Frontmatter 中的标签（逗号分隔） | 无 |
| **HTTP 请求** | | |
| `--cookie` | Cookie 字符串 | 无 |
| `--cookies-file` | Netscape 格式 cookies.txt 文件路径 | 无 |
| `--headers` | 自定义请求头（JSON 格式） | 无 |
| `--header` | 单个请求头（可重复） | 无 |
| `--ua-preset` | User-Agent 预设 | `chrome-win` |
| `--user-agent`, `--ua` | 自定义 User-Agent | 无 |
| **内容提取** | | |
| `--keep-html` | 保留复杂表格为原始 HTML | False |
| `--target-id` | 通过元素 ID 提取内容 | 无 |
| `--target-class` | 通过元素 class 提取内容 | 无 |
| `--spa-warn-len` | SPA 警告阈值（0 禁用） | 500 |

### 输出结构

```
example.com_article.md                # Markdown 文件（frontmatter + 可见标题/来源 + 正文）
example.com_article.assets/           # 图片资源文件夹
  ├── 01-hero-image.png
  ├── 02-diagram.jpg
  └── 03-screenshot.webp
example.com_article.md.assets.json    # URL 到本地路径的映射
example.com_article.pdf               # 可选 PDF（带 --with-pdf）
```

### 使用示例

```bash
# 基础：抓取 Anthropic 博客文章
python grab_web_to_md.py https://www.anthropic.com/research/building-effective-agents

# 带标签和校验
python grab_web_to_md.py https://lilianweng.github.io/posts/2023-06-23-agent/ \
  --tags "ai,agents,llm" --validate

# 需要认证的页面，带自定义请求头
python grab_web_to_md.py https://private.example.com/doc \
  --cookie "session=xxx" \
  --header "Authorization: Bearer yyy" \
  --ua-preset edge-win

# 复杂表格页面
python grab_web_to_md.py https://docs.example.com/api-reference \
  --keep-html --target-id "main-content"

# 旧格式（无 frontmatter）
python grab_web_to_md.py https://example.com/article \
  --no-frontmatter --out legacy-format.md
```

### 技术细节

- **HTML 解析**：自定义 `HTMLParser` 子类用于图片收集和 Markdown 转换
- **图片格式检测**：通过 content-type 头和二进制嗅探支持 PNG、JPEG、GIF、WebP、SVG、AVIF
- **噪音过滤**：跳过 `<script>`、`<style>`、`<svg>`、`<video>`、按钮和 Ghost CMS UI 元素
- **表格转换**：将 HTML 表格转换为 Markdown 管道表格；复杂表格可保留为 HTML
- **自闭合标签**：正确处理 `<img>` 和 `<img/>` 两种格式
- **PDF 导出**：使用本机 Edge/Chrome headless `--print-to-pdf`（无需新增 Python 依赖；如已安装 python-markdown 可提升 HTML 一致性）
- **路径安全**：自动截断文件名以避免 Windows 260 字符路径限制

### 校验功能

使用 `--validate` 时，工具会检查：
- Markdown 中的图片引用总数
- 本地与远程图片引用数量
- 资源目录中的文件数量
- 缺失的图片文件（如发现则返回退出码 3）

### 测试

```bash
python -m unittest -q scripts/test_grab_web_to_md.py
```

---

## Changelog / 更新日志

### v1.2.0 (2026-01-18)

**New Features / 新功能：**
- ✨ **Best-Effort Image Download**: New `--best-effort-images` flag to warn and skip failed image downloads instead of aborting
- ✨ **Nested Table Support**: Properly handle tables within tables; nested tables are preserved as HTML when `--keep-html` is enabled

**Improvements / 改进：**
- 🔧 YAML frontmatter values (`source`, `date`) now properly quoted for better YAML parser compatibility
- 🔧 Improved nested table handling: inner table elements no longer interfere with outer table parsing
- 🔧 Python version requirement updated to 3.10+

### v1.1.0 (2026-01-18)

**New Features / 新功能：**
- ✨ **YAML Frontmatter**: Auto-generates metadata headers compatible with Obsidian/Hugo/Jekyll (`--frontmatter`, `--no-frontmatter`, `--tags`)
- ✨ **Cookie/Header Support**: Inject cookies and custom headers for authenticated pages (`--cookie`, `--cookies-file`, `--headers`, `--header`)
- ✨ **User-Agent Presets**: Choose from common browser UA strings (`--ua-preset`, `--user-agent`)
- ✨ **Complex Table Handling**: Preserve tables with colspan/rowspan as raw HTML (`--keep-html`)
- ✨ **Manual Content Selector**: Specify target element by ID or class (`--target-id`, `--target-class`)
- ✨ **SPA Warning**: Alert when content is too short (`--spa-warn-len`)
- ✨ **Path Length Safety**: Auto-truncate filenames for Windows compatibility

**Improvements / 改进：**
- 🔧 Default User-Agent changed from tool identifier to real browser UA
- 🔧 Visible title and source line are now **always included** for better readability (regardless of frontmatter setting)
- 🔧 PDF generation strips frontmatter but keeps visible title/source
- 🔧 Better handling of nested tables

---

## License / 许可证

This script is provided as-is for personal and educational use.

本脚本按原样提供，供个人和教育用途使用。
