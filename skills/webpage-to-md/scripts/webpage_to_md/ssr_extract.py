"""SSR (Server-Side Rendering) 数据提取模块。

自动从嵌入在 HTML 中的 SSR 数据块里提取文章/文档正文，
适用于正文由 JavaScript 动态渲染、但 SSR 数据已序列化到 HTML 的站点。

**两阶段策略**：
1. **精确匹配**：检测已知 SSR 框架标记（``__NEXT_DATA__``, ``_ROUTER_DATA``），
   导航到文章数据并转换。
2. **通用兜底**：正文缺失时扫描所有 ``<script>`` 标签，寻找结构化
   富文本 JSON 数据（兼容 ProseMirror / Slate / Editor.js / Lexical / Quill Delta），
   使用通用转换器输出 HTML。

当前精确适配的站点：
- **Next.js** (``__NEXT_DATA__``)：腾讯云开发者社区等
- **Modern.js** (``window._ROUTER_DATA``)：火山引擎文档等

**公共 API**（供调用方在获取 ``SSRContent`` 后使用）：
- :func:`collect_md_image_urls` — 从 Markdown 文本中提取图片 URL
- :func:`resolve_relative_md_images` — 将 Markdown 中的相对图片 URL 解析为绝对 URL
- :func:`richtext_json_to_html` — 通用 JSON 富文本→HTML 转换器
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import escape as html_escape
from typing import Any, Optional
from urllib.parse import urljoin


# ═══════════════════════════════════════════════════════════════════════════
# 公共数据类
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class SSRContent:
    """提取到的 SSR 文章内容。"""
    title: str
    body: str
    source_type: str   # "nextjs" | "modernjs" | "json_fallback"
    is_markdown: bool  # True → body 已经是 Markdown；False → body 是 HTML


# ═══════════════════════════════════════════════════════════════════════════
# 通用 JSON 富文本 → HTML 转换器
# ═══════════════════════════════════════════════════════════════════════════
# 兼容 ProseMirror / Tiptap / Slate / Editor.js / Lexical 的 JSON Schema。
# 核心思路：所有框架在语义层面共享一组有限的节点类型（段落、标题、列表、
# 代码块、图片、表格等）。差异仅在 JSON key 名称和 mark 表示方式。

# ── 节点类型名称映射（各框架 → 语义类型）──────────────────────────────
_TYPE_PARAGRAPH = {"paragraph", "p"}
_TYPE_HEADING = {"heading", "header"}
_TYPE_BULLET_LIST = {"bulletList", "bullet_list", "bulleted-list", "unordered-list"}
_TYPE_ORDERED_LIST = {"orderedList", "ordered_list", "ordered-list", "numbered-list"}
_TYPE_LIST_ITEM = {"listItem", "list-item", "list_item", "listitem"}
_TYPE_CODE_BLOCK = {"codeBlock", "code-block", "code_block", "code"}
_TYPE_BLOCKQUOTE = {"blockquote", "block-quote", "block_quote", "quote"}
_TYPE_IMAGE = {"image", "img"}
_TYPE_TABLE = {"table"}
_TYPE_TABLE_ROW = {"tableRow", "table-row", "table_row", "tablerow"}
_TYPE_TABLE_CELL = {"tableCell", "table-cell", "table_cell", "tablecell"}
_TYPE_TABLE_HEADER = {"tableHeader", "table-header", "table_header", "tableheader"}
_TYPE_HARD_BREAK = {"hardBreak", "hard_break", "linebreak"}
_TYPE_HR = {"horizontalRule", "horizontal_rule", "horizontalrule", "delimiter", "divider"}
_TYPE_TASK_LIST = {"taskList", "task_list", "task-list", "check-list"}
_TYPE_TASK_ITEM = {"taskItem", "task-item", "task_item", "check-list-item"}
_TYPE_CALLOUT = {"highlightBlock", "callout", "alert", "admonition", "warning", "info", "tip"}
_TYPE_DOC_ROOT = {"doc", "root", "document"}

# Lexical format 位掩码
_LEXICAL_BOLD = 1
_LEXICAL_ITALIC = 2
_LEXICAL_STRIKETHROUGH = 4
_LEXICAL_UNDERLINE = 8
_LEXICAL_CODE = 16

# ── Editor.js 内联 HTML 清洗 ─────────────────────────────────────────────
# Editor.js 的 data.text / data.items 内容通常已含 HTML 格式标记（如 <b>、<a>）。
# 直接 html_escape 会双重转义导致显示源码；完全不处理有 XSS 风险。
# 折中方案：移除危险标签和事件属性，保留安全的格式标签。

_DANGEROUS_TAG_RE = re.compile(
    r'<\s*/?\s*(?:script|style|iframe|object|embed|form|input|textarea|button|select)\b[^>]*>',
    re.IGNORECASE,
)
# 事件属性：匹配带引号和无引号两种写法
# onclick="alert(1)" / onclick='alert(1)' / onclick=alert(1)
_EVENT_ATTR_RE = re.compile(
    r'''\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)''',
    re.IGNORECASE,
)
# javascript: 协议：匹配带引号和无引号两种写法
# href="javascript:..." / href='javascript:...' / href=javascript:...
_JS_HREF_RE = re.compile(
    r'''href\s*=\s*(?:"javascript:[^"]*"|'javascript:[^']*'|javascript:[^\s>]+)''',
    re.IGNORECASE,
)


def _sanitize_editorjs_html(html_text: str) -> str:
    """清洗 Editor.js 的内联 HTML 内容。

    保留安全的格式标签（``<b>``, ``<i>``, ``<a>``, ``<code>`` 等），
    移除危险标签（``<script>``, ``<style>``, ``<iframe>`` 等）和事件属性（``on*``）。
    """
    if not html_text:
        return ""
    result = _DANGEROUS_TAG_RE.sub("", html_text)
    result = _EVENT_ATTR_RE.sub("", result)
    result = _JS_HREF_RE.sub('href=""', result)
    return result


def richtext_json_to_html(node: Any) -> str:
    """通用 JSON 富文本 → HTML 转换。

    兼容 ProseMirror / Tiptap / Slate / Editor.js / Lexical 的节点结构。
    未知节点类型安全回退为输出子内容。

    也支持 Editor.js 的顶层 ``{"blocks": [...]}`` 结构。
    """
    if isinstance(node, list):
        return "".join(richtext_json_to_html(c) for c in node)
    if not isinstance(node, dict):
        return html_escape(str(node)) if node else ""

    # Editor.js 顶层结构: {"blocks": [...]}
    blocks = node.get("blocks")
    if isinstance(blocks, list) and "type" not in node:
        return _convert_editorjs_blocks(blocks)

    node_type = node.get("type", "")

    # ── 子节点：不同框架使用不同 key ──
    children = node.get("content") or node.get("children") or []

    # ── 文本节点 ──
    text = node.get("text", "")
    if node_type == "text" or (text and not children and not node_type):
        return _apply_marks(text, node)

    # ── Slate 纯文本叶子节点（无 type，有 text）──
    if text and not node_type:
        return _apply_marks(text, node)

    # ── 递归处理子节点 ──
    inner = "".join(richtext_json_to_html(c) for c in children)

    # ── 语义映射 ──
    if node_type in _TYPE_DOC_ROOT:
        return inner

    if node_type in _TYPE_PARAGRAPH:
        return f"<p>{inner}</p>\n"

    if node_type in _TYPE_HEADING:
        level = _get_heading_level(node)
        return f"<h{level}>{inner}</h{level}>\n"

    if node_type in _TYPE_BULLET_LIST:
        return f"<ul>\n{inner}</ul>\n"

    if node_type in _TYPE_ORDERED_LIST:
        start = _get_attr(node, "start", 1)
        start_attr = f' start="{start}"' if start != 1 else ""
        return f"<ol{start_attr}>\n{inner}</ol>\n"

    # Editor.js list 节点（通过 data.style 区分有序/无序）
    if node_type == "list":
        data = node.get("data", {})
        style = data.get("style", "unordered") if isinstance(data, dict) else "unordered"
        items_data = data.get("items", []) if isinstance(data, dict) else []
        if items_data:
            # Editor.js items 内容可能已含 HTML 格式标记，用清洗替代转义
            inner = "".join(
                f"<li>{_sanitize_editorjs_html(str(it))}</li>\n"
                if isinstance(it, str)
                else f"<li>{_sanitize_editorjs_html(it.get('content', '') if isinstance(it, dict) else '')}</li>\n"
                for it in items_data
            )
        tag = "ol" if style == "ordered" else "ul"
        return f"<{tag}>\n{inner}</{tag}>\n"

    if node_type in _TYPE_LIST_ITEM:
        return f"<li>{inner}</li>\n"

    if node_type in _TYPE_CODE_BLOCK:
        lang = _get_attr(node, "language", "") or _get_attr(node, "lang", "")
        # Editor.js: data.code
        data = node.get("data", {})
        if isinstance(data, dict) and "code" in data and not inner:
            inner = html_escape(data["code"])
        cls = f' class="language-{html_escape(lang)}"' if lang else ""
        return f"<pre><code{cls}>{inner}</code></pre>\n"

    if node_type in _TYPE_BLOCKQUOTE:
        # Editor.js: data.text（可能含 HTML 格式标记）
        data = node.get("data", {})
        if isinstance(data, dict) and "text" in data and not inner:
            inner = _sanitize_editorjs_html(data["text"])
        return f"<blockquote>{inner}</blockquote>\n"

    if node_type in _TYPE_IMAGE:
        src, alt = _get_image_attrs(node)
        return f'<img src="{html_escape(src)}" alt="{html_escape(alt)}">\n'

    if node_type in _TYPE_HARD_BREAK:
        return "<br>"

    if node_type in _TYPE_HR:
        return "<hr>\n"

    if node_type in _TYPE_CALLOUT:
        return f'<div class="callout">{inner}</div>\n'

    if node_type in _TYPE_TABLE:
        return f"<table>\n{inner}</table>\n"

    if node_type in _TYPE_TABLE_ROW:
        return f"<tr>{inner}</tr>\n"

    if node_type in _TYPE_TABLE_HEADER:
        extra = _get_cell_attrs(node)
        return f"<th{extra}>{inner}</th>"

    if node_type in _TYPE_TABLE_CELL:
        extra = _get_cell_attrs(node)
        return f"<td{extra}>{inner}</td>"

    if node_type in _TYPE_TASK_LIST:
        return f'<ul class="task-list">\n{inner}</ul>\n'

    if node_type in _TYPE_TASK_ITEM:
        checked = " checked" if _get_attr(node, "checked", False) else ""
        return f'<li><input type="checkbox"{checked}>{inner}</li>\n'

    # ── 未知节点：安全回退为输出子内容 ──
    return inner


# ── 辅助函数 ──────────────────────────────────────────────────────────────

def _apply_marks(text: str, node: dict) -> str:
    """对文本节点应用格式标记（兼容 ProseMirror marks / Slate 扁平属性 / Lexical bitmask）。

    采用"先收集、后应用"策略：从三种框架格式中统一提取布尔标志，
    最终只包裹一次，避免双重嵌套。
    """
    if not text:
        return ""

    # ── 第一步：从所有格式源收集标志 ──
    is_bold = False
    is_italic = False
    is_code = False
    is_underline = False
    is_strike = False
    href = ""

    # 来源 1: ProseMirror / Tiptap marks 数组
    marks = node.get("marks", [])
    if isinstance(marks, list):
        for mark in marks:
            mt = mark.get("type", "") if isinstance(mark, dict) else ""
            ma = mark.get("attrs", {}) if isinstance(mark, dict) else {}
            if mt in ("bold", "strong"):
                is_bold = True
            elif mt in ("italic", "em"):
                is_italic = True
            elif mt == "code":
                is_code = True
            elif mt == "link":
                href = ma.get("href", "")
            elif mt == "underline":
                is_underline = True
            elif mt in ("strike", "strikethrough"):
                is_strike = True

    # 来源 2: Slate 扁平布尔属性
    if node.get("bold"):
        is_bold = True
    if node.get("italic"):
        is_italic = True
    if node.get("code"):
        is_code = True
    if node.get("underline"):
        is_underline = True
    if node.get("strikethrough") or node.get("strike"):
        is_strike = True
    if not href:
        href = node.get("url", "") or node.get("href", "")

    # 来源 3: Lexical format 位掩码
    fmt = node.get("format", 0)
    if isinstance(fmt, int) and fmt > 0:
        if fmt & _LEXICAL_BOLD:
            is_bold = True
        if fmt & _LEXICAL_ITALIC:
            is_italic = True
        if fmt & _LEXICAL_CODE:
            is_code = True
        if fmt & _LEXICAL_UNDERLINE:
            is_underline = True
        if fmt & _LEXICAL_STRIKETHROUGH:
            is_strike = True

    # ── 第二步：统一应用（每种标记最多一次）──
    result = html_escape(text)
    if is_code:
        result = f"<code>{result}</code>"
    if is_bold:
        result = f"<strong>{result}</strong>"
    if is_italic:
        result = f"<em>{result}</em>"
    if is_underline:
        result = f"<u>{result}</u>"
    if is_strike:
        result = f"<s>{result}</s>"
    if href:
        result = f'<a href="{html_escape(href)}">{result}</a>'

    return result


def _get_heading_level(node: dict) -> int:
    """从各框架的 heading 节点中提取标题级别。"""
    # ProseMirror / Tiptap: attrs.level
    attrs = node.get("attrs", {})
    if isinstance(attrs, dict) and "level" in attrs:
        return min(max(int(attrs["level"]), 1), 6)
    # Editor.js: data.level
    data = node.get("data", {})
    if isinstance(data, dict) and "level" in data:
        return min(max(int(data["level"]), 1), 6)
    # Slate / Lexical: 直接在节点上的 level 字段
    if "level" in node:
        return min(max(int(node["level"]), 1), 6)
    # Lexical: tag 字段 (如 "h2")
    tag = node.get("tag", "")
    if isinstance(tag, str) and tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
        return int(tag[1])
    return 2  # 默认 h2


def _get_attr(node: dict, key: str, default: Any = None) -> Any:
    """从 attrs / data / 节点本身中查找属性值。"""
    # ProseMirror: attrs.key
    attrs = node.get("attrs", {})
    if isinstance(attrs, dict) and key in attrs:
        return attrs[key]
    # Editor.js: data.key
    data = node.get("data", {})
    if isinstance(data, dict) and key in data:
        return data[key]
    # 直接在节点上
    return node.get(key, default)


def _get_image_attrs(node: dict) -> tuple[str, str]:
    """从各框架的 image 节点中提取 src 和 alt。"""
    attrs = node.get("attrs", {})
    data = node.get("data", {})
    # ProseMirror / Lexical: attrs.src 或直接 src
    src = ""
    if isinstance(attrs, dict):
        src = attrs.get("src", "")
    if not src:
        src = node.get("src", "")
    if not src and isinstance(data, dict):
        # Editor.js: data.file.url 或 data.url
        file_info = data.get("file", {})
        if isinstance(file_info, dict):
            src = file_info.get("url", "")
        if not src:
            src = data.get("url", "")
    # Slate: url 字段
    if not src:
        src = node.get("url", "")

    alt = ""
    if isinstance(attrs, dict):
        alt = attrs.get("alt", "")
    if not alt and isinstance(data, dict):
        alt = data.get("caption", "") or data.get("alt", "")
    if not alt:
        alt = node.get("alt", "")

    return str(src), str(alt)


def _get_cell_attrs(node: dict) -> str:
    """构建表格单元格的 colspan/rowspan 属性字符串。"""
    attrs = node.get("attrs", {})
    if not isinstance(attrs, dict):
        attrs = {}
    extra = ""
    colspan = attrs.get("colspan", 1) or node.get("colspan", 1)
    rowspan = attrs.get("rowspan", 1) or node.get("rowspan", 1)
    if colspan and colspan > 1:
        extra += f' colspan="{colspan}"'
    if rowspan and rowspan > 1:
        extra += f' rowspan="{rowspan}"'
    return extra


def _convert_editorjs_blocks(blocks: list) -> str:
    """将 Editor.js 的 blocks 数组转换为 HTML。

    Editor.js 的 ``data.text`` / ``data.items`` 内容通常已含 HTML 格式标记
    （如 ``<b>``, ``<a>``），使用 :func:`_sanitize_editorjs_html` 清洗
    而非 ``html_escape``，避免双重转义。代码块内容仍做 ``html_escape``。
    """
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        data = block.get("data", {})
        if not isinstance(data, dict):
            data = {}

        if btype in ("paragraph", "p"):
            parts.append(f'<p>{_sanitize_editorjs_html(data.get("text", ""))}</p>\n')
        elif btype in ("header", "heading"):
            level = min(max(int(data.get("level", 2)), 1), 6)
            parts.append(f'<h{level}>{_sanitize_editorjs_html(data.get("text", ""))}</h{level}>\n')
        elif btype == "list":
            style = data.get("style", "unordered")
            items = data.get("items", [])
            tag = "ol" if style == "ordered" else "ul"
            inner = "".join(
                f"<li>{_sanitize_editorjs_html(it if isinstance(it, str) else (it.get('content', '') if isinstance(it, dict) else ''))}</li>\n"
                for it in items
            )
            parts.append(f"<{tag}>\n{inner}</{tag}>\n")
        elif btype == "code":
            # 代码块内容必须转义（显示原始文本）
            parts.append(f'<pre><code>{html_escape(data.get("code", ""))}</code></pre>\n')
        elif btype == "quote":
            parts.append(f'<blockquote>{_sanitize_editorjs_html(data.get("text", ""))}</blockquote>\n')
        elif btype == "image":
            file_info = data.get("file", {})
            src = file_info.get("url", "") if isinstance(file_info, dict) else ""
            if not src:
                src = data.get("url", "")
            caption = data.get("caption", "")
            parts.append(f'<img src="{html_escape(src)}" alt="{html_escape(caption)}">\n')
        elif btype in ("delimiter", "divider"):
            parts.append("<hr>\n")
        elif btype == "table":
            content = data.get("content", [])
            if content:
                rows = "".join(
                    "<tr>" + "".join(f"<td>{_sanitize_editorjs_html(cell)}</td>"
                                      for cell in row) + "</tr>\n"
                    for row in content if isinstance(row, list)
                )
                parts.append(f"<table>\n{rows}</table>\n")
        else:
            # 兜底：尝试输出 data.text
            t = data.get("text", "")
            if t:
                parts.append(f"<p>{_sanitize_editorjs_html(t)}</p>\n")

    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# 两阶段兜底：从 <script> 中扫描 JSON 富文本数据
# ═══════════════════════════════════════════════════════════════════════════

_SCRIPT_TAG_RE = re.compile(
    r"<script[^>]*>(.*?)</script>", re.DOTALL
)


def _scan_scripts_for_richtext(page_html: str) -> Optional[str]:
    """扫描页面中所有 ``<script>`` 标签，寻找 JSON 富文本数据并转换为 HTML。

    检测策略（按优先级）：
    1. 包含 ``"type":"doc"`` 的 ProseMirror/Tiptap 文档
    2. 包含 ``"blocks":[`` 的 Editor.js 数据
    3. 包含 ``"children":[`` + ``"type":"`` 的 Slate/Lexical 树
    4. 包含 ``"ops":[`` 的 Quill Delta

    Returns:
        转换后的 HTML 字符串，或 None。
    """
    # 收集所有有内容的 script 标签
    candidates: list[str] = []
    for m in _SCRIPT_TAG_RE.finditer(page_html):
        body = m.group(1).strip()
        # 只关注包含 JSON 特征的脚本（至少有花括号且足够长）
        if len(body) > 200 and "{" in body:
            candidates.append(body)

    for script_body in candidates:
        result = _try_parse_richtext_from_script(script_body)
        if result and len(result.strip()) > 100:
            return result

    return None


def _try_parse_richtext_from_script(script_body: str) -> Optional[str]:
    """尝试从单个 script 内容中解析富文本 JSON 数据。"""
    # 策略 1：直接尝试解析为 JSON
    json_data = _try_parse_json(script_body)

    # 策略 2：寻找赋值语句中的 JSON (如 window.xxx = {...})
    if json_data is None:
        for m in re.finditer(r'=\s*(\{.{200,})', script_body):
            json_str = _extract_json_object_str(script_body, m.start(1))
            if json_str:
                json_data = _try_parse_json(json_str)
                if json_data is not None:
                    break

    if json_data is None:
        return None

    # 尝试从 JSON 数据中找到富文本内容
    return _find_and_convert_richtext(json_data)


def _find_and_convert_richtext(data: Any, depth: int = 0) -> Optional[str]:
    """递归搜索 JSON 数据中的富文本内容并转换为 HTML。"""
    if depth > 8:  # 防止无限递归
        return None

    if isinstance(data, dict):
        # 检查是否是 ProseMirror/Tiptap 文档根节点
        if data.get("type") == "doc" and "content" in data:
            html = richtext_json_to_html(data)
            if len(html.strip()) > 100:
                return html

        # 检查是否是 Editor.js 结构
        blocks = data.get("blocks")
        if isinstance(blocks, list) and len(blocks) > 0:
            first = blocks[0] if isinstance(blocks[0], dict) else {}
            if "type" in first and "data" in first:
                html = _convert_editorjs_blocks(blocks)
                if len(html.strip()) > 100:
                    return html

        # 检查是否是 Lexical 根节点
        root = data.get("root")
        if isinstance(root, dict) and root.get("type") in ("root", "doc"):
            html = richtext_json_to_html(root)
            if len(html.strip()) > 100:
                return html

        # 递归搜索值
        for val in data.values():
            if isinstance(val, (dict, list)):
                result = _find_and_convert_richtext(val, depth + 1)
                if result:
                    return result
            elif isinstance(val, str) and len(val) > 200:
                # 值可能是 JSON 字符串
                parsed = _try_parse_json(val)
                if parsed is not None:
                    result = _find_and_convert_richtext(parsed, depth + 1)
                    if result:
                        return result

    elif isinstance(data, list) and len(data) > 0:
        first = data[0] if isinstance(data[0], dict) else {}
        # Slate/Lexical 风格: 顶层数组，每项有 type 和 children
        if "type" in first and ("children" in first or "content" in first):
            html = richtext_json_to_html(data)
            if len(html.strip()) > 100:
                return html
        # Quill Delta 风格: ops 数组
        if "insert" in first:
            html = _convert_quill_ops(data)
            if html and len(html.strip()) > 100:
                return html

    return None


def _convert_quill_ops(ops: list) -> Optional[str]:
    """将 Quill Delta ops 数组转换为 HTML。"""
    parts: list[str] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        insert = op.get("insert", "")
        attrs = op.get("attributes", {})
        if isinstance(insert, str):
            text = html_escape(insert)
            if isinstance(attrs, dict):
                if attrs.get("bold"):
                    text = f"<strong>{text}</strong>"
                if attrs.get("italic"):
                    text = f"<em>{text}</em>"
                if attrs.get("code"):
                    text = f"<code>{text}</code>"
                if "link" in attrs:
                    text = f'<a href="{html_escape(attrs["link"])}">{text}</a>'
                if attrs.get("header"):
                    level = min(max(int(attrs["header"]), 1), 6)
                    text = f"<h{level}>{text}</h{level}>\n"
            parts.append(text)
        elif isinstance(insert, dict):
            if "image" in insert:
                parts.append(f'<img src="{html_escape(insert["image"])}">\n')

    body = "".join(parts)
    if not body.strip():
        return None
    # 段落化
    paragraphs = body.split("\n\n")
    return "\n".join(f"<p>{p}</p>" for p in paragraphs if p.strip())


def _try_parse_json(s: str) -> Any:
    """尝试将字符串解析为 JSON，失败返回 None。"""
    s = s.strip()
    if not s or s[0] not in ('{', '['):
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# 检测 + 统一入口
# ═══════════════════════════════════════════════════════════════════════════

def try_ssr_extract(page_html: str, url: str = "") -> Optional[SSRContent]:
    """尝试从 *page_html* 中提取 SSR 嵌入的文章正文。

    **两阶段策略**：
    1. 精确匹配已知 SSR 框架（Next.js / Modern.js）
    2. 通用兜底：扫描 ``<script>`` 中的 JSON 富文本数据

    成功时返回 :class:`SSRContent`，无法提取时返回 ``None``。
    """
    # 阶段 1：精确匹配已知 SSR 框架
    if "__NEXT_DATA__" in page_html:
        result = _extract_nextjs(page_html, url)
        if result:
            return result

    if "_ROUTER_DATA" in page_html:
        result = _extract_modernjs(page_html, url)
        if result:
            return result

    # 阶段 2：通用兜底 — 扫描 <script> 中的 JSON 富文本数据
    fallback_html = _scan_scripts_for_richtext(page_html)
    if fallback_html:
        # 尝试从 <title> 提取标题
        title_m = re.search(r'<title[^>]*>([^<]+)</title>', page_html, re.IGNORECASE)
        title = title_m.group(1).strip() if title_m else ""
        full_html = (
            f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f"<title>{html_escape(title)}</title></head><body>"
            f"<h1>{html_escape(title)}</h1>\n{fallback_html}</body></html>"
        )
        return SSRContent(title=title, body=full_html, source_type="json_fallback",
                          is_markdown=False)

    return None


# ═══════════════════════════════════════════════════════════════════════════
# Next.js  ──  __NEXT_DATA__
# ═══════════════════════════════════════════════════════════════════════════

_NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
    re.DOTALL,
)


def _extract_nextjs(page_html: str, url: str) -> Optional[SSRContent]:
    """从 Next.js ``__NEXT_DATA__`` 中提取文章内容。"""
    m = _NEXT_DATA_RE.search(page_html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None

    # 导航到 pageProps.fallback 中的文章详情
    props = data.get("props", {}).get("pageProps", {})
    fallback = props.get("fallback", {})
    if not isinstance(fallback, dict):
        return None

    # 寻找包含 article/detail 的 key
    article_detail = None
    for key in fallback:
        if "article/detail" in key:
            article_detail = fallback[key]
            break
    if not article_detail or not isinstance(article_detail, dict):
        return None

    article_info = article_detail.get("articleInfo", {})
    if not isinstance(article_info, dict):
        return None

    title = article_info.get("title", "")
    content_raw = article_info.get("content", "")
    if not content_raw:
        return None

    # content 可能是 JSON 字符串或已解析的 dict
    if isinstance(content_raw, str):
        try:
            content_doc = json.loads(content_raw)
        except (json.JSONDecodeError, ValueError):
            return None
    else:
        content_doc = content_raw

    if not isinstance(content_doc, dict) or content_doc.get("type") != "doc":
        return None

    body_html = richtext_json_to_html(content_doc)
    if not body_html or len(body_html.strip()) < 50:
        return None

    full_html = (
        f'<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<title>{html_escape(title)}</title></head><body>"
        f"<h1>{html_escape(title)}</h1>\n{body_html}</body></html>"
    )
    return SSRContent(title=title, body=full_html, source_type="nextjs",
                      is_markdown=False)


# ═══════════════════════════════════════════════════════════════════════════
# Modern.js  ──  window._ROUTER_DATA
# ═══════════════════════════════════════════════════════════════════════════

_ROUTER_DATA_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*")


def _extract_modernjs(page_html: str, url: str) -> Optional[SSRContent]:
    """从 Modern.js ``window._ROUTER_DATA`` 中提取文档内容。"""
    m = _ROUTER_DATA_RE.search(page_html)
    if not m:
        return None

    json_str = _extract_json_object_str(page_html, m.end())
    if not json_str:
        return None

    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None

    loader = data.get("loaderData", {})
    if not isinstance(loader, dict):
        return None

    cur_doc = None
    for key, val in loader.items():
        if isinstance(val, dict) and "curDoc" in val:
            cur_doc = val["curDoc"]
            break
    if not cur_doc or not isinstance(cur_doc, dict):
        return None

    title = cur_doc.get("Title", "") or cur_doc.get("title", "")
    md_content = cur_doc.get("MDContent", "")

    if md_content and len(md_content.strip()) > 50:
        cleaned = _clean_md_content(md_content)
        return SSRContent(title=title, body=cleaned, source_type="modernjs",
                          is_markdown=True)

    # 回退：尝试从 Content 字段提取
    content_raw = cur_doc.get("Content", "")
    if content_raw:
        body_html = _quill_content_to_html(content_raw, title)
        if body_html:
            return SSRContent(title=title, body=body_html, source_type="modernjs",
                              is_markdown=False)

    return None


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _extract_json_object_str(html: str, start: int) -> Optional[str]:
    """从 *start* 位置开始提取一个完整的 JSON 对象字符串。"""
    if start >= len(html) or html[start] != "{":
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, min(start + 5_000_000, len(html))):
        c = html[i]
        if escape_next:
            escape_next = False
            continue
        if c == "\\":
            if in_string:
                escape_next = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return html[start: i + 1]
    return None


def _clean_md_content(md_text: str) -> str:
    """清理 MDContent 中的框架残留和特殊语法。"""
    md_text = re.sub(
        r'\n\s*`?\}?\s*>\s*</RenderMd>.*$', '', md_text, flags=re.DOTALL
    )
    md_text = re.sub(r'^:::(\w+)\s*$', r'> **\1**:', md_text, flags=re.MULTILINE)
    md_text = re.sub(r'^:::\s*$', '', md_text, flags=re.MULTILINE)
    md_text = re.sub(r'<span\s+id="[^"]*"\s*>\s*</span>', '', md_text)
    md_text = re.sub(r'(!\[[^\]]*\]\([^)]+?)\s+=\d+x\d*\s*(\))', r'\1\2', md_text)
    return md_text.strip()


# ---------------------------------------------------------------------------
# 辅助：从 Markdown 文本中提取图片 URL
# ---------------------------------------------------------------------------
_MD_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')
_MD_SIZE_HINT_RE = re.compile(r'\s+=\d+x\d*\s*$')
# 标准 Markdown 图片 title：![alt](url "title") 或 ![alt](url 'title')
_MD_TITLE_RE = re.compile(r'\s+["\'][^"\']*["\']\s*$')


def collect_md_image_urls(md_text: str, base_url: str = "") -> list[str]:
    """从 Markdown 文本中提取所有图片 URL（``![alt](url)`` 格式）。

    自动去除非标准的尺寸提示（如 ``=986x``）。
    如果提供了 *base_url*，相对 URL（如 ``/assets/a.png``）会被解析为绝对 URL。

    .. note:: 如果 Markdown 中包含相对 URL 且需要后续做 URL 替换，
       请先调用 :func:`resolve_relative_md_images` 将文本中的相对 URL
       统一为绝对 URL，再调用本函数。
    """
    urls: list[str] = []
    for m in _MD_IMAGE_RE.finditer(md_text):
        url = m.group(1).strip()
        # 先剔除非标准尺寸提示（始终在最末尾）：url =986x → url
        url = _MD_SIZE_HINT_RE.sub('', url).strip()
        # 再剔除标准 Markdown title 文本：![alt](url "title") → url
        url = _MD_TITLE_RE.sub('', url).strip()
        if not url:
            continue
        if url.startswith(("http://", "https://")):
            urls.append(url)
        elif base_url and not url.startswith("data:"):
            # 相对 URL → 基于页面 URL 解析为绝对 URL
            resolved = urljoin(base_url, url)
            if resolved.startswith(("http://", "https://")):
                urls.append(resolved)
    return urls


# 匹配 Markdown 图片语法（捕获 alt 和 url 部分）
_MD_IMG_FULL_RE = re.compile(r'(!\[[^\]]*\]\()([^)]+)(\))')


def resolve_relative_md_images(md_text: str, base_url: str) -> str:
    """将 Markdown 文本中的相对图片 URL 替换为绝对 URL。

    例如 ``![img](/assets/a.png)`` → ``![img](https://example.com/assets/a.png)``

    这确保后续 ``collect_md_image_urls`` 返回的 URL 和 Markdown 文本中的
    URL 一致，使得 ``replace_image_urls_in_markdown`` 能正确匹配替换。
    """
    if not base_url:
        return md_text

    def _resolve(m: re.Match) -> str:
        prefix = m.group(1)   # "![alt]("
        raw_url = m.group(2)  # "/assets/a.png" or "https://..." or 'url "title"'
        suffix = m.group(3)   # ")"
        # 提取纯 URL（先剔除尺寸提示，再剔除 title）
        url = raw_url.strip()
        url = _MD_SIZE_HINT_RE.sub('', url).strip()
        title_match = _MD_TITLE_RE.search(url)
        title_part = title_match.group(0) if title_match else ""
        url = _MD_TITLE_RE.sub('', url).strip()
        if url.startswith(("http://", "https://", "data:")):
            return m.group(0)  # 已经是绝对 URL，不变
        resolved = urljoin(base_url, url)
        if resolved.startswith(("http://", "https://")):
            # 保留原始 title 部分
            return f"{prefix}{resolved}{title_part}{suffix}"
        return m.group(0)

    return _MD_IMG_FULL_RE.sub(_resolve, md_text)


def _quill_content_to_html(content_raw: str, title: str) -> Optional[str]:
    """从 Volcengine 的 Content 字段（Quill Delta JSON）提取并转为 HTML。"""
    try:
        content = json.loads(content_raw)
    except (json.JSONDecodeError, ValueError):
        return None

    data = content.get("data", {})
    if not isinstance(data, dict):
        return None

    all_ops: list[dict] = []
    for _key, section in sorted(data.items(), key=lambda x: str(x[0])):
        ops = section.get("ops", []) if isinstance(section, dict) else []
        all_ops.extend(ops)

    if not all_ops:
        return None

    body_html = _convert_quill_ops(all_ops)
    if not body_html or len(body_html.strip()) < 50:
        return None

    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<title>{html_escape(title)}</title></head><body>"
        f"<h1>{html_escape(title)}</h1>\n{body_html}</body></html>"
    )
