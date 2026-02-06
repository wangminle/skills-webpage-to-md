from __future__ import annotations

import datetime
import hashlib
import html as htmllib
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .markdown_conv import rewrite_internal_links
from .models import BatchPageResult
from .security import redact_url, redact_urls_in_markdown


def yaml_escape_str(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", " ")
    s = s.replace("\r", " ")
    s = s.replace("\t", " ")
    return s.strip()


def escape_markdown_link_text(text: str) -> str:
    if not text:
        return ""
    return text.replace("[", "\\[").replace("]", "\\]")


def generate_frontmatter(title: str, url: str, tags: Optional[List[str]] = None) -> str:
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_title = yaml_escape_str(title)
    safe_url = yaml_escape_str(url or "")
    lines = [
        "---",
        f'title: "{safe_title}"',
        f'source: "{safe_url}"',
        f'date: "{date_str}"',
    ]
    if tags:
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
    dirname = os.path.dirname(output_path)
    if dirname:
        return output_path
    basename = os.path.basename(output_path)
    name_without_ext = os.path.splitext(basename)[0]
    return os.path.join(name_without_ext, basename)


def _safe_path_length(base_dir: str, filename: str, max_total: int = 250) -> str:
    abs_path = os.path.abspath(os.path.join(base_dir, filename))
    if len(abs_path) <= max_total:
        return filename

    name, ext = os.path.splitext(filename)
    overflow = len(abs_path) - max_total
    truncated_len = max(10, len(name) - overflow - 8)
    truncated = name[:truncated_len]
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


def _make_anchor_id(text: str) -> str:
    anchor = text.lower()
    anchor = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", anchor)
    anchor = re.sub(r"\s+", "-", anchor)
    anchor = re.sub(r"-+", "-", anchor)
    return anchor.strip("-") or "section"


@dataclass
class AnchorCollisionStats:
    total_anchors: int = 0
    unique_anchors: int = 0
    collision_count: int = 0
    collision_examples: List[Tuple[str, int]] = field(default_factory=list)

    @property
    def has_collisions(self) -> bool:
        return self.collision_count > 0

    def print_summary(self, file=None, max_examples: int = 5) -> None:
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
    def __init__(self):
        self._anchor_counts: Dict[str, int] = {}
        self._title_to_anchor: Dict[str, str] = {}
        self._all_anchors: List[str] = []
        self._collisions: Dict[str, int] = {}

    def register(self, title: str, url: Optional[str] = None) -> str:
        base_anchor = _make_anchor_id(title)
        if base_anchor not in self._anchor_counts:
            self._anchor_counts[base_anchor] = 1
            self._all_anchors.append(base_anchor)
            return base_anchor

        count = self._anchor_counts[base_anchor] + 1
        self._anchor_counts[base_anchor] = count
        if base_anchor not in self._collisions:
            self._collisions[base_anchor] = 2
        else:
            self._collisions[base_anchor] = count
        unique_anchor = f"{base_anchor}-{count}"
        self._all_anchors.append(unique_anchor)
        return unique_anchor

    def get_anchor_for_title(self, title: str) -> Optional[str]:
        base_anchor = _make_anchor_id(title)
        if base_anchor in self._anchor_counts:
            return base_anchor
        return None

    def get_stats(self) -> AnchorCollisionStats:
        stats = AnchorCollisionStats(
            total_anchors=len(self._all_anchors),
            unique_anchors=len(self._anchor_counts),
            collision_count=len(self._collisions),
        )
        sorted_collisions = sorted(self._collisions.items(), key=lambda x: -x[1])
        stats.collision_examples = sorted_collisions[:10]
        return stats

    def reset(self) -> None:
        self._anchor_counts.clear()
        self._title_to_anchor.clear()
        self._all_anchors.clear()
        self._collisions.clear()


def build_url_to_anchor_map_with_manager(
    results: List[BatchPageResult],
    result_anchors: List[Tuple[BatchPageResult, str]],
) -> Dict[str, str]:
    url_to_anchor: Dict[str, str] = {}
    for result, anchor in result_anchors:
        if not result.success or not anchor:
            continue

        url_to_anchor[result.url] = anchor
        parsed = urlparse(result.url)
        if parsed.port:
            no_port_url = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
            if parsed.query:
                no_port_url += f"?{parsed.query}"
            url_to_anchor[no_port_url] = anchor

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


def generate_merged_markdown(
    results: List[BatchPageResult],
    include_toc: bool = True,
    main_title: Optional[str] = None,
    source_url: Optional[str] = None,
    rewrite_links: bool = False,
    show_source_summary: bool = True,
    redact_urls: bool = True,
) -> Tuple[str, AnchorCollisionStats]:
    parts: List[str] = []
    anchor_manager = AnchorManager()

    result_anchors: List[Tuple[BatchPageResult, str]] = []
    for result in results:
        if result.success:
            anchor = anchor_manager.register(result.title, result.url)
        else:
            anchor = ""
        result_anchors.append((result, anchor))

    url_to_anchor: Dict[str, str] = {}
    total_rewrite_count = 0
    if rewrite_links:
        url_to_anchor = build_url_to_anchor_map_with_manager(results, result_anchors)

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

    parts.append(f"# {title}")
    parts.append("")

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
                first_url = success_results[0].url
                parsed = urlparse(first_url)
                parts.append(f"- **来源站点**：{parsed.scheme}://{parsed.netloc}")
            parts.append("")
            parts.append("---")
            parts.append("")

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

        safe_html_title = htmllib.escape(result.title)
        parts.append(f'<h2 id="{anchor}">{safe_html_title}</h2>')
        parts.append("")
        page_url = redact_url(result.url) if redact_urls else result.url
        parts.append(f"- 来源：{page_url}")
        parts.append("")

        content = result.md_content
        content = re.sub(r"^(#{1,4})\s+", lambda m: "#" * (len(m.group(1)) + 2) + " ", content, flags=re.MULTILINE)
        if rewrite_links and url_to_anchor:
            content, count = rewrite_internal_links(content, url_to_anchor)
            total_rewrite_count += count
        if redact_urls:
            content = redact_urls_in_markdown(content)

        parts.append(content)
        parts.append("")
        parts.append("---")
        parts.append("")

    if rewrite_links and total_rewrite_count > 0:
        parts.append("")
        parts.append(f"<!-- 站内链接改写：共 {total_rewrite_count} 处 -->")

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
    parts: List[str] = []
    title = main_title or "批量导出索引"
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    safe_title = yaml_escape_str(title)
    safe_source_url = redact_url(source_url) if (redact_urls and source_url) else source_url
    if safe_source_url:
        safe_source_url = yaml_escape_str(safe_source_url)

    parts.append("---")
    parts.append(f'title: "{safe_title}"')
    if safe_source_url:
        parts.append(f'source: "{safe_source_url}"')
    success_count = len([r for r in results if r.success])
    parts.append(f'date: "{date_str}"')
    parts.append(f"pages: {success_count}")
    parts.append("---")
    parts.append("")
    parts.append(f"# {title}")
    parts.append("")
    parts.append("## 文档信息")
    parts.append("")
    parts.append(f"- **导出时间**：{date_str}")
    parts.append(f"- **页面数量**：{success_count} 页")
    if safe_source_url:
        parts.append(f"- **来源站点**：{safe_source_url}")
    elif results and results[0].url:
        parsed = urlparse(results[0].url)
        parts.append(f"- **来源站点**：{parsed.scheme}://{parsed.netloc}")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("## 页面列表")
    parts.append("")

    filename_map: Dict[int, str] = {}
    if saved_files:
        saved_idx = 0
        for i, r in enumerate(results):
            if r.success and saved_idx < len(saved_files):
                filename_map[i] = os.path.basename(saved_files[saved_idx])
                saved_idx += 1

    for i, result in enumerate(results, 1):
        safe_link_title = escape_markdown_link_text(result.title)
        if result.success:
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
    os.makedirs(output_dir, exist_ok=True)
    saved_files: List[str] = []

    for result in results:
        if not result.success:
            continue

        filename = _sanitize_filename_part(result.title)[:50]
        filename = _safe_path_length(output_dir, filename + ".md")
        filepath = os.path.join(output_dir, filename)

        base, ext = os.path.splitext(filepath)
        counter = 1
        while os.path.exists(filepath):
            filepath = f"{base}_{counter}{ext}"
            counter += 1

        content = result.md_content
        if shared_assets_dir:
            try:
                rel_assets_path = os.path.relpath(shared_assets_dir, output_dir)
                rel_assets_path = rel_assets_path.replace("\\", "/")
                content = re.sub(
                    r"(\!\[[^\]]*\]\()([^/)]+\.assets/)([^)]+\))",
                    lambda m: m.group(1) + rel_assets_path + "/" + m.group(3),
                    content,
                )
                content = re.sub(
                    r'(<img[^>]+src=["\'])([^"\'/]+\.assets/)([^"\']+)',
                    lambda m: m.group(1) + rel_assets_path + "/" + m.group(3),
                    content,
                )
            except ValueError:
                pass

        if redact_urls:
            content = redact_urls_in_markdown(content)

        with open(filepath, "w", encoding="utf-8") as f:
            page_url = redact_url(result.url) if redact_urls else result.url
            if include_frontmatter:
                f.write(generate_frontmatter(result.title, page_url))
            f.write(f"# {result.title}\n\n")
            f.write(f"- Source: {page_url}\n\n")
            f.write(content)

        saved_files.append(filepath)

    return saved_files
