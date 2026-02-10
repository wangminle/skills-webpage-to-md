from __future__ import annotations

import argparse
import codecs
import json
import re
import sys
import time
from typing import Dict, Optional, Sequence

import requests

_DEFAULT_MAX_HTML_BYTES = 10 * 1024 * 1024  # 10MB/页；设为 0 表示不限制

UA_PRESETS: Dict[str, str] = {
    "tool": "Mozilla/5.0 (compatible; grab_web_to_md/1.0)",
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


# ---------------------------------------------------------------------------
# HTML <meta> charset detection
# ---------------------------------------------------------------------------
# 仅扫描 HTML 前 4KB 的 ASCII 安全区域来寻找 <meta charset="..."> 或
# <meta http-equiv="Content-Type" content="...; charset=...">
_META_CHARSET_RE = re.compile(
    rb'<meta[^>]+charset=["\']?\s*([A-Za-z0-9_.:-]+)',
    re.IGNORECASE,
)


def _detect_meta_charset(raw: bytes, limit: int = 4096) -> Optional[str]:
    """从 HTML 原始字节的前 *limit* 字节中提取 <meta> 声明的编码。

    返回标准化后的编码名称（可直接传给 ``bytes.decode``），
    未找到或名称无法识别时返回 ``None``。
    """
    m = _META_CHARSET_RE.search(raw[:limit])
    if not m:
        return None
    charset = m.group(1).decode("ascii", errors="ignore").strip()
    if not charset:
        return None
    # 标准化编码名称（sjis → shift_jis，euc-jp → euc_jp 等）
    try:
        return codecs.lookup(charset).name
    except LookupError:
        return None


def _resolve_user_agent(user_agent: Optional[str], ua_preset: str) -> str:
    if user_agent and user_agent.strip():
        return user_agent.strip()
    return UA_PRESETS.get(ua_preset, UA_PRESETS["chrome-win"])


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

            raw = bytes(buf)

            # ── 编码检测 ──────────────────────────────────────
            # requests 在 HTTP Content-Type 未声明 charset 时会默认
            # ISO-8859-1（RFC 2616）。但很多非英语页面仅在 HTML <meta>
            # 中声明实际编码（如 Shift_JIS、EUC-JP、GB2312）。
            # 策略：
            #   1. 如果 HTTP 头明确给出了 charset → 直接采信
            #   2. 否则，尝试从 HTML <meta> 中提取 charset
            #   3. 都没有时回退到 utf-8
            http_encoding = r.encoding  # 可能为 None 或 ISO-8859-1
            is_default = (
                http_encoding is None
                or http_encoding.lower().replace("-", "") in ("iso88591", "latin1")
            )
            if is_default:
                meta_enc = _detect_meta_charset(raw)
                encoding = meta_enc or "utf-8"
            else:
                encoding = http_encoding  # type: ignore[assignment]

            return raw.decode(encoding, errors="replace")
        except Exception as e:
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
        except Exception as e:
            print(f"警告：无法解析/应用 headers JSON：{e}", file=sys.stderr)

    if args.header:
        try:
            _apply_header_lines(session.headers, args.header)
            print(f"已加载追加 Header（{len(args.header)} 个）")
        except Exception as e:
            print(f"警告：无法解析 --header：{e}", file=sys.stderr)

    return session
