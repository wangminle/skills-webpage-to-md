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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Callable
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
            elif tag == "img" and self.in_cell:
                # 表格单元格内的图片
                src = (
                    attrs.get("src")
                    or attrs.get("data-src")
                    or attrs.get("data-original")
                    or attrs.get("data-lazy-src")
                )
                if (not src) and attrs.get("srcset"):
                    src = attrs["srcset"].split(",")[0].strip().split(" ")[0]
                if src:
                    img_url = urljoin(self.base_url, htmllib.unescape(src))
                    if not is_probable_icon(img_url):
                        alt = (attrs.get("alt") or "").strip()
                        # 清理 alt 中的方括号，避免生成 ![[xxx]] 这种非标准语法
                        alt = alt.replace("[", "").replace("]", "")
                        local = self.url_to_local.get(img_url, img_url)
                        self.cell_buf.append(f"![{alt}]({local})")
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
            # 清理 alt 中的方括号，避免生成 ![[xxx]] 这种非标准语法
            alt = alt.replace("[", "").replace("]", "")
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


# ============================================================================
# 批量 URL 处理功能
# ============================================================================


@dataclass
class BatchPageResult:
    """单个页面的处理结果"""
    url: str
    title: str
    md_content: str
    success: bool
    error: Optional[str] = None
    order: int = 0  # 用于保持原始顺序
    image_urls: List[str] = field(default_factory=list)  # 收集到的图片 URL


@dataclass
class BatchConfig:
    """批量处理配置"""
    max_workers: int = 3
    delay: float = 1.0
    skip_errors: bool = False
    timeout: int = 60
    retries: int = 3
    best_effort_images: bool = True
    keep_html: bool = False
    target_id: Optional[str] = None
    target_class: Optional[str] = None
    clean_wiki_noise: bool = False  # 清理 Wiki 系统噪音（编辑按钮、导航链接等）
    download_images: bool = False  # 是否下载图片到本地
    wechat: bool = False  # 微信公众号文章模式


# ============================================================================
# 微信公众号文章支持
# ============================================================================


def is_wechat_article_url(url: str) -> bool:
    """
    检测 URL 是否为微信公众号文章链接
    
    支持的格式：
    - https://mp.weixin.qq.com/s/xxx
    - https://mp.weixin.qq.com/s?__biz=xxx
    - http://mp.weixin.qq.com/s/xxx
    """
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.netloc in ("mp.weixin.qq.com", "weixin.qq.com")


def is_wechat_article_html(html: str) -> bool:
    """
    检测 HTML 内容是否具有微信公众号文章特征
    
    检测以下特征：
    - 包含 rich_media_content class
    - 包含 js_article_data
    - 包含微信特有的 meta 标签
    """
    if not html:
        return False
    
    # 检测微信公众号特有的 class 和标识
    wechat_markers = [
        'class="rich_media_content"',
        "class='rich_media_content'",
        'id="js_article"',
        'data-mptype="article"',
        'var biz =',
        '__biz',
        'mp.weixin.qq.com',
    ]
    
    html_lower = html.lower()
    return any(marker.lower() in html_lower for marker in wechat_markers)


def extract_wechat_title(html: str) -> Optional[str]:
    """
    从微信公众号 HTML 中提取文章标题
    
    微信公众号标题通常在以下位置：
    - <h1 class="rich_media_title">标题</h1>
    - <meta property="og:title" content="标题">
    - <title>标题</title>
    """
    if not html:
        return None
    
    # 方法1：从 rich_media_title 提取
    m = re.search(
        r'<h1[^>]*class=["\'][^"\']*rich_media_title[^"\']*["\'][^>]*>(.*?)</h1>',
        html,
        re.IGNORECASE | re.DOTALL
    )
    if m:
        title = re.sub(r'<[^>]+>', '', m.group(1))  # 移除内部标签
        title = re.sub(r'\s+', ' ', htmllib.unescape(title)).strip()
        if title:
            return title
    
    # 方法2：从 og:title meta 标签提取
    m = re.search(
        r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE
    )
    if m:
        title = htmllib.unescape(m.group(1)).strip()
        if title:
            return title
    
    # 方法3：从 twitter:title meta 标签提取
    m = re.search(
        r'<meta[^>]*name=["\']twitter:title["\'][^>]*content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE
    )
    if m:
        title = htmllib.unescape(m.group(1)).strip()
        if title:
            return title
    
    return None


def clean_wechat_noise(md_content: str) -> str:
    """
    清理微信公众号文章中的噪音内容，包括：
    - 点赞、在看、分享等交互按钮文字
    - 小程序卡片提示
    - 扫码关注提示
    - 阅读原文链接噪音
    - 其他微信特有的 UI 元素
    
    Args:
        md_content: 原始 Markdown 内容
    
    Returns:
        清理后的 Markdown 内容
    """
    result = md_content
    
    # 1. 清理微信交互按钮文字
    # 如：Video Mini Program Like ，轻点两下取消赞 Wow ，轻点两下取消在看
    result = re.sub(
        r'[,，\s]*(?:Video|Mini Program|Like|Wow|Share|Comment|Favorite|听过)\s*[,，]?\s*',
        '',
        result,
        flags=re.IGNORECASE
    )
    result = re.sub(
        r'[,，\s]*轻点两下取消(?:赞|在看)\s*',
        '',
        result
    )
    
    # 2. 清理小程序/扫码提示
    result = re.sub(
        r'(?:Scan to Follow|Scan with Weixin to\s*use this Mini Program|微信扫一扫可打开此内容.*?使用完整服务)\s*',
        '',
        result,
        flags=re.IGNORECASE | re.DOTALL
    )
    
    # 3. 清理 Cancel/Allow 按钮文字
    result = re.sub(
        r'\[(?:Cancel|Allow|Got It)\]\(javascript:[^)]*\)\s*',
        '',
        result,
        flags=re.IGNORECASE
    )
    
    # 4. 清理 javascript:void(0) 链接
    result = re.sub(
        r'\[([^\]]*)\]\(javascript:(?:void\(0\)|;)\)\s*',
        r'\1',
        result,
        flags=re.IGNORECASE
    )
    
    # 5. 清理"继续滑动看下一个"等提示
    result = re.sub(
        r'(?:继续滑动看下一个|向上滑动看下一个|预览时标签不可点)\s*',
        '',
        result
    )
    
    # 6. 清理"在小说阅读器中沉浸阅读"等提示
    result = re.sub(
        r'(?:\*{2,}\s*)?在小说阅读器中沉浸阅读\s*',
        '',
        result
    )
    
    # 7. 清理微信特有的符号噪音行
    # 如：: ， ， ， ， ， ， ， ， ， ， ， ，.
    result = re.sub(
        r'^[\s:,，。\.]+$',
        '',
        result,
        flags=re.MULTILINE
    )
    
    # 8. 清理"作者头像"等图片说明
    result = re.sub(
        r'!\[作者头像\]\([^)]+\)',
        '',
        result
    )
    
    # 9. 清理文末连续的交互元素残留
    # 匹配文末可能出现的多余空白和符号
    result = re.sub(
        r'\n[\s:,，。\.\*]*$',
        '',
        result
    )
    
    # 10. 清理连续的空行（清理后可能产生多余空行）
    result = re.sub(r'\n{3,}', '\n\n', result)
    
    # 11. 清理行首行尾多余空格
    result = re.sub(r'\n[ \t]+\n', '\n\n', result)
    
    return result.strip()


def clean_wiki_noise(md_content: str) -> str:
    """
    清理 Wiki 系统产生的噪音内容，包括：
    - PukiWiki/MediaWiki 编辑图标和链接
    - 返回顶部导航链接
    - 标题中的锚点链接
    - 其他常见 Wiki UI 元素
    
    Args:
        md_content: 原始 Markdown 内容
    
    Returns:
        清理后的 Markdown 内容
    """
    result = md_content
    
    # 1. 清理编辑图标图片：![Edit](xxx/paraedit.png) 或类似的编辑图标
    # 匹配各种编辑图标：paraedit.png, edit.png, pencil.png 等
    result = re.sub(
        r'!\[(?:Edit|edit|編集|编辑)?\]\([^)]*(?:paraedit|edit|pencil|secedit)[^)]*\)\s*\n?',
        '',
        result,
        flags=re.IGNORECASE
    )
    
    # 2. 清理编辑链接：[https://xxx/cmd=secedit...](xxx) 或 [编辑](xxx?cmd=edit...)
    # 这种格式是链接文本就是 URL 的情况
    result = re.sub(
        r'\[https?://[^\]]*(?:cmd=(?:sec)?edit|action=edit)[^\]]*\]\([^)]+\)\s*\n?',
        '',
        result,
        flags=re.IGNORECASE
    )
    # 普通编辑链接
    result = re.sub(
        r'\[(?:編集|编辑|Edit|edit)\]\([^)]*(?:cmd=(?:sec)?edit|action=edit)[^)]*\)\s*\n?',
        '',
        result,
        flags=re.IGNORECASE
    )
    
    # 3. 清理返回顶部链接：[↑](xxx#navigator) 或 [↑](xxx#top)
    result = re.sub(
        r'\[↑\]\([^)]*#(?:navigator|top|head|pagetop)[^)]*\)\s*\n?',
        '',
        result,
        flags=re.IGNORECASE
    )
    
    # 4. 清理标题中的锚点链接：## 标题 [†](xxx#anchor) 或 [¶](xxx)
    # 保留标题文本，只移除锚点链接部分
    result = re.sub(
        r'(\#{1,6}\s+[^\n\[]+)\s*\[(?:†|¶|#)\]\([^)]+\)',
        r'\1',
        result
    )
    
    # 5. 清理独立的锚点符号链接（不在标题中的）
    result = re.sub(
        r'\s*\[(?:†|¶)\]\([^)]+\)',
        '',
        result
    )
    
    # 5.5. 清理评论区编辑链接：[?](xxx?cmd=edit...) 或类似的问号链接
    result = re.sub(
        r'\[\?\]\([^)]*(?:cmd=edit|action=edit)[^)]*\)',
        '',
        result,
        flags=re.IGNORECASE
    )
    
    # 6. 清理 PukiWiki 特有的导航/工具栏链接块
    # 如：[ [トップ](xxx) ] 这种格式
    result = re.sub(
        r'\[\s*\[[^\]]+\]\([^)]+\)\s*\]\s*',
        '',
        result
    )
    
    # 7. 清理连续的空行（清理后可能产生多余空行）
    result = re.sub(r'\n{3,}', '\n\n', result)
    
    # 8. 清理行首多余空格（某些清理后可能留下）
    result = re.sub(r'\n[ \t]+\n', '\n\n', result)
    
    return result.strip()


class LinkExtractor(HTMLParser):
    """从 HTML 中提取链接"""
    
    def __init__(self, base_url: str, pattern: Optional[str] = None, same_domain: bool = True):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.base_domain = urlparse(base_url).netloc
        self.pattern = re.compile(pattern) if pattern else None
        self.same_domain = same_domain
        self.links: List[Tuple[str, str]] = []  # (url, text)
        self._in_a = False
        self._current_href: Optional[str] = None
        self._current_text: List[str] = []
    
    def handle_starttag(self, tag: str, attrs_list: Sequence[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            attrs = dict(attrs_list)
            href = attrs.get("href")
            if href:
                self._in_a = True
                self._current_href = href
                self._current_text = []
    
    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._in_a:
            if self._current_href:
                full_url = urljoin(self.base_url, self._current_href)
                text = "".join(self._current_text).strip()
                
                # 检查域名
                if self.same_domain:
                    link_domain = urlparse(full_url).netloc
                    if link_domain != self.base_domain:
                        self._in_a = False
                        self._current_href = None
                        self._current_text = []
                        return
                
                # 检查模式匹配
                if self.pattern:
                    if not self.pattern.search(full_url):
                        self._in_a = False
                        self._current_href = None
                        self._current_text = []
                        return
                
                # 跳过锚点链接和编辑链接
                if (self._current_href.startswith("#") or 
                    "cmd=edit" in full_url or 
                    "cmd=secedit" in full_url):
                    self._in_a = False
                    self._current_href = None
                    self._current_text = []
                    return
                
                self.links.append((full_url, text or full_url))
            
            self._in_a = False
            self._current_href = None
            self._current_text = []
    
    def handle_data(self, data: str) -> None:
        if self._in_a and data:
            self._current_text.append(data)


def extract_links_from_html(
    html: str,
    base_url: str,
    pattern: Optional[str] = None,
    same_domain: bool = True
) -> List[Tuple[str, str]]:
    """从 HTML 中提取链接列表"""
    parser = LinkExtractor(base_url, pattern, same_domain)
    parser.feed(html)
    # 去重并保持顺序
    seen = set()
    unique_links = []
    for url, text in parser.links:
        if url not in seen:
            seen.add(url)
            unique_links.append((url, text))
    return unique_links


def read_urls_file(filepath: str) -> List[Tuple[str, Optional[str]]]:
    """
    读取 URL 列表文件
    
    支持格式：
    - 每行一个 URL
    - # 开头为注释
    - URL | 标题  格式指定自定义标题
    - 空行忽略
    """
    urls: List[Tuple[str, Optional[str]]] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue
            
            # 支持 URL | 标题 格式
            if "|" in line:
                parts = line.split("|", 1)
                url = parts[0].strip()
                title = parts[1].strip() if len(parts) > 1 else None
            else:
                url = line
                title = None
            
            # 验证 URL 格式
            if not url.startswith(("http://", "https://")):
                print(f"警告：第 {line_num} 行不是有效的 URL，已跳过：{url}", file=sys.stderr)
                continue
            
            urls.append((url, title))
    
    return urls


def _make_anchor_id(text: str) -> str:
    """生成 Markdown 锚点 ID"""
    # 转小写，移除特殊字符，空格转连字符
    anchor = text.lower()
    anchor = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", anchor)  # 保留中日韩字符
    anchor = re.sub(r"\s+", "-", anchor)
    anchor = re.sub(r"-+", "-", anchor)
    return anchor.strip("-") or "section"


def process_single_url(
    session: requests.Session,
    url: str,
    config: BatchConfig,
    custom_title: Optional[str] = None,
    order: int = 0,
) -> BatchPageResult:
    """处理单个 URL，返回结果"""
    try:
        # 获取页面
        page_html = fetch_html(
            session=session,
            url=url,
            timeout_s=config.timeout,
            retries=config.retries,
        )
        
        # 微信公众号文章自动检测
        is_wechat = config.wechat
        if not is_wechat and is_wechat_article_url(url):
            is_wechat = True
        elif not is_wechat and is_wechat_article_html(page_html):
            is_wechat = True
        
        # 确定正文提取策略
        target_id = config.target_id
        target_class = config.target_class
        
        # 微信模式下，如果未指定 target，自动使用 rich_media_content
        if is_wechat and not target_id and not target_class:
            target_class = "rich_media_content"
        
        # 提取正文
        if target_id or target_class:
            article_html = extract_target_html(
                page_html, 
                target_id=target_id, 
                target_class=target_class
            ) or ""
            if not article_html:
                article_html = extract_main_html(page_html)
        else:
            article_html = extract_main_html(page_html)
        
        # 提取标题（微信模式下优先使用专用提取函数）
        if custom_title:
            title = custom_title
        elif is_wechat:
            title = extract_wechat_title(page_html) or extract_h1(article_html) or extract_title(page_html) or "Untitled"
        else:
            title = extract_h1(article_html) or extract_title(page_html) or "Untitled"
        
        # 收集图片 URL（如果需要下载图片）
        image_urls: List[str] = []
        if config.download_images:
            collector = ImageURLCollector(base_url=url)
            collector.feed(article_html)
            image_urls = uniq_preserve_order(collector.image_urls)
        
        # 转换为 Markdown（批量模式先不替换图片路径，后续统一处理）
        md_body = html_to_markdown(
            article_html=article_html,
            base_url=url,
            url_to_local={},  # 先不替换图片路径
            keep_html=config.keep_html,
        )
        md_body = strip_duplicate_h1(md_body, title)
        
        # 清理噪音内容
        if is_wechat:
            md_body = clean_wechat_noise(md_body)
        if config.clean_wiki_noise:
            md_body = clean_wiki_noise(md_body)
        
        return BatchPageResult(
            url=url,
            title=title,
            md_content=md_body,
            success=True,
            order=order,
            image_urls=image_urls,
        )
    
    except Exception as e:
        return BatchPageResult(
            url=url,
            title=custom_title or url,
            md_content="",
            success=False,
            error=str(e),
            order=order,
        )


def batch_process_urls(
    session: requests.Session,
    urls: List[Tuple[str, Optional[str]]],
    config: BatchConfig,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[BatchPageResult]:
    """
    批量处理 URL 列表
    
    Args:
        session: requests.Session
        urls: [(url, custom_title), ...]
        config: 批量处理配置
        progress_callback: 进度回调函数 (current, total, url)
    
    Returns:
        处理结果列表
    """
    results: List[BatchPageResult] = []
    total = len(urls)
    lock = threading.Lock()
    last_request_time = [0.0]  # 使用列表以便在闭包中修改
    
    def process_with_delay(args: Tuple[int, str, Optional[str]]) -> BatchPageResult:
        idx, url, custom_title = args
        
        # 控制请求间隔
        with lock:
            now = time.time()
            elapsed = now - last_request_time[0]
            if elapsed < config.delay:
                time.sleep(config.delay - elapsed)
            last_request_time[0] = time.time()
        
        if progress_callback:
            progress_callback(idx + 1, total, url)
        
        return process_single_url(
            session=session,
            url=url,
            config=config,
            custom_title=custom_title,
            order=idx,
        )
    
    # 使用线程池并发处理
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        args_list = [(i, url, title) for i, (url, title) in enumerate(urls)]
        futures = {executor.submit(process_with_delay, args): args for args in args_list}
        
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            
            if not result.success and not config.skip_errors:
                # 取消剩余任务
                for f in futures:
                    f.cancel()
                raise RuntimeError(f"处理失败：{result.url}\n错误：{result.error}")
    
    # 按原始顺序排序
    results.sort(key=lambda r: r.order)
    return results


def batch_download_images(
    session: requests.Session,
    results: List[BatchPageResult],
    assets_dir: str,
    md_dir: str,
    timeout_s: int = 60,
    retries: int = 3,
    best_effort: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[str, str]:
    """
    批量下载所有页面的图片到统一的 assets 目录
    
    Args:
        session: requests.Session
        results: 批量处理结果列表
        assets_dir: 图片保存目录
        md_dir: Markdown 文件所在目录（用于计算相对路径）
        timeout_s: 请求超时
        retries: 重试次数
        best_effort: 失败时是否继续
        progress_callback: 进度回调 (current, total, url)
    
    Returns:
        URL 到本地相对路径的映射字典
    """
    # 收集所有唯一的图片 URL
    all_image_urls: List[str] = []
    seen: set = set()
    for result in results:
        if result.success:
            for url in result.image_urls:
                if url not in seen:
                    all_image_urls.append(url)
                    seen.add(url)
    
    if not all_image_urls:
        return {}
    
    os.makedirs(assets_dir, exist_ok=True)
    url_to_local: Dict[str, str] = {}
    total = len(all_image_urls)
    
    for idx, img_url in enumerate(all_image_urls, start=1):
        if progress_callback:
            progress_callback(idx, total, img_url)
        
        last_err: Optional[Exception] = None
        r: Optional[requests.Response] = None
        content: Optional[bytes] = None
        
        for attempt in range(1, retries + 1):
            try:
                r = session.get(img_url, timeout=timeout_s, stream=True, headers={"Connection": "close"})
                r.raise_for_status()
                content = b"".join(r.iter_content(chunk_size=1024 * 64))
                break
            except Exception as e:
                last_err = e
                if attempt >= retries:
                    break
                time.sleep(min(2.0, 0.4 * attempt))
        
        if content is None or r is None:
            if best_effort:
                print(f"  警告：图片下载失败，已跳过：{img_url[:60]}...", file=sys.stderr)
                continue
            raise last_err or RuntimeError("image download failed")
        
        # 生成本地文件名
        parsed = urlparse(img_url)
        base = os.path.basename(parsed.path.rstrip("/"))
        base = unquote(base) or f"image-{idx}"
        name_root, name_ext = os.path.splitext(base)
        
        # 已知图片扩展名列表
        known_image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".bmp", ".ico"}
        
        # 如果没有扩展名，或者扩展名不是已知图片格式，从 Content-Type 或文件内容检测
        if not name_ext or name_ext.lower() not in known_image_exts:
            detected_ext = (
                ext_from_content_type(r.headers.get("Content-Type") if r else None)
                or sniff_ext(content or b"")
            )
            if detected_ext:
                name_ext = detected_ext
            elif not name_ext:
                name_ext = ".bin"
        
        safe_root = _sanitize_filename_part(name_root)
        filename = f"{idx:03d}-{safe_root}{name_ext}"
        filename = _safe_path_length(assets_dir, filename)
        local_path = os.path.join(assets_dir, filename)
        
        with open(local_path, "wb") as f:
            f.write(content or b"")
        
        # 计算相对路径
        local_abs = os.path.abspath(local_path)
        md_dir_abs = os.path.abspath(md_dir or ".")
        rel = os.path.relpath(local_abs, start=md_dir_abs)
        url_to_local[img_url] = rel.replace("\\", "/")
    
    return url_to_local


def replace_image_urls_in_markdown(md_content: str, url_to_local: Dict[str, str]) -> str:
    """
    替换 Markdown 内容中的图片 URL 为本地路径
    
    Args:
        md_content: Markdown 内容
        url_to_local: URL 到本地路径的映射
    
    Returns:
        替换后的 Markdown 内容
    """
    result = md_content
    for url, local_path in url_to_local.items():
        # 方法1：直接字符串替换（最可靠）
        # 匹配 ](url) 模式，将 url 替换为本地路径
        result = result.replace(f"]({url})", f"]({local_path})")
        
        # 方法2：也替换可能的 URL 编码变体
        from urllib.parse import quote, unquote
        # 尝试替换 URL 编码版本
        encoded_url = quote(url, safe=':/?&=#')
        if encoded_url != url:
            result = result.replace(f"]({encoded_url})", f"]({local_path})")
        # 尝试替换解码版本
        decoded_url = unquote(url)
        if decoded_url != url:
            result = result.replace(f"]({decoded_url})", f"]({local_path})")
    
    return result


def build_url_to_anchor_map(results: List[BatchPageResult]) -> Dict[str, str]:
    """
    构建 URL 到锚点 ID 的映射表
    
    Args:
        results: 批量处理结果列表
    
    Returns:
        URL -> 锚点 ID 的映射字典
    """
    url_to_anchor: Dict[str, str] = {}
    for result in results:
        if result.success:
            anchor = _make_anchor_id(result.title)
            # 添加原始 URL
            url_to_anchor[result.url] = anchor
            # 添加常见的 URL 变体（带/不带端口、编码变体等）
            parsed = urlparse(result.url)
            # 不带端口的版本
            if parsed.port:
                no_port_url = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
                if parsed.query:
                    no_port_url += f"?{parsed.query}"
                url_to_anchor[no_port_url] = anchor
            # 带默认端口的版本
            if parsed.scheme == "https" and not parsed.port:
                with_port = f"{parsed.scheme}://{parsed.hostname}:443{parsed.path}"
                if parsed.query:
                    with_port += f"?{parsed.query}"
                url_to_anchor[with_port] = anchor
            elif parsed.scheme == "http" and not parsed.port:
                with_port = f"{parsed.scheme}://{parsed.hostname}:80{parsed.path}"
                if parsed.query:
                    with_port += f"?{parsed.query}"
                url_to_anchor[with_port] = anchor
    return url_to_anchor


def rewrite_internal_links(md_content: str, url_to_anchor: Dict[str, str]) -> Tuple[str, int]:
    """
    将 Markdown 中的外部链接改写为内部锚点链接
    
    Args:
        md_content: Markdown 内容
        url_to_anchor: URL 到锚点的映射
    
    Returns:
        (改写后的内容, 改写的链接数量)
    """
    if not url_to_anchor:
        return md_content, 0
    
    rewrite_count = 0
    result = md_content
    
    # 匹配 Markdown 链接语法：[text](url)
    # 但不匹配图片语法 ![alt](url)
    link_pattern = re.compile(r'(?<!!)\[([^\]]+)\]\(([^)]+)\)')
    
    def replace_link(match: re.Match) -> str:
        nonlocal rewrite_count
        text = match.group(1)
        url = match.group(2)
        
        # 检查 URL 是否在映射表中
        anchor = url_to_anchor.get(url)
        if anchor:
            rewrite_count += 1
            return f"[{text}](#{anchor})"
        
        # 尝试 URL 解码后匹配
        try:
            decoded_url = unquote(url)
            anchor = url_to_anchor.get(decoded_url)
            if anchor:
                rewrite_count += 1
                return f"[{text}](#{anchor})"
        except Exception:
            pass
        
        return match.group(0)  # 保持原样
    
    result = link_pattern.sub(replace_link, result)
    return result, rewrite_count


def generate_merged_markdown(
    results: List[BatchPageResult],
    include_toc: bool = True,
    main_title: Optional[str] = None,
    source_url: Optional[str] = None,
    rewrite_links: bool = False,
    show_source_summary: bool = True,
) -> str:
    """
    将多个页面结果合并为单个 Markdown 文档
    
    Args:
        results: 处理结果列表
        include_toc: 是否包含目录
        main_title: 文档主标题
        source_url: 来源 URL（用于 frontmatter）
        rewrite_links: 是否将站内链接改写为锚点
        show_source_summary: 是否显示来源信息汇总
    
    Returns:
        合并后的 Markdown 内容
    """
    parts: List[str] = []
    
    # 构建 URL 到锚点的映射（用于链接改写）
    url_to_anchor: Dict[str, str] = {}
    total_rewrite_count = 0
    if rewrite_links:
        url_to_anchor = build_url_to_anchor_map(results)
    
    # 生成 frontmatter
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = main_title or "批量导出文档"
    parts.append("---")
    parts.append(f'title: "{title}"')
    if source_url:
        parts.append(f'source: "{source_url}"')
    parts.append(f'date: "{date_str}"')
    parts.append(f'pages: {len([r for r in results if r.success])}')
    parts.append("---")
    parts.append("")
    
    # 主标题
    parts.append(f"# {title}")
    parts.append("")
    
    # 来源信息汇总（Phase 4）
    if show_source_summary:
        success_results = [r for r in results if r.success]
        if success_results:
            parts.append("## 文档信息")
            parts.append("")
            parts.append(f"- **导出时间**：{date_str}")
            parts.append(f"- **页面数量**：{len(success_results)} 页")
            if source_url:
                parts.append(f"- **来源站点**：{source_url}")
            else:
                # 从第一个 URL 提取域名
                first_url = success_results[0].url
                parsed = urlparse(first_url)
                parts.append(f"- **来源站点**：{parsed.scheme}://{parsed.netloc}")
            parts.append("")
            parts.append("---")
            parts.append("")
    
    # 生成目录
    if include_toc:
        parts.append("## 目录")
        parts.append("")
        for i, result in enumerate(results, 1):
            if result.success:
                anchor = _make_anchor_id(result.title)
                parts.append(f"{i}. [{result.title}](#{anchor})")
            else:
                parts.append(f"{i}. ~~{result.title}~~ (获取失败)")
        parts.append("")
        parts.append("---")
        parts.append("")
    
    # 添加各页面内容
    for result in results:
        if not result.success:
            parts.append(f"## {result.title}")
            parts.append("")
            parts.append(f"> ⚠️ 获取失败：{result.error}")
            parts.append("")
            parts.append(f"- 原始链接：{result.url}")
            parts.append("")
            parts.append("---")
            parts.append("")
            continue
        
        # 页面标题（使用 ## 作为二级标题）
        anchor = _make_anchor_id(result.title)
        parts.append(f'<a id="{anchor}"></a>')
        parts.append("")
        parts.append(f"## {result.title}")
        parts.append("")
        parts.append(f"- 来源：{result.url}")
        parts.append("")
        
        # 页面内容（调整标题级别：# -> ###, ## -> ####, etc.）
        content = result.md_content
        # 将原内容中的标题级别下调两级
        content = re.sub(r"^(#{1,4})\s+", lambda m: "#" * (len(m.group(1)) + 2) + " ", content, flags=re.MULTILINE)
        
        # 站内链接改写（Phase 3）
        if rewrite_links and url_to_anchor:
            content, count = rewrite_internal_links(content, url_to_anchor)
            total_rewrite_count += count
        
        parts.append(content)
        parts.append("")
        parts.append("---")
        parts.append("")
    
    # 如果启用了链接改写，在文档末尾添加统计信息
    if rewrite_links and total_rewrite_count > 0:
        parts.append("")
        parts.append(f"<!-- 站内链接改写：共 {total_rewrite_count} 处 -->")
    
    return "\n".join(parts)


def generate_index_markdown(
    results: List[BatchPageResult],
    output_dir: str,
    main_title: Optional[str] = None,
) -> str:
    """生成索引文件内容"""
    parts: List[str] = []
    
    title = main_title or "批量导出索引"
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    parts.append(f"# {title}")
    parts.append("")
    parts.append(f"生成时间：{date_str}")
    parts.append("")
    parts.append(f"共 {len(results)} 个页面，成功 {len([r for r in results if r.success])} 个")
    parts.append("")
    parts.append("## 页面列表")
    parts.append("")
    
    for i, result in enumerate(results, 1):
        if result.success:
            filename = _sanitize_filename_part(result.title)[:50] + ".md"
            parts.append(f"{i}. [{result.title}](./{filename})")
        else:
            parts.append(f"{i}. ~~{result.title}~~ (获取失败: {result.error})")
    
    parts.append("")
    return "\n".join(parts)


def batch_save_individual(
    results: List[BatchPageResult],
    output_dir: str,
    include_frontmatter: bool = True,
) -> List[str]:
    """
    将结果保存为独立的 MD 文件
    
    Returns:
        生成的文件路径列表
    """
    os.makedirs(output_dir, exist_ok=True)
    saved_files: List[str] = []
    
    for result in results:
        if not result.success:
            continue
        
        # 生成文件名
        filename = _sanitize_filename_part(result.title)[:50]
        filename = _safe_path_length(output_dir, filename + ".md")
        filepath = os.path.join(output_dir, filename)
        
        # 避免重名
        base, ext = os.path.splitext(filepath)
        counter = 1
        while os.path.exists(filepath):
            filepath = f"{base}_{counter}{ext}"
            counter += 1
        
        # 写入文件
        with open(filepath, "w", encoding="utf-8") as f:
            if include_frontmatter:
                f.write(generate_frontmatter(result.title, result.url))
            f.write(f"# {result.title}\n\n")
            f.write(f"- Source: {result.url}\n\n")
            f.write(result.md_content)
        
        saved_files.append(filepath)
    
    return saved_files


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


def _create_session(args: argparse.Namespace, referer_url: Optional[str] = None) -> requests.Session:
    """创建并配置 requests.Session"""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": _resolve_user_agent(args.user_agent, args.ua_preset),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }
    )
    if referer_url:
        session.headers.setdefault("Referer", referer_url)

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
        except Exception as e:
            print(f"警告：无法解析 --header：{e}", file=sys.stderr)

    return session


def _batch_main(args: argparse.Namespace) -> int:
    """批量处理模式的主函数"""
    
    # 创建 Session
    session = _create_session(args, referer_url=args.url)
    
    # 收集要处理的 URL 列表
    urls: List[Tuple[str, Optional[str]]] = []
    source_url: Optional[str] = None
    
    if args.urls_file:
        # 从文件读取 URL
        if not os.path.isfile(args.urls_file):
            print(f"错误：URL 列表文件不存在：{args.urls_file}", file=sys.stderr)
            return 1
        urls = read_urls_file(args.urls_file)
        print(f"从文件加载了 {len(urls)} 个 URL")
    
    if args.crawl:
        # 从索引页爬取链接
        if not args.url:
            print("错误：爬取模式需要提供索引页 URL", file=sys.stderr)
            return 1
        
        source_url = args.url
        print(f"正在从索引页提取链接：{args.url}")
        
        try:
            index_html = fetch_html(
                session=session,
                url=args.url,
                timeout_s=args.timeout,
                retries=args.retries,
            )
        except Exception as e:
            print(f"错误：无法获取索引页：{e}", file=sys.stderr)
            return 1
        
        # 提取链接
        links = extract_links_from_html(
            html=index_html,
            base_url=args.url,
            pattern=args.crawl_pattern,
            same_domain=args.same_domain,
        )
        
        # 添加到 URL 列表（避免重复）
        existing_urls = {u for u, _ in urls}
        for link_url, link_text in links:
            if link_url not in existing_urls:
                urls.append((link_url, link_text))
                existing_urls.add(link_url)
        
        print(f"从索引页提取了 {len(links)} 个链接，总计 {len(urls)} 个 URL")
    
    if not urls:
        print("错误：没有要处理的 URL", file=sys.stderr)
        return 1
    
    # 显示 URL 列表预览
    print("\n即将处理的 URL 列表：")
    for i, (url, title) in enumerate(urls[:10], 1):
        display = f"  {i}. {title or url}"
        if len(display) > 80:
            display = display[:77] + "..."
        print(display)
    if len(urls) > 10:
        print(f"  ... 共 {len(urls)} 个")
    print()
    
    # 配置批量处理
    config = BatchConfig(
        max_workers=args.max_workers,
        delay=args.delay,
        skip_errors=args.skip_errors,
        timeout=args.timeout,
        retries=args.retries,
        best_effort_images=True,
        keep_html=args.keep_html,
        target_id=args.target_id,
        target_class=args.target_class,
        clean_wiki_noise=args.clean_wiki_noise,
        download_images=args.download_images,
        wechat=args.wechat,
    )
    
    # 进度回调
    def progress_callback(current: int, total: int, url: str) -> None:
        short_url = url if len(url) <= 50 else url[:47] + "..."
        print(f"[{current}/{total}] 处理中：{short_url}")
    
    # 执行批量处理
    print(f"开始批量处理（并发数：{config.max_workers}，间隔：{config.delay}s）...\n")
    
    try:
        results = batch_process_urls(
            session=session,
            urls=urls,
            config=config,
            progress_callback=progress_callback,
        )
    except RuntimeError as e:
        print(f"\n错误：{e}", file=sys.stderr)
        return 1
    
    # 统计结果
    success_count = len([r for r in results if r.success])
    fail_count = len(results) - success_count
    print(f"\n处理完成：成功 {success_count}，失败 {fail_count}")
    
    # 下载图片（如果启用）
    url_to_local: Dict[str, str] = {}
    if args.download_images:
        # 统计图片数量
        total_images = sum(len(r.image_urls) for r in results if r.success)
        unique_images = len(set(url for r in results if r.success for url in r.image_urls))
        
        if unique_images > 0:
            # 确定 assets 目录
            if args.merge:
                output_file = args.merge_output or "merged.md"
                assets_dir = os.path.splitext(output_file)[0] + ".assets"
                md_dir = os.path.dirname(output_file) or "."
            else:
                assets_dir = os.path.join(args.output_dir, "assets")
                md_dir = args.output_dir
            
            print(f"\n发现 {unique_images} 张图片（去重后），开始下载到：{assets_dir}")
            
            def img_progress(current: int, total: int, url: str) -> None:
                short_url = url if len(url) <= 50 else url[:47] + "..."
                print(f"  [{current}/{total}] 下载：{short_url}")
            
            url_to_local = batch_download_images(
                session=session,
                results=results,
                assets_dir=assets_dir,
                md_dir=md_dir,
                timeout_s=args.timeout,
                retries=args.retries,
                best_effort=True,
                progress_callback=img_progress,
            )
            
            print(f"  图片下载完成：{len(url_to_local)} 张成功")
            
            # 更新结果中的 Markdown 内容，替换图片 URL
            for result in results:
                if result.success and result.md_content:
                    result.md_content = replace_image_urls_in_markdown(
                        result.md_content, url_to_local
                    )
        else:
            print("\n未发现需要下载的图片")
    
    # 输出结果
    if args.merge:
        # 合并输出模式
        output_file = args.merge_output or "merged.md"
        
        # 检查是否已存在
        if os.path.exists(output_file) and not args.overwrite:
            print(f"文件已存在：{output_file}（如需覆盖请加 --overwrite）", file=sys.stderr)
            return 2
        
        # 来源 URL 优先级：--source-url > 爬取模式的索引页 > None（提取域名）
        final_source_url = args.source_url or source_url
        
        merged_content = generate_merged_markdown(
            results=results,
            include_toc=args.toc,
            main_title=args.merge_title or args.title,
            source_url=final_source_url,
            rewrite_links=args.rewrite_links,
            show_source_summary=not args.no_source_summary,
        )
        
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(merged_content)
        
        print(f"\n已生成合并文档：{output_file}")
        print(f"文档大小：{len(merged_content):,} 字符")
        if url_to_local:
            assets_dir = os.path.splitext(output_file)[0] + ".assets"
            # 清理未引用的图片文件
            if os.path.isdir(assets_dir):
                used_files = set()
                for local_path in url_to_local.values():
                    # 检查文件是否在最终内容中被引用
                    if local_path in merged_content:
                        used_files.add(os.path.basename(local_path))
                
                # 删除未引用的文件
                removed_count = 0
                for filename in os.listdir(assets_dir):
                    if filename not in used_files:
                        file_path = os.path.join(assets_dir, filename)
                        try:
                            os.remove(file_path)
                            removed_count += 1
                        except Exception:
                            pass
                
                actual_count = len([f for f in os.listdir(assets_dir) if os.path.isfile(os.path.join(assets_dir, f))])
                if removed_count > 0:
                    print(f"图片目录：{assets_dir}（{actual_count} 张图片，已清理 {removed_count} 张未引用）")
                else:
                    print(f"图片目录：{assets_dir}（{actual_count} 张图片）")
            else:
                print(f"图片目录：{assets_dir}（{len(url_to_local)} 张图片）")
        
    else:
        # 独立文件输出模式
        os.makedirs(args.output_dir, exist_ok=True)
        
        saved_files = batch_save_individual(
            results=results,
            output_dir=args.output_dir,
            include_frontmatter=args.frontmatter,
        )
        
        # 生成索引文件
        index_content = generate_index_markdown(
            results=results,
            output_dir=args.output_dir,
            main_title=args.merge_title or args.title,
        )
        index_path = os.path.join(args.output_dir, "INDEX.md")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(index_content)
        
        print(f"\n已生成 {len(saved_files)} 个文件到：{args.output_dir}")
        print(f"索引文件：{index_path}")
    
    # 显示失败列表
    if fail_count > 0:
        print("\n失败的 URL：")
        for result in results:
            if not result.success:
                print(f"  - {result.url}")
                print(f"    错误：{result.error}")
    
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="抓取网页正文与图片，保存为 Markdown + assets。支持单页和批量模式。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
批量处理示例：
  # 从文件读取 URL 列表，合并为单个文档
  python grab_web_to_md.py --urls-file urls.txt --merge --merge-output output.md

  # 从索引页爬取链接并批量导出
  python grab_web_to_md.py https://example.com/index --crawl --merge --toc

  # 批量导出为独立文件
  python grab_web_to_md.py --urls-file urls.txt --output-dir ./docs

urls.txt 文件格式：
  # 这是注释
  https://example.com/page1
  https://example.com/page2 | 自定义标题
""",
    )
    ap.add_argument("url", nargs="?", help="要抓取的网页 URL（单页模式必需，批量模式可选）")
    ap.add_argument("--out", help="输出 md 文件名（默认根据 URL 自动生成）")
    ap.add_argument("--assets-dir", help="图片目录名（默认 <out>.assets）")
    ap.add_argument("--title", help="Markdown 顶部标题（默认从 <title> 提取）")
    ap.add_argument("--with-pdf", action="store_true", help="同时生成同名 PDF（需要本机 Edge/Chrome）")
    ap.add_argument("--timeout", type=int, default=60, help="请求超时（秒），默认 60")
    ap.add_argument("--retries", type=int, default=3, help="网络重试次数，默认 3")
    ap.add_argument("--best-effort-images", action="store_true", help="图片下载失败时仅警告并跳过（默认失败即退出）")
    ap.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的 md 文件")
    ap.add_argument("--validate", action="store_true", help="生成后执行校验并输出结果")
    # Frontmatter 支持
    ap.add_argument("--frontmatter", action="store_true", default=True,
                    help="生成 YAML Frontmatter 元数据头（默认启用）")
    ap.add_argument("--no-frontmatter", action="store_false", dest="frontmatter",
                    help="禁用 YAML Frontmatter")
    ap.add_argument("--tags", help="Frontmatter 中的标签，逗号分隔，如 'tech,ai,tutorial'")
    # Cookie/Header 支持
    ap.add_argument("--cookie", help="Cookie 字符串，如 'session=abc; token=xyz'")
    ap.add_argument("--cookies-file", help="Netscape 格式的 cookies.txt 文件路径")
    ap.add_argument("--headers", help="自定义请求头，JSON 格式，如 '{\"Authorization\": \"Bearer xxx\"}'")
    ap.add_argument("--header", action="append", default=[], help="追加请求头（可重复），如 'Authorization: Bearer xxx'")
    # UA 可配置
    ap.add_argument("--ua-preset", choices=sorted(UA_PRESETS.keys()), default="chrome-win", help="User-Agent 预设（默认 chrome-win）")
    ap.add_argument("--user-agent", "--ua", dest="user_agent", help="自定义 User-Agent（优先于 --ua-preset）")
    # 复杂表格保留 HTML
    ap.add_argument("--keep-html", action="store_true",
                    help="对复杂表格（含 colspan/rowspan）保留原始 HTML 而非强转 Markdown")
    # 手动指定正文区域
    ap.add_argument("--target-id", help="手动指定正文容器 id（如 content / post-content），优先级高于自动抽取")
    ap.add_argument("--target-class", help="手动指定正文容器 class（如 post-body），优先级高于自动抽取")
    # SPA 页面提示
    ap.add_argument("--spa-warn-len", type=int, default=500, help="正文文本长度低于该值时提示可能为 SPA 动态渲染，默认 500；设为 0 可关闭")
    # Wiki 噪音清理
    ap.add_argument("--clean-wiki-noise", action="store_true",
                    help="清理 Wiki 系统噪音（编辑按钮、导航链接、返回顶部等），适用于 PukiWiki/MediaWiki 等站点")
    # 微信公众号文章支持
    ap.add_argument("--wechat", action="store_true",
                    help="微信公众号文章模式：自动提取 rich_media_content 正文并清理交互按钮噪音。"
                         "如不指定，脚本会自动检测 mp.weixin.qq.com 链接并启用此模式")
    
    # ========== 批量处理参数 ==========
    batch_group = ap.add_argument_group("批量处理参数")
    batch_group.add_argument("--urls-file", help="从文件读取 URL 列表（每行一个，支持 # 注释和 URL|标题 格式）")
    batch_group.add_argument("--output-dir", default="./batch_output", help="批量输出目录（默认 ./batch_output）")
    batch_group.add_argument("--max-workers", type=int, default=3, help="并发线程数（默认 3，建议不超过 5）")
    batch_group.add_argument("--delay", type=float, default=1.0, help="请求间隔秒数（默认 1.0，避免被封）")
    batch_group.add_argument("--skip-errors", action="store_true", help="跳过失败的 URL 继续处理")
    batch_group.add_argument("--download-images", action="store_true", 
                             help="下载图片到本地 assets 目录（默认不下载，保留原始 URL）")
    
    # 合并输出参数
    merge_group = ap.add_argument_group("合并输出参数")
    merge_group.add_argument("--merge", action="store_true", help="合并所有页面为单个 MD 文件")
    merge_group.add_argument("--merge-output", help="合并输出文件名（默认 merged.md）")
    merge_group.add_argument("--toc", action="store_true", help="在合并文件开头生成目录")
    merge_group.add_argument("--merge-title", help="合并文档的主标题")
    merge_group.add_argument("--source-url", help="来源站点 URL（显示在文档信息中）")
    merge_group.add_argument("--rewrite-links", action="store_true",
                             help="将站内链接改写为文档内锚点（仅合并模式有效）")
    merge_group.add_argument("--no-source-summary", action="store_true",
                             help="不在文档开头显示来源信息汇总")
    
    # 爬取模式参数
    crawl_group = ap.add_argument_group("爬取模式参数")
    crawl_group.add_argument("--crawl", action="store_true", help="从索引页提取链接并批量抓取")
    crawl_group.add_argument("--crawl-pattern", help="链接匹配正则表达式（如 'index\\.php\\?MMR'）")
    crawl_group.add_argument("--same-domain", action="store_true", default=True, help="仅抓取同域名链接（默认启用）")
    crawl_group.add_argument("--no-same-domain", action="store_false", dest="same_domain", help="允许抓取跨域链接")
    
    args = ap.parse_args(argv)
    
    # ========== 批量处理模式 ==========
    is_batch_mode = bool(args.urls_file or args.crawl)
    
    if is_batch_mode:
        return _batch_main(args)
    
    # ========== 单页处理模式（原有逻辑） ==========
    if not args.url:
        ap.error("单页模式必须提供 URL 参数，或使用 --urls-file / --crawl 进入批量模式")

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

    session = _create_session(args, referer_url=url)

    print(f"下载页面：{url}")
    page_html = fetch_html(session=session, url=url, timeout_s=args.timeout, retries=args.retries)

    # 微信公众号文章自动检测
    is_wechat = args.wechat
    if not is_wechat and is_wechat_article_url(url):
        is_wechat = True
        print("检测到微信公众号文章，自动启用微信模式")
    elif not is_wechat and is_wechat_article_html(page_html):
        is_wechat = True
        print("检测到微信公众号文章特征，自动启用微信模式")

    # 确定正文提取策略
    target_id = args.target_id
    target_class = args.target_class
    
    # 微信模式下，如果未指定 target，自动使用 rich_media_content
    if is_wechat and not target_id and not target_class:
        target_class = "rich_media_content"
        print("使用微信正文区域：rich_media_content")

    if target_id or target_class:
        article_html = extract_target_html(page_html, target_id=target_id, target_class=target_class) or ""
        if not article_html:
            print("警告：未找到指定的目标区域，将回退到自动抽取。", file=sys.stderr)
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

    # 提取标题（微信模式下优先使用专用提取函数）
    if args.title:
        title = args.title
    elif is_wechat:
        title = extract_wechat_title(page_html) or extract_h1(article_html) or extract_title(page_html) or "Untitled"
    else:
        title = extract_h1(article_html) or extract_title(page_html) or "Untitled"
    md_body = html_to_markdown(
        article_html=article_html,
        base_url=url,
        url_to_local=url_to_local,
        keep_html=args.keep_html,
    )
    md_body = strip_duplicate_h1(md_body, title)

    # 清理噪音内容
    if is_wechat:
        md_body = clean_wechat_noise(md_body)
        print("已清理微信公众号 UI 噪音")

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
