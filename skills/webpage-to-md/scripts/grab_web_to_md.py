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
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Callable, Union
from urllib.parse import urljoin, urlparse, unquote, quote

import requests


# ============================================================================
# 退出码定义
# ============================================================================
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_EXISTS = 2
EXIT_VALIDATION_FAILED = 3
EXIT_JS_CHALLENGE = 4  # 检测到 JS 反爬保护，无法获取内容


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


_DEFAULT_MAX_IMAGE_BYTES = 25 * 1024 * 1024  # 25MB/张；设为 0 表示不限制
_DEFAULT_MAX_HTML_BYTES = 10 * 1024 * 1024  # 10MB/页；设为 0 表示不限制


def redact_url(url: str) -> str:
    """
    URL 脱敏：默认仅保留 scheme://host/path，移除 query/fragment。

    - 仅对 http/https 且含 netloc 的 URL 生效
    - 其他形式（相对路径、空字符串等）原样返回
    """
    try:
        p = urlparse(url)
        if p.scheme in ("http", "https") and p.netloc:
            return p._replace(query="", fragment="").geturl()
    except Exception:
        pass
    return url


def _redact_url_to_local_map(url_to_local: Dict[str, str]) -> Dict[str, Union[str, List[str]]]:
    """
    将 URL->本地路径映射中的 URL 脱敏（去 query/fragment）。
    为避免脱敏后 key 冲突导致覆盖，冲突时把 value 变成列表。
    """
    out: Dict[str, Union[str, List[str]]] = {}
    for raw_url, local_path in url_to_local.items():
        key = redact_url(raw_url)
        if key in out:
            prev = out[key]
            if isinstance(prev, list):
                if local_path not in prev:
                    prev.append(local_path)
            else:
                if local_path != prev:
                    out[key] = [prev, local_path]
        else:
            out[key] = local_path
    return out


_MD_HTTP_LINK_DEST_RE = re.compile(
    r"\]\(\s*(?P<langle><)?(?P<url>https?://[^)\s>]+)(?P<rangle>>)?(?P<title>\s+\"[^\"]*\")?\s*\)"
)
_HTML_HTTP_ATTR_RE = re.compile(r"(?P<prefix>\b(?:src|href)=['\"])(?P<url>https?://[^'\"]+)(?P<suffix>['\"])")


def redact_urls_in_markdown(md_text: str) -> str:
    """
    对 Markdown 正文中的 http/https URL 做脱敏（移除 query/fragment）。

    注意：
    - 仅处理脚本自身生成/常见的两类形式：
      1) 行内链接/图片：...](https://...) 或 ...](<https://...>)
      2) HTML 属性：src="https://..." / href="https://..."
    - 不处理纯文本裸 URL、srcset 等复杂场景（避免误伤）。
    """
    if not md_text:
        return md_text

    def _md_repl(m: re.Match[str]) -> str:
        url = m.group("url")
        safe = redact_url(url)
        langle = m.group("langle") or ""
        rangle = m.group("rangle") or ""
        title = m.group("title") or ""
        return f"]({langle}{safe}{rangle}{title})"

    def _html_repl(m: re.Match[str]) -> str:
        url = m.group("url")
        return f"{m.group('prefix')}{redact_url(url)}{m.group('suffix')}"

    out = _MD_HTTP_LINK_DEST_RE.sub(_md_repl, md_text)
    out = _HTML_HTTP_ATTR_RE.sub(_html_repl, out)
    return out


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _is_same_host(url_a: str, url_b: str) -> bool:
    ha = _host_of(url_a)
    hb = _host_of(url_b)
    return bool(ha) and ha == hb


def _create_anonymous_image_session(base_session: requests.Session) -> requests.Session:
    """
    创建“干净 session”用于跨域图片下载：
    - 不携带 Cookie / Authorization / 自定义 Header
    - 只保留少量安全 Header（如 UA / Accept-Language）
    """
    s = requests.Session()
    # 继承网络配置，避免“页面可访问但跨域图片因代理/证书/adapter 不一致而失败”
    try:
        s.trust_env = base_session.trust_env
    except Exception:
        pass
    try:
        s.proxies = dict(getattr(base_session, "proxies", {}) or {})
    except Exception:
        pass
    try:
        s.verify = getattr(base_session, "verify", True)
    except Exception:
        pass
    try:
        s.cert = getattr(base_session, "cert", None)
    except Exception:
        pass
    try:
        # 复用 base_session 的 adapter（如自定义 TLS/重试/代理适配器）
        for prefix, adapter in getattr(base_session, "adapters", {}).items():
            s.mount(prefix, adapter)
    except Exception:
        pass

    ua = base_session.headers.get("User-Agent") or UA_PRESETS["chrome-win"]
    accept_lang = base_session.headers.get("Accept-Language") or "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"
    s.headers.update(
        {
            "User-Agent": ua,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": accept_lang,
        }
    )
    return s


# 最大重定向次数，防止无限循环
_MAX_REDIRECTS = 10


def _safe_image_get(
    img_url: str,
    page_url: str,
    session: requests.Session,
    anon_session: requests.Session,
    timeout_s: int,
    referer: str,
) -> requests.Response:
    """
    安全地 GET 图片 URL，手动处理重定向并在跨域时切换到干净 session。
    
    防止同域 URL 重定向到第三方 CDN 时泄露敏感请求头。
    """
    current_url = img_url
    current_session = session if _is_same_host(img_url, page_url) else anon_session
    
    for _ in range(_MAX_REDIRECTS):
        headers = {"Connection": "close"}
        if referer:
            headers["Referer"] = referer
        r = current_session.get(
            current_url,
            timeout=timeout_s,
            stream=True,
            allow_redirects=False,  # 关键：禁用自动重定向
            headers=headers,
        )
        
        # 非重定向响应，直接返回
        if r.status_code not in (301, 302, 303, 307, 308):
            return r
        
        # 获取重定向目标
        location = r.headers.get("Location")
        if not location:
            # 没有 Location 头：视为错误，避免 3xx 被误当作成功写入图片
            try:
                r.close()
            except Exception:
                pass
            raise RuntimeError(f"图片重定向响应缺少 Location 头: {current_url} (status={r.status_code})")
        
        # 关闭当前响应
        try:
            r.close()
        except Exception:
            pass
        
        # 解析重定向目标（可能是相对路径）
        next_url = urljoin(current_url, location)
        
        # 每次重定向都按“目标 URL 是否与 page_url 同 host”重新选择 session：
        # - 同 host：允许携带 Cookie/Auth
        # - 跨 host：使用干净 session
        current_session = session if _is_same_host(next_url, page_url) else anon_session
        
        current_url = next_url
    
    # 超过最大重定向次数
    raise RuntimeError(f"图片 URL 重定向次数超过 {_MAX_REDIRECTS} 次: {img_url}")


def yaml_escape_str(s: str) -> str:
    """
    统一的 YAML 双引号字符串转义（Phase 3-C 增强）
    
    处理以下特殊字符：
    - \\ -> \\\\  (反斜杠必须首先处理)
    - "  -> \\"   (双引号)
    - \\n -> 空格  (换行)
    - \\r -> 空格  (回车)
    - \\t -> 空格  (制表符)
    """
    if not s:
        return ""
    # 注意顺序：先处理反斜杠，避免二次转义
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", " ")
    s = s.replace("\r", " ")
    s = s.replace("\t", " ")
    return s.strip()


def escape_markdown_link_text(text: str) -> str:
    """
    转义 Markdown 链接文本中的特殊字符（Phase 3-C 增强）
    
    处理 [ 和 ] 字符，避免破坏链接语法：
    - [ -> \\[
    - ] -> \\]
    """
    if not text:
        return ""
    return text.replace("[", "\\[").replace("]", "\\]")


def generate_frontmatter(title: str, url: str, tags: Optional[List[str]] = None) -> str:
    """生成 YAML Frontmatter 元数据头，兼容 Obsidian/Hugo/Jekyll 等工具。"""
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 使用统一的 YAML 转义
    safe_title = yaml_escape_str(title)
    safe_url = yaml_escape_str(url or "")
    lines = [
        "---",
        f'title: "{safe_title}"',
        f'source: "{safe_url}"',
        f'date: "{date_str}"',
    ]
    if tags:
        # 对每个标签也进行转义
        tags_str = ", ".join(f'"{yaml_escape_str(t)}"' for t in tags)
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


def auto_wrap_output_dir(output_path: str) -> str:
    """
    自动为输出文件创建同名上级目录（如果用户未指定目录）
    
    规则：
    - 如果输出路径包含目录（如 "docs/article.md"），保持不变
    - 如果只有文件名（如 "article.md"），创建同名目录 -> "article/article.md"
    
    Args:
        output_path: 原始输出路径
    
    Returns:
        处理后的输出路径
    
    Examples:
        >>> auto_wrap_output_dir("article.md")
        'article/article.md'
        >>> auto_wrap_output_dir("docs/article.md")
        'docs/article.md'
        >>> auto_wrap_output_dir("./output.md")
        './output.md'
    """
    dirname = os.path.dirname(output_path)
    if dirname:  # 用户指定了目录（包括 "./" 或 "../"）
        return output_path
    # 没有目录，创建同名目录
    basename = os.path.basename(output_path)
    name_without_ext = os.path.splitext(basename)[0]
    return os.path.join(name_without_ext, basename)


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


def extract_target_html_multi(
    page_html: str,
    *,
    target_ids: Optional[str] = None,
    target_classes: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    支持多值的正文提取（T2.1）
    
    Args:
        page_html: HTML 内容
        target_ids: 逗号分隔的 ID 列表，按优先级依次尝试
        target_classes: 逗号分隔的 class 列表，按优先级依次尝试
    
    Returns:
        (提取的 HTML, 匹配的选择器描述) 或 (None, None)
    """
    # 解析多值
    ids = [s.strip() for s in (target_ids or "").split(",") if s.strip()]
    classes = [s.strip() for s in (target_classes or "").split(",") if s.strip()]
    
    # 优先尝试 ID
    for tid in ids:
        result = extract_target_html(page_html, target_id=tid, target_class=None)
        if result:
            return result, f"id={tid}"
    
    # 然后尝试 class
    for tcls in classes:
        result = extract_target_html(page_html, target_id=None, target_class=tcls)
        if result:
            return result, f"class={tcls}"
    
    return None, None


# ============================================================================
# Phase 2: 智能正文容器定位（T2.1 - T2.4）
# ============================================================================

@dataclass
class DocsPreset:
    """文档框架预设配置（T2.2）"""
    name: str
    description: str
    # 检测特征（任一匹配即可）
    detect_patterns: List[str]  # HTML 中的关键字符串
    detect_classes: List[str]   # 检测的 class 名
    detect_meta: List[str]      # meta 标签内容
    # 提取配置
    target_ids: List[str]       # 正文容器 ID（按优先级）
    target_classes: List[str]   # 正文容器 class（按优先级）
    exclude_selectors: List[str]  # 需要排除的选择器


# 框架预设配置
DOCS_PRESETS: Dict[str, DocsPreset] = {
    "docusaurus": DocsPreset(
        name="docusaurus",
        description="Docusaurus (Meta/Facebook)",
        detect_patterns=["docusaurus", "__docusaurus"],
        detect_classes=["docusaurus-wrapper", "theme-doc-markdown"],
        detect_meta=["generator.*docusaurus"],
        target_ids=["__docusaurus_skipToContent_fallback"],
        target_classes=["theme-doc-markdown", "markdown", "docMainContainer"],
        exclude_selectors=[
            ".theme-doc-sidebar-container",
            ".pagination-nav",
            ".theme-doc-toc-mobile",
            ".theme-doc-toc-desktop",
            ".theme-doc-breadcrumbs",
            "nav",
            "aside",
            ".table-of-contents",
        ],
    ),
    "mintlify": DocsPreset(
        name="mintlify",
        description="Mintlify",
        detect_patterns=["mintlify", "mintcdn.com"],
        detect_classes=["mintlify"],
        detect_meta=[],
        target_ids=["content-area"],
        target_classes=["prose", "article-content", "markdown-body"],
        exclude_selectors=[
            "nav",
            "aside",
            ".sidebar",
            ".on-this-page",
            ".page-navigation",
            "[data-testid='sidebar']",
        ],
    ),
    "gitbook": DocsPreset(
        name="gitbook",
        description="GitBook",
        detect_patterns=["gitbook", "app.gitbook.com"],
        detect_classes=["gb-root", "gitbook-root"],
        detect_meta=["generator.*gitbook"],
        target_ids=[],
        target_classes=["markdown-section", "page-inner", "book-body"],
        exclude_selectors=[
            ".book-summary",
            ".navigation",
            "nav",
            ".page-toc",
        ],
    ),
    "vuepress": DocsPreset(
        name="vuepress",
        description="VuePress",
        detect_patterns=["vuepress", "VuePress"],
        detect_classes=["theme-default-content", "vuepress"],
        detect_meta=["generator.*vuepress"],
        target_ids=[],
        target_classes=["theme-default-content", "page", "content__default"],
        exclude_selectors=[
            ".sidebar",
            ".page-nav",
            ".page-edit",
            "nav",
            ".table-of-contents",
        ],
    ),
    "mkdocs": DocsPreset(
        name="mkdocs",
        description="MkDocs / Material for MkDocs",
        detect_patterns=["mkdocs", "MkDocs"],
        detect_classes=["md-content", "md-main"],
        detect_meta=["generator.*mkdocs"],
        target_ids=["content"],
        target_classes=["md-content__inner", "md-typeset", "rst-content"],
        exclude_selectors=[
            ".md-sidebar",
            ".md-nav",
            ".md-footer",
            ".md-header",
            "nav",
        ],
    ),
    "readthedocs": DocsPreset(
        name="readthedocs",
        description="Read the Docs / Sphinx",
        detect_patterns=["readthedocs", "sphinx", "Read the Docs"],
        detect_classes=["rst-content", "wy-nav-content"],
        detect_meta=["generator.*sphinx"],
        target_ids=[],
        target_classes=["rst-content", "document", "body"],
        exclude_selectors=[
            ".wy-nav-side",
            ".wy-side-nav-search",
            ".rst-versions",
            "nav",
            ".toctree-wrapper",
        ],
    ),
    "notion": DocsPreset(
        name="notion",
        description="Notion (exported or public pages)",
        detect_patterns=["notion.so", "notion-static"],
        detect_classes=["notion-page-content", "notion-app"],
        detect_meta=[],
        target_ids=[],
        target_classes=["notion-page-content", "notion-scroller"],
        exclude_selectors=[
            ".notion-sidebar",
            ".notion-topbar",
            "nav",
        ],
    ),
    "confluence": DocsPreset(
        name="confluence",
        description="Atlassian Confluence",
        detect_patterns=["confluence", "atlassian"],
        detect_classes=["wiki-content", "confluence-content"],
        detect_meta=[],
        target_ids=["main-content", "content"],
        target_classes=["wiki-content", "confluence-content-body"],
        exclude_selectors=[
            "#navigation",
            ".aui-sidebar",
            ".page-metadata",
            "nav",
        ],
    ),
    "sphinx": DocsPreset(
        name="sphinx",
        description="Sphinx documentation",
        detect_patterns=["sphinx", "Sphinx"],
        detect_classes=["sphinxsidebar", "document"],
        detect_meta=["generator.*sphinx"],
        target_ids=["content", "main-content"],
        target_classes=["document", "body", "rst-content"],
        exclude_selectors=[
            ".sphinxsidebar",
            ".sphinxsidebarwrapper",
            ".related",
            "nav",
            ".toctree-wrapper",
        ],
    ),
    "generic": DocsPreset(
        name="generic",
        description="Generic documentation site",
        detect_patterns=[],
        detect_classes=[],
        detect_meta=[],
        target_ids=["content", "main-content", "main"],
        target_classes=["content", "main-content", "article-content", "markdown-body"],
        exclude_selectors=[
            "nav",
            "aside",
            ".sidebar",
            ".navigation",
            ".toc",
            ".table-of-contents",
        ],
    ),
}


def detect_docs_framework(page_html: str) -> Tuple[Optional[str], float, List[str]]:
    """
    自动检测文档框架类型（T2.3）
    
    Args:
        page_html: HTML 内容
    
    Returns:
        (框架名称, 置信度 0-1, 匹配的特征列表) 或 (None, 0, [])
    """
    if not page_html:
        return None, 0.0, []
    
    html_lower = page_html.lower()
    best_match: Optional[str] = None
    best_score = 0.0
    best_signals: List[str] = []
    
    for name, preset in DOCS_PRESETS.items():
        signals: List[str] = []
        score = 0.0
        
        # 检测关键字符串
        for pattern in preset.detect_patterns:
            if pattern.lower() in html_lower:
                signals.append(f"pattern:{pattern}")
                score += 0.3
        
        # 检测 class
        for cls in preset.detect_classes:
            if f'class="{cls}"' in page_html or f"class='{cls}'" in page_html or f' {cls}' in page_html:
                signals.append(f"class:{cls}")
                score += 0.25
        
        # 检测 meta（正则）
        for meta_pattern in preset.detect_meta:
            if re.search(meta_pattern, page_html, re.IGNORECASE):
                signals.append(f"meta:{meta_pattern}")
                score += 0.35
        
        # 归一化分数（最高 1.0）
        score = min(1.0, score)
        
        if score > best_score:
            best_score = score
            best_match = name
            best_signals = signals
    
    # 置信度阈值
    if best_score < 0.2:
        return None, 0.0, []
    
    return best_match, best_score, best_signals


def calculate_link_density(md_content: str) -> Tuple[float, int, int]:
    """
    计算内容的链接密度（T2.4）
    
    Args:
        md_content: Markdown 内容
    
    Returns:
        (链接密度比例, 链接数量, 总字符数)
    """
    if not md_content:
        return 0.0, 0, 0
    
    # 统计 Markdown 链接数量
    link_pattern = r'\[[^\]]+\]\([^)]+\)'
    links = re.findall(link_pattern, md_content)
    link_count = len(links)
    
    # 计算链接占用的字符数
    link_chars = sum(len(link) for link in links)
    
    total_chars = len(md_content)
    if total_chars == 0:
        return 0.0, 0, 0
    
    density = link_chars / total_chars
    return density, link_count, total_chars


def check_content_quality(
    md_content: str,
    url: str,
    density_threshold: float = 0.5,
) -> List[str]:
    """
    检查内容质量并生成警告（T2.4）
    
    Args:
        md_content: Markdown 内容
        url: 来源 URL
        density_threshold: 链接密度阈值
    
    Returns:
        警告消息列表
    """
    warnings: List[str] = []
    
    density, link_count, total_chars = calculate_link_density(md_content)
    
    if density > density_threshold:
        warnings.append(
            f"⚠️ 链接密度过高 ({density:.1%})：可能包含未移除的导航菜单。"
            f"建议使用 --strip-nav 或 --docs-preset"
        )
    
    # 检测连续链接列表
    consecutive_links = re.findall(
        r'(?:^[ \t]*[-*]\s*\[[^\]]+\]\([^)]+\)\s*\n){10,}',
        md_content,
        re.MULTILINE
    )
    if consecutive_links:
        warnings.append(
            f"⚠️ 检测到 {len(consecutive_links)} 个长链接列表块。"
            f"建议使用 --anchor-list-threshold 降低阈值"
        )
    
    return warnings


def apply_docs_preset(
    preset_name: str,
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """
    应用文档框架预设
    
    Args:
        preset_name: 预设名称
    
    Returns:
        (target_ids, target_classes, exclude_selectors)
    """
    preset = DOCS_PRESETS.get(preset_name.lower())
    if not preset:
        return None, None, []
    
    target_ids = ",".join(preset.target_ids) if preset.target_ids else None
    target_classes = ",".join(preset.target_classes) if preset.target_classes else None
    exclude_selectors = preset.exclude_selectors
    
    return target_ids, target_classes, exclude_selectors


def get_available_presets() -> List[str]:
    """获取所有可用的预设名称"""
    return list(DOCS_PRESETS.keys())


# ============================================================================
# Phase 1: 导航/目录剥离功能（T1.1 - T1.5）
# ============================================================================

@dataclass
class NavStripStats:
    """导航剥离统计信息（T1.5 可观测性）"""
    elements_removed: int = 0
    chars_before: int = 0
    chars_after: int = 0
    rules_matched: Dict[str, int] = field(default_factory=dict)
    anchor_lists_removed: int = 0
    anchor_lines_removed: int = 0
    
    @property
    def chars_saved(self) -> int:
        return self.chars_before - self.chars_after
    
    def add_rule_match(self, rule: str, count: int = 1) -> None:
        self.rules_matched[rule] = self.rules_matched.get(rule, 0) + count
    
    def print_summary(self, file=None) -> None:
        """打印统计摘要"""
        if file is None:
            file = sys.stderr
        if self.elements_removed == 0 and self.anchor_lists_removed == 0:
            return
        print(f"\n📊 导航剥离统计：", file=file)
        if self.elements_removed > 0:
            print(f"  • HTML 元素移除：{self.elements_removed} 个", file=file)
        if self.anchor_lists_removed > 0:
            print(f"  • 锚点列表移除：{self.anchor_lists_removed} 块（共 {self.anchor_lines_removed} 行）", file=file)
        if self.chars_saved > 0:
            print(f"  • 节省字符数：{self.chars_saved:,} 字符", file=file)
        if self.rules_matched:
            print(f"  • 命中规则：", file=file)
            for rule, count in sorted(self.rules_matched.items(), key=lambda x: -x[1]):
                print(f"    - {rule}: {count} 次", file=file)


# 默认导航元素选择器（--strip-nav）
DEFAULT_NAV_SELECTORS = [
    "nav",                      # <nav> 标签
    "aside",                    # <aside> 标签
    "[role=navigation]",        # role="navigation"
    "[role=complementary]",     # role="complementary"（侧边栏）
    ".sidebar",                 # 常见侧边栏类名
    ".side-bar",
    ".sidenav",
    ".side-nav",
    ".nav-sidebar",
    ".menu",
    ".navigation",
    ".site-nav",
    ".doc-sidebar",
    ".theme-doc-sidebar-container",  # Docusaurus
    ".pagination-nav",               # Docusaurus 分页
]

# 默认页内目录选择器（--strip-page-toc）
# 注意：避免使用过于宽泛的选择器（如 .contents），可能误删主要内容
DEFAULT_TOC_SELECTORS = [
    ".toc",
    ".table-of-contents",
    ".on-this-page",
    ".page-toc",
    ".article-toc",
    # ".contents",  # 已移除：与 Mintlify 等框架的内容容器冲突
    "[data-toc]",
    ".theme-doc-toc-mobile",    # Docusaurus
    ".theme-doc-toc-desktop",   # Docusaurus
]


class _SimpleSelectorMatcher:
    """
    简化选择器匹配器（T1.3）
    
    支持的选择器语法：
    - tag: 匹配标签名（如 nav, aside）
    - .class: 匹配类名（如 .sidebar）
    - #id: 匹配 ID（如 #navigation）
    - [attr]: 匹配属性存在（如 [data-toc]）
    - [attr=val]: 匹配属性值（如 [role=navigation]）
    - [attr*=val]: 匹配属性包含值（如 [class*=sidebar]）
    """
    
    def __init__(self, selector: str):
        self.selector = selector.strip()
        self.tag: Optional[str] = None
        self.class_name: Optional[str] = None
        self.id_name: Optional[str] = None
        self.attr_name: Optional[str] = None
        self.attr_value: Optional[str] = None
        self.attr_contains: bool = False
        
        self._parse()
    
    def _parse(self) -> None:
        s = self.selector
        if not s:
            return
        
        if s.startswith("."):
            # .class
            self.class_name = s[1:]
        elif s.startswith("#"):
            # #id
            self.id_name = s[1:]
        elif s.startswith("[") and s.endswith("]"):
            # [attr], [attr=val], [attr*=val]
            inner = s[1:-1]
            if "*=" in inner:
                self.attr_name, self.attr_value = inner.split("*=", 1)
                self.attr_contains = True
            elif "=" in inner:
                self.attr_name, self.attr_value = inner.split("=", 1)
            else:
                self.attr_name = inner
            # Bug fix: 去除属性名和属性值两侧的空白和引号
            if self.attr_name:
                self.attr_name = self.attr_name.strip()
            if self.attr_value:
                self.attr_value = self.attr_value.strip()
                # 去除成对的单引号或双引号
                if len(self.attr_value) >= 2:
                    if (self.attr_value[0] == '"' and self.attr_value[-1] == '"') or \
                       (self.attr_value[0] == "'" and self.attr_value[-1] == "'"):
                        self.attr_value = self.attr_value[1:-1]
        else:
            # tag name
            self.tag = s.lower()
    
    def matches(self, tag: str, attrs: Dict[str, Optional[str]]) -> bool:
        """检查是否匹配"""
        tag = tag.lower()
        
        # 标签匹配
        if self.tag and self.tag != tag:
            return False
        if self.tag and self.tag == tag:
            return True
        
        # 类名匹配
        if self.class_name:
            classes = _class_list(attrs)
            if self.class_name not in classes:
                return False
            return True
        
        # ID 匹配
        if self.id_name:
            elem_id = (attrs.get("id") or "").strip()
            if elem_id != self.id_name:
                return False
            return True
        
        # 属性匹配
        if self.attr_name:
            attr_val = attrs.get(self.attr_name)
            if attr_val is None:
                return False
            if self.attr_value is None:
                return True  # 仅检查属性存在
            if self.attr_contains:
                return self.attr_value in attr_val
            return attr_val == self.attr_value
        
        return False
    
    def __repr__(self) -> str:
        return f"Selector({self.selector!r})"


class _HTMLElementStripper(HTMLParser):
    """
    HTML 元素移除器（T1.1, T1.2）
    
    移除匹配指定选择器的 HTML 元素及其内容。
    """
    
    # HTML5 void elements - 这些标签没有闭合标签
    # https://html.spec.whatwg.org/multipage/syntax.html#void-elements
    VOID_ELEMENTS = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
        # 已废弃但仍常见
        "command", "keygen", "menuitem",
    })
    
    def __init__(self, selectors: List[str]):
        super().__init__(convert_charrefs=True)
        self.matchers = [_SimpleSelectorMatcher(s) for s in selectors if s.strip()]
        self.buf: List[str] = []
        self.skip_depth = 0
        self.skip_tag: Optional[str] = None
        self.stats = NavStripStats()
    
    def _should_skip(self, tag: str, attrs: Dict[str, Optional[str]]) -> Optional[str]:
        """检查是否应该跳过该元素，返回匹配的选择器"""
        for matcher in self.matchers:
            if matcher.matches(tag, attrs):
                return matcher.selector
        return None
    
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
    
    def handle_starttag(self, tag: str, attrs_list: Sequence[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attrs = dict(attrs_list)
        
        if self.skip_depth > 0:
            # 已经在跳过的元素内部
            # Bug fix: void 标签没有 endtag，不应递增深度计数
            if tag not in self.VOID_ELEMENTS:
                self.skip_depth += 1
            return
        
        matched = self._should_skip(tag, attrs)
        if matched:
            # 开始跳过
            # Bug fix: 如果匹配的是 void 标签，直接移除不需要深度计数
            if tag in self.VOID_ELEMENTS:
                self.stats.elements_removed += 1
                self.stats.add_rule_match(matched)
                return
            self.skip_depth = 1
            self.skip_tag = tag
            self.stats.elements_removed += 1
            self.stats.add_rule_match(matched)
            return
        
        # 正常输出
        attr_str = self._attrs_to_str(attrs_list)
        if attr_str:
            self.buf.append(f"<{tag} {attr_str}>")
        else:
            self.buf.append(f"<{tag}>")
    
    def handle_startendtag(self, tag: str, attrs_list: Sequence[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attrs = dict(attrs_list)
        
        if self.skip_depth > 0:
            return
        
        matched = self._should_skip(tag, attrs)
        if matched:
            self.stats.elements_removed += 1
            self.stats.add_rule_match(matched)
            return
        
        attr_str = self._attrs_to_str(attrs_list)
        if attr_str:
            self.buf.append(f"<{tag} {attr_str}/>")
        else:
            self.buf.append(f"<{tag}/>")
    
    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        
        if self.skip_depth > 0:
            self.skip_depth -= 1
            if self.skip_depth == 0:
                self.skip_tag = None
            return
        
        self.buf.append(f"</{tag}>")
    
    def handle_data(self, data: str) -> None:
        if self.skip_depth > 0:
            return
        self.buf.append(htmllib.escape(data, quote=False))
    
    def handle_comment(self, data: str) -> None:
        if self.skip_depth > 0:
            return
        self.buf.append(f"<!--{data}-->")
    
    def handle_decl(self, decl: str) -> None:
        if self.skip_depth > 0:
            return
        self.buf.append(f"<!{decl}>")
    
    def get_result(self) -> str:
        return "".join(self.buf)


def strip_html_elements(
    html_content: str,
    selectors: List[str],
    stats: Optional[NavStripStats] = None,
) -> Tuple[str, NavStripStats]:
    """
    从 HTML 中移除匹配指定选择器的元素
    
    Args:
        html_content: HTML 内容
        selectors: 选择器列表
        stats: 可选的统计对象（用于累计统计）
    
    Returns:
        (处理后的 HTML, 统计信息)
    """
    if not selectors or not html_content:
        return html_content, stats or NavStripStats()
    
    if stats is None:
        stats = NavStripStats()
    
    stats.chars_before = len(html_content)
    
    stripper = _HTMLElementStripper(selectors)
    stripper.feed(html_content)
    result = stripper.get_result()
    
    # 合并统计
    stats.elements_removed += stripper.stats.elements_removed
    for rule, count in stripper.stats.rules_matched.items():
        stats.add_rule_match(rule, count)
    
    stats.chars_after = len(result)
    
    return result, stats


def strip_anchor_lists(
    md_content: str,
    threshold: int = 20,
    stats: Optional[NavStripStats] = None,
) -> Tuple[str, NavStripStats]:
    """
    移除高链接密度的目录块（T1.4）
    
    检测连续的链接列表，超过阈值时移除。支持：
    - 内部锚点：`- [text](#anchor)`
    - 外部链接：`- [text](https://...)`
    - 带标题的导航区块（标题 + 链接列表总行数超过阈值时）
    
    Args:
        md_content: Markdown 内容
        threshold: 连续链接行数阈值（默认 20），设为 0 关闭此功能
        stats: 可选的统计对象
    
    Returns:
        (处理后的 Markdown, 统计信息)
    """
    if stats is None:
        stats = NavStripStats()
    
    if threshold <= 0 or not md_content:
        return md_content, stats
    
    removed_count = 0
    removed_lines = 0
    result = md_content
    
    # 模式 1: 移除带标题的导航区块（如 "##### Start Here" 后跟链接列表）
    # 关键修复：nav_section_pattern 现在也受 threshold 控制
    # 标题占 1 行，所以链接列表至少需要 (threshold - 1) 行
    nav_min_links = max(3, threshold - 1)  # 至少 3 行，避免误删短列表
    nav_section_pattern = (
        r'(#{3,6}\s+[^\n]+\n\n?'  # 标题行（##### 等）
        r'(?:[ \t]*[-*]\s*\[[^\]]+\]\([^)]+\)\s*\n){' + str(nav_min_links) + r',})'
    )
    
    def replace_nav_section(match: re.Match) -> str:
        nonlocal removed_count, removed_lines
        block = match.group(0)
        lines = block.count('\n')
        removed_count += 1
        removed_lines += lines
        return ''  # 完全移除，不留注释
    
    result = re.sub(nav_section_pattern, replace_nav_section, result, flags=re.MULTILINE)
    
    # 模式 2: 移除独立的长链接列表（超过阈值）
    list_pattern = r'((?:^[ \t]*(?:[-*]|\d+\.)\s*\[[^\]]+\]\([^)]+\)\s*\n){' + str(threshold) + r',})'
    
    def replace_list(match: re.Match) -> str:
        nonlocal removed_count, removed_lines
        block = match.group(0)
        lines = block.count('\n')
        removed_count += 1
        removed_lines += lines
        return ''  # 完全移除
    
    result = re.sub(list_pattern, replace_list, result, flags=re.MULTILINE)
    
    # 模式 3: 清理孤立的标题（标题后面只有空行或另一个标题）
    # 仅在移除了导航区块后才执行，避免误删正常标题
    if removed_count > 0:
        orphan_title_pattern = r'#{3,6}\s+[^\n]+\n\n(?=#{3,6}\s+|$|\n*---)'
        result = re.sub(orphan_title_pattern, '', result, flags=re.MULTILINE)
    
    # 模式 4: 清理连续的空行（超过 2 个）
    result = re.sub(r'\n{4,}', '\n\n\n', result)
    
    stats.anchor_lists_removed += removed_count
    stats.anchor_lines_removed += removed_lines
    if removed_count > 0:
        stats.add_rule_match(f"nav-block-strip", removed_count)
    
    return result, stats


def get_strip_selectors(
    strip_nav: bool = False,
    strip_page_toc: bool = False,
    exclude_selectors: Optional[str] = None,
) -> List[str]:
    """
    根据参数组合生成选择器列表
    
    Args:
        strip_nav: 是否移除导航元素
        strip_page_toc: 是否移除页内目录
        exclude_selectors: 自定义选择器（逗号分隔）
    
    Returns:
        选择器列表
    """
    selectors: List[str] = []
    
    if strip_nav:
        selectors.extend(DEFAULT_NAV_SELECTORS)
    
    if strip_page_toc:
        selectors.extend(DEFAULT_TOC_SELECTORS)
    
    if exclude_selectors:
        # 解析逗号分隔的自定义选择器
        custom = [s.strip() for s in exclude_selectors.split(",") if s.strip()]
        selectors.extend(custom)
    
    # 去重但保持顺序
    seen = set()
    unique = []
    for s in selectors:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    
    return unique


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
    *,
    page_url: str,
    redact_urls: bool = True,
    max_image_bytes: int = _DEFAULT_MAX_IMAGE_BYTES,
) -> Dict[str, str]:
    os.makedirs(assets_dir, exist_ok=True)
    url_to_local: Dict[str, str] = {}
    anon_session = _create_anonymous_image_session(session)
    referer = redact_url(page_url) if redact_urls else page_url
    max_bytes: Optional[int] = max_image_bytes if (max_image_bytes and max_image_bytes > 0) else None
    known_image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".bmp", ".ico"}

    for idx, img_url in enumerate(image_urls, start=1):
        if not img_url:
            continue
        parsed_img = urlparse(img_url)
        if parsed_img.scheme not in ("http", "https"):
            # 不支持 data/file 等 scheme（也避免触达本地 file://）
            continue

        last_err: Optional[Exception] = None
        r: Optional[requests.Response] = None
        for attempt in range(1, retries + 1):
            try:
                # 使用安全的图片获取函数，手动处理重定向并在跨域时切换到干净 session
                r = _safe_image_get(
                    img_url=img_url,
                    page_url=page_url,
                    session=session,
                    anon_session=anon_session,
                    timeout_s=timeout_s,
                    referer=referer,
                )
                r.raise_for_status()
                break
            except Exception as e:  # noqa: BLE001 - CLI tool wants retries on network errors
                last_err = e
                # 关键：如果 raise_for_status() 失败（4xx/5xx），r 虽然非 None 但内容无效
                # 必须重置为 None，否则后续 `if r is None:` 误判为成功
                if r is not None:
                    try:
                        r.close()
                    except Exception:
                        pass
                    r = None
                if attempt >= retries:
                    break
                time.sleep(min(2.0, 0.4 * attempt))

        if r is None:
            if best_effort:
                print(f"警告：图片下载失败，已跳过：{img_url}\n  - 错误：{last_err}", file=sys.stderr)
                continue
            raise last_err or RuntimeError("image download failed")

        try:
            # 生成本地文件名（扩展名优先取 URL path，其次 Content-Type，再次嗅探首块内容）
            base = os.path.basename(parsed_img.path.rstrip("/"))
            base = unquote(base) or f"image-{idx}"
            name_root, name_ext = os.path.splitext(base)

            it = r.iter_content(chunk_size=1024 * 64)
            head = b""
            for chunk in it:
                if chunk:
                    head = chunk
                    break

            if (not name_ext) or (name_ext.lower() not in known_image_exts):
                detected = ext_from_content_type(r.headers.get("Content-Type") if r else None) or sniff_ext(head or b"")
                if detected:
                    name_ext = detected
                elif not name_ext:
                    name_ext = ".bin"

            safe_root = _sanitize_filename_part(name_root)
            filename = f"{idx:02d}-{safe_root}{name_ext}"
            filename = _safe_path_length(assets_dir, filename)
            local_path = os.path.join(assets_dir, filename)
            tmp_path = local_path + ".part"

            size = 0
            try:
                with open(tmp_path, "wb") as f:
                    if head:
                        f.write(head)
                        size += len(head)
                        if max_bytes is not None and size > max_bytes:
                            raise RuntimeError(f"图片过大（>{max_bytes} bytes）")
                    for chunk in it:
                        if not chunk:
                            continue
                        size += len(chunk)
                        if max_bytes is not None and size > max_bytes:
                            raise RuntimeError(f"图片过大（>{max_bytes} bytes）")
                        f.write(chunk)
                os.replace(tmp_path, local_path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
        except Exception as e:
            if best_effort:
                print(f"警告：图片保存失败，已跳过：{img_url}\n  - 错误：{e}", file=sys.stderr)
                continue
            raise
        finally:
            try:
                r.close()
            except Exception:
                pass

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

        # 兼容 Python < 3.10：避免 PEP604 `int | str` 写法
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
            safe_name = (name or "").strip()
            if not safe_name:
                continue
            low = safe_name.lower()

            # 安全净化：移除事件属性（onclick/onerror/...）
            if low.startswith("on"):
                continue

            # 安全净化：过滤 javascript: / vbscript:；过滤 file:（避免后续渲染链路触达本地文件）
            if value is not None and low in ("href", "src", "xlink:href", "srcset"):
                v = str(value).strip()
                if re.match(r"(?i)^(?:javascript|vbscript):", v):
                    continue
                if low in ("src", "xlink:href") and v.lower().startswith("file:"):
                    continue

            if value is None:
                parts.append(safe_name)
            else:
                escaped = htmllib.escape(str(value), quote=True)
                parts.append(f'{safe_name}="{escaped}"')
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
            if prev and prev not in ("\n", " ", "(", "[", "*", "`", "_") and text[:1] not in (" ", "\n", ".", ",", ":", ";", ")", "]"):
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


def generate_pdf_from_markdown(md_path: str, pdf_path: str, *, allow_file_access: bool = False) -> None:
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
        ]
        if allow_file_access:
            # 安全提示：该参数会放宽 file:// 资源访问限制；仅在确有需要时开启。
            common.append("--allow-file-access-from-files")
        common += [
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


# ============================================================================
# JS 反爬检测
# ============================================================================

@dataclass
class JSChallengeResult:
    """JS 反爬检测结果"""
    is_challenge: bool  # 是否为 JS 挑战页面
    confidence: str  # "high", "medium", "low"
    signals: List[str]  # 检测到的信号
    
    def get_suggestions(self, url: str) -> List[str]:
        """根据检测结果生成建议"""
        return [
            "1. 在浏览器中打开该 URL，等待页面完全加载",
            "2. 右键点击页面 → 「另存为」或「存储为」→ 保存为 .html 文件",
            "3. 使用 --local-html 参数处理本地文件：",
            f"   python grab_web_to_md.py --local-html saved.html --base-url \"{url}\" --out output.md",
        ]


def detect_js_challenge(html: str, title: Optional[str] = None) -> JSChallengeResult:
    """
    检测页面是否为 JS 反爬挑战页面（如 Cloudflare、Akamai 等）。
    
    返回 JSChallengeResult，包含是否为挑战页面、置信度和检测到的信号。
    """
    signals: List[str] = []
    
    # 提取标题（如果未提供）
    if title is None:
        title = extract_title(html) or ""
    title_lower = title.lower()
    
    # ------------------------------------------------------------------
    # 高置信度信号
    # ------------------------------------------------------------------
    
    # Cloudflare 特征
    if "__cf_chl_opt" in html or "cf-browser-verification" in html:
        signals.append("发现 Cloudflare 验证特征 (__cf_chl_opt / cf-browser-verification)")
    
    if "challenges.cloudflare.com" in html:
        signals.append("发现 Cloudflare 挑战域名引用")
    
    # 标题特征
    challenge_titles = [
        ("challenge", "标题包含 'Challenge'"),
        ("just a moment", "标题包含 'Just a moment'"),
        ("checking your browser", "标题包含 'Checking your browser'"),
        ("please wait", "标题包含 'Please wait'"),
        ("attention required", "标题包含 'Attention Required'"),
        ("ddos protection", "标题包含 'DDoS Protection'"),
    ]
    for keyword, desc in challenge_titles:
        if keyword in title_lower:
            signals.append(desc)
            break
    
    # JavaScript 必需提示
    js_required_patterns = [
        (r"javascript\s+is\s+(disabled|required)", "页面提示 JavaScript 必需/被禁用"),
        (r"please\s+(enable|turn\s+on)\s+javascript", "页面提示请启用 JavaScript"),
        (r"browser.*does\s+not\s+support.*javascript", "页面提示浏览器不支持 JavaScript"),
    ]
    html_lower = html.lower()
    for pattern, desc in js_required_patterns:
        if re.search(pattern, html_lower):
            signals.append(desc)
            break
    
    # Akamai Bot Manager
    if "akamai" in html_lower and ("bot" in html_lower or "challenge" in html_lower):
        signals.append("发现 Akamai Bot Manager 特征")
    
    # PerimeterX
    if "_pxhd" in html or "perimeterx" in html_lower:
        signals.append("发现 PerimeterX 反爬特征")
    
    # ------------------------------------------------------------------
    # 中置信度信号：内容极短 + 包含特定关键词
    # ------------------------------------------------------------------
    
    # 计算正文长度（去除 script/style/注释）
    body_text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)
    body_text = re.sub(r"<style[^>]*>.*?</style>", "", body_text, flags=re.IGNORECASE | re.DOTALL)
    body_text = re.sub(r"<!--.*?-->", "", body_text, flags=re.DOTALL)
    body_text = re.sub(r"<[^>]+>", " ", body_text)
    body_text = re.sub(r"\s+", " ", body_text).strip()
    
    if len(body_text) < 200:
        # 内容很短，检查是否有反爬相关词汇
        short_content_keywords = ["browser", "javascript", "enable", "loading", "redirect", "verify"]
        found_keywords = [kw for kw in short_content_keywords if kw in body_text.lower()]
        if found_keywords:
            signals.append(f"页面正文极短（{len(body_text)} 字符）且包含关键词: {', '.join(found_keywords)}")
    
    # <noscript> 中的警告
    noscript_match = re.search(r"<noscript[^>]*>(.*?)</noscript>", html, re.IGNORECASE | re.DOTALL)
    if noscript_match:
        noscript_content = noscript_match.group(1).lower()
        if "javascript" in noscript_content or "enable" in noscript_content:
            signals.append("发现 <noscript> 中的 JavaScript 警告")
    
    # ------------------------------------------------------------------
    # 判定结果
    # ------------------------------------------------------------------
    
    if not signals:
        return JSChallengeResult(is_challenge=False, confidence="none", signals=[])
    
    # 根据信号数量和类型判断置信度
    high_confidence_keywords = ["cloudflare", "akamai", "perimeterx", "challenge", "just a moment"]
    has_high_signal = any(
        any(kw in sig.lower() for kw in high_confidence_keywords) 
        for sig in signals
    )
    
    if has_high_signal or len(signals) >= 2:
        confidence = "high"
    elif len(signals) == 1:
        confidence = "medium"
    else:
        confidence = "low"
    
    return JSChallengeResult(is_challenge=True, confidence=confidence, signals=signals)


def print_js_challenge_warning(result: JSChallengeResult, url: str) -> None:
    """打印 JS 反爬检测警告信息"""
    confidence_map = {"high": "高", "medium": "中", "low": "低"}
    
    print(file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"⚠️  检测到 JavaScript 反爬保护（置信度：{confidence_map.get(result.confidence, result.confidence)}）", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(file=sys.stderr)
    print("检测到的信号：", file=sys.stderr)
    for sig in result.signals:
        print(f"  • {sig}", file=sys.stderr)
    print(file=sys.stderr)
    print("说明：", file=sys.stderr)
    print("  该网站使用了 JavaScript 反爬机制（如 Cloudflare）来验证访问者。", file=sys.stderr)
    print("  纯 HTTP 请求无法通过此验证，需要浏览器环境执行 JavaScript。", file=sys.stderr)
    print("  这超出了本工具（仅依赖 requests）的能力范围。", file=sys.stderr)
    print(file=sys.stderr)
    print("建议操作：", file=sys.stderr)
    for suggestion in result.get_suggestions(url):
        print(f"  {suggestion}", file=sys.stderr)
    print(file=sys.stderr)
    print("如果您确定要强制处理当前获取到的内容（可能为空或不完整），", file=sys.stderr)
    print("请添加 --force 参数。", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(file=sys.stderr)


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
    max_html_bytes: int = _DEFAULT_MAX_HTML_BYTES
    best_effort_images: bool = True
    keep_html: bool = False
    target_id: Optional[str] = None
    target_class: Optional[str] = None
    clean_wiki_noise: bool = False  # 清理 Wiki 系统噪音（编辑按钮、导航链接等）
    download_images: bool = False  # 是否下载图片到本地
    wechat: bool = False  # 微信公众号文章模式
    # Phase 1: 导航剥离参数
    strip_nav: bool = False  # 移除导航元素
    strip_page_toc: bool = False  # 移除页内目录
    exclude_selectors: Optional[str] = None  # 自定义移除选择器
    anchor_list_threshold: int = 0  # 连续锚点列表移除阈值，默认 0（关闭）
    # Phase 2: 智能正文定位参数
    docs_preset: Optional[str] = None  # 文档框架预设
    auto_detect: bool = False  # 自动检测框架


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


# ============================================================================
# Phase 3-A: 锚点冲突检测与修复
# ============================================================================

@dataclass
class AnchorCollisionStats:
    """锚点冲突统计信息"""
    total_anchors: int = 0
    unique_anchors: int = 0
    collision_count: int = 0  # 发生冲突的锚点数量
    collision_examples: List[Tuple[str, int]] = field(default_factory=list)  # (原始锚点, 重复次数)
    
    @property
    def has_collisions(self) -> bool:
        return self.collision_count > 0
    
    def print_summary(self, file=None, max_examples: int = 5) -> None:
        """打印统计摘要"""
        if file is None:
            file = sys.stderr
        if not self.has_collisions:
            return
        print(f"\n⚠️  锚点冲突检测：", file=file)
        print(f"  • 总锚点数：{self.total_anchors}", file=file)
        print(f"  • 唯一锚点：{self.unique_anchors}", file=file)
        print(f"  • 冲突锚点：{self.collision_count} 个（已自动修复）", file=file)
        if self.collision_examples:
            print(f"  • 冲突示例（显示前 {min(len(self.collision_examples), max_examples)} 个）：", file=file)
            for anchor, count in self.collision_examples[:max_examples]:
                print(f"    - #{anchor} → #{anchor}, #{anchor}-2, ... #{anchor}-{count}", file=file)


class AnchorManager:
    """
    锚点管理器 - 负责锚点生成、冲突检测与去重（Phase 3-A）
    
    使用方式：
    1. 创建实例
    2. 对每个标题调用 register(title) 获取去重后的锚点
    3. 调用 get_stats() 获取冲突统计
    
    示例：
        manager = AnchorManager()
        anchor1 = manager.register("Introduction")  # -> "introduction"
        anchor2 = manager.register("Introduction")  # -> "introduction-2"
        anchor3 = manager.register("Introduction")  # -> "introduction-3"
    """
    
    def __init__(self):
        self._anchor_counts: Dict[str, int] = {}  # 基础锚点 -> 已使用次数
        self._title_to_anchor: Dict[str, str] = {}  # 原始标题 -> 分配的锚点（仅第一次）
        self._all_anchors: List[str] = []  # 所有生成的锚点（按顺序）
        self._collisions: Dict[str, int] = {}  # 发生冲突的基础锚点 -> 总次数
    
    def register(self, title: str, url: Optional[str] = None) -> str:
        """
        注册标题并返回去重后的锚点 ID
        
        Args:
            title: 标题文本
            url: 可选的页面 URL（用于更精确的去重，暂未使用）
        
        Returns:
            去重后的锚点 ID（如 "introduction" 或 "introduction-2"）
        """
        base_anchor = _make_anchor_id(title)
        
        if base_anchor not in self._anchor_counts:
            # 首次出现，直接使用
            self._anchor_counts[base_anchor] = 1
            self._all_anchors.append(base_anchor)
            return base_anchor
        else:
            # 已存在，需要添加后缀
            count = self._anchor_counts[base_anchor] + 1
            self._anchor_counts[base_anchor] = count
            
            # 记录冲突
            if base_anchor not in self._collisions:
                self._collisions[base_anchor] = 2  # 第一次冲突时，已经有 2 个
            else:
                self._collisions[base_anchor] = count
            
            unique_anchor = f"{base_anchor}-{count}"
            self._all_anchors.append(unique_anchor)
            return unique_anchor
    
    def get_anchor_for_title(self, title: str) -> Optional[str]:
        """
        获取已注册标题的锚点（不注册新的）
        
        注意：此方法用于查询，不会创建新锚点
        """
        base_anchor = _make_anchor_id(title)
        if base_anchor in self._anchor_counts:
            return base_anchor
        return None
    
    def get_stats(self) -> AnchorCollisionStats:
        """获取锚点冲突统计信息"""
        stats = AnchorCollisionStats(
            total_anchors=len(self._all_anchors),
            unique_anchors=len(self._anchor_counts),
            collision_count=len(self._collisions),
        )
        
        # 按冲突次数排序，取前 10 个作为示例
        sorted_collisions = sorted(
            self._collisions.items(),
            key=lambda x: -x[1]
        )
        stats.collision_examples = sorted_collisions[:10]
        
        return stats
    
    def reset(self) -> None:
        """重置管理器状态"""
        self._anchor_counts.clear()
        self._title_to_anchor.clear()
        self._all_anchors.clear()
        self._collisions.clear()


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
            max_html_bytes=config.max_html_bytes,
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
        exclude_selectors = config.exclude_selectors
        strip_nav = config.strip_nav
        strip_page_toc = config.strip_page_toc
        anchor_list_threshold = config.anchor_list_threshold
        
        # 微信模式下，如果未指定 target，自动使用 rich_media_content
        if is_wechat and not target_id and not target_class:
            target_class = "rich_media_content"
        
        # Phase 2: 自动检测文档框架
        detected_preset: Optional[str] = None
        if config.auto_detect and not config.docs_preset:
            detected_preset, confidence, signals = detect_docs_framework(page_html)
            if detected_preset and confidence >= 0.5:
                preset = DOCS_PRESETS.get(detected_preset)
                if preset:
                    # 高置信度时应用预设
                    if not target_id and preset.target_ids:
                        target_id = ",".join(preset.target_ids)
                    if not target_class and preset.target_classes:
                        target_class = ",".join(preset.target_classes)
                    # 批量模式下 auto-detect 也应尽量复用预设的“去导航”能力，保持与单页模式一致
                    preset_excludes = ",".join(preset.exclude_selectors) if preset.exclude_selectors else ""
                    if preset_excludes:
                        if exclude_selectors:
                            exclude_selectors = f"{exclude_selectors},{preset_excludes}"
                        else:
                            exclude_selectors = preset_excludes
                    strip_nav = True
                    strip_page_toc = True
                    if anchor_list_threshold == 0:
                        anchor_list_threshold = 10
        
        # 提取正文（支持多值 target，T2.1）
        if target_id or target_class:
            # 使用多值提取
            article_html, matched = extract_target_html_multi(
                page_html, 
                target_ids=target_id, 
                target_classes=target_class
            )
            if not article_html:
                # 回退到单值提取（兼容旧逻辑）
                article_html = extract_target_html(
                    page_html,
                    target_id=target_id.split(",")[0] if target_id else None,
                    target_class=target_class.split(",")[0] if target_class else None,
                ) or ""
            if not article_html:
                article_html = extract_main_html(page_html)
        else:
            article_html = extract_main_html(page_html)
        
        # Phase 1: HTML 导航元素剥离（在提取正文后、转换 Markdown 前）
        strip_selectors = get_strip_selectors(
            strip_nav=strip_nav,
            strip_page_toc=strip_page_toc,
            exclude_selectors=exclude_selectors,
        )
        if strip_selectors:
            article_html, _ = strip_html_elements(article_html, strip_selectors)
        
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
        
        # Phase 1: Markdown 锚点列表剥离
        if anchor_list_threshold > 0:
            md_body, _ = strip_anchor_lists(md_body, anchor_list_threshold)
        
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
    *,
    redact_urls: bool = True,
    max_image_bytes: int = _DEFAULT_MAX_IMAGE_BYTES,
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
    anon_session = _create_anonymous_image_session(session)
    max_bytes: Optional[int] = max_image_bytes if (max_image_bytes and max_image_bytes > 0) else None
    known_image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".bmp", ".ico"}

    # 记录每个图片 URL 的“归属页面”，用于 Referer 与同域判断
    img_referer: Dict[str, str] = {}
    for result in results:
        if not result.success:
            continue
        for u in result.image_urls:
            if u and u not in img_referer:
                img_referer[u] = result.url
    
    for idx, img_url in enumerate(all_image_urls, start=1):
        if progress_callback:
            progress_callback(idx, total, img_url)

        if not img_url:
            continue
        parsed_img = urlparse(img_url)
        if parsed_img.scheme not in ("http", "https"):
            continue

        referer_url = img_referer.get(img_url) or ""
        referer = redact_url(referer_url) if (redact_urls and referer_url) else referer_url
        last_err: Optional[Exception] = None
        r: Optional[requests.Response] = None
        
        for attempt in range(1, retries + 1):
            try:
                # 使用安全的图片获取函数，手动处理重定向并在跨域时切换到干净 session
                r = _safe_image_get(
                    img_url=img_url,
                    page_url=referer_url or "",  # 用 referer_url 判断同域
                    session=session,
                    anon_session=anon_session,
                    timeout_s=timeout_s,
                    referer=referer,
                )
                r.raise_for_status()
                break
            except Exception as e:
                last_err = e
                # 关键：如果 raise_for_status() 失败（4xx/5xx），r 虽然非 None 但内容无效
                # 必须重置为 None，否则后续 `if r is None:` 误判为成功
                if r is not None:
                    try:
                        r.close()
                    except Exception:
                        pass
                    r = None
                if attempt >= retries:
                    break
                time.sleep(min(2.0, 0.4 * attempt))
        
        if r is None:
            if best_effort:
                print(f"  警告：图片下载失败，已跳过：{img_url[:60]}...", file=sys.stderr)
                continue
            raise last_err or RuntimeError("image download failed")
        
        try:
            # 生成本地文件名（扩展名优先取 URL path，其次 Content-Type，再次嗅探首块内容）
            base = os.path.basename(parsed_img.path.rstrip("/"))
            base = unquote(base) or f"image-{idx}"
            name_root, name_ext = os.path.splitext(base)

            it = r.iter_content(chunk_size=1024 * 64)
            head = b""
            for chunk in it:
                if chunk:
                    head = chunk
                    break

            if (not name_ext) or (name_ext.lower() not in known_image_exts):
                detected = ext_from_content_type(r.headers.get("Content-Type") if r else None) or sniff_ext(head or b"")
                if detected:
                    name_ext = detected
                elif not name_ext:
                    name_ext = ".bin"

            safe_root = _sanitize_filename_part(name_root)
            filename = f"{idx:03d}-{safe_root}{name_ext}"
            filename = _safe_path_length(assets_dir, filename)
            local_path = os.path.join(assets_dir, filename)
            tmp_path = local_path + ".part"

            size = 0
            try:
                with open(tmp_path, "wb") as f:
                    if head:
                        f.write(head)
                        size += len(head)
                        if max_bytes is not None and size > max_bytes:
                            raise RuntimeError(f"图片过大（>{max_bytes} bytes）")
                    for chunk in it:
                        if not chunk:
                            continue
                        size += len(chunk)
                        if max_bytes is not None and size > max_bytes:
                            raise RuntimeError(f"图片过大（>{max_bytes} bytes）")
                        f.write(chunk)
                os.replace(tmp_path, local_path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
        except Exception as e:
            if best_effort:
                print(f"  警告：图片保存失败，已跳过：{img_url[:60]}...\n    错误：{e}", file=sys.stderr)
                continue
            raise
        finally:
            try:
                r.close()
            except Exception:
                pass
        
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


def build_url_to_anchor_map_with_manager(
    results: List[BatchPageResult],
    result_anchors: List[Tuple[BatchPageResult, str]],
) -> Dict[str, str]:
    """
    构建 URL 到锚点 ID 的映射表（使用 AnchorManager 生成的去重锚点）
    
    Phase 3-A: 此函数使用预先注册的去重锚点，确保链接改写时指向正确的锚点。
    
    Args:
        results: 批量处理结果列表
        result_anchors: (result, anchor) 对列表，包含去重后的锚点
    
    Returns:
        URL -> 锚点 ID 的映射字典
    """
    url_to_anchor: Dict[str, str] = {}
    
    for result, anchor in result_anchors:
        if not result.success or not anchor:
            continue
        
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
        
        candidates: List[str] = [url]

        # 尝试 URL 解码后匹配
        try:
            decoded_url = unquote(url)
            if decoded_url and decoded_url != url:
                candidates.append(decoded_url)
        except Exception:
            pass

        # 常见变体：去 fragment（#/section），以及按脱敏规则去掉 query/fragment
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
    redact_urls: bool = True,
) -> Tuple[str, AnchorCollisionStats]:
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
        (合并后的 Markdown 内容, 锚点冲突统计)
    """
    parts: List[str] = []
    
    # Phase 3-A: 使用 AnchorManager 进行锚点去重
    anchor_manager = AnchorManager()
    
    # 先为所有结果注册锚点（确保目录和内容使用相同的锚点）
    result_anchors: List[Tuple[BatchPageResult, str]] = []
    for result in results:
        if result.success:
            anchor = anchor_manager.register(result.title, result.url)
        else:
            anchor = ""  # 失败的结果不需要锚点
        result_anchors.append((result, anchor))
    
    # 构建 URL 到锚点的映射（用于链接改写）- 使用去重后的锚点
    url_to_anchor: Dict[str, str] = {}
    total_rewrite_count = 0
    if rewrite_links:
        url_to_anchor = build_url_to_anchor_map_with_manager(results, result_anchors)
    
    # 生成 frontmatter（使用统一的 YAML 转义）
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = main_title or "批量导出文档"
    safe_title = yaml_escape_str(title)
    safe_source_url = redact_url(source_url) if (redact_urls and source_url) else source_url
    if safe_source_url:
        safe_source_url = yaml_escape_str(safe_source_url)
    parts.append("---")
    parts.append(f'title: "{safe_title}"')
    if safe_source_url:
        parts.append(f'source: "{safe_source_url}"')
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
            if safe_source_url:
                parts.append(f"- **来源站点**：{safe_source_url}")
            else:
                # 从第一个 URL 提取域名
                first_url = success_results[0].url
                parsed = urlparse(first_url)
                parts.append(f"- **来源站点**：{parsed.scheme}://{parsed.netloc}")
            parts.append("")
            parts.append("---")
            parts.append("")
    
    # 生成目录（使用预先注册的去重锚点，转义 Markdown 特殊字符）
    if include_toc:
        parts.append("## 目录")
        parts.append("")
        for i, (result, anchor) in enumerate(result_anchors, 1):
            safe_link_title = escape_markdown_link_text(result.title)
            if result.success:
                parts.append(f"{i}. [{safe_link_title}](#{anchor})")
            else:
                parts.append(f"{i}. ~~{safe_link_title}~~ (获取失败)")
        parts.append("")
        parts.append("---")
        parts.append("")
    
    # 添加各页面内容（使用预先注册的去重锚点）
    for result, anchor in result_anchors:
        if not result.success:
            parts.append(f"## {result.title}")
            parts.append("")
            parts.append(f"> ⚠️ 获取失败：{result.error}")
            parts.append("")
            fail_url = redact_url(result.url) if redact_urls else result.url
            parts.append(f"- 原始链接：{fail_url}")
            parts.append("")
            parts.append("---")
            parts.append("")
            continue
        
        # 页面标题（使用 HTML h2 标签带 id 属性，确保锚点跳转兼容性）
        # 转义标题中的 HTML 特殊字符，避免注入风险和渲染错误
        safe_html_title = htmllib.escape(result.title)
        parts.append(f'<h2 id="{anchor}">{safe_html_title}</h2>')
        parts.append("")
        page_url = redact_url(result.url) if redact_urls else result.url
        parts.append(f"- 来源：{page_url}")
        parts.append("")
        
        # 页面内容（调整标题级别：# -> ###, ## -> ####, etc.）
        content = result.md_content
        # 将原内容中的标题级别下调两级
        content = re.sub(r"^(#{1,4})\s+", lambda m: "#" * (len(m.group(1)) + 2) + " ", content, flags=re.MULTILINE)
        
        # 站内链接改写（Phase 3）
        if rewrite_links and url_to_anchor:
            content, count = rewrite_internal_links(content, url_to_anchor)
            total_rewrite_count += count

        if redact_urls:
            content = redact_urls_in_markdown(content)
        
        parts.append(content)
        parts.append("")
        parts.append("---")
        parts.append("")
    
    # 如果启用了链接改写，在文档末尾添加统计信息
    if rewrite_links and total_rewrite_count > 0:
        parts.append("")
        parts.append(f"<!-- 站内链接改写：共 {total_rewrite_count} 处 -->")
    
    # 获取锚点冲突统计
    anchor_stats = anchor_manager.get_stats()
    
    return "\n".join(parts), anchor_stats


def generate_index_markdown(
    results: List[BatchPageResult],
    output_dir: str,
    main_title: Optional[str] = None,
    source_url: Optional[str] = None,
    saved_files: Optional[List[str]] = None,
    redact_urls: bool = True,
) -> str:
    """
    生成索引文件内容（Phase 3-B2 增强版）
    
    Args:
        results: 处理结果列表
        output_dir: 输出目录
        main_title: 主标题
        source_url: 来源站点 URL
        saved_files: 已保存的文件路径列表（用于准确链接）
        redact_urls: 是否脱敏 URL
    """
    parts: List[str] = []
    
    title = main_title or "批量导出索引"
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # YAML Frontmatter（使用统一的 YAML 转义）
    safe_title = yaml_escape_str(title)
    safe_source_url = redact_url(source_url) if (redact_urls and source_url) else source_url
    if safe_source_url:
        safe_source_url = yaml_escape_str(safe_source_url)
    
    parts.append("---")
    parts.append(f'title: "{safe_title}"')
    if safe_source_url:
        parts.append(f'source: "{safe_source_url}"')
    parts.append(f'date: "{date_str}"')
    success_count = len([r for r in results if r.success])
    parts.append(f'pages: {success_count}')
    parts.append("---")
    parts.append("")
    
    # 主标题
    parts.append(f"# {title}")
    parts.append("")
    
    # 文档信息
    parts.append("## 文档信息")
    parts.append("")
    parts.append(f"- **导出时间**：{date_str}")
    parts.append(f"- **页面数量**：{success_count} 页")
    if safe_source_url:
        parts.append(f"- **来源站点**：{safe_source_url}")
    elif results and results[0].url:
        # 从第一个 URL 提取域名
        parsed = urlparse(results[0].url)
        parts.append(f"- **来源站点**：{parsed.scheme}://{parsed.netloc}")
    parts.append("")
    parts.append("---")
    parts.append("")
    
    # 页面列表
    parts.append("## 页面列表")
    parts.append("")
    
    # 构建文件名映射（如果提供了 saved_files）
    # saved_files 按顺序对应 results 中成功的项目
    filename_map: Dict[int, str] = {}
    if saved_files:
        saved_idx = 0
        for i, r in enumerate(results):
            if r.success and saved_idx < len(saved_files):
                filename_map[i] = os.path.basename(saved_files[saved_idx])
                saved_idx += 1
    
    for i, result in enumerate(results, 1):
        # 转义标题中的 Markdown 特殊字符
        safe_link_title = escape_markdown_link_text(result.title)
        if result.success:
            # 优先使用实际生成的文件名
            if (i - 1) in filename_map:
                filename = filename_map[i - 1]
            else:
                filename = _sanitize_filename_part(result.title)[:50] + ".md"
            parts.append(f"{i}. [{safe_link_title}](./{filename})")
        else:
            parts.append(f"{i}. ~~{safe_link_title}~~ (获取失败: {result.error})")
    
    parts.append("")
    return "\n".join(parts)


def batch_save_individual(
    results: List[BatchPageResult],
    output_dir: str,
    include_frontmatter: bool = True,
    redact_urls: bool = True,
    shared_assets_dir: Optional[str] = None,
) -> List[str]:
    """
    将结果保存为独立的 MD 文件（Phase 3-B2 增强版）
    
    Args:
        results: 处理结果列表
        output_dir: 输出目录
        include_frontmatter: 是否包含 frontmatter
        redact_urls: 是否脱敏 URL
        shared_assets_dir: 共享 assets 目录（用于双版本输出时调整图片路径）
    
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
        
        # 处理内容中的图片路径
        content = result.md_content
        if shared_assets_dir:
            # 计算从 output_dir 到 shared_assets_dir 的相对路径
            try:
                rel_assets_path = os.path.relpath(shared_assets_dir, output_dir)
                # 统一使用正斜杠（Windows 上 relpath 可能返回反斜杠）
                rel_assets_path = rel_assets_path.replace("\\", "/")
                # 替换图片路径：将 xxx.assets/ 替换为相对路径
                # 匹配 ![...](xxx.assets/...) 或 <img src="xxx.assets/..."
                content = re.sub(
                    r'(\!\[[^\]]*\]\()([^/)]+\.assets/)([^)]+\))',
                    lambda m: m.group(1) + rel_assets_path + '/' + m.group(3),
                    content
                )
                content = re.sub(
                    r'(<img[^>]+src=["\'])([^"\'/]+\.assets/)([^"\']+)',
                    lambda m: m.group(1) + rel_assets_path + '/' + m.group(3),
                    content
                )
            except ValueError:
                # 如果无法计算相对路径（跨驱动器等），保持原样
                pass

        if redact_urls:
            content = redact_urls_in_markdown(content)
        
        # 写入文件
        with open(filepath, "w", encoding="utf-8") as f:
            page_url = redact_url(result.url) if redact_urls else result.url
            if include_frontmatter:
                f.write(generate_frontmatter(result.title, page_url))
            f.write(f"# {result.title}\n\n")
            f.write(f"- Source: {page_url}\n\n")
            f.write(content)
        
        saved_files.append(filepath)
    
    return saved_files


def fetch_html(
    session: requests.Session,
    url: str,
    timeout_s: int,
    retries: int,
    *,
    max_html_bytes: int = _DEFAULT_MAX_HTML_BYTES,
) -> str:
    last_err: Optional[Exception] = None
    max_bytes: Optional[int] = max_html_bytes if (max_html_bytes and max_html_bytes > 0) else None
    for attempt in range(1, retries + 1):
        r: Optional[requests.Response] = None
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

            if max_bytes is not None:
                cl = r.headers.get("Content-Length")
                if cl:
                    try:
                        if int(cl) > max_bytes:
                            raise RuntimeError(f"HTML 响应过大（Content-Length={cl} > {max_bytes} bytes）：{url}")
                    except ValueError:
                        pass

            buf = bytearray()
            for chunk in r.iter_content(chunk_size=1024 * 128):
                if not chunk:
                    continue
                buf.extend(chunk)
                if max_bytes is not None and len(buf) > max_bytes:
                    raise RuntimeError(f"HTML 响应过大（>{max_bytes} bytes）：{url}")

            encoding = r.encoding or "utf-8"
            return bytes(buf).decode(encoding, errors="replace")
        except Exception as e:  # noqa: BLE001 - CLI tool wants retries on network errors
            last_err = e
            if attempt >= retries:
                raise
            time.sleep(min(3.0, 0.6 * attempt))
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
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
            if not isinstance(custom_headers, dict):
                raise ValueError("headers JSON 必须是对象（如 {\"Authorization\": \"Bearer xxx\"}）")
            sanitized: Dict[str, str] = {}
            for k, v in custom_headers.items():
                kk = str(k).strip()
                if not kk:
                    continue
                sanitized[kk] = str(v)
            session.headers.update(sanitized)
            print(f"已加载自定义 Header（{len(sanitized)} 个）")
        except Exception as e:  # noqa: BLE001
            print(f"警告：无法解析/应用 headers JSON：{e}", file=sys.stderr)

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
            return EXIT_ERROR
        urls = read_urls_file(args.urls_file)
        print(f"从文件加载了 {len(urls)} 个 URL")
    
    if args.crawl:
        # 从索引页爬取链接
        if not args.url:
            print("错误：爬取模式需要提供索引页 URL", file=sys.stderr)
            return EXIT_ERROR
        
        source_url = args.url
        print(f"正在从索引页提取链接：{args.url}")
        
        try:
            index_html = fetch_html(
                session=session,
                url=args.url,
                timeout_s=args.timeout,
                retries=args.retries,
                max_html_bytes=args.max_html_bytes,
            )
        except Exception as e:
            print(f"错误：无法获取索引页：{e}", file=sys.stderr)
            return EXIT_ERROR
        
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
        return EXIT_ERROR
    
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
        max_html_bytes=args.max_html_bytes,
        best_effort_images=args.best_effort_images,  # Bug fix: 使用用户参数而非硬编码
        keep_html=args.keep_html,
        target_id=args.target_id,
        target_class=args.target_class,
        clean_wiki_noise=args.clean_wiki_noise,
        download_images=args.download_images,
        wechat=args.wechat,
        # Phase 1: 导航剥离参数
        strip_nav=args.strip_nav,
        strip_page_toc=args.strip_page_toc,
        exclude_selectors=args.exclude_selectors,
        anchor_list_threshold=args.anchor_list_threshold,
        # Phase 2: 智能正文定位参数
        docs_preset=args.docs_preset,
        auto_detect=args.auto_detect,
    )
    
    # Phase 2: 应用文档框架预设
    if args.docs_preset:
        preset = DOCS_PRESETS.get(args.docs_preset)
        if preset:
            print(f"\n📦 使用文档框架预设：{preset.name} ({preset.description})")
            # 应用预设的 target 配置
            if not config.target_id and preset.target_ids:
                config.target_id = ",".join(preset.target_ids)
            if not config.target_class and preset.target_classes:
                config.target_class = ",".join(preset.target_classes)
            # 合并预设的 exclude_selectors
            preset_excludes = ",".join(preset.exclude_selectors)
            if config.exclude_selectors:
                config.exclude_selectors = f"{config.exclude_selectors},{preset_excludes}"
            else:
                config.exclude_selectors = preset_excludes
            # 自动启用导航剥离
            config.strip_nav = True
            config.strip_page_toc = True
            # 预设模式下，如果用户未显式设置 anchor_list_threshold，则自动启用（默认 10）
            if args.anchor_list_threshold == 0:
                config.anchor_list_threshold = 10
            print(f"  • 正文容器 ID：{config.target_id or '(未设置)'}")
            print(f"  • 正文容器 class：{config.target_class or '(未设置)'}")
            print(f"  • 排除选择器：{len(preset.exclude_selectors)} 个")
            if config.anchor_list_threshold > 0:
                print(f"  • 锚点列表阈值：{config.anchor_list_threshold} 行")
    
    # Phase 1: 打印导航剥离配置
    if args.strip_nav or args.strip_page_toc or args.exclude_selectors:
        selectors = get_strip_selectors(args.strip_nav, args.strip_page_toc, args.exclude_selectors)
        print(f"启用导航剥离：{len(selectors)} 个选择器")
        if args.anchor_list_threshold > 0:
            print(f"锚点列表移除阈值：{args.anchor_list_threshold} 行")
    
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
        return EXIT_ERROR
    
    # 统计结果
    success_count = len([r for r in results if r.success])
    fail_count = len(results) - success_count
    print(f"\n处理完成：成功 {success_count}，失败 {fail_count}")
    
    # Phase 1: 导航剥离统计（T1.5 可观测性）
    if args.strip_nav or args.strip_page_toc or args.exclude_selectors:
        selectors = get_strip_selectors(args.strip_nav, args.strip_page_toc, args.exclude_selectors)
        print(f"\n📊 导航剥离已生效：")
        print(f"  • 应用选择器：{len(selectors)} 个")
        if args.strip_nav:
            print(f"  • --strip-nav: 移除导航元素（nav/aside/.sidebar 等）")
        if args.strip_page_toc:
            print(f"  • --strip-page-toc: 移除页内目录（.toc/.on-this-page 等）")
        if args.exclude_selectors:
            print(f"  • --exclude-selectors: {args.exclude_selectors}")
        if args.anchor_list_threshold > 0:
            print(f"  • 锚点列表阈值：>{args.anchor_list_threshold} 行自动移除")
    
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
                # 自动创建同名上级目录（如果用户未指定目录）
                output_file = auto_wrap_output_dir(output_file)
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
                best_effort=bool(args.best_effort_images),
                progress_callback=img_progress,
                redact_urls=args.redact_url,
                max_image_bytes=args.max_image_bytes,
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
        # 自动创建同名上级目录（如果用户未指定目录）
        output_file = auto_wrap_output_dir(output_file)
        
        # 确保输出目录存在
        output_dir = os.path.dirname(output_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        
        # 检查是否已存在
        if os.path.exists(output_file) and not args.overwrite:
            print(f"文件已存在：{output_file}（如需覆盖请加 --overwrite）", file=sys.stderr)
            return EXIT_FILE_EXISTS
        
        # 来源 URL 优先级：--source-url > 爬取模式的索引页 > None（提取域名）
        final_source_url = args.source_url or source_url
        
        merged_content, anchor_stats = generate_merged_markdown(
            results=results,
            include_toc=args.toc,
            main_title=args.merge_title or args.title,
            source_url=final_source_url,
            rewrite_links=args.rewrite_links,
            show_source_summary=not args.no_source_summary,
            redact_urls=args.redact_url,
        )
        
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(merged_content)
        
        print(f"\n已生成合并文档：{output_file}")
        print(f"文档大小：{len(merged_content):,} 字符")
        
        # Phase 3-A: 输出锚点冲突统计
        if anchor_stats.has_collisions:
            if hasattr(args, 'warn_anchor_collisions') and args.warn_anchor_collisions:
                anchor_stats.print_summary()
            else:
                print(f"📌 锚点冲突：{anchor_stats.collision_count} 个已自动修复（使用 --warn-anchor-collisions 查看详情）")
        if url_to_local:
            assets_dir = os.path.splitext(output_file)[0] + ".assets"
            # 统计图片引用情况（非破坏性：只报告不删除）
            if os.path.isdir(assets_dir):
                # 统计实际文件数
                all_files = [f for f in os.listdir(assets_dir) if os.path.isfile(os.path.join(assets_dir, f))]
                actual_count = len(all_files)
                
                # 统计被引用的文件（保守检测：使用文件名匹配）
                unused_files = []
                for filename in all_files:
                    # 检查文件名是否在最终内容中出现
                    if filename not in merged_content:
                        unused_files.append(filename)
                
                unused_count = len(unused_files)
                if unused_count > 0:
                    print(f"图片目录：{assets_dir}（{actual_count} 张图片，{unused_count} 张可能未引用）")
                    print(f"  ⚠️ 未自动清理未引用图片（可能存在误判），如需清理请手动检查")
                else:
                    print(f"图片目录：{assets_dir}（{actual_count} 张图片）")
            else:
                print(f"图片目录：{assets_dir}（{len(url_to_local)} 张图片）")
        
        # Phase 3-B1: 双版本输出（同时生成分文件版本）
        if hasattr(args, 'split_output') and args.split_output:
            split_dir = args.split_output
            os.makedirs(split_dir, exist_ok=True)
            
            # 确定共享的 assets 目录（使用合并版本的 assets）
            shared_assets = os.path.splitext(output_file)[0] + ".assets" if url_to_local else None
            
            # 生成分文件
            saved_files = batch_save_individual(
                results=results,
                output_dir=split_dir,
                include_frontmatter=args.frontmatter,
                redact_urls=args.redact_url,
                shared_assets_dir=shared_assets,
            )
            
            # 生成索引文件
            index_content = generate_index_markdown(
                results=results,
                output_dir=split_dir,
                main_title=args.merge_title or args.title,
                source_url=final_source_url,
                saved_files=saved_files,
                redact_urls=args.redact_url,
            )
            index_path = os.path.join(split_dir, "INDEX.md")
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(index_content)
            
            print(f"\n📂 已同时生成分文件版本：")
            print(f"  • 目录：{split_dir}")
            print(f"  • 文件数：{len(saved_files)} 个")
            print(f"  • 索引：{index_path}")
            if shared_assets:
                rel_assets = os.path.relpath(shared_assets, split_dir)
                print(f"  • 共享 assets：{rel_assets}")
        
    else:
        # 独立文件输出模式
        os.makedirs(args.output_dir, exist_ok=True)
        
        saved_files = batch_save_individual(
            results=results,
            output_dir=args.output_dir,
            include_frontmatter=args.frontmatter,
            redact_urls=args.redact_url,
            shared_assets_dir=None,
        )
        
        # 来源 URL 优先级：--source-url > 爬取模式的索引页 > None（提取域名）
        final_source_url = args.source_url or source_url
        
        # 生成索引文件（使用增强版）
        index_content = generate_index_markdown(
            results=results,
            output_dir=args.output_dir,
            main_title=args.merge_title or args.title,
            source_url=final_source_url,
            saved_files=saved_files,
            redact_urls=args.redact_url,
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
    
    return EXIT_SUCCESS


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
    ap.add_argument(
        "--max-html-bytes",
        type=int,
        default=_DEFAULT_MAX_HTML_BYTES,
        help="单页 HTML 最大允许字节数（默认 10MB；设为 0 表示不限制）",
    )
    ap.add_argument("--best-effort-images", action="store_true", help="图片下载失败时仅警告并跳过（默认失败即退出）")
    ap.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的 md 文件")
    ap.add_argument("--validate", action="store_true", help="生成后执行校验并输出结果")
    # JS 反爬处理
    ap.add_argument("--local-html", metavar="FILE", help="从本地 HTML 文件读取内容（跳过网络请求，用于处理浏览器保存的页面）")
    ap.add_argument("--base-url", help="配合 --local-html 使用，指定图片下载的基准 URL")
    ap.add_argument("--force", action="store_true", help="检测到 JS 反爬时仍强制继续处理（内容可能为空或不完整）")
    ap.add_argument(
        "--max-image-bytes",
        type=int,
        default=_DEFAULT_MAX_IMAGE_BYTES,
        help="单张图片最大允许字节数（默认 25MB；设为 0 表示不限制）",
    )
    ap.add_argument(
        "--redact-url",
        dest="redact_url",
        action="store_true",
        default=True,
        help="输出文件中对 URL 脱敏（默认启用）：仅保留 scheme://host/path，移除 query/fragment",
    )
    ap.add_argument(
        "--no-redact-url",
        dest="redact_url",
        action="store_false",
        help="关闭 URL 脱敏（保留完整 URL，包括 query/fragment）",
    )
    ap.add_argument(
        "--no-map-json",
        action="store_true",
        help="不生成 *.assets.json URL→本地映射文件（避免泄露图片 URL）",
    )
    ap.add_argument(
        "--pdf-allow-file-access",
        action="store_true",
        help="生成 PDF 时允许 file:// 访问其他本地文件（可能有安全风险；默认关闭）",
    )
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
    
    # ========== 导航/目录剥离参数（Phase 1）==========
    nav_group = ap.add_argument_group("导航剥离参数（Docs/Wiki 站点优化）")
    nav_group.add_argument("--strip-nav", action="store_true",
                           help="移除导航元素（nav/aside/.sidebar 等），适用于 docs 站点批量导出")
    nav_group.add_argument("--strip-page-toc", action="store_true",
                           help="移除页内目录（.toc/.on-this-page 等）")
    nav_group.add_argument("--exclude-selectors",
                           help="自定义移除的元素选择器（逗号分隔），支持：tag/.class/#id/[attr=val]")
    nav_group.add_argument("--anchor-list-threshold", type=int, default=0,
                           help="连续锚点列表移除阈值（默认 0 关闭），建议与 --strip-nav 配合使用，推荐值 10-20")
    
    # ========== 智能正文定位参数（Phase 2）==========
    smart_group = ap.add_argument_group("智能正文定位参数（Phase 2）")
    smart_group.add_argument("--docs-preset", choices=get_available_presets(),
                             help="使用文档框架预设（自动配置 target 和 exclude）：" + 
                                  ", ".join(get_available_presets()))
    smart_group.add_argument("--auto-detect", action="store_true",
                             help="自动检测文档框架并应用预设（高置信度时）")
    smart_group.add_argument("--list-presets", action="store_true",
                             help="列出所有可用的文档框架预设")
    
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
    merge_group.add_argument("--warn-anchor-collisions", action="store_true",
                             help="显示锚点冲突详情（同名标题自动添加后缀 -2, -3...）")
    merge_group.add_argument("--split-output", metavar="DIR",
                             help="同时输出分文件版本到指定目录（与 --merge 配合使用，生成双版本）")
    
    # 爬取模式参数
    crawl_group = ap.add_argument_group("爬取模式参数")
    crawl_group.add_argument("--crawl", action="store_true", help="从索引页提取链接并批量抓取")
    crawl_group.add_argument("--crawl-pattern", help="链接匹配正则表达式（如 'index\\.php\\?MMR'）")
    crawl_group.add_argument("--same-domain", action="store_true", default=True, help="仅抓取同域名链接（默认启用）")
    crawl_group.add_argument("--no-same-domain", action="store_false", dest="same_domain", help="允许抓取跨域链接")
    
    args = ap.parse_args(argv)
    
    # ========== 列出预设 ==========
    if args.list_presets:
        print("\n📦 可用的文档框架预设：\n")
        for name, preset in DOCS_PRESETS.items():
            print(f"  {name:15} - {preset.description}")
            print(f"                   正文 ID: {', '.join(preset.target_ids) or '(无)'}")
            print(f"                   正文 class: {', '.join(preset.target_classes[:3]) or '(无)'}{'...' if len(preset.target_classes) > 3 else ''}")
            print(f"                   排除选择器: {len(preset.exclude_selectors)} 个")
            print()
        print("使用示例：python grab_web_to_md.py URL --docs-preset mintlify")
        return EXIT_SUCCESS
    
    # ========== 批量处理模式 ==========
    is_batch_mode = bool(args.urls_file or args.crawl)
    
    if is_batch_mode:
        return _batch_main(args)
    
    # ========== 单页处理模式（原有逻辑） ==========
    
    # 支持 --local-html 模式（从本地文件读取，跳过网络请求）
    if args.local_html:
        if not os.path.isfile(args.local_html):
            print(f"错误：本地 HTML 文件不存在：{args.local_html}", file=sys.stderr)
            return EXIT_ERROR

        # 本地文件同样做体积保护（与 fetch_html 的 --max-html-bytes 行为保持一致）
        try:
            size = os.path.getsize(args.local_html)
            if args.max_html_bytes and args.max_html_bytes > 0 and size > args.max_html_bytes:
                print(
                    f"错误：本地 HTML 文件过大（{size} > {args.max_html_bytes} bytes）：{args.local_html}",
                    file=sys.stderr,
                )
                return EXIT_ERROR
        except OSError:
            pass
        
        # --local-html 模式下，url 参数可选，用于图片下载；优先使用 --base-url
        url = args.base_url or args.url or ""
        if not url:
            print("警告：未指定 --base-url 或 url，图片将无法下载（仅保留原始引用）", file=sys.stderr)
        
        with open(args.local_html, "r", encoding="utf-8", errors="replace") as f:
            page_html = f.read()
        print(f"从本地文件读取：{args.local_html}")
        
        # 输出文件名
        if args.out:
            base = args.out
        else:
            base = os.path.splitext(os.path.basename(args.local_html))[0] + ".md"
    else:
        # 网络模式：必须提供 URL
        if not args.url:
            ap.error("单页模式必须提供 URL 参数，或使用 --urls-file / --crawl 进入批量模式，或使用 --local-html 读取本地文件")
        
        url = args.url
        base = args.out or (_default_basename(url) + ".md")
    
    out_md = base
    # 自动创建同名上级目录（如果用户未指定目录）
    out_md = auto_wrap_output_dir(out_md)
    # 检查输出文件路径长度
    md_dir = os.path.dirname(out_md) or "."
    out_md_name = os.path.basename(out_md)
    out_md_name = _safe_path_length(md_dir, out_md_name)
    out_md = os.path.join(md_dir, out_md_name) if md_dir != "." else out_md_name
    assets_dir = args.assets_dir or (os.path.splitext(out_md)[0] + ".assets")
    map_json = out_md + ".assets.json"
    
    # 确保输出目录存在
    if md_dir != ".":
        os.makedirs(md_dir, exist_ok=True)

    if os.path.exists(out_md) and not args.overwrite:
        print(f"文件已存在：{out_md}（如需覆盖请加 --overwrite）", file=sys.stderr)
        return EXIT_FILE_EXISTS

    session = _create_session(args, referer_url=url)

    # 网络模式下下载页面
    if not args.local_html:
        print(f"下载页面：{url}")
        page_html = fetch_html(
            session=session,
            url=url,
            timeout_s=args.timeout,
            retries=args.retries,
            max_html_bytes=args.max_html_bytes,
        )
        
        # ====== JS 反爬检测 ======
        js_detection = detect_js_challenge(page_html)
        if js_detection.is_challenge:
            print_js_challenge_warning(js_detection, url)
            if not args.force:
                return EXIT_JS_CHALLENGE
            print("已添加 --force 参数，强制继续处理...", file=sys.stderr)

    # 微信公众号文章自动检测
    is_wechat = args.wechat
    if url and not is_wechat and is_wechat_article_url(url):
        is_wechat = True
        print("检测到微信公众号文章，自动启用微信模式")
    elif not is_wechat and is_wechat_article_html(page_html):
        is_wechat = True
        print("检测到微信公众号文章特征，自动启用微信模式")

    # 确定正文提取策略
    target_id = args.target_id
    target_class = args.target_class
    exclude_selectors = args.exclude_selectors
    strip_nav = args.strip_nav
    strip_page_toc = args.strip_page_toc
    anchor_list_threshold = args.anchor_list_threshold
    
    # 单页模式：应用 docs-preset（Phase 2）
    if hasattr(args, 'docs_preset') and args.docs_preset:
        preset = DOCS_PRESETS.get(args.docs_preset)
        if preset:
            print(f"📦 使用文档框架预设：{preset.name} ({preset.description})")
            # 应用预设的 target 配置（仅当用户未指定时）
            if not target_id and preset.target_ids:
                target_id = ",".join(preset.target_ids)
            if not target_class and preset.target_classes:
                target_class = ",".join(preset.target_classes)
            # 合并预设的 exclude_selectors
            preset_excludes = ",".join(preset.exclude_selectors)
            if exclude_selectors:
                exclude_selectors = f"{exclude_selectors},{preset_excludes}"
            else:
                exclude_selectors = preset_excludes
            # 自动启用导航剥离
            strip_nav = True
            strip_page_toc = True
            # 预设模式下自动启用锚点列表剥离
            if anchor_list_threshold == 0:
                anchor_list_threshold = 10
            print(f"  • 正文容器 ID：{target_id or '(未设置)'}")
            print(f"  • 正文容器 class：{target_class or '(未设置)'}")
    
    # 单页模式：自动检测文档框架（Phase 2）
    elif hasattr(args, 'auto_detect') and args.auto_detect:
        framework, confidence, signals = detect_docs_framework(page_html)
        if framework and confidence >= 0.6:
            preset = DOCS_PRESETS.get(framework)
            if preset:
                print(f"🔍 自动检测到文档框架：{preset.name}（置信度：{confidence:.0%}）")
                # 应用预设配置
                if not target_id and preset.target_ids:
                    target_id = ",".join(preset.target_ids)
                if not target_class and preset.target_classes:
                    target_class = ",".join(preset.target_classes)
                preset_excludes = ",".join(preset.exclude_selectors)
                if exclude_selectors:
                    exclude_selectors = f"{exclude_selectors},{preset_excludes}"
                else:
                    exclude_selectors = preset_excludes
                strip_nav = True
                strip_page_toc = True
                if anchor_list_threshold == 0:
                    anchor_list_threshold = 10
        elif framework:
            print(f"🔍 检测到可能的文档框架：{framework}（置信度：{confidence:.0%}，未自动应用）")
    
    # 微信模式下，如果未指定 target，自动使用 rich_media_content
    if is_wechat and not target_id and not target_class:
        target_class = "rich_media_content"
        print("使用微信正文区域：rich_media_content")

    # 使用多值 target 提取（Phase 2 支持逗号分隔）
    if target_id or target_class:
        article_html, matched_selector = extract_target_html_multi(
            page_html, target_ids=target_id, target_classes=target_class
        )
        if not article_html:
            print("警告：未找到指定的目标区域，将回退到自动抽取。", file=sys.stderr)
            article_html = extract_main_html(page_html)
        elif matched_selector:
            print(f"使用正文容器：{matched_selector}")
    else:
        article_html = extract_main_html(page_html)

    # 单页模式：应用导航剥离（Phase 1）
    strip_selectors = get_strip_selectors(
        strip_nav=strip_nav,
        strip_page_toc=strip_page_toc,
        exclude_selectors=exclude_selectors,
    )
    if strip_selectors:
        article_html, strip_stats = strip_html_elements(article_html, strip_selectors)
        if strip_stats.elements_removed > 0:
            print(f"已移除 {strip_stats.elements_removed} 个导航元素")

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
        page_url=url,
        redact_urls=args.redact_url,
        max_image_bytes=args.max_image_bytes,
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

    # 单页模式：锚点列表剥离（Phase 1）
    # 使用局部变量 anchor_list_threshold（可能被预设修改）
    if anchor_list_threshold > 0:
        md_body, anchor_stats = strip_anchor_lists(md_body, anchor_list_threshold)
        if anchor_stats.anchor_lists_removed > 0:
            print(f"已移除 {anchor_stats.anchor_lists_removed} 个锚点列表块（共 {anchor_stats.anchor_lines_removed} 行）")

    # 解析 tags 参数
    tags: Optional[List[str]] = None
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    display_url = redact_url(url) if args.redact_url else url
    if args.redact_url:
        md_body = redact_urls_in_markdown(md_body)

    with open(out_md, "w", encoding="utf-8") as f:
        if args.frontmatter:
            f.write(generate_frontmatter(title, display_url, tags))
        # 保持正文可读性：无论是否启用 frontmatter，都写入可见标题与来源行。
        f.write(f"# {title}\n\n")
        f.write(f"- Source: {display_url}\n\n")
        f.write(md_body)

    wrote_map_json = False
    if not args.no_map_json:
        with open(map_json, "w", encoding="utf-8") as f:
            map_payload = _redact_url_to_local_map(url_to_local) if args.redact_url else url_to_local
            json.dump(map_payload, f, ensure_ascii=False, indent=2)
        wrote_map_json = True
    else:
        # Bug fix: --no-map-json 时删除旧的映射文件，避免遗留未脱敏的历史 URL
        if os.path.exists(map_json):
            try:
                os.remove(map_json)
                print(f"已删除旧映射文件：{map_json}")
            except OSError as e:
                print(f"警告：无法删除旧映射文件 {map_json}: {e}", file=sys.stderr)

    print(f"已生成：{out_md}")
    print(f"图片目录：{assets_dir}")
    if wrote_map_json:
        print(f"映射文件：{map_json}")

    if args.with_pdf:
        out_pdf = os.path.splitext(out_md)[0] + ".pdf"
        if os.path.exists(out_pdf) and (not args.overwrite):
            print(f"PDF 已存在，跳过：{out_pdf}（如需覆盖请加 --overwrite）", file=sys.stderr)
        else:
            print(f"生成 PDF：{out_pdf}")
            if args.frontmatter:
                # md 文件保留 frontmatter；但 PDF 渲染时剥离元数据块，并补一个可见标题/来源行。
                pdf_md = f"# {title}\n\n- Source: {display_url}\n\n{md_body}"
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
                    generate_pdf_from_markdown(md_path=tmp, pdf_path=out_pdf, allow_file_access=args.pdf_allow_file_access)
                finally:
                    if tmp and os.path.isfile(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
            else:
                generate_pdf_from_markdown(md_path=out_md, pdf_path=out_pdf, allow_file_access=args.pdf_allow_file_access)

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
            return EXIT_VALIDATION_FAILED
        else:
            print("- 缺失文件：0")

    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
