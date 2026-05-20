from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import shutil
import subprocess
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


# ---------------------------------------------------------------------------
# Browser-based HTML fetching (--browser-fetch)
# ---------------------------------------------------------------------------

def _find_browser() -> Optional[str]:
    """查找系统安装的 Chromium 系浏览器。

    检测顺序：PATH 中的可执行文件 → macOS 常见路径 → Windows 常见路径。
    用于 --browser-fetch；PDF 导出由专门的 PDF/文档 skill 处理。
    """
    path_names = [
        "google-chrome", "google-chrome-stable", "chrome",
        "chromium", "chromium-browser", "msedge",
    ]
    candidates = [shutil.which(n) for n in path_names]
    # macOS
    candidates += [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    # Windows
    candidates += [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


_CF_CHALLENGE_HINTS = (
    "just a moment", "checking your browser", "cf-browser-verification",
    "cf_chl_opt", "__cf_chl", "cloudflare", "安全验证", "正在进行安全",
    "请稍候", "please wait",
)


def _is_challenge_html(html: str) -> bool:
    """粗判 HTML 是否仍然是 Cloudflare 等挑战页面。"""
    lower = html[:4000].lower()
    return any(h in lower for h in _CF_CHALLENGE_HINTS) and len(html) < 15000


def browser_fetch_html(url: str, *, timeout_s: int = 60) -> str:
    """使用系统 Chrome/Edge headless 获取 JS 渲染后的页面 HTML。

    采用两阶段策略处理 Cloudflare 等 JS 挑战：

    1. **Phase 1 — 验证**：启动 Chrome（带远程调试端口），导航到目标 URL，
       通过 HTTP API 轮询页面状态直到 JS 挑战完成并重定向到真实页面。
       期间浏览器的 cookie 会写入临时 ``user-data-dir``。
    2. **Phase 2 — 获取**：用同一 ``user-data-dir`` 重新运行
       ``chrome --dump-dom``，此时 CF clearance cookie 已存在，
       直接获得真实页面 DOM。

    对不需要挑战的普通站点，Phase 1 检测到页面已就绪后直接进入 Phase 2，
    额外开销约 2–3 秒。

    不引入任何新 pip 依赖——仅使用系统浏览器 + 标准库。

    Raises:
        RuntimeError: 浏览器未找到、超时或输出异常。
    """
    import socket
    import tempfile
    import urllib.request

    browser = _find_browser()
    if not browser:
        raise RuntimeError(
            "未找到可用的 Chromium 系浏览器（Chrome / Edge / Chromium）。\n"
            "--browser-fetch 需要系统安装上述浏览器之一。\n"
            "替代方案：在浏览器中手动保存页面后使用 --local-html 处理。"
        )

    print(f"  浏览器路径：{browser}")

    tmpdir = tempfile.mkdtemp(prefix="wmd_browser_")

    # 分配空闲端口
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
        _s.bind(("127.0.0.1", 0))
        port = _s.getsockname()[1]

    _base_flags = [
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--user-data-dir={tmpdir}",
    ]

    # ── Phase 1: 让 Chrome 完成 JS 挑战 ────────────────────
    proc = subprocess.Popen(
        [browser] + _base_flags + [
            f"--remote-debugging-port={port}",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1280,720",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    challenge_detected = False
    try:
        deadline = time.time() + timeout_s
        stable_url: Optional[str] = None
        stable_count = 0
        phase1_ok = False

        print("  Phase 1：等待页面加载（若有 JS 挑战会自动通过）...")

        while time.time() < deadline:
            time.sleep(1)
            try:
                resp = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=2,
                )
                pages = json.loads(resp.read())
                # 只关注 type=page，过滤扩展 background 等
                real = [p for p in pages if p.get("type") == "page"]
                if not real:
                    continue

                cur_url = real[0].get("url", "")
                title = real[0].get("title", "")

                # 仍在挑战页
                title_lower = title.lower()
                if any(h in title_lower for h in _CF_CHALLENGE_HINTS):
                    challenge_detected = True
                    stable_count = 0
                    continue

                # 跳过空白页
                if cur_url in ("", "about:blank") or not title:
                    continue

                if cur_url == stable_url:
                    stable_count += 1
                    if stable_count >= 2:
                        phase1_ok = True
                        break
                else:
                    stable_url = cur_url
                    stable_count = 0
            except Exception:
                pass

        if challenge_detected and not phase1_ok:
            print(
                "  ⚠ JS 挑战未在超时时间内通过（站点可能使用了 Turnstile 等"
                "高级人机验证，headless 浏览器无法自动完成）"
            )

        # 多等 1 秒确保 cookie 写入磁盘
        time.sleep(1)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    # ── Phase 2: 用持久化的 cookie 获取 DOM ─────────────────
    print("  Phase 2：使用持久化 cookie 获取页面内容...")
    try:
        proc2 = subprocess.run(
            [browser] + _base_flags + ["--dump-dom", url],
            capture_output=True,
            text=True,
            timeout=timeout_s + 15,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"浏览器获取超时（{timeout_s}s）：{url}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    html = proc2.stdout or ""

    if _is_challenge_html(html):
        raise RuntimeError(
            "浏览器两阶段获取后仍为挑战页面。可能原因：\n"
            "  • 站点需要真人交互验证（如 CAPTCHA）\n"
            "  • 浏览器 headless 模式被检测\n"
            "建议：在浏览器中手动保存页面后使用 --local-html 处理。"
        )

    if len(html.strip()) < 100:
        raise RuntimeError(
            "浏览器返回的 HTML 内容为空或过短，可能站点需要登录或有更强的反爬保护。"
        )

    return html


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
