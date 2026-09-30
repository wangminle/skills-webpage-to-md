"""华为开发者文档提取模块。

developer.huawei.com 的文档页面是 Angular SPA：普通 HTTP 请求只能拿到
约 1.7KB 的 JS 空壳（无 SSR 数据），正文由前端调用
``documentPortal/getDocumentById`` 接口动态渲染。本模块直接调用该接口
获取文档正文 HTML，供 html_to_markdown 转换链使用。

接口要点（2026-09 逆向验证）：
- 端点：POST https://developer.huawei.com/consumer/cn/documentPortal/getDocumentById
  （路径中的 ``cn`` 段固定，多语言由 body 的 ``language`` 控制；
  站点前端实际走 svc-drcn.developer.huawei.com/community/servlet 前缀，
  两个端点行为一致，这里用同域简写形式）
- Body：{"objectId": "<fileName>", "language": "cn" | "en"}
  fileName 即文档 URL 的最后一段，必须剥离 query/fragment
  （如 ``?catalogVersion=`` 会直接 document not found）与 ``.md``
  后缀（官方 llms.txt/MCP 索引使用 ``xxx.md`` 链接形式，API 的
  objectId 不含该后缀）
- 成功：{"code": 0, "value": {"title": ..., "content": {"type": "html",
  "content": "<html>..."}, ...}}
- 失败：code != 0 的 JSON（92531031 = document not found 等），
  或整个 404 HTML 页（文档无对应语言版本时）
- 正文内图片为绝对 URL（communityfile CDN），子页链接为指向同站
  /doc/ 路径的绝对 <a href>，可配合 --crawl 使用
- 无需 Cookie，普通 UA 即可

公共 API：
- :func:`is_huawei_doc_url` — 判断是否为华为开发者文档 URL
- :func:`fetch_huawei_doc` — 通过接口获取文档并返回 HTML + 标题
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests

from .http_client import _DEFAULT_MAX_HTML_BYTES

# API 端点（路径段固定为 cn，语言由 body 参数控制）
_API_ENDPOINT = "https://developer.huawei.com/consumer/cn/documentPortal/getDocumentById"

# URL 模式：developer.huawei.com/consumer/{lang}/doc/{catalogName}/{fileName}
# fileName 必须存在（形如 xxx-0000001111151802-V1），仅 /doc/ 路径才算文档页
_HUAWEI_DOC_RE = re.compile(
    r"^/consumer/([A-Za-z-]{2,10})/doc/[^/]+/([^/?#]+)/?$"
)

# 站点支持的语言（site JS 的 supportLang）；URL 语言段 → API language 参数
_LANG_MAP = {"cn": "cn", "en": "en"}

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def parse_huawei_doc_url(url: str) -> Optional[Tuple[str, str]]:
    """解析华为开发者文档 URL，返回 (language, fileName)。

    不匹配时返回 ``None``。fileName 已剥离 query/fragment 与 ``.md``
    后缀（llms.txt 索引链接形式，API objectId 不含后缀）。
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if host not in ("developer.huawei.com",):
        return None
    m = _HUAWEI_DOC_RE.match(parsed.path)
    if not m:
        return None
    lang_seg, file_name = m.group(1).lower(), m.group(2)
    # llms.txt 索引的 .md 后缀不属于 objectId，带后缀请求会 document not found
    if file_name.lower().endswith(".md"):
        file_name = file_name[:-3]
    if not file_name:
        return None
    return lang_seg, file_name


def is_huawei_doc_url(url: str) -> bool:
    """判断 URL 是否为华为开发者文档页（developer.huawei.com/consumer/{lang}/doc/...）。"""
    return parse_huawei_doc_url(url) is not None


def _read_capped_body(
    resp: requests.Response,
    max_bytes: Optional[int],
    url: str,
) -> bytes:
    """流式读取响应体，超过 ``max_bytes`` 立即中止（``None`` 表示不限制）。

    与 :func:`http_client.fetch_html` 保持一致：先按 Content-Length 预判，
    再在分块读取中累计校验，避免超大响应被完整载入内存。
    """
    if max_bytes is not None:
        cl = resp.headers.get("Content-Length")
        if cl:
            try:
                if int(cl) > max_bytes:
                    resp.close()
                    raise RuntimeError(
                        f"华为文档接口响应过大（Content-Length={cl} > {max_bytes} bytes）：{url}"
                    )
            except ValueError:
                pass

    buf = bytearray()
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        buf.extend(chunk)
        if max_bytes is not None and len(buf) > max_bytes:
            resp.close()
            raise RuntimeError(f"华为文档接口响应过大（>{max_bytes} bytes）：{url}")
    return bytes(buf)


def fetch_huawei_doc(
    url: str,
    timeout_s: int = 30,
    retries: int = 3,
    max_html_bytes: int = _DEFAULT_MAX_HTML_BYTES,
) -> Tuple[str, str]:
    """通过 getDocumentById 接口获取华为开发者文档。

    返回 ``(html, title)``。接口失败（文档不存在、无对应语言版本、
    网络错误等）时抛出 ``RuntimeError``，由调用方决定是否回退到
    普通 HTTP 抓取（不建议：SPA 空壳无正文）。

    ``max_html_bytes`` 为响应体大小上限（默认 10MB，与 ``--max-html-bytes``
    同义；``0`` 表示不限制）。响应超限属于确定性失败，不重试。
    """
    parsed = parse_huawei_doc_url(url)
    if not parsed:
        raise RuntimeError(f"不是华为开发者文档 URL：{url}")
    lang_seg, file_name = parsed
    language = _LANG_MAP.get(lang_seg, "cn")

    body = {"objectId": file_name, "language": language}
    max_bytes: Optional[int] = (
        max_html_bytes if (max_html_bytes and max_html_bytes > 0) else None
    )
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        resp: Optional[requests.Response] = None
        try:
            resp = requests.post(
                _API_ENDPOINT,
                json=body,
                headers=_HEADERS,
                timeout=timeout_s,
                stream=True,
            )
            # 文档无对应语言版本时接口返回 404 HTML 页
            if resp.status_code == 404:
                raise RuntimeError(
                    f"华为文档接口返回 404：{url}（可能不存在 {language} 版本）"
                )
            resp.raise_for_status()
            # 防御：个别错误场景返回 200 + HTML 错误页
            content_type = resp.headers.get("Content-Type", "")
            if "json" not in content_type.lower():
                raise RuntimeError(
                    f"华为文档接口返回非 JSON 响应（{content_type or '未知类型'}）：{url}"
                )
            # 流式读取并执行大小上限（--max-html-bytes 在 API 路径同样生效）
            data = json.loads(_read_capped_body(resp, max_bytes, url))
            code = data.get("code")
            if code != 0:
                message = data.get("message", "未知错误")
                raise RuntimeError(
                    f"华为文档接口错误（code={code}）：{message}（{url}）"
                )
            value = data.get("value") or {}
            html = ((value.get("content") or {}).get("content")) or ""
            title = value.get("title") or ""
            if not html:
                raise RuntimeError(f"华为文档接口返回空正文：{url}")
            return html, title
        except RuntimeError:
            # 确定性失败（404/业务错误码/非 JSON/空正文/响应超限）不重试
            raise
        except Exception as e:
            last_err = e
            if attempt >= retries:
                break
            time.sleep(min(3.0, 0.6 * attempt))
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
    raise RuntimeError(f"华为文档接口请求失败：{url}（{last_err}）")
