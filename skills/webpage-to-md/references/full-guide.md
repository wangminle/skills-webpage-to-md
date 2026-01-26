# Web to Markdown Grabber 完整参考手册

抓取网页并转换为 Markdown 格式的 Python 工具。支持单页抓取、批量处理、从索引页爬取整个子目录，并可下载图片到本地。

## 功能特性

**内容提取**：智能正文抽取（article → main → body）、手动选择器（`--target-id`/`--target-class`）、SPA 检测

**Markdown 转换**：标题/段落/列表、表格（支持复杂表格保留 HTML）、代码块、引用块、链接/图片、数学公式

**图片处理**：支持 `src`/`data-src`/`srcset`/`<picture>`、自动检测格式（PNG/JPEG/GIF/WebP/SVG/AVIF）、过滤图标

**批量处理**：URL 文件读取、索引页爬取、并发下载、合并输出/独立文件

**特定站点**：微信公众号（自动检测）、Wiki 系统噪音清理

**其他**：YAML Frontmatter、反爬支持、PDF 导出、Windows 路径安全

---

## 安装

```bash
# Python 3.10+
pip install requests
```

---

## 参数完整说明

### 基础参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `url` | 目标网页 URL | - |
| `--out` | 输出文件名 | 根据 URL 生成 |
| `--assets-dir` | 图片目录 | `<out>.assets` |
| `--title` | 文档标题 | 从 `<title>` 提取 |
| `--overwrite` | 覆盖已存在文件 | `False` |
| `--validate` | 校验图片引用 | `False` |

### 网络请求参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--timeout` | 超时（秒） | `60` |
| `--retries` | 重试次数 | `3` |
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
| `--target-id` | 正文容器 id | - |
| `--target-class` | 正文容器 class | - |
| `--keep-html` | 复杂表格保留 HTML | `False` |
| `--spa-warn-len` | SPA 警告阈值 | `500` |
| `--clean-wiki-noise` | 清理 Wiki 噪音 | `False` |
| `--wechat` | 微信模式 | 自动 |

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

---

## 使用场景

### 场景 1：单页导出

```bash
# 基础用法
python scripts/grab_web_to_md.py https://example.com/article

# 指定输出和标签
python scripts/grab_web_to_md.py https://example.com/article \
  --out my-article.md --tags "ai,tutorial"

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
```

**自动处理**：提取 `rich_media_content`、标题从 `og:title` 获取、清理交互按钮

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

**单页模式**：
```
article.md
article.assets/
  ├── 01-hero.png
  └── 02-diagram.jpg
article.md.assets.json
```

**批量独立文件**：
```
output_dir/
  ├── INDEX.md
  ├── 文章1.md
  └── 文章2.md
```

**批量合并**：
```
merged.md  # 含目录
```

---

## 技术细节

- **HTML 解析**：标准库 `HTMLParser`（无 BeautifulSoup 依赖）
- **图片检测**：Content-Type + 二进制嗅探
- **噪音过滤**：跳过 script/style/svg/video/按钮
- **表格**：简单→Markdown，复杂→保留 HTML
- **PDF**：Edge/Chrome headless `--print-to-pdf`
- **路径**：自动截断避免 Windows 260 字符限制

---

## 更新日志

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
