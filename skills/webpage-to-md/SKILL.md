---
name: webpage-to-md
description: Web scraping and Markdown conversion toolkit for extracting web content with images. Use when Claude needs to: (1) Save web articles/blogs as Markdown files, (2) Export WeChat articles (mp.weixin.qq.com), (3) Batch crawl Wiki sites and merge into single document, (4) Download webpage images locally, (5) Convert HTML tables/code blocks to Markdown format.
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
| `--target-id ID` | Extract element by id (e.g., `body`, `content`) |
| `--target-class CLASS` | Extract element by class (e.g., `article-body`) |
| `--clean-wiki-noise` | Remove Wiki system noise (PukiWiki/MediaWiki) |
| `--wechat` | Force WeChat article mode |

## Batch Processing Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `--urls-file` | - | Read URLs from file |
| `--max-workers` | 3 | Concurrent threads |
| `--delay` | 1.0 | Request interval (seconds) |
| `--skip-errors` | False | Continue on failures |
| `--download-images` | False | Download images locally |

## Anti-Scraping Support

```bash
# With cookies
python SKILL_DIR/scripts/grab_web_to_md.py "URL" --cookie "session=xxx"

# With custom headers
python SKILL_DIR/scripts/grab_web_to_md.py "URL" --header "Authorization: Bearer xxx"

# Change User-Agent
python SKILL_DIR/scripts/grab_web_to_md.py "URL" --ua-preset firefox-win
```

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
