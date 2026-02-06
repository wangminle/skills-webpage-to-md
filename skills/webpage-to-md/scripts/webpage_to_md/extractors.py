from __future__ import annotations

import html as htmllib
import re
import sys
from html.parser import HTMLParser
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse


def _find_best_section(html: str, tag: str) -> Optional[str]:
    pattern = re.compile(rf"<{tag}\b[^>]*>(.*?)</{tag}>", re.IGNORECASE | re.DOTALL)
    matches = list(pattern.finditer(html))
    if not matches:
        return None
    best = max(matches, key=lambda m: len(m.group(1)))
    return best.group(1)


def extract_main_html(page_html: str) -> str:
    for tag in ("article", "main", "body"):
        section = _find_best_section(page_html, tag)
        if section:
            return section
    return page_html


def _class_list(attrs: Dict[str, Optional[str]]) -> List[str]:
    cls = attrs.get("class")
    if not cls:
        return []
    if isinstance(cls, str):
        return [c for c in cls.split() if c]
    return [str(cls)]


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
    ids = [s.strip() for s in (target_ids or "").split(",") if s.strip()]
    classes = [s.strip() for s in (target_classes or "").split(",") if s.strip()]

    for tid in ids:
        result = extract_target_html(page_html, target_id=tid, target_class=None)
        if result:
            return result, f"id={tid}"

    for tcls in classes:
        result = extract_target_html(page_html, target_id=None, target_class=tcls)
        if result:
            return result, f"class={tcls}"

    return None, None


def extract_title(page_html: str) -> Optional[str]:
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


def is_wechat_article_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.netloc in ("mp.weixin.qq.com", "weixin.qq.com")


def is_wechat_article_html(html: str) -> bool:
    if not html:
        return False

    wechat_markers = [
        'class="rich_media_content"',
        "class='rich_media_content'",
        'id="js_article"',
        'data-mptype="article"',
        "var biz =",
        "__biz",
        "mp.weixin.qq.com",
    ]

    html_lower = html.lower()
    return any(marker.lower() in html_lower for marker in wechat_markers)


def extract_wechat_title(html: str) -> Optional[str]:
    if not html:
        return None

    m = re.search(
        r'<h1[^>]*class=["\'][^"\']*rich_media_title[^"\']*["\'][^>]*>(.*?)</h1>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1))
        title = re.sub(r"\s+", " ", htmllib.unescape(title)).strip()
        if title:
            return title

    m = re.search(
        r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        title = htmllib.unescape(m.group(1)).strip()
        if title:
            return title

    m = re.search(
        r'<meta[^>]*name=["\']twitter:title["\'][^>]*content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        title = htmllib.unescape(m.group(1)).strip()
        if title:
            return title

    return None


class LinkExtractor(HTMLParser):
    def __init__(self, base_url: str, pattern: Optional[str] = None, same_domain: bool = True):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.base_domain = urlparse(base_url).netloc
        self.pattern = re.compile(pattern) if pattern else None
        self.same_domain = same_domain
        self.links: List[Tuple[str, str]] = []
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

                if self.same_domain:
                    link_domain = urlparse(full_url).netloc
                    if link_domain != self.base_domain:
                        self._in_a = False
                        self._current_href = None
                        self._current_text = []
                        return

                if self.pattern:
                    if not self.pattern.search(full_url):
                        self._in_a = False
                        self._current_href = None
                        self._current_text = []
                        return

                if self._current_href.startswith("#") or "cmd=edit" in full_url or "cmd=secedit" in full_url:
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
    same_domain: bool = True,
) -> List[Tuple[str, str]]:
    parser = LinkExtractor(base_url, pattern, same_domain)
    parser.feed(html)
    seen = set()
    unique_links = []
    for url, text in parser.links:
        if url not in seen:
            seen.add(url)
            unique_links.append((url, text))
    return unique_links


def read_urls_file(filepath: str) -> List[Tuple[str, Optional[str]]]:
    urls: List[Tuple[str, Optional[str]]] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if "|" in line:
                parts = line.split("|", 1)
                url = parts[0].strip()
                title = parts[1].strip() if len(parts) > 1 else None
            else:
                url = line
                title = None

            if not url.startswith(("http://", "https://")):
                print(f"警告：第 {line_num} 行不是有效的 URL，已跳过：{url}", file=sys.stderr)
                continue

            urls.append((url, title))

    return urls
