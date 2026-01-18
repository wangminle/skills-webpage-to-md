#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
抓取网页正文与图片，保存为 Markdown + 本地 assets 目录。

依赖说明：
- 必需依赖：requests（HTTP 请求）
- 可选依赖：markdown（用于 PDF 渲染时的 Markdown→HTML 转换，无则使用内置简易转换）
- PDF 生成：使用系统已安装的 Edge/Chrome 浏览器 headless 模式，无需额外安装工具
- 不依赖：pandoc、playwright、selenium、bs4、lxml

设计目标（来自之前四个站点的实践）：
- 优先提取 <article>（其次 <main>/<body>），减少导航/页脚噪音
- 仅用标准库 HTMLParser（不依赖 bs4/lxml），适配离线/受限环境
- 图片下载支持：src/data-src/srcset/picture/source；相对 URL；content-type 缺失时嗅探格式
- Ghost/Anthropic 等站点会把视频播放器/图标混进正文：跳过常见 UI 标签/类
- 处理 <tag/> 自闭合导致的 skip 栈不出栈：实现 handle_startendtag
- 简单表格转换为 Markdown table；并提供校验（引用数=文件数/文件存在）
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse, unquote

import requests


UA_PRESETS: Dict[str, str] = {
    # 兼容旧行为（但部分站点会拦截“工具 UA”）
    "tool": "Mozilla/5.0 (compatible; grab_web_to_md/1.0)",
    # 常见真实浏览器 UA（不追求绝对最新，只要“像”浏览器即可）
    "edge-win": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    ),
    "chrome-win": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "firefox-win": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) "
        "Gecko/20100101 Firefox/122.0"
    ),
    "chrome-mac": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "safari-mac": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.3 Safari/605.1.15"
    ),
    "chrome-linux": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _resolve_user_agent(user_agent: Optional[str], ua_preset: str) -> str:
    if user_agent and user_agent.strip():
        return user_agent.strip()
    return UA_PRESETS.get(ua_preset, UA_PRESETS["chrome-win"])


def generate_frontmatter(title: str, url: str, tags: Optional[List[str]] = None) -> str:
    """生成 YAML Frontmatter 元数据头，兼容 Obsidian/Hugo/Jekyll 等工具。"""
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 转义标题中的特殊字符
    safe_title = title.replace('"', '\\"').replace("\n", " ")
    safe_url = (url or "").replace('"', '\\"').replace("\n", " ").strip()
    lines = [
        "---",
        f'title: "{safe_title}"',
        f'source: "{safe_url}"',
        f'date: "{date_str}"',
    ]
    if tags:
        tags_str = ", ".join(f'"{t}"' for t in tags)
        lines.append(f"tags: [{tags_str}]")
    lines.append("---")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def _sanitize_filename_part(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[^\w.\-]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "untitled"


def _safe_path_length(base_dir: str, filename: str, max_total: int = 250) -> str:
    """确保完整路径不超过 Windows 限制，必要时截断文件名。"""
    abs_path = os.path.abspath(os.path.join(base_dir, filename))
    if len(abs_path) <= max_total:
        return filename

    name, ext = os.path.splitext(filename)
    overflow = len(abs_path) - max_total
    # 至少保留 10 个字符的文件名
    truncated_len = max(10, len(name) - overflow - 8)
    truncated = name[:truncated_len]
    # 添加哈希后缀以保证唯一性
    suffix = hashlib.sha1(filename.encode("utf-8")).hexdigest()[:6]
    new_filename = f"{truncated}-{suffix}{ext}"
    return new_filename


def _default_basename(url: str, max_len: int = 80) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.strip("/")
    if not path:
        base = host
    else:
        parts = [p for p in path.split("/") if p]
        base = "_".join([host] + parts)
    base = _sanitize_filename_part(base)
    if len(base) <= max_len:
        return base
    suffix = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return (base[: max_len - 9] + "-" + suffix).rstrip("-")


def _find_best_section(html: str, tag: str) -> Optional[str]:
    pattern = re.compile(rf"<{tag}\b[^>]*>(.*?)</{tag}>", re.IGNORECASE | re.DOTALL)
    matches = list(pattern.finditer(html))
    if not matches:
        return None
    # 选择最长的那个，避免拿到导航/推荐模块之类的短 article
    best = max(matches, key=lambda m: len(m.group(1)))
    return best.group(1)


def extract_main_html(page_html: str) -> str:
    for tag in ("article", "main", "body"):
        section = _find_best_section(page_html, tag)
        if section:
            return section
    return page_html


class _TargetSectionExtractor(HTMLParser):
    def __init__(self, *, target_id: Optional[str], target_class: Optional[str]):
        super().__init__(convert_charrefs=True)
        self.target_id = (target_id or "").strip() or None
        self.target_class = (target_class or "").strip() or None
        self.depth = 0
        self.done = False
        self.buf: List[str] = []

    @staticmethod
    def _attrs_to_str(attrs_list: Sequence[Tuple[str, Optional[str]]]) -> str:
        parts = []
        for name, value in attrs_list:
            if value is None:
                parts.append(name)
            else:
                escaped = htmllib.escape(str(value), quote=True)
                parts.append(f'{name}="{escaped}"')
        return " ".join(parts)

    def _match(self, attrs: Dict[str, Optional[str]]) -> bool:
        if self.target_id and (attrs.get("id") or "").strip() == self.target_id:
            return True
        if self.target_class:
            classes = _class_list(attrs)
            if self.target_class in classes:
                return True
        return False

    def handle_starttag(self, tag: str, attrs_list: Sequence[Tuple[str, Optional[str]]]) -> None:
        if self.done:
            return
        tag = tag.lower()
        attrs = dict(attrs_list)
        if self.depth == 0:
            if not self._match(attrs):
                return
            self.depth = 1
        else:
            self.depth += 1
        attr_str = self._attrs_to_str(attrs_list)
        if attr_str:
            self.buf.append(f"<{tag} {attr_str}>")
        else:
            self.buf.append(f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs_list: Sequence[Tuple[str, Optional[str]]]) -> None:
        if self.done:
            return
        tag = tag.lower()
        attrs = dict(attrs_list)
        if self.depth == 0:
            if not self._match(attrs):
                return
            self.done = True
        attr_str = self._attrs_to_str(attrs_list)
        if attr_str:
            self.buf.append(f"<{tag} {attr_str}/>")
        else:
            self.buf.append(f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        if self.done or self.depth == 0:
            return
        tag = tag.lower()
        self.buf.append(f"</{tag}>")
        self.depth -= 1
        if self.depth == 0:
            self.done = True

    def handle_data(self, data: str) -> None:
        if self.done or self.depth == 0 or not data:
            return
        self.buf.append(htmllib.escape(data, quote=False))


def extract_target_html(page_html: str, *, target_id: Optional[str], target_class: Optional[str]) -> Optional[str]:
    parser = _TargetSectionExtractor(target_id=target_id, target_class=target_class)
    parser.feed(page_html or "")
    out = "".join(parser.buf).strip()
    return out or None


class _TextLenExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.n = 0

    def handle_data(self, data: str) -> None:
        if not data or data.isspace():
            return
        self.n += len(re.sub(r"\s+", " ", data.strip()))


def html_text_len(html: str) -> int:
    parser = _TextLenExtractor()
    parser.feed(html or "")
    return parser.n


def sniff_ext(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return ".webp"
    head = data[:200].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<svg"):
        return ".svg"
    return None


def ext_from_content_type(content_type: Optional[str]) -> Optional[str]:
    if not content_type:
        return None
    ct = content_type.split(";", 1)[0].strip().lower()
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "image/avif": ".avif",
    }.get(ct)


def is_probable_icon(url: str) -> bool:
    low = url.lower()
    return (
        "favicon" in low
        or "/icon/" in low
        or low.endswith(".ico")
        or "pinned-octocat" in low
        or "/apple-touch-icon" in low
    )


class ImageURLCollector(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.image_urls: List[str] = []
        self._in_picture = False
        self._picture_sources: List[str] = []

    def _add_url(self, raw: Optional[str]) -> None:
        if not raw:
            return
        raw = htmllib.unescape(raw).strip()
        if not raw or raw.startswith("data:"):
            return
        full = urljoin(self.base_url, raw)
        if is_probable_icon(full):
            return
        self.image_urls.append(full)

    def handle_starttag(self, tag: str, attrs_list: Sequence[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attrs = dict(attrs_list)

        if tag == "picture":
            self._in_picture = True
            self._picture_sources = []
            return

        if tag == "source" and self._in_picture:
            srcset = attrs.get("srcset")
            if srcset:
                first = srcset.split(",")[0].strip().split(" ")[0]
                self._picture_sources.append(first)
            return

        if tag == "img":
            # 优先 picture/source 的 srcset
            if self._in_picture and self._picture_sources:
                self._add_url(self._picture_sources[0])
                self._picture_sources = []
                return

            candidates = [
                attrs.get("src"),
                attrs.get("data-src"),
                attrs.get("data-original"),
                attrs.get("data-lazy-src"),
            ]
            src = next((c for c in candidates if c), None)
            if (not src) or (src and src.startswith("data:")):
                srcset = attrs.get("srcset")
                if srcset:
                    src = srcset.split(",")[0].strip().split(" ")[0]
            self._add_url(src)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "picture":
            self._in_picture = False
            self._picture_sources = []


def uniq_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for it in items:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


def download_images(
    session: requests.Session,
    image_urls: Sequence[str],
    assets_dir: str,
    md_dir: str,
    timeout_s: int,
    retries: int = 3,
    best_effort: bool = False,
) -> Dict[str, str]:
    os.makedirs(assets_dir, exist_ok=True)
    url_to_local: Dict[str, str] = {}

    for idx, img_url in enumerate(image_urls, start=1):
        last_err: Optional[Exception] = None
        r: Optional[requests.Response] = None
        content: Optional[bytes] = None
        for attempt in range(1, retries + 1):
            try:
                r = session.get(img_url, timeout=timeout_s, stream=True, headers={"Connection": "close"})
                r.raise_for_status()
                content = b"".join(r.iter_content(chunk_size=1024 * 64))
                break
            except Exception as e:  # noqa: BLE001 - CLI tool wants retries on network errors
                last_err = e
                if attempt >= retries:
                    break
                time.sleep(min(2.0, 0.4 * attempt))

        if content is None or r is None:
            if best_effort:
                print(f"警告：图片下载失败，已跳过：{img_url}\n  - 错误：{last_err}", file=sys.stderr)
                continue
            raise last_err or RuntimeError("image download failed")

        parsed = urlparse(img_url)
        base = os.path.basename(parsed.path.rstrip("/"))
        base = unquote(base) or f"image-{idx}"
        name_root, name_ext = os.path.splitext(base)

        if not name_ext:
            name_ext = (
                ext_from_content_type(r.headers.get("Content-Type") if r else None)
                or sniff_ext(content or b"")
                or ".bin"
            )

        safe_root = _sanitize_filename_part(name_root)
        filename = f"{idx:02d}-{safe_root}{name_ext}"
        # 检查路径长度，必要时截断
        filename = _safe_path_length(assets_dir, filename)
        local_path = os.path.join(assets_dir, filename)

        with open(local_path, "wb") as f:
            f.write(content or b"")

        local_abs = os.path.abspath(local_path)
        md_dir_abs = os.path.abspath(md_dir or ".")
        rel = os.path.relpath(local_abs, start=md_dir_abs)
        url_to_local[img_url] = rel.replace("\\", "/")

    return url_to_local


VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
    # svg 自闭合常见形状
    "path",
    "rect",
    "circle",
    "polygon",
    "polyline",
    "line",
    "ellipse",
}

SKIP_TAGS = {
    "script",
    "style",
    "svg",
    "video",
    "audio",
}


def _class_list(attrs: Dict[str, Optional[str]]) -> List[str]:
    cls = attrs.get("class")
    if not cls:
        return []
    if isinstance(cls, str):
        return [c for c in cls.split() if c]
    return [str(cls)]


class HTMLToMarkdown(HTMLParser):
    def __init__(self, base_url: str, url_to_local: Dict[str, str], keep_html: bool = False):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.url_to_local = url_to_local
        self.keep_html = keep_html  # 是否对复杂表格保留 HTML（colspan/rowspan/nested table）
        self.out: List[str] = []

        self.skip_stack: List[str] = []

        self.in_heading = False
        self.heading_out_start: Optional[int] = None
        self.heading_text: List[str] = []

        self.in_pre = False
        self.pre_buf: List[str] = []
        self.pre_lang: str = ""

        self.in_math_script = False
        self.math_script_display = False
        self.math_script_buf: List[str] = []

        self.in_annotation_tex = False
        self.annotation_display = False
        self.annotation_buf: List[str] = []

        self.tag_stack: List[Tuple[str, bool, bool]] = []
        self.katex_depth = 0
        self.katex_display_depth = 0

        self.in_inline_code = False
        self.inline_code_buf: List[str] = []

        self.in_a = False
        self.a_href: Optional[str] = None
        self.a_text: List[str] = []

        self.list_stack: List[Dict[str, int | str]] = []

        self.in_table = False
        self.table_depth = 0
        self.table_rows: List[List[str]] = []
        self.current_row: Optional[List[str]] = None
        self.in_cell = False
        self.cell_buf: List[str] = []
        self.table_in_a = False
        self.table_a_href: Optional[str] = None
        self.table_a_text: List[str] = []

        # 复杂表格的 HTML 原样保留模式
        self.raw_table_mode = False
        self.raw_table_buf: List[str] = []
        self.raw_table_depth = 0
        self.table_capture_html = False
        self.table_capture_buf: List[str] = []
        self.table_capture_depth = 0
        self.table_is_complex = False

    @staticmethod
    def _is_complex_table_attrs(attrs: Dict[str, Optional[str]]) -> bool:
        """检测表格单元格属性是否包含 colspan/rowspan（复杂表格标志）。"""
        colspan = attrs.get("colspan")
        rowspan = attrs.get("rowspan")
        if colspan and colspan != "1":
            return True
        if rowspan and rowspan != "1":
            return True
        return False

    @staticmethod
    def _attrs_to_str(attrs_list: Sequence[Tuple[str, Optional[str]]]) -> str:
        """将属性列表转换为 HTML 属性字符串。"""
        parts = []
        for name, value in attrs_list:
            if value is None:
                parts.append(name)
            else:
                escaped = htmllib.escape(value, quote=True)
                parts.append(f'{name}="{escaped}"')
        return " ".join(parts)

    @staticmethod
    def _extract_code_language(attrs: Dict[str, Optional[str]]) -> str:
        # 常见形态：class="language-python" / class="lang-python" / data-language="python" / class="python"
        for key in ("data-language", "data-lang", "lang"):
            val = (attrs.get(key) or "").strip()
            if val:
                return val.split()[0]

        classes = _class_list(attrs)
        for c in classes:
            m = re.match(r"^(?:language|lang)[-_]([A-Za-z0-9_+.-]+)$", c)
            if m:
                return m.group(1)

        # 兜底：部分站点会直接用 class="python"
        known = {
            "bash",
            "c",
            "cpp",
            "csharp",
            "css",
            "go",
            "html",
            "java",
            "javascript",
            "js",
            "json",
            "kotlin",
            "perl",
            "php",
            "python",
            "py",
            "ruby",
            "rust",
            "scala",
            "shell",
            "sh",
            "sql",
            "swift",
            "toml",
            "typescript",
            "ts",
            "xml",
            "yaml",
            "yml",
        }
        for c in classes:
            low = c.lower()
            if low in known:
                return low

        return ""

    @staticmethod
    def _sanitize_fence_language(lang: str) -> str:
        parts = (lang or "").strip().split()
        lang = parts[0] if parts else ""
        if not lang:
            return ""
        if not re.match(r"^[A-Za-z0-9_+.-]+$", lang):
            return ""
        return lang

    def _tail(self) -> str:
        return "".join(self.out[-8:]) if self.out else ""

    def _ensure_blank_line(self) -> None:
        if not self.out:
            return
        tail = self._tail()
        if not tail.endswith("\n\n"):
            if tail.endswith("\n"):
                self.out.append("\n")
            else:
                self.out.append("\n\n")

    def _append_text(self, text: str) -> None:
        if not text:
            return
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        # 一些站点在 <strong>/<em>/<code> 后会带空格，避免输出成 "** foo**"
        if self.out:
            tail = self._tail()
            if tail.endswith(("**", "*", "`")):
                text = text.lstrip()
        # 避免把两个“词”粘在一起
        if self.out:
            prev = self._tail()[-1:]
            if prev and prev not in ("\n", " ", "(", "[") and text[:1] not in (" ", "\n", ".", ",", ":", ";", ")", "]"):
                self.out.append(" ")
        self.out.append(text)

    def _table_append(self, text: str) -> None:
        if not text:
            return
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        self.cell_buf.append(text)

    def _switch_to_raw_table_mode(self, current_attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        """切换到原始 HTML 模式，重建之前已处理的表格内容。"""
        self.raw_table_mode = True
        self.raw_table_buf = ["<table>"]
        # 重建之前已处理的行
        for row in self.table_rows:
            self.raw_table_buf.append("<tr>")
            for cell in row:
                # 之前的内容都当作 td 处理
                self.raw_table_buf.append(f"<td>{cell}</td>")
            self.raw_table_buf.append("</tr>")
        # 重建当前行（如果有）
        if self.current_row is not None:
            self.raw_table_buf.append("<tr>")
            for cell in self.current_row:
                self.raw_table_buf.append(f"<td>{cell}</td>")
            # 当前单元格的内容
            if self.cell_buf:
                cell_content = "".join(self.cell_buf)
                self.raw_table_buf.append(f"<td>{cell_content}</td>")
        # 添加触发切换的单元格
        attr_str = self._attrs_to_str(current_attrs)
        tag = "td"  # 默认 td，实际上可能是 th
        if attr_str:
            self.raw_table_buf.append(f"<{tag} {attr_str}>")
        else:
            self.raw_table_buf.append(f"<{tag}>")
        # 重置普通表格状态
        self.table_rows = []
        self.current_row = None
        self.in_cell = False
        self.cell_buf = []

    def _should_skip(self, tag: str, attrs: Dict[str, Optional[str]]) -> bool:
        if tag == "script":
            # MathJax 常用：<script type="math/tex"> 或 <script type="math/tex; mode=display">
            t = (attrs.get("type") or "").strip().lower()
            if t.startswith("math/tex"):
                return False
        if tag in SKIP_TAGS:
            return True

        # Ghost 等站点的 video/file/audio UI（但保留 figure/figcaption 的正文）
        classes = _class_list(attrs)
        if classes and tag not in ("figure", "figcaption"):
            if any(c.startswith(("kg-video-", "kg-audio-", "kg-file-")) for c in classes):
                return True
            if any("kg-video" in c for c in classes):
                return True

        # 纯交互元素
        if tag in ("button",):
            return True

        return False

    def _enter_skip(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        self.skip_stack.append(tag)

    def handle_starttag(self, tag: str, attrs_list: Sequence[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attrs = dict(attrs_list)

        # 追踪 tag 嵌套，用于判断 KaTeX display/inline，并避免输出 KaTeX 渲染后的重复文本。
        # 注意：VOID_TAGS 不压栈，因为它们没有对应的结束标签。
        if tag not in VOID_TAGS:
            is_katex = False
            is_katex_display = False
            if tag == "span":
                classes = _class_list(attrs)
                is_katex_display = "katex-display" in classes
                is_katex = is_katex_display or ("katex" in classes)
            self.tag_stack.append((tag, is_katex, is_katex_display))
            if is_katex:
                self.katex_depth += 1
            if is_katex_display:
                self.katex_display_depth += 1

        if self.skip_stack:
            if self._should_skip(tag, attrs):
                self._enter_skip(tag)
            return

        if self._should_skip(tag, attrs):
            self._enter_skip(tag)
            return

        # table（若 table 内再出现 table，视为复杂结构：不要重置状态）
        if tag == "table" and self.in_table:
            self.table_depth += 1
            if self.keep_html:
                self.table_is_complex = True
            if self.table_capture_html:
                attr_str = self._attrs_to_str(attrs_list)
                if attr_str:
                    self.table_capture_buf.append(f"<table {attr_str}>")
                else:
                    self.table_capture_buf.append("<table>")
                self.table_capture_depth += 1
                self.table_is_complex = True
            return

        # table（顶层）
        if tag == "table":
            self._ensure_blank_line()
            self.in_table = True
            self.table_depth = 1
            self.table_rows = []
            # 如果启用 keep_html：从 table 开始就同步捕获 HTML，遇到复杂结构时直接输出捕获内容。
            self.raw_table_mode = False
            self.raw_table_buf = []
            self.raw_table_depth = 1
            self.table_capture_html = bool(self.keep_html)
            self.table_is_complex = False
            if self.table_capture_html:
                attr_str = self._attrs_to_str(attrs_list)
                if attr_str:
                    self.table_capture_buf = [f"<table {attr_str}>"]
                else:
                    self.table_capture_buf = ["<table>"]
                self.table_capture_depth = 1
            return

        # 复杂表格的原始 HTML 模式：直接记录所有内容
        if self.raw_table_mode:
            attr_str = self._attrs_to_str(attrs_list)
            if attr_str:
                self.raw_table_buf.append(f"<{tag} {attr_str}>")
            else:
                self.raw_table_buf.append(f"<{tag}>")
            if tag == "table":
                self.raw_table_depth += 1
            return

        if self.in_table:
            # 嵌套 table 内部：避免把内层 tr/td 误当外层表格结构解析；只做 HTML 捕获。
            if self.table_depth > 1:
                if self.table_capture_html:
                    attr_str = self._attrs_to_str(attrs_list)
                    if attr_str:
                        self.table_capture_buf.append(f"<{tag} {attr_str}>")
                    else:
                        self.table_capture_buf.append(f"<{tag}>")
                    if tag == "table":
                        self.table_capture_depth += 1
                        self.table_is_complex = True
                return

            if self.table_capture_html:
                attr_str = self._attrs_to_str(attrs_list)
                if attr_str:
                    self.table_capture_buf.append(f"<{tag} {attr_str}>")
                else:
                    self.table_capture_buf.append(f"<{tag}>")

            if tag == "tr":
                self.current_row = []
            elif tag in ("th", "td"):
                # 检测是否为复杂表格（含 colspan/rowspan）
                if self.keep_html and self._is_complex_table_attrs(attrs):
                    self.table_is_complex = True
                self.in_cell = True
                self.cell_buf = []
            elif tag == "br" and self.in_cell:
                # Markdown 表格单元格里保留换行：用 <br>（多数渲染器支持）
                if self.cell_buf and (self.cell_buf[-1].strip().lower() != "<br>"):
                    self.cell_buf.append("<br>")
            elif tag in ("p", "div", "li") and self.in_cell:
                # 表格内的块级/列表元素需要一个“软换行”，避免内容粘连
                if self.cell_buf and (self.cell_buf[-1].strip().lower() != "<br>"):
                    self.cell_buf.append("<br>")
            elif tag == "a" and self.in_cell:
                self.table_in_a = True
                self.table_a_href = attrs.get("href")
                self.table_a_text = []
            return

        # block-ish tags
        if tag in ("p",):
            # 列表项内的 <p> 很常见；强行 blank line 会把 "1. " 和内容拆开，造成空条目
            if not self.list_stack:
                self._ensure_blank_line()
        elif tag == "br":
            self.out.append("\n")
        elif tag == "hr":
            self._ensure_blank_line()
            self.out.append("---\n\n")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._ensure_blank_line()
            level = int(tag[1])
            self.in_heading = True
            self.heading_out_start = len(self.out)
            self.heading_text = []
            self.out.append("#" * level + " ")
        elif tag == "script":
            t = (attrs.get("type") or "").strip().lower()
            if t.startswith("math/tex"):
                self.in_math_script = True
                self.math_script_display = "mode=display" in t
                self.math_script_buf = []
                return
            self._enter_skip(tag)
        elif tag == "pre":
            self._ensure_blank_line()
            self.in_pre = True
            self.pre_buf = []
            self.pre_lang = self._sanitize_fence_language(self._extract_code_language(attrs))
        elif tag == "code":
            if self.in_pre:
                if not self.pre_lang:
                    self.pre_lang = self._sanitize_fence_language(self._extract_code_language(attrs))
                return
            self.in_inline_code = True
            self.inline_code_buf = []
        elif tag == "annotation":
            enc = (attrs.get("encoding") or "").strip().lower()
            if enc in ("application/x-tex", "text/tex"):
                self.in_annotation_tex = True
                self.annotation_display = self.katex_display_depth > 0
                self.annotation_buf = []
                return
        elif tag == "strong" or tag == "b":
            self.out.append("**")
        elif tag == "em" or tag == "i":
            self.out.append("*")
        elif tag == "a":
            self.in_a = True
            self.a_href = attrs.get("href")
            self.a_text = []
        elif tag == "img":
            src = (
                attrs.get("src")
                or attrs.get("data-src")
                or attrs.get("data-original")
                or attrs.get("data-lazy-src")
            )
            if (not src) and attrs.get("srcset"):
                src = attrs["srcset"].split(",")[0].strip().split(" ")[0]
            if not src:
                return
            img_url = urljoin(self.base_url, htmllib.unescape(src))
            if is_probable_icon(img_url):
                return
            alt = (attrs.get("alt") or "").strip()
            local = self.url_to_local.get(img_url, img_url)
            self._ensure_blank_line()
            self.out.append(f"![{alt}]({local})\n")
        elif tag in ("ul", "ol"):
            # 嵌套列表不要强行插入空行，否则可能破坏渲染；只确保换行即可。
            if self.list_stack:
                if not self._tail().endswith("\n"):
                    self.out.append("\n")
            else:
                self._ensure_blank_line()
            self.list_stack.append({"type": tag, "n": 0})
        elif tag == "li":
            if self.list_stack:
                if self.out and (not self._tail().endswith("\n")):
                    self.out.append("\n")
                self.list_stack[-1]["n"] = int(self.list_stack[-1]["n"]) + 1
                indent = "  " * (len(self.list_stack) - 1)
                if self.list_stack[-1]["type"] == "ol":
                    prefix = f"{self.list_stack[-1]['n']}. "
                else:
                    prefix = "- "
                self.out.append(indent + prefix)
        elif tag == "blockquote":
            self._ensure_blank_line()
            self.out.append("> ")

    def handle_startendtag(self, tag: str, attrs_list: Sequence[Tuple[str, Optional[str]]]) -> None:
        # 处理 <tag/>，避免 skip_stack 因 void/self-closing 形态不一致而泄漏
        tag = tag.lower()
        self.handle_starttag(tag, attrs_list)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        # VOID_TAGS 不应有结束标签；如果遇到，直接忽略（不出栈）。
        if tag in VOID_TAGS:
            pass
        elif self.tag_stack:
            # 尝试匹配栈顶 tag；如果不匹配，可能是 HTML 不规范或有未闭合标签。
            # 策略：向下搜索栈，找到匹配的 tag 并弹出它及其上方的所有元素（容错）。
            matched_idx = -1
            for idx in range(len(self.tag_stack) - 1, -1, -1):
                if self.tag_stack[idx][0] == tag:
                    matched_idx = idx
                    break
            if matched_idx >= 0:
                # 弹出从匹配位置到栈顶的所有元素
                for _ in range(len(self.tag_stack) - matched_idx):
                    _, is_katex, is_katex_display = self.tag_stack.pop()
                    if is_katex:
                        self.katex_depth = max(0, self.katex_depth - 1)
                    if is_katex_display:
                        self.katex_display_depth = max(0, self.katex_display_depth - 1)

        if self.skip_stack:
            if tag == self.skip_stack[-1]:
                self.skip_stack.pop()
            return

        # 复杂表格的原始 HTML 模式
        if self.raw_table_mode:
            if tag == "table":
                self.raw_table_depth -= 1
                if self.raw_table_depth <= 0:
                    # 表格结束，输出原始 HTML
                    self.raw_table_buf.append("</table>")
                    self.out.append("\n".join(self.raw_table_buf))
                    self.out.append("\n\n")
                    self.raw_table_mode = False
                    self.raw_table_buf = []
                    self.in_table = False
                else:
                    self.raw_table_buf.append("</table>")
            elif tag not in VOID_TAGS:
                self.raw_table_buf.append(f"</{tag}>")
            return

        if self.in_table:
            if self.table_capture_html:
                if tag not in VOID_TAGS:
                    self.table_capture_buf.append(f"</{tag}>")
                if tag == "table":
                    self.table_capture_depth -= 1

            if tag == "table":
                self.table_depth = max(0, self.table_depth - 1)
            elif self.table_depth > 1:
                # 嵌套 table 内的结束标签：不参与 Markdown 表格状态机
                return

            if tag == "a" and self.table_in_a:
                text = "".join(self.table_a_text).strip() or (self.table_a_href or "")
                href = self.table_a_href
                if href:
                    href = urljoin(self.base_url, href)
                    self._table_append(f"[{text}]({href})")
                else:
                    self._table_append(text)
                self.table_in_a = False
                self.table_a_href = None
                self.table_a_text = []
            elif tag in ("p", "div", "li") and self.in_cell:
                if self.cell_buf and (self.cell_buf[-1].strip().lower() != "<br>"):
                    self.cell_buf.append("<br>")
            elif tag in ("th", "td") and self.in_cell:
                cell = "".join(self.cell_buf)
                cell = cell.replace("\r\n", "\n").replace("\r", "\n")
                cell = re.sub(r"[ \t\f\v]+", " ", cell)
                cell = re.sub(r"\s*\n\s*", "<br>", cell)
                cell = re.sub(r"\s*<br>\s*", "<br>", cell, flags=re.IGNORECASE)
                cell = re.sub(r"(<br>){2,}", "<br>", cell, flags=re.IGNORECASE)
                cell = re.sub(r"^(<br>)+", "", cell, flags=re.IGNORECASE)
                cell = re.sub(r"(<br>)+$", "", cell, flags=re.IGNORECASE)
                cell = cell.strip()
                if self.current_row is not None:
                    self.current_row.append(cell)
                self.in_cell = False
                self.cell_buf = []
            elif tag == "tr":
                if self.current_row is not None and any(c.strip() for c in self.current_row):
                    self.table_rows.append(self.current_row)
                self.current_row = None
            elif tag == "table":
                # 嵌套表格：仅减少深度，不在这里结束整个表格解析（复杂表格建议 --keep-html）。
                if self.table_depth > 0:
                    return
                rows = self.table_rows
                self.in_table = False
                self.table_rows = []
                self.current_row = None

                if self.table_capture_html and self.table_capture_depth <= 0:
                    # 只有在顶层 table 完整闭合时才决定输出策略
                    if self.keep_html and self.table_is_complex:
                        self.out.append("".join(self.table_capture_buf))
                        self.out.append("\n\n")
                        self.table_capture_html = False
                        self.table_capture_buf = []
                        self.table_capture_depth = 0
                        self.table_is_complex = False
                        return
                    self.table_capture_html = False
                    self.table_capture_buf = []
                    self.table_capture_depth = 0
                    self.table_is_complex = False

                if rows:
                    cols = max(len(r) for r in rows)
                    norm = [r + [""] * (cols - len(r)) for r in rows]
                    header = norm[0]
                    body = norm[1:]
                    self.out.append("| " + " | ".join(h.replace("|", r"\|") for h in header) + " |\n")
                    self.out.append("| " + " | ".join(["---"] * cols) + " |\n")
                    for r in body:
                        self.out.append("| " + " | ".join(c.replace("|", r"\|") for c in r) + " |\n")
                    self.out.append("\n")
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            # 过滤空标题（一些站点会生成无文本 heading，或只包含“#”锚点）
            if self.in_heading and (self.heading_out_start is not None):
                heading_text = "".join(self.heading_text).strip()
                if not heading_text:
                    del self.out[self.heading_out_start :]
                else:
                    self.out.append("\n\n")
            else:
                self.out.append("\n\n")
            self.in_heading = False
            self.heading_out_start = None
            self.heading_text = []
        elif tag == "p":
            self.out.append("\n\n")
        elif tag == "annotation" and self.in_annotation_tex:
            tex = "".join(self.annotation_buf).strip()
            self.in_annotation_tex = False
            self.annotation_buf = []
            if tex:
                if self.annotation_display:
                    self._ensure_blank_line()
                    self.out.append(f"$$\n{tex}\n$$\n\n")
                else:
                    self._append_text(f"${tex.replace(chr(10), ' ')}$")
            self.annotation_display = False
        elif tag == "script" and self.in_math_script:
            tex = "".join(self.math_script_buf).strip()
            display = self.math_script_display
            self.in_math_script = False
            self.math_script_display = False
            self.math_script_buf = []
            if tex:
                if display:
                    self._ensure_blank_line()
                    self.out.append(f"$$\n{tex}\n$$\n\n")
                else:
                    self._append_text(f"${tex.replace(chr(10), ' ')}$")
        elif tag == "pre":
            code = "".join(self.pre_buf)
            code = code.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
            fence_lang = self._sanitize_fence_language(self.pre_lang)
            self.out.append(f"```{fence_lang}\n" + code + "\n```\n\n")
            self.in_pre = False
            self.pre_buf = []
            self.pre_lang = ""
        elif tag == "code":
            if self.in_pre:
                return
            code = "".join(self.inline_code_buf).strip()
            self.out.append("`" + code.replace("`", r"\`") + "`")
            self.in_inline_code = False
            self.inline_code_buf = []
        elif tag == "strong" or tag == "b":
            self.out.append("**")
        elif tag == "em" or tag == "i":
            self.out.append("*")
        elif tag == "a":
            text = "".join(self.a_text).strip() or (self.a_href or "")
            href = self.a_href

            # heading 的小锚点（# / ¶ 等）属于噪音
            if href:
                full = urljoin(self.base_url, href)
                if text.strip() in ("#", "¶", "§") and (href.startswith("#") or full.startswith(self.base_url + "#")):
                    self.in_a = False
                    self.a_href = None
                    self.a_text = []
                    return

            # Ghost 的 heading 小锚点通常渲染成“tag”，属于噪音
            if text.lower() == "tag" and href and (href.startswith("#") or href.startswith(self.base_url + "#")):
                self.in_a = False
                self.a_href = None
                self.a_text = []
                return

            if href:
                href = urljoin(self.base_url, href)
                self.out.append(f"[{text}]({href})")
            else:
                self.out.append(text)
            self.in_a = False
            self.a_href = None
            self.a_text = []
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n")
        elif tag == "blockquote":
            self.out.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self.skip_stack:
            return
        # 复杂表格的原始 HTML 模式
        if self.raw_table_mode:
            if data:
                self.raw_table_buf.append(htmllib.escape(data))
            return
        if self.in_annotation_tex:
            self.annotation_buf.append(data or "")
            return
        if self.in_math_script:
            self.math_script_buf.append(data or "")
            return
        # KaTeX 渲染出来的 HTML 文本会导致公式重复输出；只保留 annotation 的 TeX 源。
        if self.katex_depth > 0:
            return
        # <pre> 内必须保留全部内容（包括空白行/缩进），否则会出现 token 粘连（例如 loopwhile）。
        if self.in_table and self.table_capture_html and data:
            self.table_capture_buf.append(htmllib.escape(data, quote=False))
        if self.in_pre:
            self.pre_buf.append(data or "")
            return
        if self.in_table and self.table_depth > 1:
            # 嵌套 table 内部：不把文本拼进外层 Markdown table cell
            return
        if self.in_table:
            if self.table_in_a:
                self.table_a_text.append(data)
                return
            if self.in_cell:
                self._table_append(data)
            return
        if self.in_inline_code:
            self.inline_code_buf.append(data)
            return
        if self.in_a:
            if self.in_heading and data and (not data.isspace()) and data.strip() not in ("#", "¶", "§"):
                self.heading_text.append(data)
            self.a_text.append(data)
            return
        if not data or data.isspace():
            return
        if self.in_heading:
            self.heading_text.append(data)
        self._append_text(data)


def _convert_latex_delimiters_outside_code(md: str) -> str:
    # 把 \(...\)/\[...\] 统一转为 $/$$，并跳过 fenced code block。
    out_lines: List[str] = []
    in_fence = False
    in_inline_code = False
    inline_tick_len = 0
    for line in md.splitlines(True):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
            continue

        i = 0
        converted: List[str] = []
        while i < len(line):
            if line[i] == "`":
                j = i
                while j < len(line) and line[j] == "`":
                    j += 1
                ticks = j - i
                converted.append(line[i:j])
                if not in_inline_code:
                    in_inline_code = True
                    inline_tick_len = ticks
                elif ticks == inline_tick_len:
                    in_inline_code = False
                    inline_tick_len = 0
                i = j
                continue

            j = line.find("`", i)
            if j == -1:
                j = len(line)
            seg = line[i:j]
            if not in_inline_code:
                seg = seg.replace(r"\[", "$$").replace(r"\]", "$$")
                seg = seg.replace(r"\(", "$").replace(r"\)", "$")
            converted.append(seg)
            i = j

        out_lines.append("".join(converted))
    return "".join(out_lines)


def html_to_markdown(article_html: str, base_url: str, url_to_local: Dict[str, str], keep_html: bool = False) -> str:
    parser = HTMLToMarkdown(base_url=base_url, url_to_local=url_to_local, keep_html=keep_html)
    parser.feed(article_html)
    md = "".join(parser.out)
    md = md.replace("\r\n", "\n")
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r"\n\s*/\s*\n", "\n\n", md)  # 少数站点的残留 UI 符号
    md = _convert_latex_delimiters_outside_code(md)
    # 去除空标题行（例如单独的 "###" / "# "）
    md = re.sub(r"(?m)^\s*#{1,6}\s*$\n?", "", md)
    # 去除标题中的小锚点噪音（例如 "Heading[#](...)"）
    md = re.sub(r"(?m)^(#{1,6}\s+.*?)(\s*\[\s*[#¶§]\s*\]\([^)]+\))+\s*$", r"\1", md)
    return md.strip() + "\n"


def _path_to_file_uri(path: str) -> str:
    p = os.path.abspath(path).replace("\\", "/")
    # Windows: C:/x -> file:///C:/x
    if re.match(r"^[A-Za-z]:/", p):
        return "file:///" + p
    # POSIX: /x -> file:///x
    if p.startswith("/"):
        return "file://" + p
    return "file:///" + p


def _find_pdf_browser() -> Optional[str]:
    # 优先使用已安装的 Chromium 系浏览器（Edge/Chrome）。这是“尽量使用标准库”的现实取舍：
    # Python 标准库本身不提供高保真 Markdown→PDF 渲染能力。
    candidates = [
        shutil.which("msedge"),
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("msedge.exe"),
    ]

    # Windows 常见安装路径兜底
    candidates += [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]

    for c in candidates:
        if not c:
            continue
        if os.path.isfile(c):
            return c
    return None


def _markdown_css() -> str:
    # 轻量 CSS：尽量接近常见 Markdown 预览风格（标题/代码/表格/引用/图片）。
    return """
    :root { color-scheme: light; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; line-height: 1.6; }
    .markdown-body { max-width: 920px; margin: 0 auto; padding: 32px 20px; color: #1f2328; }
    h1,h2,h3,h4,h5,h6 { margin: 24px 0 12px; line-height: 1.25; }
    h1 { font-size: 2em; border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }
    h2 { font-size: 1.5em; border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }
    p { margin: 0 0 12px; }
    a { color: #0969da; text-decoration: none; }
    a:hover { text-decoration: underline; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; background: #f6f8fa; padding: 0.15em 0.3em; border-radius: 4px; }
    pre { background: #0b1021; color: #e6edf3; padding: 12px 14px; border-radius: 6px; overflow-x: auto; }
    pre code { background: transparent; padding: 0; color: inherit; }
    blockquote { margin: 0 0 12px; padding: 0 1em; color: #57606a; border-left: 0.25em solid #d0d7de; }
    ul,ol { margin: 0 0 12px 1.2em; }
    li { margin: 0.25em 0; }
    table { border-collapse: collapse; margin: 0 0 12px; width: 100%; }
    th, td { border: 1px solid #d0d7de; padding: 6px 10px; vertical-align: top; }
    th { background: #f6f8fa; }
    img { max-width: 100%; height: auto; }
    hr { border: 0; border-top: 1px solid #d0d7de; margin: 20px 0; }
    """


def _escape_html(text: str) -> str:
    return htmllib.escape(text, quote=False)


def _md_fallback_to_html(md: str) -> str:
    # 仅覆盖本脚本产出的常见子集：标题、段落、列表、引用、代码块、图片、链接、表格。
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    html_parts: List[str] = []

    in_pre = False
    pre_lang = ""
    pre_buf: List[str] = []

    in_ul = False
    in_ol = False
    in_blockquote = False

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            html_parts.append("</ul>")
            in_ul = False
        if in_ol:
            html_parts.append("</ol>")
            in_ol = False

    def close_blockquote() -> None:
        nonlocal in_blockquote
        if in_blockquote:
            html_parts.append("</blockquote>")
            in_blockquote = False

    def flush_pre() -> None:
        nonlocal in_pre, pre_lang, pre_buf
        if not in_pre:
            return
        code = "\n".join(pre_buf)
        cls = f' class="language-{_escape_html(pre_lang)}"' if pre_lang else ""
        html_parts.append(f"<pre><code{cls}>{_escape_html(code)}</code></pre>")
        in_pre = False
        pre_lang = ""
        pre_buf = []

    def render_inlines(text: str) -> str:
        br_token = "\0__BR__\0"
        raw = re.sub(r"<br\s*/?>", br_token, text, flags=re.IGNORECASE)

        # 图片：![alt](src)
        def img_repl(m: re.Match[str]) -> str:
            alt = _escape_html(m.group(1))
            src = m.group(2).strip()
            return f'<img alt="{alt}" src="{_escape_html(src)}">'

        # 链接：[text](href)
        def link_repl(m: re.Match[str]) -> str:
            label = _escape_html(m.group(1))
            href = m.group(2).strip()
            return f'<a href="{_escape_html(href)}">{label}</a>'

        # 行内 code：`...`
        def code_repl(m: re.Match[str]) -> str:
            return f"<code>{_escape_html(m.group(1))}</code>"

        # 粗体 **...** / 斜体 *...*（简化，避免跨行/嵌套复杂情况）
        out = _escape_html(raw).replace(br_token, "<br/>")
        out = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", img_repl, out)
        out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_repl, out)
        out = re.sub(r"`([^`]+)`", code_repl, out)
        out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
        out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
        return out

    i = 0
    while i < len(lines):
        line = lines[i]

        # 透传复杂表格保留下来的 raw HTML table
        if line.lstrip().startswith("<table"):
            block: List[str] = [line]
            if "</table>" not in line.lower():
                i += 1
                while i < len(lines):
                    block.append(lines[i])
                    if "</table>" in lines[i].lower():
                        break
                    i += 1
            html_parts.append("\n".join(block))
            i += 1
            continue

        fence = re.match(r"^```(\S+)?\s*$", line.strip())
        if fence:
            if in_pre:
                flush_pre()
            else:
                close_lists()
                close_blockquote()
                in_pre = True
                pre_lang = (fence.group(1) or "").strip()
                pre_buf = []
            i += 1
            continue

        if in_pre:
            pre_buf.append(line)
            i += 1
            continue

        if line.strip() == "":
            close_lists()
            close_blockquote()
            i += 1
            continue

        # 表格（pipe table）
        if "|" in line and line.strip().startswith("|"):
            # 收集连续的 |...| 行
            table_lines: List[str] = []
            while i < len(lines) and lines[i].strip().startswith("|") and ("|" in lines[i]):
                if lines[i].strip() == "":
                    break
                table_lines.append(lines[i].strip())
                i += 1
            if len(table_lines) >= 2 and re.match(r"^\|\s*---", table_lines[1]):
                rows: List[List[str]] = []
                for tl in table_lines:
                    parts = [p.strip() for p in tl.strip("|").split("|")]
                    rows.append(parts)
                header = rows[0]
                body = rows[2:] if len(rows) >= 3 else []
                html_parts.append("<table>")
                html_parts.append("<thead><tr>" + "".join(f"<th>{render_inlines(c)}</th>" for c in header) + "</tr></thead>")
                if body:
                    html_parts.append("<tbody>")
                    for r in body:
                        html_parts.append("<tr>" + "".join(f"<td>{render_inlines(c)}</td>" for c in r) + "</tr>")
                    html_parts.append("</tbody>")
                html_parts.append("</table>")
                continue
            # 不是标准表格就按普通行处理

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close_lists()
            close_blockquote()
            level = len(m.group(1))
            html_parts.append(f"<h{level}>{render_inlines(m.group(2).strip())}</h{level}>")
            i += 1
            continue

        # 引用
        if line.lstrip().startswith("> "):
            close_lists()
            if not in_blockquote:
                html_parts.append("<blockquote>")
                in_blockquote = True
            html_parts.append(f"<p>{render_inlines(line.lstrip()[2:])}</p>")
            i += 1
            continue

        # 列表（简化：只支持最常见的 - / 1. 且不做深层嵌套 HTML 结构）
        m_ul = re.match(r"^\s*-\s+(.*)$", line)
        m_ol = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
        if m_ul:
            close_blockquote()
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False
            if not in_ul:
                html_parts.append("<ul>")
                in_ul = True
            html_parts.append(f"<li>{render_inlines(m_ul.group(1).strip())}</li>")
            i += 1
            continue
        if m_ol:
            close_blockquote()
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if not in_ol:
                html_parts.append("<ol>")
                in_ol = True
            html_parts.append(f"<li>{render_inlines(m_ol.group(2).strip())}</li>")
            i += 1
            continue

        # 分割线
        if line.strip() == "---":
            close_lists()
            close_blockquote()
            html_parts.append("<hr>")
            i += 1
            continue

        close_lists()
        close_blockquote()
        html_parts.append(f"<p>{render_inlines(line.strip())}</p>")
        i += 1

    flush_pre()
    close_lists()
    close_blockquote()
    return "\n".join(html_parts)


# 模块级别检测 markdown 库是否可用（只检测一次）
_HAS_MARKDOWN_LIB = False
try:
    import markdown as _markdown_lib  # type: ignore
    _HAS_MARKDOWN_LIB = True
except ImportError:
    _markdown_lib = None  # type: ignore


def markdown_to_html(md_text: str, verbose: bool = False) -> str:
    """
    将 Markdown 文本转换为 HTML。
    
    优先使用 python-markdown 库（如已安装），否则回退到内置简易转换。
    """
    if _HAS_MARKDOWN_LIB and _markdown_lib is not None:
        if verbose:
            print("使用 python-markdown 库进行 Markdown→HTML 转换")
        try:
            html = _markdown_lib.markdown(
                md_text,
                extensions=[
                    "fenced_code",
                    "tables",
                    "sane_lists",
                ],
                output_format="html5",
            )
            return html
        except Exception:
            # markdown 库调用出错时回退到内置实现
            pass
    if verbose:
        print("使用内置 Markdown→HTML 转换（如需更好的渲染效果，可安装 python-markdown：pip install markdown）")
    return _md_fallback_to_html(md_text)


def strip_yaml_frontmatter(md_text: str) -> str:
    text = md_text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        return md_text
    lines = text.split("\n")
    for i in range(1, min(len(lines), 2000)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :]).lstrip("\n")
    return md_text


def generate_pdf_from_markdown(md_path: str, pdf_path: str) -> None:
    browser = _find_pdf_browser()
    if not browser:
        raise RuntimeError("未找到可用于打印 PDF 的浏览器（msedge/chrome）。请安装 Edge/Chrome 或加入 PATH。")

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    body_html = markdown_to_html(md_text, verbose=True)
    html_doc = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>{_markdown_css()}</style>
  </head>
  <body>
    <main class="markdown-body">
      {body_html}
    </main>
  </body>
</html>
"""

    out_dir = os.path.dirname(os.path.abspath(pdf_path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    # HTML 必须与 md 同目录，才能让相对图片路径（assets/xx.png）正确解析。
    md_dir = os.path.dirname(os.path.abspath(md_path)) or "."
    html_tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".html", dir=md_dir, delete=False) as tf:
            tf.write(html_doc)
            html_tmp = tf.name

        url = _path_to_file_uri(html_tmp)

        pdf_abs = os.path.abspath(pdf_path)
        common = [
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            f"--print-to-pdf={pdf_abs}",
            url,
        ]

        variants = [
            ["--headless=new", "--print-to-pdf-no-header", *common],
            ["--headless=new", *common],
            ["--headless", "--print-to-pdf-no-header", *common],
            ["--headless", *common],
        ]

        last_err: Optional[Exception] = None
        last_stderr = ""
        for argv in variants:
            try:
                p = subprocess.run(
                    [browser, *argv],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if p.stderr:
                    # 少数版本会在 stderr 输出警告，但仍成功生成 PDF；不当作失败。
                    last_stderr = p.stderr
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                try:
                    if isinstance(e, subprocess.CalledProcessError) and e.stderr:
                        last_stderr = str(e.stderr)
                except Exception:
                    pass

        if last_err is not None:
            raise RuntimeError(f"浏览器打印 PDF 失败：{last_err}\n{last_stderr}".strip())
    finally:
        if html_tmp and os.path.isfile(html_tmp):
            try:
                os.remove(html_tmp)
            except OSError:
                pass


def _normalize_title(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


def strip_duplicate_h1(md_body: str, title: str, max_scan_lines: int = 80) -> str:
    """
    顶部会写入 "# {title}"；而正文抽取常常包含同名 <h1>。
    这里在正文中扫描前 N 行，删除第一个匹配 title 的 "# ..." 行，避免重复。
    """
    title_n = _normalize_title(title)
    if not title_n:
        return md_body

    lines = md_body.splitlines()
    scan = min(len(lines), max_scan_lines)
    for i in range(scan):
        line = lines[i].strip()
        if not line:
            continue
        if re.fullmatch(r"#{1,6}", line):
            # 空标题
            del lines[i : i + 1]
            break
        if line.startswith("# "):
            if _normalize_title(line[2:]) == title_n:
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                del lines[i:j]
                break
    return "\n".join(lines).lstrip("\n").rstrip() + "\n"


def extract_title(page_html: str) -> Optional[str]:
    # <title>...</title>
    m = re.search(r"<title\b[^>]*>(.*?)</title>", page_html, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    title = re.sub(r"\s+", " ", htmllib.unescape(m.group(1))).strip()
    return title or None


class _H1Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_h1 = False
        self.done = False
        self.buf: List[str] = []

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        if self.done:
            return
        if tag.lower() == "h1":
            self.in_h1 = True

    def handle_endtag(self, tag: str) -> None:
        if self.done:
            return
        if tag.lower() == "h1" and self.in_h1:
            self.in_h1 = False
            self.done = True

    def handle_data(self, data: str) -> None:
        if self.done or (not self.in_h1) or (not data) or data.isspace():
            return
        self.buf.append(data)


def extract_h1(article_html: str) -> Optional[str]:
    parser = _H1Extractor()
    parser.feed(article_html)
    title = re.sub(r"\s+", " ", "".join(parser.buf)).strip()
    return title or None


@dataclass
class ValidationResult:
    image_refs: int
    local_image_refs: int
    asset_files: int
    missing_files: List[str]


def validate_markdown(md_path: str, assets_dir: str) -> ValidationResult:
    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()

    # 仅校验图片引用：![](...)
    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    refs = [r.strip() for r in refs]
    local_refs = [r for r in refs if not re.match(r"^[a-z]+://", r, re.IGNORECASE)]

    missing: List[str] = []
    for r in local_refs:
        # 支持相对路径
        if os.path.isabs(r) or re.match(r"^[A-Za-z]:[\\/]", r):
            p = os.path.normpath(r)
        else:
            p = os.path.normpath(os.path.join(os.path.dirname(md_path), r))
        if not os.path.exists(p):
            missing.append(r)

    asset_files = 0
    if os.path.isdir(assets_dir):
        asset_files = len([f for f in os.listdir(assets_dir) if os.path.isfile(os.path.join(assets_dir, f))])

    return ValidationResult(
        image_refs=len(refs),
        local_image_refs=len(local_refs),
        asset_files=asset_files,
        missing_files=missing,
    )


def fetch_html(
    session: requests.Session,
    url: str,
    timeout_s: int,
    retries: int,
) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(
                url,
                timeout=timeout_s,
                stream=True,
                headers={
                    "Connection": "close",
                    "Accept-Encoding": "identity",
                },
            )
            r.raise_for_status()
            content = b"".join(r.iter_content(chunk_size=1024 * 128))
            encoding = r.encoding or "utf-8"
            return content.decode(encoding, errors="replace")
        except Exception as e:  # noqa: BLE001 - CLI tool wants retries on network errors
            last_err = e
            if attempt >= retries:
                raise
            time.sleep(min(3.0, 0.6 * attempt))
    raise last_err or RuntimeError("fetch failed")


def _parse_cookies_file(filepath: str) -> Dict[str, str]:
    """解析 Netscape 格式的 cookies.txt 文件。"""
    cookies: Dict[str, str] = {}
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                # Netscape 格式: domain, flag, path, secure, expiry, name, value
                name, value = parts[5], parts[6]
                cookies[name] = value
    return cookies


def _parse_cookie_string(cookie_str: str) -> Dict[str, str]:
    """解析 Cookie 字符串，如 'session=abc; token=xyz'。"""
    cookies: Dict[str, str] = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            name, value = part.split("=", 1)
            cookies[name.strip()] = value.strip()
    return cookies


def _apply_header_lines(headers: Dict[str, str], header_lines: Sequence[str]) -> None:
    for h in header_lines:
        if not h:
            continue
        if ":" not in h:
            raise ValueError(f"--header 格式应为 'Key: Value'，收到：{h!r}")
        k, v = h.split(":", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            raise ValueError(f"--header Key 不能为空：{h!r}")
        headers[k] = v


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="抓取网页正文与图片，保存为 Markdown + assets。")
    ap.add_argument("url", help="要抓取的网页 URL")
    ap.add_argument("--out", help="输出 md 文件名（默认根据 URL 自动生成）")
    ap.add_argument("--assets-dir", help="图片目录名（默认 <out>.assets）")
    ap.add_argument("--title", help="Markdown 顶部标题（默认从 <title> 提取）")
    ap.add_argument("--with-pdf", action="store_true", help="同时生成同名 PDF（需要本机 Edge/Chrome）")
    ap.add_argument("--timeout", type=int, default=60, help="请求超时（秒），默认 60")
    ap.add_argument("--retries", type=int, default=3, help="网络重试次数，默认 3")
    ap.add_argument("--best-effort-images", action="store_true", help="图片下载失败时仅警告并跳过（默认失败即退出）")
    ap.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的 md 文件")
    ap.add_argument("--validate", action="store_true", help="生成后执行校验并输出结果")
    # 新增：Frontmatter 支持
    ap.add_argument("--frontmatter", action="store_true", default=True,
                    help="生成 YAML Frontmatter 元数据头（默认启用）")
    ap.add_argument("--no-frontmatter", action="store_false", dest="frontmatter",
                    help="禁用 YAML Frontmatter")
    ap.add_argument("--tags", help="Frontmatter 中的标签，逗号分隔，如 'tech,ai,tutorial'")
    # 新增：Cookie/Header 支持
    ap.add_argument("--cookie", help="Cookie 字符串，如 'session=abc; token=xyz'")
    ap.add_argument("--cookies-file", help="Netscape 格式的 cookies.txt 文件路径")
    ap.add_argument("--headers", help="自定义请求头，JSON 格式，如 '{\"Authorization\": \"Bearer xxx\"}'")
    ap.add_argument("--header", action="append", default=[], help="追加请求头（可重复），如 'Authorization: Bearer xxx'")
    # 新增：UA 可配置
    ap.add_argument("--ua-preset", choices=sorted(UA_PRESETS.keys()), default="chrome-win", help="User-Agent 预设（默认 chrome-win）")
    ap.add_argument("--user-agent", "--ua", dest="user_agent", help="自定义 User-Agent（优先于 --ua-preset）")
    # 新增：复杂表格保留 HTML
    ap.add_argument("--keep-html", action="store_true",
                    help="对复杂表格（含 colspan/rowspan）保留原始 HTML 而非强转 Markdown")
    # 新增：手动指定正文区域
    ap.add_argument("--target-id", help="手动指定正文容器 id（如 content / post-content），优先级高于自动抽取")
    ap.add_argument("--target-class", help="手动指定正文容器 class（如 post-body），优先级高于自动抽取")
    # 新增：SPA 页面提示
    ap.add_argument("--spa-warn-len", type=int, default=500, help="正文文本长度低于该值时提示可能为 SPA 动态渲染，默认 500；设为 0 可关闭")
    args = ap.parse_args(argv)

    url = args.url
    base = args.out or (_default_basename(url) + ".md")
    out_md = base
    # 检查输出文件路径长度
    md_dir = os.path.dirname(out_md) or "."
    out_md_name = os.path.basename(out_md)
    out_md_name = _safe_path_length(md_dir, out_md_name)
    out_md = os.path.join(md_dir, out_md_name) if md_dir != "." else out_md_name
    assets_dir = args.assets_dir or (os.path.splitext(out_md)[0] + ".assets")
    map_json = out_md + ".assets.json"

    if os.path.exists(out_md) and not args.overwrite:
        print(f"文件已存在：{out_md}（如需覆盖请加 --overwrite）", file=sys.stderr)
        return 2

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": _resolve_user_agent(args.user_agent, args.ua_preset),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }
    )
    session.headers.setdefault("Referer", url)

    # 处理 Cookie
    if args.cookies_file:
        try:
            cookies = _parse_cookies_file(args.cookies_file)
            session.cookies.update(cookies)
            print(f"已加载 Cookie 文件：{args.cookies_file}（{len(cookies)} 个 cookie）")
        except Exception as e:
            print(f"警告：无法解析 cookies 文件：{e}", file=sys.stderr)
    if args.cookie:
        cookies = _parse_cookie_string(args.cookie)
        session.cookies.update(cookies)
        print(f"已加载 Cookie 字符串（{len(cookies)} 个 cookie）")

    # 处理自定义 Header
    if args.headers:
        try:
            custom_headers = json.loads(args.headers)
            session.headers.update(custom_headers)
            print(f"已加载自定义 Header（{len(custom_headers)} 个）")
        except json.JSONDecodeError as e:
            print(f"警告：无法解析 headers JSON：{e}", file=sys.stderr)

    if args.header:
        try:
            _apply_header_lines(session.headers, args.header)
            print(f"已加载追加 Header（{len(args.header)} 个）")
        except Exception as e:  # noqa: BLE001
            print(f"警告：无法解析 --header：{e}", file=sys.stderr)

    print(f"下载页面：{url}")
    page_html = fetch_html(session=session, url=url, timeout_s=args.timeout, retries=args.retries)

    if args.target_id or args.target_class:
        article_html = extract_target_html(page_html, target_id=args.target_id, target_class=args.target_class) or ""
        if not article_html:
            print("警告：未找到指定的目标区域（--target-id/--target-class），将回退到自动抽取。", file=sys.stderr)
            article_html = extract_main_html(page_html)
    else:
        article_html = extract_main_html(page_html)

    if args.spa_warn_len and html_text_len(article_html) < args.spa_warn_len:
        print(
            f"警告：抽取到的正文内容较短（<{args.spa_warn_len} 字符），该页面可能为 SPA 动态渲染；"
            "如内容为空/不完整，可尝试：1) 使用 --target-id/--target-class 指定正文区域；"
            "2) 等待页面完整加载后保存 HTML 再处理；3) 使用浏览器开发者工具获取渲染后的 HTML。",
            file=sys.stderr,
        )

    collector = ImageURLCollector(base_url=url)
    collector.feed(article_html)
    image_urls = uniq_preserve_order(collector.image_urls)

    print(f"发现图片：{len(image_urls)} 张，开始下载到：{assets_dir}")
    url_to_local = download_images(
        session=session,
        image_urls=image_urls,
        assets_dir=assets_dir,
        md_dir=md_dir,
        timeout_s=args.timeout,
        retries=args.retries,
        best_effort=bool(args.best_effort_images),
    )

    title = args.title or extract_h1(article_html) or extract_title(page_html) or "Untitled"
    md_body = html_to_markdown(
        article_html=article_html,
        base_url=url,
        url_to_local=url_to_local,
        keep_html=args.keep_html,
    )
    md_body = strip_duplicate_h1(md_body, title)

    # 解析 tags 参数
    tags: Optional[List[str]] = None
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    with open(out_md, "w", encoding="utf-8") as f:
        if args.frontmatter:
            f.write(generate_frontmatter(title, url, tags))
        # 保持正文可读性：无论是否启用 frontmatter，都写入可见标题与来源行。
        f.write(f"# {title}\n\n")
        f.write(f"- Source: {url}\n\n")
        f.write(md_body)

    with open(map_json, "w", encoding="utf-8") as f:
        json.dump(url_to_local, f, ensure_ascii=False, indent=2)

    print(f"已生成：{out_md}")
    print(f"图片目录：{assets_dir}")
    print(f"映射文件：{map_json}")

    if args.with_pdf:
        out_pdf = os.path.splitext(out_md)[0] + ".pdf"
        if os.path.exists(out_pdf) and (not args.overwrite):
            print(f"PDF 已存在，跳过：{out_pdf}（如需覆盖请加 --overwrite）", file=sys.stderr)
        else:
            print(f"生成 PDF：{out_pdf}")
            if args.frontmatter:
                # md 文件保留 frontmatter；但 PDF 渲染时剥离元数据块，并补一个可见标题/来源行。
                pdf_md = f"# {title}\n\n- Source: {url}\n\n{md_body}"
                md_dir_abs = os.path.dirname(os.path.abspath(out_md)) or "."
                tmp = None
                try:
                    with tempfile.NamedTemporaryFile(
                        "w",
                        encoding="utf-8",
                        suffix=".no_frontmatter.md",
                        dir=md_dir_abs,
                        delete=False,
                    ) as tf:
                        tf.write(strip_yaml_frontmatter(pdf_md))
                        tmp = tf.name
                    generate_pdf_from_markdown(md_path=tmp, pdf_path=out_pdf)
                finally:
                    if tmp and os.path.isfile(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
            else:
                generate_pdf_from_markdown(md_path=out_md, pdf_path=out_pdf)

    if args.validate:
        result = validate_markdown(out_md, assets_dir)
        print("\n校验结果：")
        print(f"- 图片引用数（总）：{result.image_refs}")
        print(f"- 图片引用数（本地）：{result.local_image_refs}")
        print(f"- assets 文件数：{result.asset_files}")
        if result.missing_files:
            print("- 缺失文件：")
            for m in result.missing_files:
                print(f"  - {m}")
            return 3
        else:
            print("- 缺失文件：0")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
