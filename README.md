# Web to Markdown Grabber / 网页转 Markdown 工具

一个功能强大的 Python 工具，用于抓取网页并转换为干净的 Markdown 格式。支持单页抓取、批量处理、从索引页爬取整个子目录，并可下载图片到本地。

---

## 目录

- [功能特性](#功能特性)
- [安装要求](#安装要求)
- [快速开始](#快速开始)
- [参数完整说明](#参数完整说明)
- [默认参数组合](#默认参数组合)
- [使用场景指南](#使用场景指南)
  - [场景 1：单页导出基础用法](#场景-1单页导出基础用法)
  - [场景 2：单页导出带图片的网页](#场景-2单页导出带图片的网页)
  - [场景 3：单页导出复杂表格](#场景-3单页导出复杂表格保持原格式)
  - [场景 4：批量导出多个网页](#场景-4批量导出多个网页从文件读取-url)
  - [场景 5：批量导出并合并为单文件](#场景-5批量导出并合并为单文件)
  - [场景 6：从索引页爬取整个子目录](#场景-6从索引页爬取整个子目录)
  - [场景 7：过滤导航栏/边栏](#场景-7过滤导航栏边栏)
  - [场景 8：处理反爬/需要认证的页面](#场景-8处理反爬需要认证的页面)
- [实战案例](#实战案例)
- [输出结构](#输出结构)
- [技术细节](#技术细节)
- [更新日志](#更新日志)

---

## 功能特性

### 内容提取
- ✅ **智能正文抽取**：优先提取 `<article>` → `<main>` → `<body>`，自动过滤导航/页脚噪音
- ✅ **手动选择器**：可通过 `--target-id` 或 `--target-class` 精确指定正文容器
- ✅ **SPA 检测**：正文过短时发出警告（可能是动态渲染页面）

### Markdown 转换
- ✅ 标题、段落、列表（有序/无序）
- ✅ 表格（支持 `<br>` 换行、复杂表格可保留原始 HTML）
- ✅ 代码块（保留空白、尽力识别语言）
- ✅ 引用块、链接、图片、粗体/斜体
- ✅ 数学公式（MathJax/KaTeX 转换为 `$...$` / `$$...$$`）

### 图片处理
- ✅ 支持 `src`、`data-src`、`srcset`、`<picture>`、`<source>` 等多种格式
- ✅ 相对 URL 自动转绝对路径
- ✅ 自动检测图片格式（PNG、JPEG、GIF、WebP、SVG、AVIF）
- ✅ 过滤网站图标和 UI 图标

### 批量处理
- ✅ **从文件读取 URL 列表**：支持注释和自定义标题
- ✅ **从索引页爬取链接**：支持正则过滤、同域名限制
- ✅ **并发下载**：可配置线程数和请求间隔
- ✅ **合并输出**：多页合并为单个 Markdown（带目录）
- ✅ **独立文件**：每页一个文件 + 自动生成 INDEX.md

### 其他特性
- ✅ **YAML Frontmatter**：兼容 Obsidian/Hugo/Jekyll
- ✅ **反爬支持**：User-Agent 预设、Cookie/Header 注入
- ✅ **PDF 导出**：使用本机 Edge/Chrome headless
- ✅ **Windows 路径安全**：自动截断过长文件名
- ✅ **纯标准库 HTML 解析**：无需 BeautifulSoup

---

## 安装要求

**Python 版本**：3.10+

```bash
# 安装必需依赖
pip install requests
```

---

## 快速开始

```bash
# 1. 最简单的用法：抓取单个网页
python grab_web_to_md.py https://example.com/article

# 2. 批量抓取：从 URL 文件读取
python grab_web_to_md.py --urls-file urls.txt --output-dir ./docs

# 3. 爬取模式：从索引页抓取所有子页面并合并
python grab_web_to_md.py "https://wiki.example.com/index" \
  --crawl --crawl-pattern 'page=wiki' \
  --merge --toc --merge-output wiki.md
```

---

## 参数完整说明

### 基础参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `url` | 目标网页 URL（单页模式必需，批量模式可选作为索引页） | - |
| `--out` | 输出 Markdown 文件名 | 根据 URL 自动生成 |
| `--assets-dir` | 图片资源目录 | `<输出文件>.assets` |
| `--title` | 文档标题（覆盖自动提取的标题） | 从 `<title>` 或 `<h1>` 提取 |
| `--overwrite` | 允许覆盖已存在的文件 | `False` |
| `--validate` | 转换后校验图片引用 | `False` |

### 网络请求参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--timeout` | 请求超时时间（秒） | `60` |
| `--retries` | 网络重试次数 | `3` |
| `--best-effort-images` | 图片下载失败时仅警告并跳过（不中断） | `False` |

### HTTP 请求定制

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--ua-preset` | User-Agent 预设 | `chrome-win` |
| `--user-agent` / `--ua` | 自定义 User-Agent（优先于预设） | - |
| `--cookie` | Cookie 字符串，如 `session=abc; token=xyz` | - |
| `--cookies-file` | Netscape 格式 cookies.txt 文件路径 | - |
| `--headers` | 自定义请求头（JSON 格式） | - |
| `--header` | 追加单个请求头（可重复使用） | - |

**User-Agent 预设可选值**：
- `chrome-win`（默认）、`chrome-mac`、`chrome-linux`
- `edge-win`、`firefox-win`、`safari-mac`
- `tool`（工具标识，部分站点会拦截）

### Frontmatter 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--frontmatter` | 生成 YAML Frontmatter 元数据头 | `True`（默认启用） |
| `--no-frontmatter` | 禁用 YAML Frontmatter | - |
| `--tags` | Frontmatter 标签（逗号分隔），如 `ai,tutorial` | - |

### 内容提取参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--target-id` | 指定正文容器的 `id`（如 `content`、`body`、`post-content`） | - |
| `--target-class` | 指定正文容器的 `class`（如 `article-body`、`main`） | - |
| `--keep-html` | 复杂表格（含 colspan/rowspan）保留原始 HTML | `False` |
| `--spa-warn-len` | 正文长度低于此值时警告（0 禁用） | `500` |
| `--clean-wiki-noise` | 清理 Wiki 系统噪音（编辑按钮、导航链接等） | `False` |

### 批量处理参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--urls-file` | 从文件读取 URL 列表（每行一个） | - |
| `--output-dir` | 批量输出目录（独立文件模式） | `./batch_output` |
| `--max-workers` | 并发线程数（建议不超过 5） | `3` |
| `--delay` | 请求间隔秒数（避免被封） | `1.0` |
| `--skip-errors` | 跳过失败的 URL 继续处理 | `False` |
| `--download-images` | 下载图片到本地 assets 目录 | `False` |

### 合并输出参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--merge` | 合并所有页面为单个 MD 文件 | `False` |
| `--merge-output` | 合并输出文件名 | `merged.md` |
| `--toc` | 在合并文件开头生成目录 | `False` |
| `--merge-title` | 合并文档的主标题 | - |
| `--source-url` | 来源站点 URL（显示在文档信息中） | 自动提取域名 |
| `--rewrite-links` | 将站内链接改写为文档内锚点 | `False` |
| `--no-source-summary` | 不显示来源信息汇总 | `False` |

### 爬取模式参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--crawl` | 启用爬取模式：从索引页提取链接并批量抓取 | `False` |
| `--crawl-pattern` | 链接匹配正则表达式（过滤无关链接） | - |
| `--same-domain` | 仅抓取同域名链接 | `True`（默认启用） |
| `--no-same-domain` | 允许抓取跨域链接 | - |

### PDF 导出参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--with-pdf` | 同时生成同名 PDF（需要本机 Edge/Chrome） | `False` |

---

## 默认参数组合

当你不指定任何参数，仅提供 URL 时，脚本使用以下默认配置：

```bash
python grab_web_to_md.py https://example.com/article

# 等效于：
python grab_web_to_md.py https://example.com/article \
  --ua-preset chrome-win \
  --timeout 60 \
  --retries 3 \
  --frontmatter \
  --spa-warn-len 500
```

**默认行为**：
- 输出文件名根据 URL 自动生成（如 `example.com_article.md`）
- 图片保存到 `<文件名>.assets/` 目录
- 生成 YAML Frontmatter（含 title、source、date）
- 使用 Chrome Windows User-Agent
- 智能提取正文（article → main → body）
- 图片下载失败会中断（非 best-effort）

---

## 使用场景指南

### 场景 1：单页导出基础用法

**需求**：导出一篇博客文章或技术文档为 Markdown。

```bash
# 最简用法
python grab_web_to_md.py https://www.anthropic.com/research/building-effective-agents

# 指定输出文件名和标题
python grab_web_to_md.py https://example.com/article \
  --out my-article.md \
  --title "我的文章标题"

# 添加标签（会写入 Frontmatter）
python grab_web_to_md.py https://example.com/article \
  --tags "ai,agents,tutorial"

# 不要 Frontmatter（纯 Markdown）
python grab_web_to_md.py https://example.com/article --no-frontmatter
```

**输出效果**：
```
my-article.md              # Markdown 文件
my-article.assets/         # 图片目录
  ├── 01-hero.png
  └── 02-diagram.jpg
my-article.md.assets.json  # URL→本地映射
```

---

### 场景 2：单页导出带图片的网页

**需求**：导出图片较多的页面，希望图片下载失败时不中断。

```bash
# 图片失败仅警告（不中断整个流程）
python grab_web_to_md.py https://example.com/gallery \
  --best-effort-images \
  --overwrite

# 指定图片目录
python grab_web_to_md.py https://example.com/article \
  --assets-dir ./images \
  --out article.md

# 转换后校验图片完整性
python grab_web_to_md.py https://example.com/article --validate
```

**效果预期**：
- `--best-effort-images`：某张图片下载失败时输出警告，继续处理其他图片
- `--validate`：转换完成后检查所有图片引用是否存在本地文件

---

### 场景 3：单页导出复杂表格（保持原格式）

**需求**：页面包含合并单元格（colspan/rowspan）或嵌套表格，标准 Markdown 表格无法正确表示。

```bash
# 复杂表格保留原始 HTML
python grab_web_to_md.py https://docs.example.com/api-reference \
  --keep-html \
  --out api-docs.md
```

**效果预期**：
- 简单表格仍转换为 Markdown pipe 表格
- 复杂表格（含 colspan/rowspan）保留为 HTML 代码块，确保格式不丢失

**输出示例**：

```markdown
## API 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| id | int | 用户 ID |
| name | string | 用户名 |

## 复杂矩阵表

<table>
<tr><th rowspan="2">项目</th><th colspan="2">数据</th></tr>
<tr><td>A</td><td>B</td></tr>
</table>
```

---

### 场景 4：批量导出多个网页（从文件读取 URL）

**需求**：有一批 URL 需要逐个导出为独立的 Markdown 文件。

**Step 1**：创建 URL 文件 `urls.txt`

```text
# 这是注释，会被忽略
# 格式：URL | 自定义标题（标题可选）

https://example.com/article1 | 第一篇文章
https://example.com/article2 | 第二篇文章
https://example.com/article3
```

**Step 2**：执行批量导出

```bash
# 导出为独立文件（每页一个 .md）
python grab_web_to_md.py \
  --urls-file urls.txt \
  --output-dir ./docs \
  --max-workers 3 \
  --delay 1.0

# 跳过失败的 URL 继续处理
python grab_web_to_md.py \
  --urls-file urls.txt \
  --output-dir ./docs \
  --skip-errors
```

**效果预期**：
```
./docs/
  ├── INDEX.md           # 自动生成的目录索引
  ├── 第一篇文章.md
  ├── 第二篇文章.md
  └── article3.md        # 无自定义标题时使用 URL 生成
```

---

### 场景 5：批量导出并合并为单文件

**需求**：将多个页面合并为一个完整的文档（如制作离线手册）。

```bash
# 从 URL 文件读取，合并为单文件 + 生成目录
python grab_web_to_md.py \
  --urls-file urls.txt \
  --merge \
  --toc \
  --merge-output handbook.md \
  --merge-title "完整手册"

# 简化版
python grab_web_to_md.py --urls-file urls.txt --merge --toc
```

**效果预期**：

```markdown
---
title: "完整手册"
date: "2026-01-25 22:35:32"
pages: 10
---

# 完整手册

## 目录

1. [第一篇文章](#第一篇文章)
2. [第二篇文章](#第二篇文章)
3. [第三篇文章](#第三篇文章)

---

<a id="第一篇文章"></a>

## 第一篇文章

- 来源：https://example.com/article1

正文内容...

---

<a id="第二篇文章"></a>

## 第二篇文章

- 来源：https://example.com/article2

正文内容...
```

---

### 场景 6：从索引页爬取整个子目录

**需求**：Wiki 或文档站点有一个目录/索引页，想一次性抓取所有子页面。

**示例：抓取 PukiWiki 站点的整个 MMR 攻略目录**

```bash
# 方案 A：合并为单个文档
python grab_web_to_md.py "https://metalmaniax.com/index.php?MMR%2F%B9%B6%CE%AC" \
  --crawl \
  --crawl-pattern 'index\.php\?MMR' \
  --merge \
  --toc \
  --merge-output MMR攻略.md \
  --merge-title "METAL MAX RETURNS 完整攻略" \
  --max-workers 3 \
  --delay 1.0

# 方案 B：每页一个文件
python grab_web_to_md.py "https://metalmaniax.com/index.php?MMR%2F%B9%B6%CE%AC" \
  --crawl \
  --crawl-pattern 'index\.php\?MMR' \
  --output-dir ./mmr_wiki \
  --max-workers 3 \
  --delay 1.0
```

**参数说明**：
- `--crawl`：启用爬取模式，从索引页提取所有链接
- `--crawl-pattern`：正则表达式，只抓取匹配的链接（过滤导航栏/外链）
- `--same-domain`：默认启用，只抓取同域名链接

**效果预期**：
- 脚本先访问索引页，提取所有匹配 `crawl-pattern` 的链接
- 然后并发抓取每个子页面
- 最后合并或独立输出

---

### 场景 7：过滤导航栏/边栏 + 清理 Wiki 噪音

**需求**：页面有侧边栏、导航菜单等干扰内容，只想抓取正文区域。对于 Wiki 站点，还需要清理编辑按钮、返回顶部链接等系统噪音。

**方法一**：通过 `--target-id` / `--target-class` 指定正文容器

```bash
# 通过 id 指定正文区域（PukiWiki 常用 id="body"）
python grab_web_to_md.py "https://wiki.example.com/page" \
  --target-id "body"

# 通过 class 指定正文区域
python grab_web_to_md.py "https://blog.example.com/post" \
  --target-class "article-content"
```

**方法二**：使用 `--clean-wiki-noise` 清理 Wiki 系统噪音

```bash
# 清理 PukiWiki/MediaWiki 等 Wiki 系统的噪音内容
python grab_web_to_md.py "https://wiki.example.com/page" \
  --target-id body \
  --clean-wiki-noise
```

`--clean-wiki-noise` 会自动清理以下内容：
- `![Edit](xxx/paraedit.png)` - 编辑图标
- `[https://xxx/cmd=secedit...](...)` - 编辑链接
- `[↑](xxx#navigator)` - 返回顶部链接
- `## 标题 [†](xxx#anchor)` - 标题中的锚点链接

**实战：抓取 PukiWiki 整站并清理噪音**

```bash
python grab_web_to_md.py \
  --urls-file mmr_urls.txt \
  --merge \
  --toc \
  --merge-output MMR_Wiki完整攻略.md \
  --merge-title "METAL MAX RETURNS 完整攻略" \
  --target-id body \
  --clean-wiki-noise \
  --max-workers 3 \
  --delay 1.0 \
  --overwrite
```

**常见网站的正文容器**：

| 网站类型 | 常用参数组合 |
|----------|-------------|
| PukiWiki | `--target-id body --clean-wiki-noise` |
| MediaWiki | `--target-id content --clean-wiki-noise` |
| WordPress | `--target-class entry-content` |
| Ghost CMS | `--target-class post-content` |
| Medium | `--target-class article-body` |

**效果对比**：

| 参数组合 | 侧边栏 | 编辑按钮 | 返回顶部 | 文件大小 |
|---------|-------|---------|---------|---------|
| 无参数 | ❌ 包含 | ❌ 包含 | ❌ 包含 | 最大 |
| `--target-id body` | ✅ 过滤 | ❌ 包含 | ❌ 包含 | 较小 |
| `--target-id body --clean-wiki-noise` | ✅ 过滤 | ✅ 清理 | ✅ 清理 | **最小** |

---

### 场景 8：处理反爬/需要认证的页面

**需求**：页面需要登录、有 Cookie 验证或反爬检测。

```bash
# 方法 1：直接传入 Cookie 字符串
python grab_web_to_md.py https://private.example.com/doc \
  --cookie "session=abc123; token=xyz789"

# 方法 2：使用 cookies.txt 文件（从浏览器导出）
python grab_web_to_md.py https://private.example.com/doc \
  --cookies-file cookies.txt

# 方法 3：自定义请求头（JSON 格式）
python grab_web_to_md.py https://api.example.com/docs \
  --headers '{"Authorization": "Bearer xxx", "X-API-Key": "yyy"}'

# 方法 4：单个请求头（可重复）
python grab_web_to_md.py https://example.com/article \
  --header "Authorization: Bearer xxx" \
  --header "X-Custom-Header: value"

# 方法 5：切换 User-Agent 绕过简单检测
python grab_web_to_md.py https://example.com/article \
  --ua-preset firefox-win

# 综合使用
python grab_web_to_md.py https://private.example.com/doc \
  --cookie "session=xxx" \
  --header "Authorization: Bearer yyy" \
  --ua-preset edge-win \
  --timeout 120 \
  --retries 5
```

**如何获取 Cookie**：
1. 在浏览器中登录目标网站
2. 打开开发者工具（F12）→ Network → 刷新页面
3. 点击任意请求 → Headers → 复制 `Cookie` 值
4. 或使用浏览器插件导出为 Netscape 格式的 `cookies.txt`

---

## 实战案例

### 案例 1：导出 Anthropic 博客文章

```bash
python grab_web_to_md.py \
  https://www.anthropic.com/research/building-effective-agents \
  --tags "ai,agents,anthropic" \
  --validate
```

### 案例 2：导出 PukiWiki 整站（METAL MAX RETURNS 攻略）

```bash
# 准备 URL 文件 mmr_urls.txt
# 然后执行（纯文本版）：

python grab_web_to_md.py \
  --urls-file mmr_urls.txt \
  --merge \
  --toc \
  --merge-output MMR_Wiki完整攻略.md \
  --merge-title "METAL MAX RETURNS 完整攻略" \
  --target-id body \
  --clean-wiki-noise \
  --max-workers 3 \
  --delay 1.0 \
  --overwrite

# 图文离线版（下载图片到本地）：

python grab_web_to_md.py \
  --urls-file mmr_urls.txt \
  --merge \
  --toc \
  --merge-output MMR_Wiki完整攻略.md \
  --merge-title "METAL MAX RETURNS 完整攻略" \
  --target-id body \
  --clean-wiki-noise \
  --download-images \
  --max-workers 3 \
  --delay 1.0 \
  --overwrite

# 完整离线版（图片 + 站内链接改写 + 来源 URL）：

python grab_web_to_md.py \
  --urls-file mmr_urls.txt \
  --merge \
  --toc \
  --merge-output MMR_Wiki完整攻略.md \
  --merge-title "METAL MAX RETURNS 完整攻略" \
  --source-url "https://metalmaniax.com/index.php?MMR" \
  --target-id body \
  --clean-wiki-noise \
  --download-images \
  --rewrite-links \
  --skip-errors \
  --max-workers 3 \
  --delay 1.0 \
  --overwrite
```

**效果**：
- 32 个页面合并为单文件
- 自动生成目录
- `--target-id body`：过滤侧边栏菜单
- `--clean-wiki-noise`：清理编辑按钮、返回顶部链接、锚点符号
- `--download-images`：下载图片到 `<文件名>.assets/` 目录
- 文件大小从 820KB 减少到 531KB（减少约 **35%**）
- 清理噪音：`cmd=secedit` 505→0，`[↑]` 473→0，`paraedit.png` 505→0

### 案例 3：导出技术文档并生成 PDF

```bash
python grab_web_to_md.py \
  https://docs.example.com/api/v2 \
  --keep-html \
  --target-id main-content \
  --with-pdf \
  --out api-v2-docs.md
```

### 案例 4：批量导出并跳过错误

```bash
python grab_web_to_md.py \
  --urls-file large_url_list.txt \
  --output-dir ./backup \
  --skip-errors \
  --best-effort-images \
  --max-workers 5 \
  --delay 0.5
```

---

## 输出结构

### 单页模式

```
article.md                # Markdown 文件
article.assets/           # 图片资源目录
  ├── 01-hero.png
  ├── 02-diagram.jpg
  └── 03-chart.webp
article.md.assets.json    # URL→本地路径映射
article.pdf               # 可选 PDF（--with-pdf）
```

### 批量独立文件模式

```
output_dir/
  ├── INDEX.md            # 自动生成的目录
  ├── 文章1.md
  ├── 文章2.md
  └── 文章3.md
```

### 批量合并模式

```
merged.md                 # 合并后的单文件（含目录）
```

---

## 技术细节

- **HTML 解析**：自定义 `HTMLParser` 子类（纯标准库，无 BeautifulSoup 依赖）
- **图片格式检测**：通过 Content-Type 头 + 二进制嗅探，支持 PNG/JPEG/GIF/WebP/SVG/AVIF
- **噪音过滤**：跳过 `<script>`、`<style>`、`<svg>`、`<video>`、按钮等元素
- **表格处理**：简单表格转 Markdown pipe 表格；复杂表格可保留 HTML
- **嵌套表格**：内表格隔离处理，防止结构损坏
- **PDF 导出**：使用本机 Edge/Chrome headless `--print-to-pdf`
- **路径安全**：自动截断文件名以避免 Windows 260 字符限制

---

## 更新日志

### v1.3.3 (2026-01-25)

**新功能：**
- ✨ **站内链接改写**：`--rewrite-links` 将外部链接改写为文档内锚点
  - 自动识别批量导出范围内的页面链接
  - 将 `[标题](https://xxx)` 改写为 `[标题](#锚点)`
  - 支持离线阅读时的文档内跳转
- ✨ **来源信息汇总**：合并文档开头自动显示
  - 导出时间、页面数量、来源站点
  - 可通过 `--no-source-summary` 禁用
- ✨ **自定义来源 URL**：`--source-url` 指定来源站点地址
  - 适用于 `--urls-file` 模式指定入口页面
  - 不指定时自动提取域名

**修复：**
- 🐛 修复表格内图片丢失的问题
- 🐛 修复动态 URL 图片扩展名错误（如 `.php` → `.gif`）

### v1.3.2 (2026-01-25)

**新功能：**
- ✨ **批量模式图片下载**：`--download-images` 下载图片到本地
  - 自动收集所有页面的图片 URL
  - 去重后统一下载到 `<输出文件>.assets/` 目录
  - 自动替换 Markdown 中的图片路径为本地相对路径
  - 支持离线阅读的完整图文版本

### v1.3.1 (2026-01-25)

**新功能：**
- ✨ **Wiki 噪音清理**：`--clean-wiki-noise` 清理 Wiki 系统噪音
  - 清理编辑图标（`paraedit.png` 等）
  - 清理编辑链接（`cmd=secedit` 等）
  - 清理返回顶部链接（`[↑](#navigator)` 等）
  - 清理标题锚点（`[†](xxx#anchor)` 等）
  - PukiWiki 实测：文件大小减少 **35%**

### v1.3.0 (2026-01-25)

**新功能：**
- ✨ **批量处理模式**：`--urls-file` 从文件读取 URL 列表
- ✨ **爬取模式**：`--crawl` 从索引页提取链接并批量抓取
- ✨ **合并输出**：`--merge` 将多页合并为单文件，`--toc` 生成目录
- ✨ **链接过滤**：`--crawl-pattern` 正则过滤，`--same-domain` 同域限制
- ✨ **并发控制**：`--max-workers` 线程数，`--delay` 请求间隔
- ✨ **独立文件模式**：`--output-dir` + 自动生成 INDEX.md

### v1.2.0 (2026-01-18)

**新功能：**
- ✨ **Best-Effort 图片下载**：`--best-effort-images` 失败时仅警告
- ✨ **嵌套表格支持**：正确处理表中表

**改进：**
- 🔧 YAML frontmatter 值正确加引号
- 🔧 Python 版本要求更新为 3.10+

### v1.1.0 (2026-01-18)

**新功能：**
- ✨ **YAML Frontmatter**：`--frontmatter`/`--no-frontmatter`/`--tags`
- ✨ **Cookie/Header 支持**：`--cookie`/`--cookies-file`/`--headers`/`--header`
- ✨ **User-Agent 预设**：`--ua-preset`/`--user-agent`
- ✨ **复杂表格处理**：`--keep-html`
- ✨ **手动内容选择**：`--target-id`/`--target-class`
- ✨ **SPA 警告**：`--spa-warn-len`
- ✨ **路径长度安全**：自动截断文件名

---

## 测试

```bash
python -m unittest -q scripts/test_grab_web_to_md.py
```

---

## License / 许可证

本脚本按原样提供，供个人和教育用途使用。
