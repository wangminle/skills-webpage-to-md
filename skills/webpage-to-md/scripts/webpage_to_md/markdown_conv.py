from __future__ import annotations

import re
from typing import Dict, List, Tuple
from urllib.parse import unquote, urlparse

from .security import redact_url


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
    result = md_content
    result = re.sub(
        r"[,，\s]*(?:Video|Mini Program|Like|Wow|Share|Comment|Favorite|听过)\s*[,，]?\s*",
        "",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(r"[,，\s]*轻点两下取消(?:赞|在看)\s*", "", result)
    result = re.sub(
        r"(?:Scan to Follow|Scan with Weixin to\s*use this Mini Program|微信扫一扫可打开此内容.*?使用完整服务)\s*",
        "",
        result,
        flags=re.IGNORECASE | re.DOTALL,
    )
    result = re.sub(r"(?:Cancel|Allow|取消|允许)\s*", "", result, flags=re.IGNORECASE)
    result = re.sub(r"(?:阅读原文|Read more|Read original)\s*", "", result, flags=re.IGNORECASE)
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

    result = link_pattern.sub(replace_link, result)
    return result, rewrite_count
