from __future__ import annotations

import html as htmllib
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import quote, unquote, urljoin, urlparse

from .security import redact_url


def _safe_markdown_url(url: str) -> str:
    """
    Ensure URL is safe for Markdown link destination (encode spaces and parentheses).
    """
    if not url:
        return ""
    # Only replace spaces and parentheses which break standard Markdown links.
    # Avoid full quote() to prevent double-encoding % or other characters if not needed.
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def is_probable_icon(url: str) -> bool:
    low = url.lower()
    return (
        "favicon" in low
        or "/icon/" in low
        or low.endswith(".ico")
        or "pinned-octocat" in low
        or "/apple-touch-icon" in low
    )


def _is_cjk(ch: str) -> bool:
    """判断单个字符是否为 CJK 字符（中文/日文/韩文）。"""
    if not ch:
        return False
    cp = ord(ch)
    return (
        0x3400 <= cp <= 0x9FFF      # CJK Unified Ideographs + Ext A
        or 0x3040 <= cp <= 0x30FF    # Hiragana + Katakana
        or 0xAC00 <= cp <= 0xD7AF    # Hangul Syllables
        or 0xFF00 <= cp <= 0xFFEF    # Fullwidth Forms
    )


def _extract_img_src(attrs: Dict[str, Optional[str]]) -> str:
    """从 img/picture 的属性中提取真实图片 URL。

    优先级：src/data-src/data-original/data-lazy-src → srcset 第一项。
    自动跳过 ``data:`` 占位 URI（base64 透明像素等懒加载占位图），
    回退到真实懒加载属性。
    """
    candidates = [
        attrs.get("src"),
        attrs.get("data-src"),
        attrs.get("data-original"),
        attrs.get("data-lazy-src"),
    ]
    src = next(
        (c for c in candidates if c and not c.strip().lower().startswith("data:")),
        None,
    )
    if not src:
        srcset = attrs.get("srcset")
        if srcset:
            src = srcset.split(",")[0].strip().split(" ")[0]
    return src or ""


_UNSAFE_URL_RE = re.compile(r"^(?:javascript|vbscript|file):", re.IGNORECASE)


def _is_unsafe_link_url(raw: str) -> bool:
    """判断 href 值是否含可执行脚本的不安全协议。

    浏览器会忽略 URL 中的 tab/newline 等控制字符（``java\\tscript:`` 等变体），
    因此先剥离控制字符再匹配。``data:text/html`` 在 href 中可执行脚本，也拦截。
    """
    if not raw:
        return False
    v_stripped = re.sub(r"[\x00-\x20]+", "", raw).lower()
    if _UNSAFE_URL_RE.match(v_stripped):
        return True
    if v_stripped.startswith("data:"):
        return True
    return False


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

# 块级标签：用于检测未闭合的行内元素（如 katex span）是否越界
_BLOCK_LEVEL_TAGS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "td", "th",
    "blockquote", "pre", "hr", "section", "article", "header",
    "footer", "main", "aside", "nav", "figure", "figcaption",
    "details", "summary", "form", "fieldset",
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
        self.keep_html = keep_html
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
        self.a_has_image: bool = False  # <a> 内是否已输出图片

        self.list_stack: List[Dict[str, Union[int, str]]] = []

        self.in_table = False
        self.table_depth = 0
        self.table_rows: List[List[str]] = []
        self.current_row: Optional[List[str]] = None
        self.in_cell = False
        self.cell_buf: List[str] = []
        self.table_in_a = False
        self.table_a_href: Optional[str] = None
        self.table_a_text: List[str] = []

        self.table_capture_html = False
        self.table_capture_buf: List[str] = []
        self.table_capture_depth = 0
        self.table_is_complex = False

    @staticmethod
    def _is_complex_table_attrs(attrs: Dict[str, Optional[str]]) -> bool:
        colspan = attrs.get("colspan")
        rowspan = attrs.get("rowspan")
        if colspan and colspan != "1":
            return True
        if rowspan and rowspan != "1":
            return True
        return False

    @staticmethod
    def _attrs_to_str(attrs_list: Sequence[Tuple[str, Optional[str]]]) -> str:
        parts = []
        for name, value in attrs_list:
            safe_name = (name or "").strip()
            if not safe_name:
                continue
            low = safe_name.lower()

            if low.startswith("on"):
                continue

            if value is not None and low in ("href", "src", "xlink:href", "srcset"):
                v = str(value).strip()
                # 浏览器会忽略 URL 中的 tab/newline 等控制字符，
                # 因此 java\tscript: 等变体也需拦截
                v_stripped = re.sub(r"[\x00-\x20]+", "", v).lower()
                if re.match(r"^(?:javascript|vbscript):", v_stripped):
                    continue
                # data: 协议在 href 中可执行脚本（data:text/html），
                # 在 src 中仅 img/data:image 安全
                if low == "href" and v_stripped.startswith("data:"):
                    continue
                if low in ("src", "xlink:href") and v_stripped.startswith("file:"):
                    continue

            if value is None:
                parts.append(safe_name)
            else:
                escaped = htmllib.escape(str(value), quote=True)
                parts.append(f'{safe_name}="{escaped}"')
        return " ".join(parts)

    @staticmethod
    def _extract_code_language(attrs: Dict[str, Optional[str]]) -> str:
        for key in ("data-language", "data-lang", "lang"):
            val = (attrs.get(key) or "").strip()
            if val:
                return val.split()[0]

        classes = _class_list(attrs)
        for c in classes:
            m = re.match(r"^(?:language|lang)[-_]([A-Za-z0-9_+.-]+)$", c)
            if m:
                return m.group(1)

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
        if self.out:
            tail = self._tail()
            if tail.endswith(("**", "*", "`")):
                text = text.lstrip()
        if self.out:
            prev = self._tail()[-1:]
            if (
                prev
                and prev not in ("\n", " ", "(", "[", "*", "`", "_")
                and text[:1] not in (" ", "\n", ".", ",", ":", ";", ")", "]")
                # 两侧均为 CJK 字符时不插入空格（避免割裂中文/日文/韩文文本）
                and not (_is_cjk(prev) and _is_cjk(text[:1]))
            ):
                self.out.append(" ")
        self.out.append(text)

    def _table_append(self, text: str) -> None:
        if not text:
            return
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        self.cell_buf.append(text)

    def _should_skip(self, tag: str, attrs: Dict[str, Optional[str]]) -> bool:
        if tag == "script":
            t = (attrs.get("type") or "").strip().lower()
            if t.startswith("math/tex"):
                return False
        if tag in SKIP_TAGS:
            return True

        classes = _class_list(attrs)
        if classes and tag not in ("figure", "figcaption"):
            if any(c.startswith(("kg-video-", "kg-audio-", "kg-file-")) for c in classes):
                return True
            if any("kg-video" in c for c in classes):
                return True

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

        # 未闭合的 katex span 会吞掉后续所有文本；
        # 遇块级标签起始时强制归零（katex 是行内元素，不应跨块）
        if self.katex_depth > 0 and tag in _BLOCK_LEVEL_TAGS:
            self.katex_depth = 0
            self.katex_display_depth = 0

        if self.skip_stack:
            if self._should_skip(tag, attrs):
                self._enter_skip(tag)
            return

        if self._should_skip(tag, attrs):
            self._enter_skip(tag)
            return

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

        if tag == "table":
            self._ensure_blank_line()
            self.in_table = True
            self.table_depth = 1
            self.table_rows = []
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

        if self.in_table:
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
                if self.keep_html and self._is_complex_table_attrs(attrs):
                    self.table_is_complex = True
                self.in_cell = True
                self.cell_buf = []
            elif tag == "br" and self.in_cell:
                if self.cell_buf and (self.cell_buf[-1].strip().lower() != "<br>"):
                    self.cell_buf.append("<br>")
            elif tag in ("p", "div", "li") and self.in_cell:
                if self.cell_buf and (self.cell_buf[-1].strip().lower() != "<br>"):
                    self.cell_buf.append("<br>")
            elif tag == "a" and self.in_cell:
                self.table_in_a = True
                thref = attrs.get("href")
                if thref and _is_unsafe_link_url(thref):
                    thref = None
                self.table_a_href = thref
                self.table_a_text = []
            elif tag == "img" and self.in_cell:
                src = _extract_img_src(attrs)
                if src:
                    img_url = urljoin(self.base_url, htmllib.unescape(src))
                    if not is_probable_icon(img_url):
                        alt = (attrs.get("alt") or "").strip()
                        alt = alt.replace("[", "").replace("]", "")
                        local = self.url_to_local.get(img_url, img_url)
                        # Encode spaces/parens in URL for valid Markdown
                        safe_url = _safe_markdown_url(local)
                        self.cell_buf.append(f"![{alt}]({safe_url})")
            return

        if tag in ("p",):
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
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "a":
            self.in_a = True
            href = attrs.get("href")
            # 拦截不安全协议（javascript:/vbscript:/file:/data:text/html 等，
            # 含控制字符变体）——避免输出可执行链接
            if href and _is_unsafe_link_url(href):
                href = None
            self.a_href = href
            self.a_text = []
            self.a_has_image = False
        elif tag == "img":
            src = _extract_img_src(attrs)
            if not src:
                return
            # 跳过 data: URI，与 ImageURLCollector 行为一致
            if src.strip().lower().startswith("data:"):
                return
            img_url = urljoin(self.base_url, htmllib.unescape(src))
            if is_probable_icon(img_url):
                return
            alt = (attrs.get("alt") or "").strip()
            alt = alt.replace("[", "").replace("]", "")
            local = self.url_to_local.get(img_url, img_url)
            # Encode spaces/parens in URL for valid Markdown
            safe_url = _safe_markdown_url(local)
            if self.in_a:
                # 图片在 <a> 内：行内输出（不独占一行），标记已产出图片
                self.a_has_image = True
                self.out.append(f"![{alt}]({safe_url})")
            else:
                self._ensure_blank_line()
                self.out.append(f"![{alt}]({safe_url})\n")
        elif tag in ("ul", "ol"):
            if self.list_stack:
                if not self._tail().endswith("\n"):
                    self.out.append("\n")
            else:
                self._ensure_blank_line()
            # <ol start="5"> 支持自定义起始编号
            start_n = 0
            if tag == "ol":
                attrs = dict(attrs_list)
                start_val = attrs.get("start")
                if start_val:
                    try:
                        start_n = int(start_val) - 1  # -1 因为 li 处理时会 +1
                    except (ValueError, TypeError):
                        pass
            self.list_stack.append({"type": tag, "n": start_n})
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
        tag = tag.lower()
        self.handle_starttag(tag, attrs_list)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag in VOID_TAGS:
            pass
        elif self.tag_stack:
            matched_idx = -1
            for idx in range(len(self.tag_stack) - 1, -1, -1):
                if self.tag_stack[idx][0] == tag:
                    matched_idx = idx
                    break
            if matched_idx >= 0:
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

        if self.in_table:
            if self.table_capture_html:
                if tag not in VOID_TAGS:
                    self.table_capture_buf.append(f"</{tag}>")
                if tag == "table":
                    self.table_capture_depth -= 1

            if tag == "table":
                self.table_depth = max(0, self.table_depth - 1)
            elif self.table_depth > 1:
                return

            if tag == "a" and self.table_in_a:
                text = "".join(self.table_a_text).strip() or (self.table_a_href or "")
                href = self.table_a_href
                if href:
                    href = urljoin(self.base_url, href)
                    safe_href = _safe_markdown_url(href)
                    self._table_append(f"[{text}]({safe_href})")
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
                if self.table_depth > 0:
                    return
                rows = self.table_rows
                self.in_table = False
                self.table_rows = []
                self.current_row = None

                if self.table_capture_html and self.table_capture_depth <= 0:
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
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "a":
            href = self.a_href
            text = "".join(self.a_text).strip()
            has_image = self.a_has_image

            # <a> 内仅含图片（无文本）：图片已行内输出，不再追加回退裸链接，
            # 否则会多出一行 [https://host/page](https://host/page)。
            if not text and has_image:
                self.in_a = False
                self.a_href = None
                self.a_text = []
                self.a_has_image = False
                return

            # 无文本也无图片时回退到 href 作为文本
            if not text:
                text = href or ""

            if href:
                full = urljoin(self.base_url, href)
                if text.strip() in ("#", "¶", "§") and (href.startswith("#") or full.startswith(self.base_url + "#")):
                    self.in_a = False
                    self.a_href = None
                    self.a_text = []
                    self.a_has_image = False
                    return

            if text.lower() == "tag" and href and (href.startswith("#") or href.startswith(self.base_url + "#")):
                self.in_a = False
                self.a_href = None
                self.a_text = []
                self.a_has_image = False
                return

            if href:
                href = urljoin(self.base_url, href)
                safe_href = _safe_markdown_url(href)
                self.out.append(f"[{text}]({safe_href})")
            else:
                self.out.append(text)
            self.in_a = False
            self.a_href = None
            self.a_text = []
            self.a_has_image = False
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
        if self.in_annotation_tex:
            self.annotation_buf.append(data or "")
            return
        if self.in_math_script:
            self.math_script_buf.append(data or "")
            return
        if self.katex_depth > 0:
            return
        if self.in_table and self.table_capture_html and data:
            self.table_capture_buf.append(htmllib.escape(data, quote=False))
        if self.in_pre:
            self.pre_buf.append(data or "")
            return
        if self.in_table and self.table_depth > 1:
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


def _is_fence_line(line: str) -> bool:
    """判断一行是否是 Markdown 代码围栏（``` 或 ~~~）的起始/结束。"""
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _process_outside_code(md: str, fn) -> str:
    """对 Markdown 文本逐行应用 *fn*，但跳过代码围栏（```/~~~）内的行。

    *fn* 接收单行文本（含换行符），返回替换后的文本。
    """
    out_lines: List[str] = []
    in_fence = False
    for line in md.splitlines(True):
        if _is_fence_line(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
        else:
            out_lines.append(fn(line))
    return "".join(out_lines)


def _demote_headings_outside_code(md: str, shift: int) -> str:
    """将代码块外的 ATX 标题（# ~ ######）等级提升 *shift* 级（封顶 6）。

    用于合并模式：把每页的 #..#### 下移以免与合并文档的顶层标题冲突。
    代码围栏（```/~~~）内的 # 注释行不受影响。
    """
    pat = re.compile(r"^(#{1,6})(\s+)")

    def _bump(line: str) -> str:
        m = pat.match(line)
        if not m:
            return line
        new_level = min(6, len(m.group(1)) + shift)
        return "#" * new_level + m.group(2) + line[m.end():]

    return _process_outside_code(md, _bump)


def _strip_empty_headings_outside_code(md: str) -> str:
    """删除代码块外只含井号、无文本的标题行（如 ``###`` 单独成行）。"""
    return _process_outside_code(
        md, lambda line: re.sub(r"^\s*#{1,6}\s*\n?$\n?", "", line)
    )


def _collapse_blank_lines_outside_code(md: str) -> str:
    """把代码块外连续 3+ 空行折叠为 2 个空行（代码块内原样保留）。"""
    out_lines: List[str] = []
    in_fence = False
    blank_run = 0
    for line in md.splitlines(True):
        if _is_fence_line(line):
            in_fence = not in_fence
            out_lines.append(line)
            blank_run = 0
            continue
        if in_fence:
            out_lines.append(line)
            continue
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                out_lines.append("\n" if line.endswith("\n") else "")
        else:
            blank_run = 0
            out_lines.append(line)
    return "".join(out_lines)


def _convert_latex_delimiters_outside_code(md: str) -> str:
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
    # 以下后处理均在代码围栏（```/~~~）外执行，避免破坏代码块内容
    md = _collapse_blank_lines_outside_code(md)
    md = _convert_latex_delimiters_outside_code(md)
    md = _strip_empty_headings_outside_code(md)
    # 标题尾部的锚点链接 [#](url) / [¶](url) 剥离（仅在代码块外）
    md = _process_outside_code(
        md,
        lambda line: re.sub(
            r"^(#{1,6}\s+.*?)(\s*\[\s*[#¶§]\s*\]\([^)]+\))+\s*$",
            r"\1",
            line,
        ),
    )
    return md.strip() + "\n"


def _normalize_title(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip()).lower()
    t = re.sub(r"[^\w\u4e00-\u9fff ]+", "", t)
    return t


def strip_duplicate_h1(md_body: str, title: str, max_scan_lines: int = 80) -> str:
    lines = md_body.splitlines()
    if not lines:
        return md_body
    title_n = _normalize_title(title)
    if not title_n:
        return md_body

    scan = min(len(lines), max_scan_lines)
    for i in range(scan):
        line = lines[i].strip()
        if not line:
            continue
        if re.fullmatch(r"#{1,6}", line):
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


def clean_wechat_noise(md_content: str) -> str:
    # 微信底部交互按钮（取消/允许/Cancel/Allow/Video/Share 等）的特征是
    # 转成 Markdown 后**独占一行**（原本是独立 <a> 或按钮）。
    # 因此所有清理规则都带行锚点 ^...$，避免误删正文句子中的同名词。
    result = md_content
    # 行内交互按钮串：删除独占整行、仅由逗号/空格/这些词构成的行
    result = re.sub(
        r"(?m)^[ \t,，]*(?:Video|Mini Program|Like|Wow|Share|Comment|Favorite|听过)"
        r"(?:[ \t,，]*(?:Video|Mini Program|Like|Wow|Share|Comment|Favorite|听过))*[ \t,，]*$",
        "",
        result,
        flags=re.IGNORECASE,
    )
    # "取消赞"/"取消在看" 等长按提示——独占整行
    result = re.sub(r"(?m)^[ \t,，]*轻点两下取消(?:赞|在看)[ \t,，]*$", "", result)
    result = re.sub(
        r"(?m)^[ \t,，]*(?:Scan to Follow|Scan with Weixin to\s*use this Mini Program"
        r"|微信扫一扫可打开此内容.*?使用完整服务)[ \t,，]*$",
        "",
        result,
        flags=re.IGNORECASE,
    )
    # Cancel/Allow/取消/允许 按钮文本——仅在独占整行时删除（纯文本或链接形式）
    result = re.sub(
        r"(?m)^[ \t,，]*(?:\[?(?:Cancel|Allow|取消|允许)\]?\]\([^)]*\)|Cancel|Allow|取消|允许)[ \t,，]*$",
        "",
        result,
        flags=re.IGNORECASE,
    )
    # "阅读原文"/"Read more" 按钮链接——独占整行的纯文本或链接形式均清理
    result = re.sub(
        r"(?m)^[ \t,，]*(?:\[(?:阅读原文|Read more|Read original)\]\([^)]*\)"
        r"|(?:阅读原文|Read more|Read original))[ \t,，]*$",
        "",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"\n[ \t]+\n", "\n\n", result)
    return result.strip()


def clean_wiki_noise(md_content: str) -> str:
    result = md_content
    result = re.sub(r"\[\[(?:Edit|编辑|修改|更新)\]\([^)]*\)\]", "", result, flags=re.IGNORECASE)
    result = re.sub(
        r"\[(?:Edit|编辑|修改|更新|History|历史|Diff|差分|Raw|源代码|附件|Attach|新建)\]\([^)]*\)",
        "",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(r"\[\^\]\([^)]*\)", "", result)
    result = re.sub(
        r"\[(?:↑|↓|↖|↗|↙|↘|Top|顶部|返回顶部)\]\([^)]*\)",
        "",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(r"\[\?\]\([^)]*(?:cmd=edit|action=edit)[^)]*\)", "", result, flags=re.IGNORECASE)
    result = re.sub(r"\[\s*\[[^\]]+\]\([^)]+\)\s*\]\s*", "", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"\n[ \t]+\n", "\n\n", result)
    return result.strip()


def rewrite_internal_links(md_content: str, url_to_anchor: Dict[str, str]) -> Tuple[str, int]:
    if not url_to_anchor:
        return md_content, 0

    rewrite_count = 0
    result = md_content
    link_pattern = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")

    def replace_link(match: re.Match) -> str:
        nonlocal rewrite_count
        text = match.group(1)
        url = match.group(2)

        candidates: List[str] = [url]
        try:
            decoded_url = unquote(url)
            if decoded_url and decoded_url != url:
                candidates.append(decoded_url)
        except Exception:
            pass

        try:
            parsed = urlparse(url)
            if parsed.fragment:
                candidates.append(parsed._replace(fragment="").geturl())
        except Exception:
            pass
        try:
            candidates.append(redact_url(url))
        except Exception:
            pass

        for c in candidates:
            if not c:
                continue
            anchor = url_to_anchor.get(c)
            if anchor:
                rewrite_count += 1
                return f"[{text}](#{anchor})"

        return match.group(0)

    # 按代码围栏分段，仅处理围栏外的部分（避免破坏代码示例中的链接）
    fence_re = re.compile(r"^([`~]{3,})")
    lines = result.split("\n")
    parts: List[str] = []
    in_fence = False
    fence_char = ""
    outside_buf: List[str] = []

    def flush_outside() -> None:
        if outside_buf:
            parts.append(link_pattern.sub(replace_link, "\n".join(outside_buf)))
            outside_buf.clear()

    for line in lines:
        m = fence_re.match(line.strip())
        if m and (not in_fence or line.strip().startswith(fence_char * 3)):
            # 围栏开/闭行
            flush_outside()
            if not in_fence:
                in_fence = True
                fence_char = m.group(1)[0]
            else:
                in_fence = False
                fence_char = ""
            parts.append(line)
        elif in_fence:
            parts.append(line)
        else:
            outside_buf.append(line)

    flush_outside()
    result = "\n".join(parts)
    return result, rewrite_count
