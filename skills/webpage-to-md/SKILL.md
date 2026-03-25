---
name: webpage-to-md
description: "Web scraping and Markdown conversion toolkit for extracting web content with images. Use when the agent needs to: (1) Save web articles/blogs as Markdown files, (2) Export WeChat articles (mp.weixin.qq.com), (3) Batch crawl Wiki/Docs sites and merge into single document, (4) Download webpage images locally, (5) Convert HTML tables/code blocks to Markdown format, (6) Export Notion public pages (notion.so / *.notion.site). Do NOT use for: generating Markdown from scratch (no URL involved), editing existing Markdown files, or browser automation tasks (Playwright/Selenium)."
---

# Web to Markdown Grabber

Extract web content and convert to clean Markdown with local images.

## Script Location

This skill uses a modular Python package:

```
scripts/
├── grab_web_to_md.py       # CLI entry point (argument parsing + orchestration)
└── webpage_to_md/          # Core package (10 submodules)
    ├── models.py           # Data models (BatchConfig, BatchPageResult, etc.)
    ├── security.py         # URL redaction / JS challenge detection / validation
    ├── http_client.py      # HTTP session creation and HTML fetching
    ├── ssr_extract.py      # SSR data extraction (Next.js/Modern.js → HTML/Markdown)
    ├── notion.py           # Notion public page API extraction (Block→HTML)
    ├── images.py           # Image download, format sniffing, path replacement
    ├── extractors.py       # Content/title/link extraction + docs presets + nav stripping
    ├── markdown_conv.py    # HTML→Markdown converter + noise cleanup + link rewriting
    ├── output.py           # Merged/split/index/frontmatter output generation
    └── pdf_utils.py        # Markdown→HTML→PDF rendering (Edge/Chrome headless)
```

When using this skill, replace `SKILL_DIR` with the actual skill installation path:
- Claude Code: `~/.claude/skills/webpage-to-md/`
- Cursor: `~/.cursor/skills/webpage-to-md/` (if installed there)

## Quick Start

```bash
# Single page export
python SKILL_DIR/scripts/grab_web_to_md.py "https://example.com/article" --out output.md --validate

# Auto-name from page title (e.g. "如何学Python" → 如何学Python/如何学Python.md)
python SKILL_DIR/scripts/grab_web_to_md.py "https://example.com/article" --auto-title

# Local WeChat HTML offline (title can still be extracted without --base-url)
python SKILL_DIR/scripts/grab_web_to_md.py --local-html wechat_saved.html --auto-title

# WeChat article (auto-detected)
python SKILL_DIR/scripts/grab_web_to_md.py "https://mp.weixin.qq.com/s/xxx" --out article.md

# Wiki batch crawl + merge
python SKILL_DIR/scripts/grab_web_to_md.py "https://wiki.example.com/index" \
  --crawl --crawl-pattern 'page=' \
  --merge --toc --merge-output wiki.md
```

### SSR Dynamic Site Export (Tencent Cloud, Volcengine, etc.)

```bash
# Tencent Cloud developer article (Next.js + ProseMirror) — auto-extracted
# Single-page mode downloads images by default, no need for --download-images
python SKILL_DIR/scripts/grab_web_to_md.py "https://cloud.tencent.com/developer/article/xxx" \
  --auto-title

# Volcengine docs (Modern.js + MDContent) — auto-extracted
python SKILL_DIR/scripts/grab_web_to_md.py "https://www.volcengine.com/docs/xxx" \
  --auto-title --best-effort-images

# Disable SSR extraction if needed
python SKILL_DIR/scripts/grab_web_to_md.py "URL" --no-ssr
```

**Auto behavior**: Detects `__NEXT_DATA__` (Next.js) or `window._ROUTER_DATA` (Modern.js) in HTML → extracts embedded article content → converts ProseMirror JSON to HTML or uses raw MDContent directly. Skips JS anti-scraping warnings when SSR data is available.

### Notion Public Page Export

```bash
# Notion public page — auto-detected via notion.so / *.notion.site domains
python SKILL_DIR/scripts/grab_web_to_md.py \
  "https://www.notion.so/Page-Title-29cbd3b8020080d5a1e5f7cd300576dd" \
  --auto-title

# *.notion.site domains also supported
python SKILL_DIR/scripts/grab_web_to_md.py \
  "https://team.notion.site/Guide-abcdef0123456789abcdef0123456789" \
  --auto-title

# Disable Notion API extraction
python SKILL_DIR/scripts/grab_web_to_md.py "https://www.notion.so/..." --no-notion
```

**Auto behavior**: Detects `notion.so` and `*.notion.site` URLs → fetches all blocks via Notion internal API (`loadPageChunk` + `syncRecordValues`) → converts block tree to HTML → runs standard HTML→Markdown pipeline. Supports 15+ block types including text, headings, lists, code, quotes, toggles, to-do items, images, bookmarks. Only works for **publicly shared** pages. If API extraction fails, the tool **reports an error** instead of silently falling back to HTTP (which would yield empty Notion shell HTML).

## Offline Smoke Test (No Network Required)

Verify the skill is installed correctly using a local HTML file:

```bash
# 1. Create a minimal test HTML
echo '<html><head><title>Smoke Test</title></head><body><h1>Hello</h1><p>Skill works.</p></body></html>' > /tmp/smoke.html

# 2. Convert local HTML → Markdown (no network needed)
python SKILL_DIR/scripts/grab_web_to_md.py --local-html /tmp/smoke.html --out /tmp/smoke.md

# 3. Verify output contains expected content
grep -q "# Hello" /tmp/smoke.md && grep -q "Skill works" /tmp/smoke.md && echo "✅ PASS" || echo "❌ FAIL"

# 4. Run unit tests (also offline)
python -m pytest SKILL_DIR/../../tests/ -q
```

Expected: exit code 0, output file contains `# Hello` and `Skill works.`.

## Core Parameters

| Parameter | Purpose | Example |
|-----------|---------|---------|
| `--out` / `--output` | Output file path (`--output` is an alias) | `--out docs/article.md` |
| `--auto-title` | Auto-name output file from page title (single-page only; ignored when `--out` is set or in batch/crawl mode) | `--auto-title` |
| `--validate` | Verify image integrity | `--validate` |
| `--max-html-bytes` | Max HTML bytes per page (0=unlimited) | `--max-html-bytes 0` |
| `--keep-html` | Preserve complex tables | `--keep-html` |
| `--tags` | Add YAML frontmatter tags | `--tags "ai,tutorial"` |

## Main Use Cases

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

**Auto behavior**: Detects WeChat URL or WeChat HTML markers → extracts content → cleans interaction buttons.

Supports two WeChat article formats:
- **Traditional articles**: Extracts `rich_media_content` HTML with images
- **New-format posts** (WeChat "图文笔记/小绿书" short posts, `item_show_type=10`): Content is embedded in JavaScript (`window.cgiDataNew`) and loaded asynchronously. The script automatically detects this format and extracts text content from the embedded data. Note: images in new-format posts are loaded dynamically and cannot be extracted via HTTP.

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

### 4. Docs Site Export

```bash
# Using preset for Mintlify docs (e.g., OpenClaw)
python SKILL_DIR/scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl \
  --merge --toc \
  --docs-preset mintlify \
  --merge-output docs_guide.md \
  --download-images

# Dual output: merged + split files
python SKILL_DIR/scripts/grab_web_to_md.py "https://docs.example.com/" \
  --crawl --merge --toc \
  --docs-preset mintlify \
  --merge-output output/merged.md \
  --split-output output/pages/ \
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

**Dual output benefits** (`--split-output`):
- Generates both merged.md and individual page files
- Shared assets directory (images downloaded once)
- INDEX.md with links to each page file
- Compatible with Obsidian, search tools, collaborative editing

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
| `--docs-preset NAME` | Use framework preset (mintlify/docusaurus/gitbook/vuepress/mkdocs/readthedocs/sphinx/notion/confluence/generic) |
| `--auto-detect` | Auto-detect docs framework and apply preset |
| `--list-presets` | Show all available presets |
| `--split-output DIR` | Output split files alongside merged (dual output mode) |
| `--warn-anchor-collisions` | Show anchor collision details (auto-fixed with -2, -3 suffixes) |

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

**Built-in security** (always active): cross-origin session isolation, Referer redaction, HTML sanitization, streaming download. For details see [references/full-guide.md](references/full-guide.md) §数据安全与隐私.

## Anti-Scraping Support

```bash
# With cookies
python SKILL_DIR/scripts/grab_web_to_md.py "URL" --cookie "session=xxx"

# With custom headers
python SKILL_DIR/scripts/grab_web_to_md.py "URL" --header "Authorization: Bearer xxx"

# Change User-Agent
python SKILL_DIR/scripts/grab_web_to_md.py "URL" --ua-preset firefox-win
```

## JS Challenge & Local HTML Fallback

JS-protected sites (Cloudflare, etc.) are auto-detected (exit code **4**). Workaround:

```bash
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

Auto-handled sites: Tencent Cloud (SSR), Volcengine (SSR), Notion (API). For details on JS challenge detection, known protected sites, and SSR extraction, see [references/full-guide.md](references/full-guide.md) §JS Challenge Detection.

## Output Structure

**Output path behavior**:

```bash
# Input: --out article.md (explicit path)
# Output structure (kept as-is, no auto wrapper):
./
├── article.md
├── article.assets/
└── article.md.assets.json

# Input: --out docs/article.md (user specified directory, unchanged)
docs/
├── article.md
├── article.assets/
└── article.md.assets.json

# Input: --auto-title (title is "My Article")
My-Article/
├── My-Article.md
├── My-Article.assets/
└── My-Article.md.assets.json
```

## Common Site Configurations

| Site Type | Recommended Parameters |
|-----------|----------------------|
| PukiWiki | `--target-id body --clean-wiki-noise` |
| MediaWiki | `--target-id content --clean-wiki-noise` |
| WordPress | `--target-class entry-content` |
| WeChat | Auto-detected, or `--wechat` |
| Tech Blog | `--keep-html --tags` |
| Docs Frameworks | `--docs-preset NAME` (10 presets: mintlify / docusaurus / gitbook / vuepress / mkdocs / readthedocs / sphinx / notion / confluence / generic; use `--list-presets` to see all) |

## Dependencies

- **Required**: `requests` (HTTP requests)
- **Optional**: `markdown` (for PDF export with `--with-pdf`)

Install: `pip install requests`

## References

For complete documentation, see [references/full-guide.md](references/full-guide.md):
- **All parameters with defaults**: see Parameter Reference tables
- **JS challenge handling & known sites**: see JS Challenge Detection section
- **Security architecture**: see Data Security & Privacy section
- **Architecture & module details**: see Modular Architecture section
- **10 usage scenarios with real-world cases**
- **Changelog history**
