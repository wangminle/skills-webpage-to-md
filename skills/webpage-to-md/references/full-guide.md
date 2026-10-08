# Web to Markdown Grabber 完整参考手册

> 当前版本：0.4.3

> **命令说明**：文中使用 `python3` / `pip3`（macOS/Linux 默认），Windows 用户请替换为 `python` / `pip`。脚本兼容 Python 3.8+。

抓取网页并转换为 Markdown 格式的 Python 工具。支持单页抓取、批量处理、从索引页爬取整个子目录，并可下载图片到本地。

## 目录

- [功能特性](#功能特性)
- [安装](#安装)
- [参数完整说明](#参数完整说明)（基础 / 网络 / HTTP / Frontmatter / 内容提取 / 导航剥离 / 批量 / 合并 / 爬取 / 安全）
- [参数支持矩阵](#参数支持矩阵)
- [使用场景](#使用场景)（单页 / 批量 / 爬取 / 内容过滤 / 反爬 / 微信 / Docs / SSR / Notion / 华为 / 安全）
- [实战案例](#实战案例)（微信 / 博客 / Wiki）
- [输出结构](#输出结构)
- [技术细节](#技术细节)（模块化架构）
- [更新日志](#更新日志)

---

## 功能特性

**内容提取**：智能正文抽取（article → main → body）、手动选择器（`--target-id`/`--target-class`）、SPA 检测

**Markdown 转换**：标题/段落/列表、表格（支持复杂表格保留 HTML）、代码块、引用块、链接/图片、数学公式

**图片处理**：支持 `src`/`data-src`/`srcset`/`<picture>`、自动检测格式（PNG/JPEG/GIF/WebP/SVG/AVIF）、过滤图标

**批量处理**：URL 文件读取、索引页爬取、并发下载、合并输出/独立文件

**特定站点**：微信公众号（自动检测，支持传统长文 + 图文笔记/小绿书新格式）、Wiki 系统噪音清理

**Notion 公开页面**：自动检测 `notion.so` 和 `*.notion.site` URL，通过内部 API 递归获取所有 Block 并转换为 HTML，支持标题/段落/列表/代码/引用/折叠/待办/图片等 Block 类型

**华为开发者文档**：自动检测 `developer.huawei.com/consumer/{cn|en}/doc/` URL，通过站点自身 `getDocumentById` API 获取正文 HTML 与标题（Angular SPA 页面普通 HTTP 只有 JS 空壳），支持单页 / 批量 / 爬取合并三种模式

**浏览器获取**：`--browser-fetch` 使用系统 Chrome/Edge headless 获取页面，绕过 JS 反爬（Cloudflare 等），无需额外 pip 依赖

**其他**：YAML Frontmatter、反爬支持、Windows 路径安全、模块化架构

---

## 安装

```bash
# Python 3.8+（兼容 Python <3.10）
pip3 install requests

# 可选：--browser-fetch 需要系统安装 Chrome 或 Edge
# macOS: 安装 Google Chrome 或 Microsoft Edge
# Linux: apt install google-chrome-stable 或 microsoft-edge-stable

# 可选：运行测试
pip3 install pytest
```

---

## 参数完整说明

### 基础参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `url` | 目标网页 URL | - |
| `--out` / `--output` | 输出文件名（仅单页模式；`--output` 为别名） | 根据 URL 生成 |
| `--auto-title` | 自动按页面标题生成输出文件名（仅单页模式；未指定 `--out` 时生效） | `False` |
| `--assets-dir` | 图片目录（仅单页模式） | `<out>.assets` |
| `--title` | 文档标题 | 从 `<title>` 提取 |
| `--overwrite` | 覆盖上次运行的已存在文件（同批次同名页面始终用数字后缀区分，不会互相覆盖） | `False` |
| `--validate` | 校验图片引用完整性（单页模式及批量模式均支持） | `False` |

### 网络请求参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--timeout` | 超时（秒） | `60` |
| `--retries` | 重试次数 | `3` |
| `--max-html-bytes` | 单页 HTML 最大字节数（0 表示不限制） | `10MB` |
| `--best-effort-images` | 图片失败仅警告 | `False` |

### 浏览器获取参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--browser-fetch` | 使用系统 Chrome/Edge headless 获取页面（绕过 JS 反爬） | `False` |

**工作原理**：两阶段策略——
1. **Phase 1（验证）**：启动 Chrome headless + `--remote-debugging-port`，访问目标 URL，等待 JS challenge 自动通过，cookie 持久化到临时 `user-data-dir`
2. **Phase 2（获取）**：用同一 profile 重启 Chrome + `--dump-dom`，此时 clearance cookie 已存在，直接获得真实页面 HTML

**适用范围**：需要 JS 执行的站点、基本的 Cloudflare JS Challenge。对使用 Turnstile 等高级人机验证的站点，headless 浏览器可能仍被检测，此时建议使用 `--local-html` 手动保存。

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
| `--no-notion` | 禁用 Notion 公开页面 API 自动提取 | `False`（默认自动检测） |
| `--no-huawei-api` | 禁用华为开发者文档 API 自动提取 | `False`（默认自动检测） |

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

### 安全参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--redact-url` | 输出文件中对 URL 脱敏（移除 query/fragment） | `True` |
| `--no-redact-url` | 关闭 URL 脱敏（保留完整 URL） | - |
| `--no-map-json` | 不生成 `*.assets.json` 映射文件（并清理已存在的旧映射文件） | `False` |
| `--max-image-bytes` | 单张图片最大字节数（0 表示不限制） | `25MB` |

---

## 参数支持矩阵

下表说明各参数在不同运行模式下的可用性。

| 参数 | 单页模式 | 批量/爬取模式 | 合并输出 | 说明 |
|------|:--------:|:------------:|:--------:|------|
| `--out` | ✅ | ❌ | ❌ | 单页输出文件名 |
| `--auto-title` | ✅ | ❌ | ❌ | 按页面标题自动命名 |
| `--assets-dir` | ✅ | ❌ | ❌ | 自定义图片目录 |
| `--overwrite` | ✅ | ✅ | ✅ | 覆盖已存在文件 |
| `--validate` | ✅ | ✅ | ✅ | 校验图片引用完整性 |
| `--download-images` | ❌（默认下载） | ✅ | ✅ | 批量模式默认不下载图片 |
| `--clean-wiki-noise` | ✅ | ✅ | ✅ | 清理 Wiki 噪音 |
| `--target-id` / `--target-class` | ✅ | ✅ | ✅ | 内容选择器 |
| `--browser-fetch` | ✅ | ✅ | ✅ | 使用系统浏览器获取页面 |
| `--local-html` | ✅ | ❌ | ❌ | 从本地文件读取 |
| `--urls-file` | ❌ | ✅ | ✅ | 批量 URL 文件 |
| `--merge` | ❌ | ✅ | - | 启用合并输出 |
| `--crawl` | ❌ | ✅ | ✅ | 启用爬取模式 |
| `--redact-url` | ✅ | ✅ | ✅ | URL 脱敏（仅影响输出文本） |
| `--no-notion` | ✅ | ✅ | ✅ | 禁用 Notion 公开页面 API 提取 |
| `--no-huawei-api` | ✅ | ✅ | ✅ | 禁用华为开发者文档 API 提取 |

> **注意**：单页模式默认下载图片到 `<输出文件名>.assets/` 目录；批量模式默认保留原始图片 URL，需显式加 `--download-images` 才下载。

---

## 使用场景

### 场景 1：单页导出

```bash
# 基础用法
python3 scripts/grab_web_to_md.py https://example.com/article

# 指定输出和标签
python3 scripts/grab_web_to_md.py https://example.com/article \
  --out my-article.md --tags "ai,tutorial"

# 自动按标题命名（例如：学习笔记/学习笔记.md）
python3 scripts/grab_web_to_md.py https://example.com/article --auto-title

# 图片失败不中断
python3 scripts/grab_web_to_md.py https://example.com/gallery --best-effort-images

# 复杂表格保留 HTML
python3 scripts/grab_web_to_md.py https://docs.example.com/api --keep-html
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
python3 scripts/grab_web_to_md.py --urls-file urls.txt --output-dir ./docs

# 合并为单文件
python3 scripts/grab_web_to_md.py --urls-file urls.txt --merge --toc --merge-output handbook.md
```

### 场景 3：爬取索引页

```bash
# 爬取并合并
python3 scripts/grab_web_to_md.py "https://wiki.example.com/index" \
  --crawl --crawl-pattern 'page=wiki' \
  --merge --toc --merge-output wiki.md

# 爬取为独立文件
python3 scripts/grab_web_to_md.py "https://wiki.example.com/index" \
  --crawl --crawl-pattern 'page=wiki' \
  --output-dir ./wiki_docs
```

### 场景 4：内容过滤

```bash
# 指定正文容器
python3 scripts/grab_web_to_md.py "https://wiki.example.com/page" --target-id body

# 清理 Wiki 噪音
python3 scripts/grab_web_to_md.py "https://wiki.example.com/page" \
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
python3 scripts/grab_web_to_md.py URL --cookie "session=abc"

# 请求头
python3 scripts/grab_web_to_md.py URL --header "Authorization: Bearer xxx"

# 切换 UA
python3 scripts/grab_web_to_md.py URL --ua-preset firefox-win
```

`--cookies-file` 读取七列 Netscape 格式。host-only Cookie 只发给精确主机；同名的合法共享 Cookie 仍按域名、路径、HTTPS 和有效期发送。名称或值含非法分隔符、空白或控制字符的行会被忽略，合法值保持原样。显式 `--header "Cookie: …"` 按用户输入保留；重定向按 requests 的规则删除该头并重新生成受域限制的 Cookie 头。

**JS Challenge 检测**：工具会自动识别 JavaScript 反爬保护（Cloudflare、Akamai 等），检测到时返回 exit code **4** 并提示使用 `--browser-fetch` 或 `--local-html`。

**三层应对策略**（由工具自动/半自动处理）：
1. **SSR/API 自动提取**：腾讯云、火山引擎、Notion、华为开发者文档等——零配置
2. **`--browser-fetch` 浏览器获取**：适用于需要 JS 执行或基本 Cloudflare JS Challenge 的站点
3. **`--local-html` 手动兜底**：适用于 Turnstile 等高级人机验证（headless 无法通过）

**已知 JS 保护站点**：

| 站点 | 保护类型 | 应对方式 |
|------|----------|----------|
| 腾讯云开发者 | Next.js SSR | **自动处理**（SSR 提取） |
| 火山引擎文档 | Modern.js SSR | **自动处理**（SSR 提取） |
| Notion 公开页面 | SPA（无 SSR） | **自动处理**（Notion API 提取） |
| 华为开发者文档 | Angular SPA（无 SSR） | **自动处理**（getDocumentById API 提取） |
| 一般 JS 渲染站点 | 需要 JS 执行 | 使用 `--browser-fetch` |
| Cloudflare JS Challenge | 基本 JS 验证 | 使用 `--browser-fetch` |
| 知乎 | 高强度 JS | 先试 `--browser-fetch`，再试 `--local-html` |
| Cloudflare Turnstile | 高级人机验证 | 使用 `--local-html`（headless 被检测） |
| PyPI | Cloudflare | 先试 `--browser-fetch`，再试 `--local-html` |
| 部分 GitHub 页面 | Cloudflare | 先试 `--browser-fetch`，再试 `--local-html` |
| 新闻站点 | 多种类型 | 先试 `--ua-preset`，再试 `--browser-fetch`，最后 `--local-html` |

### 场景 6：微信公众号

```bash
# 自动检测
python3 scripts/grab_web_to_md.py "https://mp.weixin.qq.com/s/xxx"

# 强制启用
python3 scripts/grab_web_to_md.py "URL" --wechat

# 离线 HTML（未提供 URL 也可从微信页面特征提取标题）
python3 scripts/grab_web_to_md.py --local-html wechat_saved.html --auto-title
```

**自动处理**：
- 传统长文：提取 `rich_media_content`、标题从 `og:title` 获取、清理交互按钮、下载图片
- 图文笔记（小绿书，`item_show_type=10`，`is_async=1`）：自动检测异步渲染格式，从 `window.cgiDataNew` 中提取标题、公众号信息和正文文本。此类文章的图片由 JS 动态加载，无法通过 HTTP 直接提取

### 场景 7：Docs 站点导出

```bash
# 使用预设导出 Mintlify 文档
python3 scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl \
  --merge --toc \
  --docs-preset mintlify \
  --merge-output docs.md \
  --download-images

# 双版本输出：同时生成合并版和分文件版
python3 scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl --merge --toc \
  --docs-preset mintlify \
  --merge-output output/merged.md \
  --split-output output/pages/ \
  --download-images

# 手动配置导航剥离
python3 scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl \
  --merge --toc \
  --strip-nav \
  --strip-page-toc \
  --anchor-list-threshold 15 \
  --merge-output docs.md

# 自动检测框架
python3 scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl \
  --merge --toc \
  --auto-detect \
  --merge-output docs.md

# 查看可用预设
python3 scripts/grab_web_to_md.py --list-presets
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
# 单页模式默认下载图片，无需 --download-images
python3 scripts/grab_web_to_md.py \
  "https://cloud.tencent.com/developer/article/2624003" \
  --auto-title

# 火山引擎文档 — Modern.js SSR 自动提取 MDContent
python3 scripts/grab_web_to_md.py \
  "https://www.volcengine.com/docs/6396/2189942" \
  --auto-title --best-effort-images

# 禁用 SSR 提取，回退到普通 HTML 解析
python3 scripts/grab_web_to_md.py URL --no-ssr
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

### 场景 9：Notion 公开页面导出

```bash
# Notion 公开页面 — 自动检测 notion.so 和 *.notion.site 域名
python3 scripts/grab_web_to_md.py \
  "https://www.notion.so/Kiro-29cbd3b8020080d5a1e5f7cd300576dd" \
  --auto-title

# *.notion.site 域名同样支持
python3 scripts/grab_web_to_md.py \
  "https://team.notion.site/Guide-abcdef0123456789abcdef0123456789" \
  --auto-title

# 禁用 Notion 自动检测
python3 scripts/grab_web_to_md.py \
  "https://www.notion.so/Page-ID" --no-notion
```

**工作原理**：

1. 检测到 `notion.so` 或 `*.notion.site` 域名后，自动切换到 Notion API 管线
2. 通过 `loadPageChunk` 获取初始 Block 数据（最多 300 个）
3. 递归调用 `syncRecordValues` 获取所有子 Block
4. 将 Block 树转换为 HTML（支持 15+ 种 Block 类型）
5. 图片使用 Notion 代理 URL，由标准图片下载管线处理

**支持的 Block 类型**：

| Block 类型 | 转换结果 |
|-----------|---------|
| text, header, sub_header, sub_sub_header | 段落/标题 |
| bulleted_list, numbered_list | 列表（自动合并连续项） |
| code | 代码块（含语言标识） |
| quote, callout | 引用/提示框 |
| toggle | 折叠块 (details/summary) |
| image | 图片（通过 Notion 代理 URL 下载） |
| to_do | 待办事项 |
| bookmark, embed, video | 链接/嵌入 |
| divider | 分割线 |
| column_list, column | 多列布局 |

> **注意**：仅适用于公开页面。私有页面需要 Cookie 或 Integration Token（暂不支持）。
>
> **错误处理**：Notion API 提取失败时**不会**静默回退到普通 HTTP（因为 Notion 空壳 HTML 无内容），而是直接报错。

### 场景 10：华为开发者文档导出

```bash
# 单篇文档 — 自动检测 developer.huawei.com/consumer/{cn|en}/doc/ URL
python3 scripts/grab_web_to_md.py \
  "https://developer.huawei.com/consumer/cn/doc/design-guides-V1/multi-devices_voice_experience-0000001111151802-V1" \
  --auto-title

# 文档目录页：crawl 全部子页并合并为单文件
python3 scripts/grab_web_to_md.py \
  "https://developer.huawei.com/consumer/cn/doc/design-guides-V1/multi-devices_voice_experience-0000001111151802-V1" \
  --crawl --crawl-pattern 'design-guides-V1' \
  --merge --merge-output huawei_docs.md \
  --download-images --validate

# 禁用华为 API 自动检测
python3 scripts/grab_web_to_md.py URL --no-huawei-api
```

**工作原理**：

1. 检测到 `developer.huawei.com/consumer/{lang}/doc/{catalog}/{fileName}` URL 后，自动切换到华为文档 API 管线（Angular SPA，普通 HTTP 只能拿到 ~1.7KB JS 空壳，无 SSR 数据，`--browser-fetch` 也可能超时）
2. 调用站点自身的 `documentPortal/getDocumentById` 接口：POST body 为 `{"objectId": fileName, "language": "cn"|"en"}`（fileName 即 URL 最后一段，自动剥离 query/fragment 与 `.md` 后缀——官方 llms.txt/MCP 索引使用 `xxx.md` 链接形式，objectId 不含后缀），无需 Cookie
3. 返回 `value.title`（标题）与 `value.content.content`（正文 HTML），进入标准 HTML→Markdown 管线
4. 单页 / 批量 / 爬取三种模式均支持：正文中的子页链接是同站绝对链接，目录页配合 `--crawl --merge` 可导出全部子页
5. 图片为绝对 URL（communityfile CDN），由标准图片下载管线处理
6. `--max-html-bytes` 对本 API 路径同样生效：响应以流式读取，先按 `Content-Length` 预判、再在分块累计中校验，超限立即中止（不重试），避免大响应完整载入内存
7. `--auto-title` 命名优先使用接口返回的 `value.title`（其次才看正文 HTML 的 `<h1>`/`<title>`），因此正文 HTML 缺少标题标签时也不会退化为 `Untitled`

**语言支持**：URL 中的 `/cn/`、`/en/` 段决定接口 `language` 参数（合法值仅 cn/en）；文档无对应语言版本时接口返回 404。

> **错误处理**：华为 API 提取失败时**不会**静默回退到普通 HTTP（因为 Angular SPA 空壳无内容），而是直接报错并给出兜底建议——首选「浏览器另存页面后以 `--local-html` 导入」（最可靠），其次 `--browser-fetch`（该站 SPA 渲染较慢，实测 15s/60s 均可能超时）。预先携带 `--browser-fetch` 时会跳过 API 适配器，直接用浏览器渲染页面（标题也从渲染后的页面提取），此时报错文案里的 `--browser-fetch` 建议才真正可达。

### 场景 11：数据安全与隐私

```bash
# 默认行为：URL 脱敏开启，分享给他人时不会泄露 token/签名
python3 scripts/grab_web_to_md.py "https://mp.weixin.qq.com/s/xxx?token=secret&..."

# 调试用途：保留完整 URL
python3 scripts/grab_web_to_md.py URL --no-redact-url

# 不生成映射文件（减少敏感信息输出；并会清理已存在的旧 *.assets.json，避免残留）
python3 scripts/grab_web_to_md.py URL --no-map-json

# 处理大图站点：调整单图上限为 50MB
python3 scripts/grab_web_to_md.py URL --max-image-bytes 52428800

# 不限制图片大小（谨慎使用）
python3 scripts/grab_web_to_md.py URL --max-image-bytes 0
```

**安全特性（默认生效）**：
- URL 脱敏：输出文件中的 URL 只保留 `scheme://host/path`
- 跨域凭据隔离：包括 30x 重定向到 CDN 的场景；跨域请求不携带原站 Cookie/Authorization，同域则可携带
- 跨域 Referer 脱敏：跨域图片请求的 Referer 自动移除 query/fragment（防止 token 泄露给第三方 CDN），同域保留完整 URL
- 网络配置继承：干净 session 会继承代理/证书/adapter 配置，避免企业网络环境跨域图片下载失败
- 流式下载：图片写入临时文件而非内存，防止 OOM
- HTML 净化：保留 HTML 时自动移除 `onclick`/`onerror` 等事件属性

---

## 实战案例

### 案例 1：微信公众号文章

```bash
python3 scripts/grab_web_to_md.py \
  "https://mp.weixin.qq.com/s/xxx" \
  --out output/wechat.md --validate --overwrite
```

**输出**：`wechat.md` + `wechat.assets/`（图片）

### 案例 2：技术博客（带代码块）

```bash
python3 scripts/grab_web_to_md.py \
  "https://claude.com/blog/xxx" \
  --out output/blog.md --keep-html \
  --tags "ai,agents" --validate --overwrite
```

**输出**：完整保留代码块、YAML Frontmatter 含标签

### 案例 3：Wiki 批量导出

```bash
python3 scripts/grab_web_to_md.py \
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

### 输出路径规则（当前行为）

```bash
# 输入：--out article.md（显式指定输出文件）
# 输出（保持原路径，不自动包目录）：
./
├── article.md
├── article.assets/
└── article.md.assets.json

# 输入：--out docs/article.md（用户指定目录，保持不变）
docs/
├── article.md
├── article.assets/
└── article.md.assets.json

# 输入：--auto-title（标题为“学习笔记”）
学习笔记/
├── 学习笔记.md
├── 学习笔记.assets/
└── 学习笔记.md.assets.json
```

**单页模式**：
```
<auto-name>/
├── <auto-name>.md
├── <auto-name>.assets/
│   ├── 01-hero.png
│   └── 02-diagram.jpg
└── <auto-name>.md.assets.json
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
- **路径**：自动截断避免 Windows 260 字符限制
- **安全**：
  - URL 脱敏：`urllib.parse` 解析后移除 query/fragment
  - 跨域凭据隔离：对比 URL 主机名（含重定向链），跨域请求使用干净 session
  - 跨域 Referer 脱敏：跨域图片请求自动移除 Referer 中的 query/fragment，同域保留完整 URL
  - 网络配置继承：干净 session 继承 `proxies/verify/cert/adapters`，避免网络环境差异导致下载失败
  - 流式下载：`iter_content(chunk_size=65536)` + 临时文件
  - HTML 净化：正则过滤 `on\w+=` 属性、`javascript:/vbscript:/file:` 协议

### 模块化架构（v2.0.0+）

项目从单文件重构为模块化包，核心功能按职责拆分：

```
scripts/
├── grab_web_to_md.py       # CLI 入口（~1730 行）：参数解析 + 流程调度
└── webpage_to_md/          # 核心功能包
    ├── __init__.py          # 包入口，导出数据模型
    ├── models.py            # 数据模型（~76 行）
    ├── security.py          # URL 脱敏 / JS 检测 / 校验（~270 行）
    ├── http_client.py       # HTTP 会话 + HTML 抓取 + 浏览器 headless 获取（~490 行）
    ├── ssr_extract.py       # SSR 数据提取：Next.js/Modern.js（~1090 行）
    ├── notion.py            # Notion 公开页面 API 提取（~590 行）
    ├── huawei.py            # 华为开发者文档 API 提取（~150 行）
    ├── images.py            # 图片下载与路径替换（~535 行）
    ├── extractors.py        # 正文提取 + 框架预设 + 导航剥离 + 微信异步提取（~1325 行）
    ├── markdown_conv.py     # HTML→Markdown + 噪音清理（~1160 行）
    └── output.py            # 合并/分文件/索引/frontmatter（~480 行）
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
```

**设计原则**：
- CLI 入口仅负责参数解析和流程编排，不包含业务逻辑
- 各模块职责单一，可独立测试
- 仅依赖 `requests`（必需）和标准库，保持轻量

---

## 更新日志

### v0.4.3 (2026-10-08)
- ✅ **2026-10-08 Cookie 边界补修（BUG-067～069）**：记录服务器 Cookie 的真实来源主机，兼容 localhost、内网单标签主机和 IPv6，并防止 `.local` 内部表示混淆不同主机；自动 Cookie 头先按 Cookie 对象筛选再生成，合法共享 Cookie 不再因同名同值被误删；拒绝非法导入名值及额外列，保留空值、等号和合法引号值。显式 Cookie 头继续保留，重定向自动头重新标记来源。新增 13 项测试，295 项全绿；修复前工作区快照有 21 个断言失败、0 个运行错误，见 CHK-015 与审查报告最新章节。
- ✅ **GitHub issue #1–#4 补修（BUG-059/060 P1）**：HTML5 省略结束标签的标签栈改为作用域限定的隐式闭合——新的 `<tr>`/`<td>`/`<li>` 等 start tag 自栈顶向下搜索最近目标元素并连同其上未闭合内容一起弹出，遇屏障元素（嵌套的 `table`/`ul`/`ol`/`menu`/`tr` 等）立即放弃，内层表格/列表不会误闭合外层元素；父元素的结束标签（如 `<ul><li class="drop">…</ul>`、`<menu><li>…</menu>` 的父结束标签、`</div>` 闭合省略 `</p>` 的目标）现在会隐式闭合被剥离/提取目标，剥离不再吞掉后续正文、提取不再超采；提取器记录目标外祖先链，无对应真实祖先的游离父结束标签（如 `</ol>`）不会提前截断采集
- ✅ **GitHub issue #4 补修（BUG-061 P1）**：cookies.txt 第二列 include-subdomains 标志生效——FALSE 的 host-only Cookie 只发送给精确域名（新增 `_HostOnlyCookieSession` 在请求准备、重定向与克隆路径强制精确匹配），不再泄漏给子域名；同时识别 RFC 6265 标准 host-only 属性，服务器 Set-Cookie 未带 Domain 更新/新增的 Cookie 一并仅限本域，显式 `Domain=` 的 Cookie 仍子域共享；TRUE/点前缀域名归一化为子域共享；空 domain 行直接拒绝；用户显式设置的 Cookie 请求头（`--header`）不受过滤影响
- ✅ **GitHub issue #3 补修（BUG-062 P1 / BUG-063 P2）**：HTML 代码块按正文行首反引号串最长长度动态选取更长的外层围栏（正文含 ``` 时生成 ````），代码内容不再被后处理误删；`rewrite_internal_links` 围栏识别统一改用 `_FenceTracker`（长度与关闭行尾规则），长围栏内的短反引号行之后链接不再被改写，围栏外文本块整体匹配使跨行链接（`[first\nsecond](url)`）也能正常改写
- ✅ **单元测试 248 → 295 项**：新增省略结束标签（含嵌套表格、menu、游离标签反例）、cookies host-only（prepare_request/deepcopy 克隆/批量 worker 克隆/302 重定向/Set-Cookie 刷新/显式头混用/localhost/IPv6/同名同值/异常导入值）、围栏与跨行链接等回归用例，并加强 2 处断言过弱的旧测试为整串比较；历轮验证记录见 `docs/issue-fix-review-20261006.md`

### v0.4.2 (2026-09-30)
- ✅ **华为开发者文档适配**：新增 `huawei.py` 模块（~150 行），自动检测 `developer.huawei.com/consumer/{cn|en}/doc/` URL，通过站点自身 `documentPortal/getDocumentById` API 直接获取正文 HTML 与标题（该站为 Angular SPA，普通 HTTP 只有 ~1.7KB JS 空壳，无 SSR 数据）。单页 / 批量 / `--crawl --merge` 三种模式全覆盖，支持中英文档，无需 Cookie；支持官方 llms.txt/MCP 索引的 `xxx.md` 链接形式（objectId 自动剥离 `.md` 后缀，否则接口返回 code=92531031）；API 失败时直接报错并给出兜底建议，不静默回退空壳
- ✅ **新增 `--no-huawei-api` 参数**：禁用华为开发者文档自动检测与 API 提取
- ✅ **专项审查修复（BUG-055 P1 / BUG-056 P2）**：`.md` 后缀剥离修复后，原本 HTTP 直连 404 的 `.md` URL 也能经 API 正常导出（实测 12/12 图片本地化）；`--browser-fetch` 显式指定时跳过 API 适配器，使报错文案中的浏览器兜底建议真正可达
- ✅ **专项审查修复（BUG-057 P2 / BUG-058 P2）**：`--auto-title` 改用接口返回的标题命名（正文 HTML 无 `<h1>`/`<title>` 时不再退化为 `Untitled/Untitled.md`，避免连续导出文件名冲突与 `--overwrite` 误覆盖）；华为 API 路径接入 `--max-html-bytes` 并改为流式读取，超限立即中止（修复前 10 字节上限对 579 字节正文完全失效）
- ✅ **回归测试集扩充**：新增「华为开发者文档」场景（目录页 crawl+merge 全部 5 子页 + 内容子页单页导出 + 官方 llms.txt 的 `.md` 链接形式），执行器支持对应命令构造与 API 适配日志校验
- ✅ **单元测试 199 → 231 项**：新增华为 URL 识别 / API 请求构造与错误处理 / 批量模式集成 / `.md` 后缀剥离 / `--browser-fetch` 跳过适配器（批量·单页·auto-title 三路径）/ 大小上限（超限·预判·0 不限制·上限传递）/ API 标题命名等用例

### v0.4.1 (2026-08-07)
- ✅ **全量代码审查与集中修复**：修复 9 个 P1（微信噪音误删正文、合并标题降级破坏代码块、void 元素深度泄漏、图片链接回退裸链接、Editor.js 崩溃、Quill 行级属性丢失、Notion 表格静默丢失、JS 反爬误判、browser-fetch noscript 误拦截）与 41 个 P2 边界问题（BUG-001~051，BUG-010 能力缺口遗留），详见 task-list.md
- ✅ 测试基线 146 → 199 项全绿

### v0.4.0 (2026-05-20)
- ✅ **发布版本对齐**：README、Skill 说明和完整手册统一标注当前版本为 `0.4.0`
- ✅ **PDF 职责拆分**：移除内置 PDF 导出入口和维护代码，本 Skill 只负责生成 Markdown 与本地 assets；需要 PDF 时，先生成 Markdown，再交给 `pdf` skill 或专门的文档/PDF 工具转换
- ✅ **回归测试行为收敛**：测试集缺失时直接报告失败原因，不再依赖 fallback 示例文件
- ✅ **Skill 元数据规范化**：frontmatter 保持 `name` / `description` 两项，description 改为 `Use when...` 触发条件描述

> 注：`0.4.3` 为当前 Skill 发布版本；下方 `v2.x` 为早期内部功能迭代记录。

### v2.2.0 (2026-04-10)
- ✨ **`--browser-fetch` 浏览器获取模式**：
  - 新增 `--browser-fetch` 参数，使用系统 Chrome/Edge headless 获取页面
  - 两阶段策略：Phase 1 启动浏览器等待 JS challenge 通过并持久化 cookie → Phase 2 用同一 profile `--dump-dom` 获取真实内容
  - 自动检测系统安装的 Chromium 浏览器（macOS Applications / Windows Program Files / PATH）
  - 包含 Cloudflare challenge 检测（支持中英文关键词：`请稍候`/`Just a moment`/`cf-browser-verification` 等）
  - 反检测优化：`--disable-blink-features=AutomationControlled`、标准窗口尺寸
  - 适用于需要 JS 执行和基本 Cloudflare JS Challenge 的站点
  - 对 Cloudflare Turnstile 等高级人机验证的站点，headless 仍会被检测，自动提示使用 `--local-html`
  - 无需额外 pip 依赖，使用系统浏览器检测逻辑
  - 单页模式和批量/爬取模式均支持
- 🐛 **JS challenge 提示优化**：
  - `JSChallengeResult.get_suggestions()` 现在优先推荐 `--browser-fetch`，其次是 `--local-html`
  - 回归测试套件更新，识别 `--browser-fetch` 提示为有效的预期失败处理

### v2.1.3 (2026-03-25)
- 🐛 **修复单页 `--out` 路径被改写问题**：
  - 显式指定 `--out article.md` 时，输出保持为 `./article.md`，不再自动包 `article/article.md`
  - 自动包同名目录行为保留给自动命名场景（如 `--auto-title` 或默认 URL 命名）
- ✨ **新增 `--output` 别名**：
  - `--output` 与 `--out` 等价，兼容历史调用方式
  - 避免与 `--output-dir` 的参数缩写歧义

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
  - HTML 属性净化：过滤 `on*` 事件、`javascript:` 协议
- ✨ 新增参数：`--no-redact-url`、`--no-map-json`、`--max-image-bytes`

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
