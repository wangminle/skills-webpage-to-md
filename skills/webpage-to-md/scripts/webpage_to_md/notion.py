"""Notion 公开页面提取模块。

通过 Notion 内部 API（/api/v3/）递归获取公开页面的 Block 数据，
转换为 HTML 后供 html_to_markdown 转换链使用。

**仅适用于公开页面**，私有页面需要 Cookie 或 Integration Token。

公共 API：
- :func:`is_notion_url` — 判断是否为 Notion 公开页面 URL
- :func:`fetch_notion_page` — 获取 Notion 页面并返回 HTML + 标题
"""

from __future__ import annotations

import re
import sys
import time
from html import escape as html_escape
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import requests

# Notion 内部 API 端点
_API_BASE = "https://www.notion.so/api/v3"
_LOAD_PAGE_CHUNK = f"{_API_BASE}/loadPageChunk"
_SYNC_RECORD_VALUES = f"{_API_BASE}/syncRecordValues"

_NOTION_HOST_RE = re.compile(
    r"^(?:(?:www\.)?notion\.so|[\w-]+\.notion\.site)$", re.IGNORECASE
)
_PAGE_ID_RE = re.compile(r"([0-9a-f]{32})$")

_SYNC_BATCH_SIZE = 50
_MAX_RECURSION_ROUNDS = 20


def is_notion_url(url: str) -> bool:
    """判断 URL 是否为 Notion 公开页面链接（域名匹配 + 路径中包含可解析的 32 位 page ID）。"""
    try:
        parsed = urlparse(url)
        if not (
            parsed.scheme in ("http", "https")
            and _NOTION_HOST_RE.match(parsed.hostname or "")
            and parsed.path
            and parsed.path != "/"
        ):
            return False
        return _extract_page_id(url) is not None
    except Exception:
        return False


def _extract_page_id(url: str) -> Optional[str]:
    """从 Notion URL 中提取并格式化 Page ID（插入连字符）。"""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    # URL 末尾 32 位 hex 就是 page id
    m = _PAGE_ID_RE.search(path.replace("-", ""))
    if not m:
        # 尝试从整个路径中匹配
        clean = path.split("/")[-1].replace("-", "")
        m = _PAGE_ID_RE.search(clean)
    if not m:
        return None
    raw = m.group(1)
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def _api_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# API 调用
# ═══════════════════════════════════════════════════════════════════════════

def _load_page_chunk(
    session: requests.Session,
    page_id: str,
    timeout_s: int = 30,
) -> Dict[str, Any]:
    """调用 loadPageChunk 获取页面初始 Block 数据。"""
    payload = {
        "page": {"id": page_id},
        "limit": 300,
        "cursor": {"stack": []},
        "chunkNumber": 0,
        "verticalColumns": False,
    }
    r = session.post(
        _LOAD_PAGE_CHUNK,
        json=payload,
        headers=_api_headers(),
        timeout=timeout_s,
    )
    r.raise_for_status()
    return r.json()


def _sync_record_values(
    session: requests.Session,
    block_ids: List[str],
    timeout_s: int = 30,
) -> Dict[str, Any]:
    """调用 syncRecordValues 批量获取指定 Block 的详细数据。"""
    requests_list = [
        {"pointer": {"table": "block", "id": bid}, "version": -1}
        for bid in block_ids
    ]
    payload = {"requests": requests_list}
    r = session.post(
        _SYNC_RECORD_VALUES,
        json=payload,
        headers=_api_headers(),
        timeout=timeout_s,
    )
    r.raise_for_status()
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# 递归获取全部 Block
# ═══════════════════════════════════════════════════════════════════════════

def _fetch_all_blocks(
    session: requests.Session,
    page_id: str,
    timeout_s: int = 30,
) -> Dict[str, Dict]:
    """递归获取页面所有 Block 数据，返回 {block_id: block_value} 映射。"""
    all_blocks: Dict[str, Dict] = {}

    # Step 1: loadPageChunk
    data = _load_page_chunk(session, page_id, timeout_s)
    record_map = data.get("recordMap", {})
    block_map = record_map.get("block", {})
    for bid, entry in block_map.items():
        value = entry.get("value", {})
        if value:
            all_blocks[bid] = value

    # Step 2: 递归获取子 Block
    for _ in range(_MAX_RECURSION_ROUNDS):
        missing: List[str] = []
        for block in list(all_blocks.values()):
            for child_id in block.get("content", []):
                if child_id not in all_blocks:
                    missing.append(child_id)
        if not missing:
            break

        # 批量获取
        for i in range(0, len(missing), _SYNC_BATCH_SIZE):
            batch = missing[i : i + _SYNC_BATCH_SIZE]
            resp = _sync_record_values(session, batch, timeout_s)
            results = resp.get("recordMap", {}).get("block", {})
            for bid, entry in results.items():
                value = entry.get("value", {})
                if value:
                    all_blocks[bid] = value
            if i + _SYNC_BATCH_SIZE < len(missing):
                time.sleep(0.2)

    return all_blocks


# ═══════════════════════════════════════════════════════════════════════════
# Block → HTML 转换
# ═══════════════════════════════════════════════════════════════════════════

def _rich_text_to_html(title_arr: Any) -> str:
    """将 Notion 富文本数组转换为 HTML 字符串。

    格式: [["text", [["b"], ["i"], ["a", "url"], ...]], ["plain text"], ...]
    """
    if not isinstance(title_arr, list):
        return ""
    parts: List[str] = []
    for segment in title_arr:
        if not isinstance(segment, list) or not segment:
            continue
        text = html_escape(str(segment[0]))
        if len(segment) > 1 and isinstance(segment[1], list):
            for fmt in segment[1]:
                if not isinstance(fmt, list) or not fmt:
                    continue
                code = fmt[0]
                if code == "b":
                    text = f"<b>{text}</b>"
                elif code == "i":
                    text = f"<i>{text}</i>"
                elif code == "s":
                    text = f"<s>{text}</s>"
                elif code == "c":
                    text = f"<code>{text}</code>"
                elif code == "_":
                    text = f"<u>{text}</u>"
                elif code == "h":
                    text = f"<mark>{text}</mark>"
                elif code == "a" and len(fmt) > 1:
                    href = html_escape(str(fmt[1]))
                    text = f'<a href="{href}">{text}</a>'
        parts.append(text)
    return "".join(parts)


def _get_block_title(block: Dict) -> str:
    """提取 block 的标题/文本内容。"""
    props = block.get("properties", {})
    if not isinstance(props, dict):
        return ""
    return _rich_text_to_html(props.get("title", []))


def _make_image_proxy_url(raw_url: str, block_id: str) -> str:
    """将 Notion 内部图片 URL 转为代理 URL。"""
    if raw_url.startswith("attachment:") or "s3" in raw_url:
        encoded = quote(raw_url, safe="")
        return f"https://www.notion.so/image/{encoded}?table=block&id={block_id}"
    if raw_url.startswith("/"):
        return f"https://www.notion.so{raw_url}"
    return raw_url


def _get_image_url(block: Dict) -> str:
    """从 image block 中提取图片 URL。"""
    props = block.get("properties", {})
    sources = []
    if isinstance(props, dict):
        src = props.get("source", [])
        if isinstance(src, list) and src:
            first = src[0]
            if isinstance(first, list) and first:
                sources.append(str(first[0]))
    fmt = block.get("format", {})
    if isinstance(fmt, dict):
        display_src = fmt.get("display_source", "")
        if display_src:
            sources.append(str(display_src))
    return sources[0] if sources else ""


def _get_caption(block: Dict) -> str:
    """提取 image/embed block 的 caption。"""
    props = block.get("properties", {})
    if isinstance(props, dict):
        cap = props.get("caption", [])
        return _rich_text_to_html(cap)
    return ""


def _blocks_to_html(
    all_blocks: Dict[str, Dict],
    page_id: str,
) -> Tuple[str, str]:
    """将 Block 树转换为 HTML，返回 (html_content, page_title)。"""
    page_block = all_blocks.get(page_id, {})
    page_title = ""
    props = page_block.get("properties", {})
    if isinstance(props, dict):
        title_arr = props.get("title", [])
        if isinstance(title_arr, list) and title_arr:
            first = title_arr[0]
            if isinstance(first, list) and first:
                page_title = str(first[0])

    content_ids = page_block.get("content", [])
    body_html = _render_block_children(all_blocks, content_ids)

    full_html = (
        f'<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<title>{html_escape(page_title)}</title></head><body>"
        f"<h1>{html_escape(page_title)}</h1>\n{body_html}</body></html>"
    )
    return full_html, page_title


def _render_block_children(
    all_blocks: Dict[str, Dict],
    child_ids: List[str],
) -> str:
    """递归渲染一组子 Block ID 列表为 HTML。"""
    parts: List[str] = []
    i = 0
    while i < len(child_ids):
        bid = child_ids[i]
        block = all_blocks.get(bid, {})
        btype = block.get("type", "")

        # 列表合并：连续的同类列表项包裹到一个 <ul>/<ol> 中
        if btype in ("bulleted_list", "numbered_list"):
            tag = "ul" if btype == "bulleted_list" else "ol"
            items: List[str] = []
            while i < len(child_ids):
                cb = all_blocks.get(child_ids[i], {})
                if cb.get("type") != btype:
                    break
                item_html = _get_block_title(cb)
                sub_ids = cb.get("content", [])
                if sub_ids:
                    item_html += _render_block_children(all_blocks, sub_ids)
                items.append(f"<li>{item_html}</li>\n")
                i += 1
            parts.append(f"<{tag}>\n{''.join(items)}</{tag}>\n")
            continue

        parts.append(_render_single_block(all_blocks, block))
        i += 1
    return "".join(parts)


def _render_single_block(all_blocks: Dict[str, Dict], block: Dict) -> str:
    """将单个 Block 转换为 HTML 片段。"""
    btype = block.get("type", "")
    bid = block.get("id", "")
    title = _get_block_title(block)
    child_ids = block.get("content", [])
    children_html = _render_block_children(all_blocks, child_ids) if child_ids else ""

    if btype == "text":
        if not title and not children_html:
            return ""
        return f"<p>{title}</p>\n{children_html}"

    if btype == "header":
        return f"<h1>{title}</h1>\n{children_html}"
    if btype == "sub_header":
        return f"<h2>{title}</h2>\n{children_html}"
    if btype == "sub_sub_header":
        return f"<h3>{title}</h3>\n{children_html}"

    if btype == "to_do":
        props = block.get("properties", {})
        checked = False
        if isinstance(props, dict):
            ch = props.get("checked", [])
            if isinstance(ch, list) and ch:
                first = ch[0]
                if isinstance(first, list) and first:
                    checked = str(first[0]).lower() == "yes"
        marker = "☑" if checked else "☐"
        return f"<p>{marker} {title}</p>\n{children_html}"

    if btype == "toggle":
        return (
            f"<details><summary>{title}</summary>\n"
            f"{children_html}</details>\n"
        )

    if btype == "quote":
        return f"<blockquote>{title}{children_html}</blockquote>\n"

    if btype == "callout":
        fmt = block.get("format", {})
        icon = ""
        if isinstance(fmt, dict):
            icon_val = fmt.get("page_icon", "")
            if icon_val:
                icon = f"{html_escape(str(icon_val))} "
        return f'<div class="callout"><p>{icon}{title}</p>{children_html}</div>\n'

    if btype == "code":
        props = block.get("properties", {})
        lang = ""
        if isinstance(props, dict):
            lang_arr = props.get("language", [])
            if isinstance(lang_arr, list) and lang_arr:
                first = lang_arr[0]
                if isinstance(first, list) and first:
                    lang = str(first[0]).lower()
        # title 中的文本已被 html_escape，但代码块内容需要原始文本
        raw_title = ""
        if isinstance(props, dict):
            raw_arr = props.get("title", [])
            if isinstance(raw_arr, list):
                raw_parts = []
                for seg in raw_arr:
                    if isinstance(seg, list) and seg:
                        raw_parts.append(str(seg[0]))
                raw_title = "".join(raw_parts)
        code_content = html_escape(raw_title) if raw_title else title
        cls = f' class="language-{html_escape(lang)}"' if lang else ""
        return f"<pre><code{cls}>{code_content}</code></pre>\n"

    if btype == "image":
        raw_url = _get_image_url(block)
        if raw_url:
            proxy_url = _make_image_proxy_url(raw_url, bid)
            caption = _get_caption(block)
            alt = caption or ""
            img_tag = f'<img src="{html_escape(proxy_url)}" alt="{html_escape(alt)}">'
            if caption:
                return f"<figure>{img_tag}<figcaption>{caption}</figcaption></figure>\n"
            return f"{img_tag}\n"
        return ""

    if btype == "divider":
        return "<hr/>\n"

    if btype == "bookmark":
        props = block.get("properties", {})
        link = ""
        link_title = ""
        if isinstance(props, dict):
            link_arr = props.get("link", [])
            if isinstance(link_arr, list) and link_arr:
                first = link_arr[0]
                if isinstance(first, list) and first:
                    link = str(first[0])
            title_arr = props.get("title", [])
            if isinstance(title_arr, list) and title_arr:
                first = title_arr[0]
                if isinstance(first, list) and first:
                    link_title = str(first[0])
        if link:
            display = link_title or link
            return f'<p><a href="{html_escape(link)}">{html_escape(display)}</a></p>\n'
        return ""

    if btype in ("column_list", "column"):
        return f'<div class="columns">{children_html}</div>\n'

    if btype in ("embed", "video", "audio", "file"):
        props = block.get("properties", {})
        src = ""
        if isinstance(props, dict):
            src_arr = props.get("source", [])
            if isinstance(src_arr, list) and src_arr:
                first = src_arr[0]
                if isinstance(first, list) and first:
                    src = str(first[0])
        if src:
            return f'<p><a href="{html_escape(src)}">{html_escape(src)}</a></p>\n'
        return children_html

    if btype == "table_of_contents":
        return ""

    if btype == "page":
        if title:
            return f"<h2>{title}</h2>\n{children_html}"
        return children_html

    # 兜底
    if title:
        return f"<p>{title}</p>\n{children_html}"
    return children_html


# ═══════════════════════════════════════════════════════════════════════════
# 公共入口
# ═══════════════════════════════════════════════════════════════════════════

def fetch_notion_page(
    url: str,
    timeout_s: int = 30,
    retries: int = 3,
) -> Tuple[str, str]:
    """获取 Notion 公开页面内容。

    Args:
        url: Notion 页面 URL
        timeout_s: 请求超时（秒）
        retries: 重试次数

    Returns:
        (html_content, page_title) 元组

    Raises:
        ValueError: URL 无法解析出 Page ID
        requests.HTTPError: API 请求失败
    """
    page_id = _extract_page_id(url)
    if not page_id:
        raise ValueError(f"无法从 URL 中提取 Notion Page ID：{url}")

    session = requests.Session()
    session.headers.update(_api_headers())

    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            all_blocks = _fetch_all_blocks(session, page_id, timeout_s)
            html, title = _blocks_to_html(all_blocks, page_id)
            return html, title
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(min(2.0, 0.5 * attempt))
                print(f"  Notion API 重试 ({attempt}/{retries})：{e}", file=sys.stderr)

    raise last_err or RuntimeError("Notion page fetch failed")
