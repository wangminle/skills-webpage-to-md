from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from typing import Callable, Dict, List, Optional, Sequence
from urllib.parse import quote, unquote, urljoin, urlparse

import requests

from .models import BatchPageResult
from .security import redact_url

_DEFAULT_MAX_IMAGE_BYTES = 25 * 1024 * 1024  # 25MB/张；设为 0 表示不限制
_MAX_REDIRECTS = 10


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _is_same_host(url_a: str, url_b: str) -> bool:
    ha = _host_of(url_a)
    hb = _host_of(url_b)
    return bool(ha) and ha == hb


def _sanitize_filename_part(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[^\w.\-]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "untitled"


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


def sniff_ext(data: bytes) -> Optional[str]:
    if len(data) >= 12 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if len(data) >= 6 and (data[:6] in (b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if len(data) >= 4 and data[:4] == b"%PDF":
        return ".pdf"
    head = data[:512].lstrip()
    if head.startswith(b"<svg") or b"<svg" in head[:100]:
        return ".svg"
    return None


def ext_from_content_type(content_type: Optional[str]) -> Optional[str]:
    if not content_type:
        return None
    ct = content_type.split(";")[0].strip().lower()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/bmp": ".bmp",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
        "image/avif": ".avif",
    }
    return mapping.get(ct)


def _create_anonymous_image_session(base_session: requests.Session) -> requests.Session:
    """
    创建“干净 session”用于跨域图片下载：
    - 不携带 Cookie / Authorization / 自定义 Header
    - 只保留少量安全 Header（如 UA / Accept-Language）
    """
    s = requests.Session()
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
        for prefix, adapter in getattr(base_session, "adapters", {}).items():
            s.mount(prefix, adapter)
    except Exception:
        pass

    ua = base_session.headers.get("User-Agent") or "Mozilla/5.0"
    accept_lang = base_session.headers.get("Accept-Language") or "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"
    s.headers.update(
        {
            "User-Agent": ua,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": accept_lang,
        }
    )
    return s


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
            allow_redirects=False,
            headers=headers,
        )

        if r.status_code not in (301, 302, 303, 307, 308):
            return r

        location = r.headers.get("Location")
        if not location:
            try:
                r.close()
            except Exception:
                pass
            raise RuntimeError(f"图片重定向响应缺少 Location 头: {current_url} (status={r.status_code})")

        try:
            r.close()
        except Exception:
            pass

        next_url = urljoin(current_url, location)
        current_session = session if _is_same_host(next_url, page_url) else anon_session
        current_url = next_url

    raise RuntimeError(f"图片 URL 重定向次数超过 {_MAX_REDIRECTS} 次: {img_url}")


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
            continue

        last_err: Optional[Exception] = None
        r: Optional[requests.Response] = None
        for attempt in range(1, retries + 1):
            try:
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
            except Exception as e:
                last_err = e
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
                r = _safe_image_get(
                    img_url=img_url,
                    page_url=referer_url or "",
                    session=session,
                    anon_session=anon_session,
                    timeout_s=timeout_s,
                    referer=referer,
                )
                r.raise_for_status()
                break
            except Exception as e:
                last_err = e
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

        local_abs = os.path.abspath(local_path)
        md_dir_abs = os.path.abspath(md_dir or ".")
        rel = os.path.relpath(local_abs, start=md_dir_abs)
        url_to_local[img_url] = rel.replace("\\", "/")

    return url_to_local


def replace_image_urls_in_markdown(md_content: str, url_to_local: Dict[str, str]) -> str:
    if not md_content or not url_to_local:
        return md_content

    result = md_content
    md_img_pattern = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)\)')
    html_img_pattern = re.compile(r'(<img\b[^>]*\bsrc=["\'])([^"\']+)(["\'])', re.IGNORECASE)

    def _lookup_local(url: str) -> Optional[str]:
        candidates = [url]
        encoded_url = quote(url, safe=":/?&=#")
        if encoded_url != url:
            candidates.append(encoded_url)
        decoded_url = unquote(url)
        if decoded_url != url:
            candidates.append(decoded_url)
        for c in candidates:
            mapped = url_to_local.get(c)
            if mapped:
                return mapped
        return None

    def _replace_md_img(m: re.Match[str]) -> str:
        alt = m.group(1)
        raw_url = m.group(2)
        local = _lookup_local(raw_url)
        if not local:
            return m.group(0)
        return f"![{alt}]({local})"

    def _replace_html_img(m: re.Match[str]) -> str:
        raw_url = m.group(2)
        local = _lookup_local(raw_url)
        if not local:
            return m.group(0)
        return f"{m.group(1)}{local}{m.group(3)}"

    result = md_img_pattern.sub(_replace_md_img, result)
    result = html_img_pattern.sub(_replace_html_img, result)
    return result
