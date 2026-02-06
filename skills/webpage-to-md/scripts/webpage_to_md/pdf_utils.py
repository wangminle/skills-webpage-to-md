from __future__ import annotations

import html as htmllib
import os
import re
import shutil
import subprocess
import tempfile
from typing import List, Optional

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
