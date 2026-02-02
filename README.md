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
- ✅ **导航剥离**：自动移除侧边栏/页内目录，支持 8 种文档框架预设
- ✅ **框架识别**：自动检测 Docusaurus/Mintlify/GitBook 等站点模板

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

# 支持的预设：mintlify, docusaurus, gitbook, vuepress, mkdocs, readthedocs, sphinx, generic
python skills/webpage-to-md/scripts/grab_web_to_md.py --list-presets
```

## 常用参数

| 参数 | 说明 |
|------|------|
| `--out` | 输出文件路径 |
| `--validate` | 校验图片完整性 |
| `--keep-html` | 复杂表格保留 HTML |
| `--tags` | YAML Frontmatter 标签 |
| `--target-id` / `--target-class` | 指定正文容器（支持逗号分隔多值） |
| `--crawl` | 启用爬取模式 |
| `--merge --toc` | 合并输出并生成目录 |
| `--download-images` | 下载图片到本地 |
| `--clean-wiki-noise` | 清理 Wiki 系统噪音 |
| `--rewrite-links` | 站内链接改写为锚点 |
| `--docs-preset` | 文档框架预设（mintlify/docusaurus/gitbook 等） |
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
skills-webpage-to-md-pdf/
├── README.md                           # 本文件
├── skills/
│   └── webpage-to-md/                  # Claude Skills 目录
│       ├── SKILL.md                    # Skills 核心文件
│       ├── scripts/
│       │   └── grab_web_to_md.py       # 主脚本
│       └── references/
│           └── full-guide.md           # 完整参考手册
└── output/                             # 示例输出（已 gitignore）
```

## 文档

- **Skills 入口**：[skills/webpage-to-md/SKILL.md](skills/webpage-to-md/SKILL.md) - Claude Skills 核心用法
- **完整手册**：[skills/webpage-to-md/references/full-guide.md](skills/webpage-to-md/references/full-guide.md) - 所有参数、场景、案例

## 依赖

- **必需**：`requests`（HTTP 请求）
- **可选**：`markdown`（PDF 导出时使用）

```bash
pip install requests
```

## 输出结构

```
article.md                # Markdown 文件
article.assets/           # 图片目录
article.md.assets.json    # URL→本地映射
```

## License

本脚本按原样提供，供个人和教育用途使用。
