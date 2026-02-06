from __future__ import annotations

import html as htmllib
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse


@dataclass
class DocsPreset:
    """文档框架预设配置。"""

    name: str
    description: str
    detect_patterns: List[str]
    detect_classes: List[str]
    detect_meta: List[str]
    target_ids: List[str]
    target_classes: List[str]
    exclude_selectors: List[str]


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
    if not page_html:
        return None, 0.0, []

    html_lower = page_html.lower()
    best_match: Optional[str] = None
    best_score = 0.0
    best_signals: List[str] = []

    for name, preset in DOCS_PRESETS.items():
        signals: List[str] = []
        score = 0.0

        for pattern in preset.detect_patterns:
            if pattern.lower() in html_lower:
                signals.append(f"pattern:{pattern}")
                score += 0.3

        for cls in preset.detect_classes:
            if f'class="{cls}"' in page_html or f"class='{cls}'" in page_html or f" {cls}" in page_html:
                signals.append(f"class:{cls}")
                score += 0.25

        for meta_pattern in preset.detect_meta:
            if re.search(meta_pattern, page_html, re.IGNORECASE):
                signals.append(f"meta:{meta_pattern}")
                score += 0.35

        score = min(1.0, score)

        if score > best_score:
            best_score = score
            best_match = name
            best_signals = signals

    if best_score < 0.2:
        return None, 0.0, []

    return best_match, best_score, best_signals


def calculate_link_density(md_content: str) -> Tuple[float, int, int]:
    if not md_content:
        return 0.0, 0, 0

    links = re.findall(r"\[[^\]]+\]\([^)]+\)", md_content)
    link_count = len(links)
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
    warnings: List[str] = []
    density, _link_count, _total_chars = calculate_link_density(md_content)

    if density > density_threshold:
        warnings.append(
            f"⚠️ 链接密度过高 ({density:.1%})：可能包含未移除的导航菜单。"
            "建议使用 --strip-nav 或 --docs-preset"
        )

    consecutive_links = re.findall(
        r"(?:^[ \t]*[-*]\s*\[[^\]]+\]\([^)]+\)\s*\n){10,}",
        md_content,
        re.MULTILINE,
    )
    if consecutive_links:
        warnings.append(
            f"⚠️ 检测到 {len(consecutive_links)} 个长链接列表块。"
            "建议使用 --anchor-list-threshold 降低阈值"
        )

    return warnings


def apply_docs_preset(preset_name: str) -> Tuple[Optional[str], Optional[str], List[str]]:
    preset = DOCS_PRESETS.get(preset_name.lower())
    if not preset:
        return None, None, []

    target_ids = ",".join(preset.target_ids) if preset.target_ids else None
    target_classes = ",".join(preset.target_classes) if preset.target_classes else None
    exclude_selectors = preset.exclude_selectors

    return target_ids, target_classes, exclude_selectors


def get_available_presets() -> List[str]:
    return list(DOCS_PRESETS.keys())


@dataclass
class NavStripStats:
    """导航剥离统计信息。"""

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
        if file is None:
            file = sys.stderr
        if self.elements_removed == 0 and self.anchor_lists_removed == 0:
            return
        print("\n📊 导航剥离统计：", file=file)
        if self.elements_removed > 0:
            print(f"  • HTML 元素移除：{self.elements_removed} 个", file=file)
        if self.anchor_lists_removed > 0:
            print(f"  • 锚点列表移除：{self.anchor_lists_removed} 块（共 {self.anchor_lines_removed} 行）", file=file)
        if self.chars_saved > 0:
            print(f"  • 节省字符数：{self.chars_saved:,} 字符", file=file)
        if self.rules_matched:
            print("  • 命中规则：", file=file)
            for rule, count in sorted(self.rules_matched.items(), key=lambda x: -x[1]):
                print(f"    - {rule}: {count} 次", file=file)


DEFAULT_NAV_SELECTORS = [
    "nav",
    "aside",
    "[role=navigation]",
    "[role=complementary]",
    ".sidebar",
    ".side-bar",
    ".sidenav",
    ".side-nav",
    ".nav-sidebar",
    ".menu",
    ".navigation",
    ".site-nav",
    ".doc-sidebar",
    ".theme-doc-sidebar-container",
    ".pagination-nav",
]

DEFAULT_TOC_SELECTORS = [
    ".toc",
    ".table-of-contents",
    ".on-this-page",
    ".page-toc",
    ".article-toc",
    "[data-toc]",
    ".theme-doc-toc-mobile",
    ".theme-doc-toc-desktop",
]


def _class_list(attrs: Dict[str, Optional[str]]) -> List[str]:
    cls = attrs.get("class")
    if not cls:
        return []
    if isinstance(cls, str):
        return [c for c in cls.split() if c]
    return [str(cls)]


class _SimpleSelectorMatcher:
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
            self.class_name = s[1:]
        elif s.startswith("#"):
            self.id_name = s[1:]
        elif s.startswith("[") and s.endswith("]"):
            inner = s[1:-1]
            if "*=" in inner:
                self.attr_name, self.attr_value = inner.split("*=", 1)
                self.attr_contains = True
            elif "=" in inner:
                self.attr_name, self.attr_value = inner.split("=", 1)
            else:
                self.attr_name = inner
            if self.attr_name:
                self.attr_name = self.attr_name.strip()
            if self.attr_value:
                self.attr_value = self.attr_value.strip()
                if len(self.attr_value) >= 2:
                    if (
                        (self.attr_value[0] == '"' and self.attr_value[-1] == '"')
                        or (self.attr_value[0] == "'" and self.attr_value[-1] == "'")
                    ):
                        self.attr_value = self.attr_value[1:-1]
        else:
            self.tag = s.lower()

    def matches(self, tag: str, attrs: Dict[str, Optional[str]]) -> bool:
        tag = tag.lower()

        if self.tag and self.tag != tag:
            return False
        if self.tag and self.tag == tag:
            return True

        if self.class_name:
            classes = _class_list(attrs)
            if self.class_name not in classes:
                return False
            return True

        if self.id_name:
            elem_id = (attrs.get("id") or "").strip()
            if elem_id != self.id_name:
                return False
            return True

        if self.attr_name:
            attr_val = attrs.get(self.attr_name)
            if attr_val is None:
                return False
            if self.attr_value is None:
                return True
            if self.attr_contains:
                return self.attr_value in attr_val
            return attr_val == self.attr_value

        return False


class _HTMLElementStripper(HTMLParser):
    VOID_ELEMENTS = frozenset(
        {
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
            "command",
            "keygen",
            "menuitem",
        }
    )

    def __init__(self, selectors: List[str]):
        super().__init__(convert_charrefs=True)
        self.matchers = [_SimpleSelectorMatcher(s) for s in selectors if s.strip()]
        self.buf: List[str] = []
        self.skip_depth = 0
        self.skip_tag: Optional[str] = None
        self.stats = NavStripStats()

    def _should_skip(self, tag: str, attrs: Dict[str, Optional[str]]) -> Optional[str]:
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
            if tag not in self.VOID_ELEMENTS:
                self.skip_depth += 1
            return

        matched = self._should_skip(tag, attrs)
        if matched:
            if tag in self.VOID_ELEMENTS:
                self.stats.elements_removed += 1
                self.stats.add_rule_match(matched)
                return
            self.skip_depth = 1
            self.skip_tag = tag
            self.stats.elements_removed += 1
            self.stats.add_rule_match(matched)
            return

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
    if not selectors or not html_content:
        return html_content, stats or NavStripStats()

    if stats is None:
        stats = NavStripStats()

    stats.chars_before = len(html_content)

    stripper = _HTMLElementStripper(selectors)
    stripper.feed(html_content)
    result = stripper.get_result()

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
    if stats is None:
        stats = NavStripStats()

    if threshold <= 0 or not md_content:
        return md_content, stats

    removed_count = 0
    removed_lines = 0
    result = md_content

    nav_min_links = max(3, threshold - 1)
    nav_section_pattern = (
        r"(#{3,6}\s+[^\n]+\n\n?"
        r"(?:[ \t]*[-*]\s*\[[^\]]+\]\([^)]+\)\s*\n){" + str(nav_min_links) + r",})"
    )

    def replace_nav_section(match: re.Match) -> str:
        nonlocal removed_count, removed_lines
        block = match.group(0)
        lines = block.count("\n")
        removed_count += 1
        removed_lines += lines
        return ""

    result = re.sub(nav_section_pattern, replace_nav_section, result, flags=re.MULTILINE)

    list_pattern = r"((?:^[ \t]*(?:[-*]|\d+\.)\s*\[[^\]]+\]\([^)]+\)\s*\n){" + str(threshold) + r",})"

    def replace_list(match: re.Match) -> str:
        nonlocal removed_count, removed_lines
        block = match.group(0)
        lines = block.count("\n")
        removed_count += 1
        removed_lines += lines
        return ""

    result = re.sub(list_pattern, replace_list, result, flags=re.MULTILINE)

    if removed_count > 0:
        orphan_title_pattern = r"#{3,6}\s+[^\n]+\n\n(?=#{3,6}\s+|$|\n*---)"
        result = re.sub(orphan_title_pattern, "", result, flags=re.MULTILINE)

    result = re.sub(r"\n{4,}", "\n\n\n", result)

    stats.anchor_lists_removed += removed_count
    stats.anchor_lines_removed += removed_lines
    if removed_count > 0:
        stats.add_rule_match("nav-block-strip", removed_count)

    return result, stats


def get_strip_selectors(
    strip_nav: bool = False,
    strip_page_toc: bool = False,
    exclude_selectors: Optional[str] = None,
) -> List[str]:
    selectors: List[str] = []

    if strip_nav:
        selectors.extend(DEFAULT_NAV_SELECTORS)

    if strip_page_toc:
        selectors.extend(DEFAULT_TOC_SELECTORS)

    if exclude_selectors:
        custom = [s.strip() for s in exclude_selectors.split(",") if s.strip()]
        selectors.extend(custom)

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

                if self.pattern and not self.pattern.search(full_url):
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
