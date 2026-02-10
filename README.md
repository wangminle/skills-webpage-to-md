# Web to Markdown Grabber

一个功能强大的 Python 工具，用于抓取网页并转换为干净的 Markdown 格式。

## 功能特性

- ✅ **智能正文抽取**：自动识别 article/main/body，过滤导航噪音
- ✅ **Markdown 转换**：标题、表格、代码块、列表、链接、图片、数学公式
- ✅ **图片本地化**：自动下载并检测格式（PNG/JPEG/GIF/WebP/SVG）
- ✅ **批量处理**：URL 文件读取、索引页爬取、合并输出
- ✅ **特定站点**：微信公众号（自动检测）、Wiki 噪音清理
- ✅ **反爬支持**：Cookie/Header/UA 定制
- ✅ **YAML Frontmatter**：兼容 Obsidian/Hugo/Jekyll
- ✅ **数据安全**：URL 脱敏、跨域凭据隔离、流式下载防 OOM
- ✅ **导航剥离**：自动移除侧边栏/页内目录，支持 10 种文档框架预设
- ✅ **框架识别**：自动检测 Docusaurus/Mintlify/GitBook 等站点模板
- ✅ **双版本输出**：同时生成合并版和分文件版，共享 assets 目录
- ✅ **智能目录管理**：自动创建同名上级目录，保持输出整洁

## 安装到 Claude Code

将 `skills/webpage-to-md/` 文件夹复制到 `~/.claude/skills/` 目录即可：

```bash
cp -r skills/webpage-to-md ~/.claude/skills/
```

安装后，在 Claude Code 中使用以下方式触发：

| 触发方式 | 示例 |
|---------|------|
| 斜杠命令 | `/webpage-to-md 帮我保存这个网页` |
| 自然语言 | "帮我把这个微信文章保存为 Markdown" |
| 直接描述 | "导出这个 Wiki 站点的所有页面" |

Claude Code 会自动识别并调用此 Skill 完成网页抓取任务。

## 快速开始

```bash
# 安装依赖
pip install requests

# 单页导出
python skills/webpage-to-md/scripts/grab_web_to_md.py "https://example.com/article" --out article.md

# 自动按页面标题命名（例如：如何学Python/如何学Python.md）
python skills/webpage-to-md/scripts/grab_web_to_md.py "https://example.com/article" --auto-title

# 离线微信 HTML 也支持自动标题（无需 --base-url 即可提取微信标题）
python skills/webpage-to-md/scripts/grab_web_to_md.py --local-html wechat.html --auto-title

# 微信公众号（自动检测）
python skills/webpage-to-md/scripts/grab_web_to_md.py "https://mp.weixin.qq.com/s/xxx"

# Wiki 批量爬取
python skills/webpage-to-md/scripts/grab_web_to_md.py "https://wiki.example.com/index" \
  --crawl --crawl-pattern 'page=' \
  --merge --toc --merge-output wiki.md
```

## 四种典型使用场景

| 场景 | 说明 |
|------|------|
| **微信公众号** | 自动检测 mp.weixin.qq.com，清理交互按钮噪音 |
| **技术博客** | `--keep-html --tags` 保留代码块和复杂表格 |
| **Wiki 批量** | `--crawl --merge --clean-wiki-noise` 爬取合并 |
| **Docs 站点** | `--docs-preset mintlify` 一键导出，自动剥离导航 |

### Docs 站点导出示例

```bash
# 使用预设导出 Mintlify 文档站点（如 OpenClaw）
python skills/webpage-to-md/scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl \
  --merge --toc \
  --docs-preset mintlify \
  --merge-output docs-export.md

# 双版本输出：同时生成合并版和分文件版
python skills/webpage-to-md/scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl --merge --toc \
  --docs-preset mintlify \
  --merge-output output/merged.md \
  --split-output output/pages/ \
  --download-images

# 支持的预设：mintlify, docusaurus, gitbook, vuepress, mkdocs, readthedocs, sphinx, notion, confluence, generic
python skills/webpage-to-md/scripts/grab_web_to_md.py --list-presets
```

## 常用参数

| 参数 | 说明 |
|------|------|
| `--out` | 输出文件路径 |
| `--auto-title` | 自动按页面标题生成文件名（仅单页模式；未指定 `--out` 时生效；批量/爬取模式无效） |
| `--validate` | 校验图片完整性 |
| `--max-html-bytes` | 单页 HTML 最大字节数（默认 10MB；0 表示不限制） |
| `--keep-html` | 复杂表格保留 HTML |
| `--tags` | YAML Frontmatter 标签 |
| `--target-id` / `--target-class` | 指定正文容器（支持逗号分隔多值） |
| `--crawl` | 启用爬取模式 |
| `--merge --toc` | 合并输出并生成目录 |
| `--download-images` | 下载图片到本地 |
| `--clean-wiki-noise` | 清理 Wiki 系统噪音 |
| `--rewrite-links` | 站内链接改写为锚点 |
| `--docs-preset` | 文档框架预设（mintlify/docusaurus/gitbook 等） |
| `--split-output DIR` | 同时输出分文件版本（与 --merge 配合使用） |
| `--strip-nav` | 移除导航元素（侧边栏等） |
| `--strip-page-toc` | 移除页内目录 |

## 数据安全

本工具在设计时充分考虑了数据安全和隐私保护：

### 🔒 默认安全策略

| 安全措施 | 说明 | 相关参数 |
|---------|------|---------|
| **URL 脱敏** | 输出文件中默认移除 URL 的 query/fragment 参数，避免泄露 token/签名等敏感信息 | `--no-redact-url` 可关闭 |
| **跨域凭据隔离** | 下载图片时，仅同域名请求携带 Cookie/Authorization；跨域（含 30x 重定向到 CDN）使用"干净 session" | 自动生效 |
| **流式下载** | 图片采用流式写入，避免大图导致内存溢出（OOM） | 自动生效 |
| **单图大小限制** | 默认限制单张图片 25MB，防止恶意/超大响应 | `--max-image-bytes` |
| **映射文件可选** | 可选择不生成 `*.assets.json` 映射文件（并清理已存在的旧映射文件） | `--no-map-json` |
| **PDF 本地访问** | 生成 PDF 时默认关闭 `--allow-file-access-from-files` | `--pdf-allow-file-access` 可开启 |
| **HTML 属性净化** | 保留 HTML 时自动过滤 `on*` 事件属性和 `javascript:` 协议 | 自动生效 |

### 安全相关参数

```bash
# 保留完整 URL（含 query 参数）
python grab_web_to_md.py URL --no-redact-url

# 不生成图片 URL 映射文件
python grab_web_to_md.py URL --no-map-json

# 调整单图大小限制（0 表示不限制）
python grab_web_to_md.py URL --max-image-bytes 52428800  # 50MB

# 生成 PDF 时允许访问本地文件（有安全风险）
python grab_web_to_md.py URL --with-pdf --pdf-allow-file-access
```

### 典型场景

- **分享导出文件给他人**：默认行为即可，URL 中的 token/签名会被自动移除
- **需要完整 URL 用于调试**：添加 `--no-redact-url`
- **处理付费内容/需登录页面**：Cookie 仅用于页面抓取，不会泄露到第三方图片域名
- **避免旧映射残留**：启用 `--no-map-json` 会自动删除已存在的 `<out>.assets.json`

## 项目结构

```
skills-webpage-to-md/
├── README.md                           # 本文件
├── skills/
│   └── webpage-to-md/                  # Claude Skills 目录
│       ├── SKILL.md                    # Skills 核心文件
│       ├── scripts/
│       │   ├── grab_web_to_md.py       # CLI 入口（参数解析 + 流程调度）
│       │   └── webpage_to_md/          # 核心功能包（8 个子模块）
│       │       ├── __init__.py         # 包入口，导出数据模型
│       │       ├── models.py           # 数据模型（BatchConfig / BatchPageResult 等）
│       │       ├── security.py         # URL 脱敏 / JS challenge 检测 / 校验
│       │       ├── http_client.py      # HTTP 会话创建与 HTML 抓取
│       │       ├── images.py           # 图片下载、格式嗅探与路径替换
│       │       ├── extractors.py       # 正文 / 标题 / 链接提取 + docs 框架预设 + 导航剥离
│       │       ├── markdown_conv.py    # HTML→Markdown 转换 + 噪音清理 + 链接改写
│       │       ├── output.py           # 合并 / 分文件 / 索引 / frontmatter 输出
│       │       └── pdf_utils.py        # Markdown→HTML→PDF 渲染（Edge/Chrome headless）
│       └── references/
│           └── full-guide.md           # 完整参考手册
├── tests/
│   └── test_grab_web_to_md.py          # 单元测试
├── docs/                               # 设计文档（已 gitignore 部分内容）
└── output/                             # 示例输出（已 gitignore）
```

### 模块化架构

项目采用模块化设计，`grab_web_to_md.py` 仅负责 CLI 参数解析和流程调度，核心功能拆分为 `webpage_to_md` 包：

| 模块 | 行数 | 职责 |
|------|------|------|
| `models.py` | ~70 | 数据模型定义（BatchConfig、BatchPageResult、JSChallengeResult 等） |
| `security.py` | ~240 | URL 脱敏、JS 反爬检测、Markdown 校验 |
| `http_client.py` | ~200 | UA 预设、Session 创建、HTML 抓取（含重试/大小限制） |
| `images.py` | ~500 | 图片下载（流式/跨域隔离）、格式嗅探、路径替换 |
| `extractors.py` | ~1100 | 正文/标题/链接提取、10 种 Docs 框架预设、导航剥离 |
| `markdown_conv.py` | ~940 | HTML→Markdown 解析器、LaTeX 公式、表格、噪音清理 |
| `output.py` | ~450 | Frontmatter 生成、合并/分文件/索引输出、锚点管理 |
| `pdf_utils.py` | ~420 | Markdown→HTML 渲染、PDF 打印（Edge/Chrome headless） |

依赖关系：`models` ← `security` ← `markdown_conv` / `images` / `output`，无循环依赖。

## 文档

- **Skills 入口**：[skills/webpage-to-md/SKILL.md](skills/webpage-to-md/SKILL.md) - Claude Skills 核心用法
- **完整手册**：[skills/webpage-to-md/references/full-guide.md](skills/webpage-to-md/references/full-guide.md) - 所有参数、场景、案例

## 测试

```bash
# 运行全部测试
python -m pytest tests/ -v

# 快速验证导入
python -c "import sys; sys.path.insert(0, 'skills/webpage-to-md/scripts'); import grab_web_to_md; print('OK')"
```

## 依赖

- **必需**：`requests`（HTTP 请求）
- **可选**：`markdown`（PDF 导出时使用）
- **测试**：`pytest`（可选）

```bash
pip install requests
```

## 输出结构

**自动创建同名目录**：如果只指定文件名（不含目录），会自动创建同名目录：

```bash
# 输入：--out article.md
# 输出结构：
article/
├── article.md              # Markdown 文件
├── article.assets/         # 图片目录
└── article.md.assets.json  # URL→本地映射

# 输入：--out docs/article.md（用户指定目录，保持不变）
# 输出结构：
docs/
├── article.md
├── article.assets/
└── article.md.assets.json

# 输入：--auto-title（标题为“我的文章”）
# 输出结构：
我的文章/
├── 我的文章.md
├── 我的文章.assets/
└── 我的文章.md.assets.json
```

## License

本脚本按原样提供，供个人和教育用途使用。
