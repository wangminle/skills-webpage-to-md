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

## 三种典型使用场景

| 场景 | 说明 |
|------|------|
| **微信公众号** | 自动检测 mp.weixin.qq.com，清理交互按钮噪音 |
| **技术博客** | `--keep-html --tags` 保留代码块和复杂表格 |
| **Wiki 批量** | `--crawl --merge --clean-wiki-noise` 爬取合并 |

## 常用参数

| 参数 | 说明 |
|------|------|
| `--out` | 输出文件路径 |
| `--validate` | 校验图片完整性 |
| `--keep-html` | 复杂表格保留 HTML |
| `--tags` | YAML Frontmatter 标签 |
| `--target-id` / `--target-class` | 指定正文容器 |
| `--crawl` | 启用爬取模式 |
| `--merge --toc` | 合并输出并生成目录 |
| `--download-images` | 下载图片到本地 |
| `--clean-wiki-noise` | 清理 Wiki 系统噪音 |
| `--rewrite-links` | 站内链接改写为锚点 |

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
