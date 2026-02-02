---
name: webpage-to-md
description: "Web scraping and Markdown conversion toolkit for extracting web content with images. Use when Claude needs to: (1) Save web articles/blogs as Markdown files, (2) Export WeChat articles (mp.weixin.qq.com), (3) Batch crawl Wiki sites and merge into single document, (4) Download webpage images locally, (5) Convert HTML tables/code blocks to Markdown format."
---

# Web to Markdown Grabber

Extract web content and convert to clean Markdown with local images.

## Script Location

This skill includes a Python script at `scripts/grab_web_to_md.py`.

When using this skill, replace `SKILL_DIR` with the actual skill installation path:
- Claude Code: `~/.claude/skills/webpage-to-md/`
- Cursor: `~/.cursor/skills/webpage-to-md/` (if installed there)

## Quick Start

```bash
# Single page export
python SKILL_DIR/scripts/grab_web_to_md.py "https://example.com/article" --out output.md --validate

# WeChat article (auto-detected)
python SKILL_DIR/scripts/grab_web_to_md.py "https://mp.weixin.qq.com/s/xxx" --out article.md

# Wiki batch crawl + merge
python SKILL_DIR/scripts/grab_web_to_md.py "https://wiki.example.com/index" \
  --crawl --crawl-pattern 'page=' \
  --merge --toc --merge-output wiki.md
```

## Core Parameters

| Parameter | Purpose | Example |
|-----------|---------|---------|
| `--out` | Output file path | `--out docs/article.md` |
| `--validate` | Verify image integrity | `--validate` |
| `--keep-html` | Preserve complex tables | `--keep-html` |
| `--tags` | Add YAML frontmatter tags | `--tags "ai,tutorial"` |

## Three Main Use Cases

### 1. Single Page Export (Blog/News)

```bash
python SKILL_DIR/scripts/grab_web_to_md.py "URL" \
  --out output.md \
  --keep-html \
  --tags "topic1,topic2" \
  --validate
```

**Auto behavior**: Downloads images to `output.assets/`, generates YAML frontmatter.

### 2. WeChat Article Export

```bash
python SKILL_DIR/scripts/grab_web_to_md.py "https://mp.weixin.qq.com/s/xxx" \
  --out article.md
```

**Auto behavior**: Detects WeChat URL → extracts `rich_media_content` → cleans interaction buttons.

### 3. Wiki Batch Crawl + Merge

```bash
python SKILL_DIR/scripts/grab_web_to_md.py "https://wiki.example.com/index" \
  --crawl \
  --crawl-pattern 'page=wiki' \
  --merge \
  --toc \
  --merge-output wiki_guide.md \
  --target-id body \
  --clean-wiki-noise \
  --rewrite-links \
  --download-images
```

**Parameters explained**:
- `--crawl`: Extract links from index page
- `--crawl-pattern`: Regex to filter content pages
- `--merge --toc`: Combine into single file with TOC
- `--target-id body`: Extract only main content area
- `--clean-wiki-noise`: Remove edit buttons, navigation links
- `--rewrite-links`: Convert external URLs to internal anchors
- `--download-images`: Save images locally

## Content Extraction Parameters

| Parameter | Purpose |
|-----------|---------|
| `--target-id ID` | Extract element by id (supports comma-separated values for priority fallback) |
| `--target-class CLASS` | Extract element by class (supports comma-separated values) |
| `--clean-wiki-noise` | Remove Wiki system noise (PukiWiki/MediaWiki) |
| `--wechat` | Force WeChat article mode |

## Navigation Stripping Parameters (Docs/Wiki Sites)

| Parameter | Purpose |
|-----------|---------|
| `--strip-nav` | Remove navigation elements (nav, aside, sidebar) |
| `--strip-page-toc` | Remove page-level TOC (.toc, .on-this-page) |
| `--exclude-selectors STR` | Custom selectors to remove (comma-separated, simplified CSS) |
| `--anchor-list-threshold N` | Remove link lists exceeding N lines (default 0=off, preset mode uses 10) |
| `--docs-preset NAME` | Use framework preset (mintlify/docusaurus/gitbook/vuepress/mkdocs/readthedocs/sphinx/generic) |
| `--auto-detect` | Auto-detect docs framework and apply preset |
| `--list-presets` | Show all available presets |

### 4. Docs Site Export (NEW)

```bash
# Using preset for Mintlify docs (e.g., OpenClaw)
python SKILL_DIR/scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl \
  --merge --toc \
  --docs-preset mintlify \
  --merge-output docs_guide.md \
  --download-images

# Manual stripping without preset
python SKILL_DIR/scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl \
  --merge --toc \
  --strip-nav \
  --strip-page-toc \
  --anchor-list-threshold 15 \
  --merge-output docs_guide.md
```

**Preset benefits**:
- Auto-configures target containers (e.g., `article`, `main`)
- Auto-excludes navigation selectors
- Auto-enables anchor list stripping (threshold=10)
- Reduces output size by 50%+ for docs sites

## Batch Processing Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `--urls-file` | - | Read URLs from file |
| `--max-workers` | 3 | Concurrent threads |
| `--delay` | 1.0 | Request interval (seconds) |
| `--skip-errors` | False | Continue on failures |
| `--download-images` | False | Download images locally |

## Security Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `--redact-url` | True | Remove query/fragment from URLs in output (default ON) |
| `--no-redact-url` | - | Keep full URLs including query params |
| `--no-map-json` | False | Skip generating *.assets.json mapping file (and remove existing one) |
| `--max-image-bytes` | 25MB | Max size per image (0=unlimited) |
| `--pdf-allow-file-access` | False | Allow file:// access when generating PDF |

**Security features (always active)**:
- Cross-origin image downloads use clean session (no Cookie/Auth leak), including redirect chains
- Redirects back to same host switch back to credentialed session when needed
- Clean session inherits proxy/cert/adapters from the base session (still no sensitive headers)
- HTML attributes sanitized (removes `on*` events, `javascript:` URLs)
- Streaming download prevents OOM on large images

## Anti-Scraping Support

```bash
# With cookies
python SKILL_DIR/scripts/grab_web_to_md.py "URL" --cookie "session=xxx"

# With custom headers
python SKILL_DIR/scripts/grab_web_to_md.py "URL" --header "Authorization: Bearer xxx"

# Change User-Agent
python SKILL_DIR/scripts/grab_web_to_md.py "URL" --ua-preset firefox-win
```

## JS Challenge Detection (Cloudflare, etc.)

Some websites use JavaScript-based anti-bot protection (Cloudflare, Akamai, etc.). The script **automatically detects** these challenges and provides clear guidance.

> **Note**: JS Challenge detection only works in **single-page mode**. In batch mode (`--urls-file` or `--crawl`), individual pages are processed without JS challenge detection to avoid interrupting the batch process.

### What happens when JS protection is detected

```
⚠️  检测到 JavaScript 反爬保护（置信度：高）

检测到的信号：
  • 标题包含 'Challenge'
  • 页面提示 JavaScript 必需/被禁用

说明：
  该网站使用了 JavaScript 反爬机制来验证访问者。
  纯 HTTP 请求无法通过此验证，需要浏览器环境执行 JavaScript。
  这超出了本工具（仅依赖 requests）的能力范围。

建议操作：
  1. 在浏览器中打开该 URL，等待页面完全加载
  2. 右键点击页面 → 「另存为」→ 保存为 .html 文件
  3. 使用 --local-html 参数处理本地文件
```

Exit code: **4** (JS_CHALLENGE)

### Workaround: Process locally saved HTML

```bash
# Step 1: Manually save the webpage in browser (File → Save As → .html)

# Step 2: Process the local HTML file
python SKILL_DIR/scripts/grab_web_to_md.py \
  --local-html saved_page.html \
  --base-url "https://original-url.com/page" \
  --out output.md
```

| Parameter | Purpose |
|-----------|---------|
| `--local-html FILE` | Read from local HTML file (skip network request) |
| `--base-url URL` | Base URL for downloading images (used with --local-html) |
| `--force` | Force continue even when JS challenge detected (content may be empty) |

### Known JS-protected sites

| Site | Protection | Workaround |
|------|------------|------------|
| PyPI | Cloudflare | Use `--local-html` |
| Some GitHub pages | Cloudflare | Use `--local-html` |
| News sites | Various | Try `--ua-preset` first, then `--local-html` |

### Design principle

This tool **intentionally** does not include browser automation (Playwright, Selenium) to:
- Minimize dependencies (only `requests` required)
- Avoid complex setup and maintenance
- Keep the tool lightweight and portable

When JS protection is encountered, the tool honestly reports the limitation and provides a manual workaround.

## Output Structure

```
output.md                 # Markdown file
output.assets/            # Images directory
  ├── 01-hero.png
  └── 02-diagram.jpg
output.md.assets.json     # URL→local mapping
```

## Common Site Configurations

| Site Type | Recommended Parameters |
|-----------|----------------------|
| PukiWiki | `--target-id body --clean-wiki-noise` |
| MediaWiki | `--target-id content --clean-wiki-noise` |
| WordPress | `--target-class entry-content` |
| WeChat | Auto-detected, or `--wechat` |
| Tech Blog | `--keep-html --tags` |
| **Mintlify Docs** | `--docs-preset mintlify` |
| **Docusaurus** | `--docs-preset docusaurus` |
| **GitBook** | `--docs-preset gitbook` |
| **VuePress** | `--docs-preset vuepress` |
| **MkDocs** | `--docs-preset mkdocs` |
| **ReadTheDocs** | `--docs-preset readthedocs` |
| **Sphinx** | `--docs-preset sphinx` |
| **Generic Docs** | `--docs-preset generic` or `--strip-nav --strip-page-toc` |

## Dependencies

- **Required**: `requests` (HTTP requests)
- **Optional**: `markdown` (for PDF export with `--with-pdf`)

Install: `pip install requests`

## References

For complete documentation, see [references/full-guide.md](references/full-guide.md):
- All parameter explanations with defaults
- 9 usage scenarios with examples
- 3 detailed real-world cases
- Output structure diagrams
- Technical implementation details
- Changelog history
