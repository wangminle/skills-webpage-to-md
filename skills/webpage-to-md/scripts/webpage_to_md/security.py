from __future__ import annotations

import html as htmllib
import os
import re
import sys
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse

from .models import JSChallengeResult, ValidationResult


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


def _extract_title(html: str) -> Optional[str]:
    m = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    title = re.sub(r"\s+", " ", htmllib.unescape(m.group(1))).strip()
    return title or None


def detect_js_challenge(html: str, title: Optional[str] = None) -> JSChallengeResult:
    """
    检测页面是否为 JS 反爬挑战页面（如 Cloudflare、Akamai 等）。

    返回 JSChallengeResult，包含是否为挑战页面、置信度和检测到的信号。
    """
    signals: List[str] = []

    # 提取标题（如果未提供）
    if title is None:
        title = _extract_title(html) or ""
    title_lower = title.lower()

    # ------------------------------------------------------------------
    # 高置信度信号
    # ------------------------------------------------------------------
    if "__cf_chl_opt" in html or "cf-browser-verification" in html:
        signals.append("发现 Cloudflare 验证特征 (__cf_chl_opt / cf-browser-verification)")
    if "challenges.cloudflare.com" in html:
        signals.append("发现 Cloudflare 挑战域名引用")

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

    if "akamai" in html_lower and ("bot" in html_lower or "challenge" in html_lower):
        signals.append("发现 Akamai Bot Manager 特征")
    if "_pxhd" in html or "perimeterx" in html_lower:
        signals.append("发现 PerimeterX 反爬特征")

    # ------------------------------------------------------------------
    # 中置信度信号：内容极短 + 包含特定关键词
    # ------------------------------------------------------------------
    body_text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)
    body_text = re.sub(r"<style[^>]*>.*?</style>", "", body_text, flags=re.IGNORECASE | re.DOTALL)
    body_text = re.sub(r"<!--.*?-->", "", body_text, flags=re.DOTALL)
    body_text = re.sub(r"<[^>]+>", " ", body_text)
    body_text = re.sub(r"\s+", " ", body_text).strip()

    if len(body_text) < 200:
        short_content_keywords = ["browser", "javascript", "enable", "loading", "redirect", "verify"]
        found_keywords = [kw for kw in short_content_keywords if kw in body_text.lower()]
        if found_keywords:
            signals.append(f"页面正文极短（{len(body_text)} 字符）且包含关键词: {', '.join(found_keywords)}")

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

    high_confidence_keywords = ["cloudflare", "akamai", "perimeterx", "challenge", "just a moment"]
    has_high_signal = any(any(kw in sig.lower() for kw in high_confidence_keywords) for sig in signals)
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


def validate_markdown(md_path: str, assets_dir: str) -> ValidationResult:
    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()

    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    refs = [r.strip() for r in refs]
    local_refs = [r for r in refs if not re.match(r"^[a-z]+://", r, re.IGNORECASE)]

    missing: List[str] = []
    for r in local_refs:
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
