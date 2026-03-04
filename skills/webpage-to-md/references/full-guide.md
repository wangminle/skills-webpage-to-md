# Web to Markdown Grabber 完整参考手册

抓取网页并转换为 Markdown 格式的 Python 工具。支持单页抓取、批量处理、从索引页爬取整个子目录，并可下载图片到本地。

## 功能特性

**内容提取**：智能正文抽取（article → main → body）、手动选择器（`--target-id`/`--target-class`）、SPA 检测

**Markdown 转换**：标题/段落/列表、表格（支持复杂表格保留 HTML）、代码块、引用块、链接/图片、数学公式

**图片处理**：支持 `src`/`data-src`/`srcset`/`<picture>`、自动检测格式（PNG/JPEG/GIF/WebP/SVG/AVIF）、过滤图标

**批量处理**：URL 文件读取、索引页爬取、并发下载、合并输出/独立文件

**特定站点**：微信公众号（自动检测，支持传统长文 + 图文笔记/小绿书新格式）、Wiki 系统噪音清理

**其他**：YAML Frontmatter、反爬支持、PDF 导出、Windows 路径安全、模块化架构（8 个子模块）

---

## 安装

```bash
# Python 3.8+（兼容 Python <3.10）
pip install requests

# 可选：用于 PDF 导出的 Markdown 渲染
pip install markdown

# 可选：运行测试
pip install pytest
```

---

## 参数完整说明

### 基础参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `url` | 目标网页 URL | - |
| `--out` | 输出文件名 | 根据 URL 生成 |
| `--auto-title` | 自动按页面标题生成输出文件名（仅单页模式；未指定 `--out` 时生效；批量/爬取模式无效） | `False` |
| `--assets-dir` | 图片目录 | `<out>.assets` |
| `--title` | 文档标题 | 从 `<title>` 提取 |
| `--overwrite` | 覆盖已存在文件 | `False` |
| `--validate` | 校验图片引用 | `False` |

### 网络请求参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--timeout` | 超时（秒） | `60` |
| `--retries` | 重试次数 | `3` |
| `--max-html-bytes` | 单页 HTML 最大字节数（0 表示不限制） | `10MB` |
| `--best-effort-images` | 图片失败仅警告 | `False` |

### HTTP 请求定制

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--ua-preset` | UA 预设：`chrome-win`/`chrome-mac`/`chrome-linux`/`edge-win`/`firefox-win`/`safari-mac`/`tool` | `chrome-win` |
| `--user-agent` / `--ua` | 自定义 UA | - |
| `--cookie` | Cookie 字符串 | - |
| `--cookies-file` | Netscape cookies.txt | - |
| `--headers` | 请求头（JSON） | - |
| `--header` | 单个请求头（可重复） | - |

### Frontmatter 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--frontmatter` | 生成 YAML Frontmatter | `True` |
| `--no-frontmatter` | 禁用 Frontmatter | - |
| `--tags` | 标签（逗号分隔） | - |

### 内容提取参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--target-id` | 正文容器 id（支持逗号分隔多值，按优先级尝试） | - |
| `--target-class` | 正文容器 class（支持逗号分隔多值） | - |
| `--keep-html` | 复杂表格保留 HTML | `False` |
| `--spa-warn-len` | SPA 警告阈值 | `500` |
| `--clean-wiki-noise` | 清理 Wiki 噪音 | `False` |
| `--wechat` | 微信模式 | 自动 |

### 导航剥离参数（Docs/Wiki 站点优化）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--strip-nav` | 移除导航元素（nav/aside/.sidebar 等） | `False` |
| `--strip-page-toc` | 移除页内目录（.toc/.on-this-page 等） | `False` |
| `--exclude-selectors` | 自定义移除选择器（逗号分隔，简化 CSS 语法） | - |
| `--anchor-list-threshold` | 连续锚点列表移除阈值（默认 0 关闭，预设模式自动 10） | `0` |
| `--docs-preset` | 文档框架预设（见下表） | - |
| `--auto-detect` | 自动检测框架并应用预设 | `False` |
| `--list-presets` | 列出所有可用预设 | - |

**支持的文档框架预设**：

| 预设名称 | 适用站点 |
|----------|----------|
| `mintlify` | Mintlify 文档（如 OpenClaw） |
| `docusaurus` | Docusaurus 文档 |
| `gitbook` | GitBook 文档 |
| `vuepress` | VuePress 文档 |
| `mkdocs` | MkDocs / Material for MkDocs |
| `readthedocs` | Read the Docs |
| `sphinx` | Sphinx 文档 |
| `notion` | Notion 公开页面 |
| `confluence` | Atlassian Confluence |
| `generic` | 通用文档站点 |

### 批量处理参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--urls-file` | URL 文件 | - |
| `--output-dir` | 输出目录 | `./batch_output` |
| `--max-workers` | 并发数 | `3` |
| `--delay` | 请求间隔（秒） | `1.0` |
| `--skip-errors` | 跳过失败 | `False` |
| `--download-images` | 下载图片 | `False` |

### 合并输出参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--merge` | 合并为单文件 | `False` |
| `--merge-output` | 输出文件名 | `merged.md` |
| `--toc` | 生成目录 | `False` |
| `--merge-title` | 主标题 | - |
| `--source-url` | 来源 URL | 自动提取 |
| `--rewrite-links` | 链接改写为锚点 | `False` |
| `--no-source-summary` | 不显示来源信息 | `False` |
| `--split-output DIR` | 同时输出分文件版本（双版本模式） | - |
| `--warn-anchor-collisions` | 显示锚点冲突详情 | `False` |

### 爬取模式参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--crawl` | 启用爬取模式 | `False` |
| `--crawl-pattern` | 链接过滤正则 | - |
| `--same-domain` | 仅同域名 | `True` |
| `--no-same-domain` | 允许跨域 | - |

### PDF 导出

| 参数 | 说明 |
|------|------|
| `--with-pdf` | 生成 PDF（需 Edge/Chrome） |

### 安全参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--redact-url` | 输出文件中对 URL 脱敏（移除 query/fragment） | `True` |
| `--no-redact-url` | 关闭 URL 脱敏（保留完整 URL） | - |
| `--no-map-json` | 不生成 `*.assets.json` 映射文件（并清理已存在的旧映射文件） | `False` |
| `--max-image-bytes` | 单张图片最大字节数（0 表示不限制） | `25MB` |
| `--pdf-allow-file-access` | 生成 PDF 时允许 file:// 访问本地文件 | `False` |

---

## 使用场景

### 场景 1：单页导出

```bash
# 基础用法
python scripts/grab_web_to_md.py https://example.com/article

# 指定输出和标签
python scripts/grab_web_to_md.py https://example.com/article \
  --out my-article.md --tags "ai,tutorial"

# 自动按标题命名（例如：学习笔记/学习笔记.md）
python scripts/grab_web_to_md.py https://example.com/article --auto-title

# 图片失败不中断
python scripts/grab_web_to_md.py https://example.com/gallery --best-effort-images

# 复杂表格保留 HTML
python scripts/grab_web_to_md.py https://docs.example.com/api --keep-html
```

### 场景 2：批量导出（从文件）

**urls.txt 格式**：
```text
# 注释
https://example.com/page1 | 自定义标题
https://example.com/page2
```

```bash
# 独立文件
python scripts/grab_web_to_md.py --urls-file urls.txt --output-dir ./docs

# 合并为单文件
python scripts/grab_web_to_md.py --urls-file urls.txt --merge --toc --merge-output handbook.md
```

### 场景 3：爬取索引页

```bash
# 爬取并合并
python scripts/grab_web_to_md.py "https://wiki.example.com/index" \
  --crawl --crawl-pattern 'page=wiki' \
  --merge --toc --merge-output wiki.md

# 爬取为独立文件
python scripts/grab_web_to_md.py "https://wiki.example.com/index" \
  --crawl --crawl-pattern 'page=wiki' \
  --output-dir ./wiki_docs
```

### 场景 4：内容过滤

```bash
# 指定正文容器
python scripts/grab_web_to_md.py "https://wiki.example.com/page" --target-id body

# 清理 Wiki 噪音
python scripts/grab_web_to_md.py "https://wiki.example.com/page" \
  --target-id body --clean-wiki-noise
```

**常见站点配置**：

| 站点类型 | 参数 |
|----------|------|
| PukiWiki | `--target-id body --clean-wiki-noise` |
| MediaWiki | `--target-id content --clean-wiki-noise` |
| WordPress | `--target-class entry-content` |
| Ghost CMS | `--target-class post-content` |

### 场景 5：反爬处理

```bash
# Cookie
python scripts/grab_web_to_md.py URL --cookie "session=abc"

# 请求头
python scripts/grab_web_to_md.py URL --header "Authorization: Bearer xxx"

# 切换 UA
python scripts/grab_web_to_md.py URL --ua-preset firefox-win
```

### 场景 6：微信公众号

```bash
# 自动检测
python scripts/grab_web_to_md.py "https://mp.weixin.qq.com/s/xxx"

# 强制启用
python scripts/grab_web_to_md.py "URL" --wechat

# 离线 HTML（未提供 URL 也可从微信页面特征提取标题）
python scripts/grab_web_to_md.py --local-html wechat_saved.html --auto-title
```

**自动处理**：
- 传统长文：提取 `rich_media_content`、标题从 `og:title` 获取、清理交互按钮、下载图片
- 图文笔记（小绿书，`item_show_type=10`，`is_async=1`）：自动检测异步渲染格式，从 `window.cgiDataNew` 中提取标题、公众号信息和正文文本。此类文章的图片由 JS 动态加载，无法通过 HTTP 直接提取

### 场景 7：Docs 站点导出

```bash
# 使用预设导出 Mintlify 文档
python scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl \
  --merge --toc \
  --docs-preset mintlify \
  --merge-output docs.md \
  --download-images

# 双版本输出：同时生成合并版和分文件版
python scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl --merge --toc \
  --docs-preset mintlify \
  --merge-output output/merged.md \
  --split-output output/pages/ \
  --download-images

# 手动配置导航剥离
python scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl \
  --merge --toc \
  --strip-nav \
  --strip-page-toc \
  --anchor-list-threshold 15 \
  --merge-output docs.md

# 自动检测框架
python scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl \
  --merge --toc \
  --auto-detect \
  --merge-output docs.md

# 查看可用预设
python scripts/grab_web_to_md.py --list-presets
```

**预设优势**：
- 自动配置正文容器（如 `article`、`main`）
- 自动排除导航选择器
- 自动启用锚点列表剥离（阈值=10）
- 对 docs 站点可减少 50%+ 输出大小

**双版本输出优势**（`--split-output`）：
- 同时生成 merged.md 和独立页面文件
- 共享 assets 目录（图片只下载一次）
- 生成 INDEX.md 索引文件
- 适配 Obsidian、检索工具、协作编辑等场景

### 场景 8：SSR 动态站点自动提取

```bash
# 腾讯云开发者文章 — Next.js SSR 自动提取 ProseMirror JSON
python scripts/grab_web_to_md.py \
  "https://cloud.tencent.com/developer/article/2624003" \
  --auto-title --download-images

# 火山引擎文档 — Modern.js SSR 自动提取 MDContent
python scripts/grab_web_to_md.py \
  "https://www.volcengine.com/docs/6396/2189942" \
  --auto-title --download-images --best-effort-images

# 禁用 SSR 提取，回退到普通 HTML 解析
python scripts/grab_web_to_md.py URL --no-ssr
```

**检测逻辑**：工具会自动扫描 HTML 中的 SSR 数据标记：
- `<script id="__NEXT_DATA__">` → Next.js 站点 → 提取 ProseMirror JSON → 转换为 HTML
- `window._ROUTER_DATA = {...}` → Modern.js 站点 → 提取 MDContent（已是 Markdown）

**智能反爬绕过**：如果检测到 JS 反爬信号但同时存在 SSR 数据，工具会自动跳过反爬警告继续提取。

**支持站点**：
| 站点 | SSR 框架 | 数据格式 |
|------|---------|---------|
| 腾讯云开发者社区 | Next.js | ProseMirror JSON |
| 火山引擎文档 | Modern.js | Markdown (MDContent) |

### 场景 9：数据安全与隐私

```bash
# 默认行为：URL 脱敏开启，分享给他人时不会泄露 token/签名
python scripts/grab_web_to_md.py "https://mp.weixin.qq.com/s/xxx?token=secret&..."

# 调试用途：保留完整 URL
python scripts/grab_web_to_md.py URL --no-redact-url

# 不生成映射文件（减少敏感信息输出；并会清理已存在的旧 *.assets.json，避免残留）
python scripts/grab_web_to_md.py URL --no-map-json

# 处理大图站点：调整单图上限为 50MB
python scripts/grab_web_to_md.py URL --max-image-bytes 52428800

# 不限制图片大小（谨慎使用）
python scripts/grab_web_to_md.py URL --max-image-bytes 0
```

**安全特性（默认生效）**：
- URL 脱敏：输出文件中的 URL 只保留 `scheme://host/path`
- 跨域凭据隔离：包括 30x 重定向到 CDN 的场景；跨域请求不携带原站 Cookie/Authorization，同域则可携带
- 网络配置继承：干净 session 会继承代理/证书/adapter 配置，避免企业网络环境跨域图片下载失败
- 流式下载：图片写入临时文件而非内存，防止 OOM
- HTML 净化：保留 HTML 时自动移除 `onclick`/`onerror` 等事件属性

---

## 实战案例

### 案例 1：微信公众号文章

```bash
python scripts/grab_web_to_md.py \
  "https://mp.weixin.qq.com/s/xxx" \
  --out output/wechat.md --validate --overwrite
```

**输出**：`wechat.md` + `wechat.assets/`（图片）

### 案例 2：技术博客（带代码块）

```bash
python scripts/grab_web_to_md.py \
  "https://claude.com/blog/xxx" \
  --out output/blog.md --keep-html \
  --tags "ai,agents" --validate --overwrite
```

**输出**：完整保留代码块、YAML Frontmatter 含标签

### 案例 3：Wiki 批量导出

```bash
python scripts/grab_web_to_md.py \
  "https://wiki.example.com/index" \
  --crawl --crawl-pattern 'page=wiki' \
  --no-same-domain \
  --merge --toc \
  --merge-output output/wiki.md \
  --merge-title "完整攻略" \
  --target-id body \
  --clean-wiki-noise \
  --rewrite-links \
  --download-images \
  --max-workers 3 --delay 1.0 \
  --skip-errors --overwrite
```

**输出**：合并文档 + 目录 + 本地图片 + 锚点跳转

---

## 输出结构

### 自动创建同名目录

如果只指定文件名（不含目录），会**自动创建同名上级目录**，保持输出整洁：

```bash
# 输入：--out article.md
# 输出：
article/
├── article.md
├── article.assets/
└── article.md.assets.json

# 输入：--out docs/article.md（用户指定目录，保持不变）
docs/
├── article.md
├── article.assets/
└── article.md.assets.json
```

**单页模式**：
```
article/
├── article.md
├── article.assets/
│   ├── 01-hero.png
│   └── 02-diagram.jpg
└── article.md.assets.json
```

**批量独立文件**：
```
output_dir/
├── INDEX.md
├── 文章1.md
└── 文章2.md
```

**批量合并**：
```bash
# 输入：--merge-output wiki.md
# 输出：
wiki/
├── wiki.md    # 含目录
└── wiki.assets/
```

**双版本输出**（`--split-output`）：
```
output/
├── merged.md               # 合并版（单文件，带全局目录）
├── merged.assets/          # 图片目录（共享）
└── pages/                  # 分文件版
    ├── INDEX.md            # 结构索引
    ├── Page-Title-1.md
    └── Page-Title-2.md
```

---

## 技术细节

- **HTML 解析**：标准库 `HTMLParser`（无 BeautifulSoup 依赖）
- **图片检测**：Content-Type + 二进制嗅探
- **噪音过滤**：跳过 script/style/svg/video/按钮
- **表格**：简单→Markdown，复杂→保留 HTML
- **PDF**：Edge/Chrome headless `--print-to-pdf`
- **路径**：自动截断避免 Windows 260 字符限制
- **安全**：
  - URL 脱敏：`urllib.parse` 解析后移除 query/fragment
  - 跨域凭据隔离：对比 URL 主机名（含重定向链），跨域请求使用干净 session
  - 网络配置继承：干净 session 继承 `proxies/verify/cert/adapters`，避免网络环境差异导致下载失败
  - 流式下载：`iter_content(chunk_size=65536)` + 临时文件
  - HTML 净化：正则过滤 `on\w+=` 属性、`javascript:/vbscript:/file:` 协议

### 模块化架构（v2.0.0+）

项目从单文件重构为模块化包，总计约 5400 行代码：

```
scripts/
├── grab_web_to_md.py       # CLI 入口（~1450 行）：参数解析 + 流程调度
└── webpage_to_md/          # 核心功能包（~4100 行）
    ├── __init__.py          # 包入口，导出数据模型
    ├── models.py            # 数据模型（~70 行）
    ├── security.py          # URL 脱敏 / JS 检测 / 校验（~240 行）
    ├── http_client.py       # HTTP 会话与 HTML 抓取（~200 行）
    ├── ssr_extract.py       # SSR 数据提取：Next.js/Modern.js（~260 行）
    ├── images.py            # 图片下载与路径替换（~500 行）
    ├── extractors.py        # 正文提取 + 框架预设 + 导航剥离 + 微信异步提取（~1210 行）
    ├── markdown_conv.py     # HTML→Markdown + 噪音清理（~940 行）
    ├── output.py            # 合并/分文件/索引/frontmatter（~450 行）
    └── pdf_utils.py         # Markdown→PDF 渲染（~420 行）
```

**依赖关系**（无循环依赖）：
```
models (无依赖)
  ↑
security → models
  ↑
http_client (无包内依赖)
extractors (无包内依赖)
markdown_conv → security
images → models, security
output → markdown_conv, models, security
pdf_utils (无包内依赖)
```

**设计原则**：
- CLI 入口仅负责参数解析和流程编排，不包含业务逻辑
- 各模块职责单一，可独立测试
- 仅依赖 `requests`（必需）和标准库，保持轻量

---

## 更新日志

### v2.1.2 (2026-03-03)
- ✨ **微信图文笔记（小绿书）新格式支持**：
  - 自动检测 `is_async=1` + `item_show_type=10` 的异步渲染文章
  - 从 `window.cgiDataNew` 中提取标题、公众号名称、签名和正文文本
  - 实现 `JsDecode` 转义还原（`\x0a`→换行、`\x3c`→`<` 等）
  - 新增函数：`is_wechat_async_article()`、`extract_wechat_async_content()`、`wechat_async_to_markdown()`
  - 传统 `rich_media_content` 文章不受影响，保持原有行为
  - 注意：此类文章的图片由 JS 动态加载，仅能提取文本内容

### v2.1.1 (2026-02-10)
- 🐛 **Markdown 图片 title 解析修复**：
  - `collect_md_image_urls` 正确剔除标准 Markdown 图片 title 文本（`![alt](url "title")` → 仅提取 `url`）
  - 同时处理 title 和非标准尺寸提示（`=800x`）的组合场景
  - `resolve_relative_md_images` 同步修复，保留 title 部分用于最终输出
- 🐛 **Editor.js HTML 清洗加固**：
  - `_sanitize_editorjs_html` 覆盖无引号属性写法（`onclick=alert(1)`、`href=javascript:alert(1)`）
  - 正则边界修正：无引号属性值匹配限制在 `[^\s>]`，避免吞入标签闭合符 `>`
- ✅ **新增测试用例**：7 个新测试覆盖 title 剔除和无引号 XSS 清洗

### v2.1.0 (2026-02-10)
- ✨ **`--auto-title` 自动命名**：
  - 从页面 `<h1>` / `<title>` 提取标题，清理后作为输出文件名
  - 仅单页模式生效；`--out` 优先级更高
  - 支持 `--local-html` 离线微信页面（无需 `--base-url` 即可通过 HTML 特征提取微信标题）
  - 标题长度限制 80 字符，特殊字符替换为连字符
- 🐛 **修复 `--validate` 校验误报**：
  - 修复本地图片路径包含 URL 编码（%20/%28/%29）时被误判为缺失的问题
  - 采用"先查字面路径 → 再回退解码路径"策略，兼容字面包含 `%20` 的文件名
- 🏗️ **代码重构**：
  - 新增 `_fetch_page_html()` 辅助函数，统一页面获取 + 错误处理 + JS 反爬检测
  - 新增 `_extract_title_for_filename()` 标题提取函数（微信标题 > H1 > title > Untitled）

### v2.0.0 (2026-02-06)
- 🏗️ **模块化重构**：将单文件 `grab_web_to_md.py`（~3700 行）拆分为 `webpage_to_md` 包（8 个子模块）：
  - `models.py`：数据模型（BatchConfig / BatchPageResult / JSChallengeResult / ValidationResult）
  - `security.py`：URL 脱敏、JS 反爬检测、Markdown 校验
  - `http_client.py`：UA 预设、Session 创建、HTML 抓取（重试/大小限制）
  - `images.py`：图片下载（流式/跨域隔离）、格式嗅探、路径替换
  - `extractors.py`：正文/标题/链接提取、10 种 Docs 框架预设、导航剥离
  - `markdown_conv.py`：HTML→Markdown 解析器、LaTeX 公式、表格转换、噪音清理
  - `output.py`：Frontmatter 生成、合并/分文件/索引输出、锚点冲突管理
  - `pdf_utils.py`：Markdown→HTML 渲染、Edge/Chrome headless PDF 打印
- 🏗️ **CLI 入口精简**：`grab_web_to_md.py` 仅保留参数解析和流程调度（~1220 行）
- 🏗️ **依赖链清晰**：`models` ← `security` ← `markdown_conv`/`images`/`output`，无循环依赖
- ✅ **新增单元测试**：`tests/test_grab_web_to_md.py`
- 🔧 **无功能变化**：所有 CLI 参数和行为保持 100% 向后兼容

### v1.7.0 (2026-02-03)
- ✨ **自动创建同名上级目录**：
  - 输出文件（如 `article.md`）自动放入同名目录（`article/article.md`）
  - 用户指定目录时（如 `docs/article.md`）保持不变
  - 适用于单页模式（`--out`）和批量合并模式（`--merge-output`）
- 🐛 **修复图片提取丢失**：
  - 移除 `DEFAULT_TOC_SELECTORS` 中过于宽泛的 `.contents` 选择器
  - 避免误删 Mintlify 等框架中的主要内容区域
- 🔧 **Phase 3-C 代码质量增强**：
  - 新增 `yaml_escape_str()` 统一 YAML 转义（处理 `\"/\/\n/\r/\t`）
  - 新增 `escape_markdown_link_text()` 处理 `]`/`[` 字符
  - 改用 `<h2 id="">` 锚点格式，提升 VSCode/Cursor 兼容性
  - 图片清理改为非破坏性策略（仅警告不删除）

### v1.7.0 (2026-02-10)
- ✨ **SSR 数据自动提取**：
  - 新增 `ssr_extract.py` 模块，自动检测并提取 JS 渲染站点的嵌入正文
  - 支持 Next.js `__NEXT_DATA__`（ProseMirror JSON → HTML）：腾讯云开发者社区
  - 支持 Modern.js `window._ROUTER_DATA`（MDContent → Markdown）：火山引擎文档
  - 检测到 SSR 数据时自动跳过 JS 反爬误报，无需 `--force`
  - Markdown 图片尺寸提示自动清理（如 `=986x`）
  - SSR 标题优先于 HTML 标题（auto-title 更准确）
  - 新增 `--no-ssr` 参数禁用 SSR 提取
- 🐛 **编码检测修复**：
  - 新增 HTML `<meta charset>` 编码检测，修复 Shift-JIS/EUC-JP 日文页面乱码
  - 优先级：HTTP Content-Type > HTML meta charset > UTF-8

### v1.6.0 (2026-02-02)
- ✨ **双版本输出**（Phase 3-B）：
  - 新增 `--split-output DIR` 同时输出分文件版本
  - 合并版和分文件版共享 assets 目录
  - 生成增强版 INDEX.md（含 Frontmatter 和文档信息）
  - 自动调整分文件中的图片相对路径
- 🐛 **Bug 修复**：
  - 修复 INDEX.md 链接映射可能错链的问题（相似标题场景）
  - 修复 INDEX.md YAML frontmatter 未转义特殊字符的问题
  - 修复 Windows 上图片相对路径使用反斜杠的问题

### v1.5.0 (2026-02-02)
- ✨ **导航剥离功能**：
  - 新增 `--strip-nav` 移除侧边栏/导航元素
  - 新增 `--strip-page-toc` 移除页内目录
  - 新增 `--exclude-selectors` 自定义移除选择器
  - 新增 `--anchor-list-threshold` 连续链接列表移除
- ✨ **文档框架预设**：
  - 新增 `--docs-preset` 支持 8 种框架（mintlify/docusaurus/gitbook 等）
  - 新增 `--auto-detect` 自动检测框架
  - 新增 `--list-presets` 列出可用预设
- ✨ **多值 target 支持**：`--target-id/--target-class` 支持逗号分隔多值
- ✨ **锚点冲突自动修复**（Phase 3-A）：
  - 自动检测重复标题生成的锚点冲突
  - 自动添加后缀去重（`#intro` → `#intro-2`, `#intro-3`...）
  - 新增 `--warn-anchor-collisions` 显示冲突详情
- 🐛 **Bug 修复**：
  - 修复单页模式 `--strip-nav` 等参数不生效的问题
  - 修复 `--anchor-list-threshold` 阈值语义不一致的问题
  - 批量模式默认不再启用锚点剥离（需显式启用或使用预设）

### v1.4.0 (2026-01-26)
- 🔒 **安全加固**：
  - URL 脱敏默认开启（`--no-redact-url` 可关闭）
  - 跨域图片下载不再携带 Cookie/Authorization（凭据隔离）
  - 图片流式写入 + 单图大小限制（默认 25MB）
  - PDF 生成默认关闭 `--allow-file-access-from-files`
  - HTML 属性净化：过滤 `on*` 事件、`javascript:` 协议
- ✨ 新增参数：`--no-redact-url`、`--no-map-json`、`--max-image-bytes`、`--pdf-allow-file-access`

### v1.3.4 (2026-01-26)
- ✨ 微信公众号支持：自动检测、正文提取、噪音清理

### v1.3.3 (2026-01-25)
- ✨ `--rewrite-links` 站内链接改写为锚点
- ✨ `--source-url` 自定义来源 URL
- 🐛 修复表格内图片丢失

### v1.3.2 (2026-01-25)
- ✨ `--download-images` 批量模式图片下载

### v1.3.1 (2026-01-25)
- ✨ `--clean-wiki-noise` Wiki 噪音清理

### v1.3.0 (2026-01-25)
- ✨ 批量处理模式、爬取模式、合并输出

### v1.2.0 (2026-01-18)
- ✨ `--best-effort-images`、嵌套表格支持

### v1.1.0 (2026-01-18)
- ✨ Frontmatter、Cookie/Header、UA 预设、复杂表格、手动选择器
