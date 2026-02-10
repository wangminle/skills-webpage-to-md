import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import requests


def _load_grabber_module():
    root = pathlib.Path(__file__).resolve().parents[1]
    script = root / "skills" / "webpage-to-md" / "scripts" / "grab_web_to_md.py"
    spec = importlib.util.spec_from_file_location("grab_web_to_md", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


grab = _load_grabber_module()


class TestRedaction(unittest.TestCase):
    def test_redact_url_http(self):
        self.assertEqual(grab.redact_url("https://a.com/p?q=1#x"), "https://a.com/p")
        self.assertEqual(grab.redact_url("http://a.com/p?q=1"), "http://a.com/p")

    def test_redact_url_non_http(self):
        self.assertEqual(grab.redact_url("/rel/path?a=1#b"), "/rel/path?a=1#b")
        self.assertEqual(grab.redact_url(""), "")

    def test_redact_urls_in_markdown(self):
        md = 'a [x](https://a.com/p?q=1#x) b ![i](<https://b.com/i.png?sig=1#f>) <img src="https://c.com/i.jpg?x=1#y">'
        out = grab.redact_urls_in_markdown(md)
        self.assertIn("[x](https://a.com/p)", out)
        self.assertIn("![i](<https://b.com/i.png>)", out)
        self.assertIn('src="https://c.com/i.jpg"', out)

    def test_redact_url_to_local_map_collision(self):
        m = {
            "https://a.com/i.png?sig=1": "a.assets/01.png",
            "https://a.com/i.png?sig=2": "a.assets/02.png",
        }
        out = grab._redact_url_to_local_map(m)
        self.assertIn("https://a.com/i.png", out)
        self.assertIsInstance(out["https://a.com/i.png"], list)
        self.assertEqual(set(out["https://a.com/i.png"]), {"a.assets/01.png", "a.assets/02.png"})


class TestMarkdownConversion(unittest.TestCase):
    def test_bold_spacing(self):
        html = "<article><p>Hello <strong>world</strong>.</p></article>"
        md = grab.html_to_markdown(html, base_url="https://example.com", url_to_local={}, keep_html=False)
        self.assertIn("Hello **world**.", md)


class _FakeResponse:
    def __init__(self, chunks, headers=None, encoding="utf-8", status_ok=True):
        self._chunks = list(chunks)
        self.headers = headers or {}
        self.encoding = encoding
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("status error")

    def iter_content(self, chunk_size=1):
        for c in self._chunks:
            yield c

    def close(self):
        return None


class _FakeSession:
    def __init__(self, resp: _FakeResponse):
        self._resp = resp

    def get(self, url, timeout, stream, headers):
        return self._resp


class TestFetchHtml(unittest.TestCase):
    def test_fetch_html_respects_limit(self):
        resp = _FakeResponse([b"a" * 6, b"b" * 6], headers={"Content-Length": "12"})
        session = _FakeSession(resp)
        with self.assertRaises(RuntimeError):
            grab.fetch_html(session=session, url="https://example.com", timeout_s=1, retries=1, max_html_bytes=10)


class TestLinkRewrite(unittest.TestCase):
    def test_rewrite_internal_links_fragment(self):
        md = "See [p](https://a.com/x#s)."
        out, n = grab.rewrite_internal_links(md, {"https://a.com/x": "anchor"})
        self.assertEqual(n, 1)
        self.assertIn("[p](#anchor)", out)

    def test_rewrite_internal_links_urlencoded(self):
        md = "See [p](https%3A%2F%2Fa.com%2Fx)."
        out, n = grab.rewrite_internal_links(md, {"https://a.com/x": "anchor"})
        self.assertEqual(n, 1)
        self.assertIn("[p](#anchor)", out)


class TestImageRewrite(unittest.TestCase):
    def test_replace_image_urls_only_rewrites_images(self):
        md = (
            "[普通链接](https://a.com/img.png)\n"
            "![图片](https://a.com/img.png)\n"
            '<img src="https://a.com/img.png" alt="x">'
        )
        out = grab.replace_image_urls_in_markdown(md, {"https://a.com/img.png": "assets/001.png"})
        self.assertIn("[普通链接](https://a.com/img.png)", out)
        self.assertIn("![图片](assets/001.png)", out)
        self.assertIn('<img src="assets/001.png" alt="x">', out)


class TestBatchJsChallenge(unittest.TestCase):
    def test_process_single_url_fails_on_js_challenge_without_force(self):
        challenge_html = "<html><head><title>Just a moment</title></head><body>Checking your browser</body></html>"
        config = grab.BatchConfig(force=False)
        with mock.patch.object(grab, "fetch_html", return_value=challenge_html):
            result = grab.process_single_url(session=object(), url="https://example.com/p", config=config)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)
        self.assertIn("JavaScript 反爬", result.error or "")
        self.assertIn("--local-html", result.error or "")

    def test_process_single_url_can_force_continue_on_js_challenge(self):
        challenge_html = "<html><head><title>Just a moment</title></head><body>Checking your browser</body></html>"
        config = grab.BatchConfig(force=True)
        with mock.patch.object(grab, "fetch_html", return_value=challenge_html):
            result = grab.process_single_url(session=object(), url="https://example.com/p", config=config)
        self.assertTrue(result.success)


class TestAutoTitle(unittest.TestCase):
    """--auto-title 自动命名测试"""

    def test_extract_title_for_filename_h1(self):
        """从 H1 提取标题"""
        html = "<html><head><title>Site | Blog</title></head><body><h1>如何学 Python</h1><p>正文</p></body></html>"
        title = grab._extract_title_for_filename(html, "https://example.com/post")
        self.assertEqual(title, "如何学 Python")

    def test_extract_title_for_filename_title_tag_fallback(self):
        """无 H1 时回退到 <title>"""
        html = "<html><head><title>深度学习入门</title></head><body><p>正文</p></body></html>"
        title = grab._extract_title_for_filename(html, "https://example.com/post")
        self.assertEqual(title, "深度学习入门")

    def test_extract_title_for_filename_wechat(self):
        """微信文章优先使用微信标题提取"""
        html = (
            '<html><head><title>微信</title></head><body>'
            '<h1 class="rich_media_title">公众号文章标题</h1>'
            '<div class="rich_media_content"><p>正文</p></div>'
            '</body></html>'
        )
        title = grab._extract_title_for_filename(html, "https://mp.weixin.qq.com/s/xxx")
        self.assertEqual(title, "公众号文章标题")

    def test_extract_title_for_filename_untitled(self):
        """无标题时返回 Untitled"""
        html = "<html><body><p>无标题页面</p></body></html>"
        title = grab._extract_title_for_filename(html, "")
        self.assertEqual(title, "Untitled")

    def test_auto_title_local_html(self):
        """--auto-title + --local-html 模式下使用标题命名"""
        html = "<html><head><title>Test Article</title></head><body><h1>我的文章</h1><p>content</p></body></html>"
        with tempfile.TemporaryDirectory() as td:
            html_path = str(pathlib.Path(td) / "page.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)

            original_cwd = os.getcwd()
            try:
                os.chdir(td)
                out_buf = io.StringIO()
                err_buf = io.StringIO()
                with redirect_stdout(out_buf), redirect_stderr(err_buf):
                    code = grab.main([
                        "--local-html", html_path,
                        "--auto-title",
                        "--overwrite",
                    ])
                self.assertEqual(code, grab.EXIT_SUCCESS)
                stdout_text = out_buf.getvalue()
                # 应该包含自动命名提示
                self.assertIn("自动标题命名", stdout_text)
                self.assertIn("我的文章", stdout_text)
                # 验证文件确实在临时目录中生成
                self.assertTrue(
                    os.path.isfile(os.path.join(td, "我的文章", "我的文章.md")),
                    f"Expected file 我的文章/我的文章.md in temp dir. Contents: {os.listdir(td)}"
                )
            finally:
                os.chdir(original_cwd)

    def test_auto_title_network_mode(self):
        """--auto-title 网络模式下先获取页面再命名"""
        fake_html = "<html><head><title>远程文章</title></head><body><h1>学习笔记</h1><p>content</p></body></html>"
        with tempfile.TemporaryDirectory() as td:
            import os
            original_cwd = os.getcwd()
            try:
                os.chdir(td)
                with mock.patch.object(grab, "fetch_html", return_value=fake_html):
                    with mock.patch.object(grab, "detect_js_challenge") as mock_js:
                        mock_js.return_value = mock.Mock(is_challenge=False)
                        with mock.patch.object(grab, "download_images", return_value={}):
                            out_buf = io.StringIO()
                            err_buf = io.StringIO()
                            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                                code = grab.main([
                                    "https://example.com/post",
                                    "--auto-title",
                                    "--overwrite",
                                    "--no-map-json",
                                ])
                self.assertEqual(code, grab.EXIT_SUCCESS)
                stdout_text = out_buf.getvalue()
                self.assertIn("自动标题命名", stdout_text)
                self.assertIn("学习笔记", stdout_text)
                # 检查生成的文件名包含标题
                self.assertTrue(
                    os.path.isfile(os.path.join("学习笔记", "学习笔记.md")),
                    f"Expected file 学习笔记/学习笔记.md to exist. Files: {os.listdir(td)}"
                )
            finally:
                os.chdir(original_cwd)

    def test_auto_title_out_takes_priority(self):
        """--out 应优先于 --auto-title"""
        html = "<html><head><title>Ignored Title</title></head><body><h1>Ignored</h1><p>content</p></body></html>"
        with tempfile.TemporaryDirectory() as td:
            html_path = str(pathlib.Path(td) / "page.html")
            out_path = str(pathlib.Path(td) / "custom" / "custom.md")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)

            out_buf = io.StringIO()
            err_buf = io.StringIO()
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                code = grab.main([
                    "--local-html", html_path,
                    "--auto-title",
                    "--out", out_path,
                    "--overwrite",
                ])
            self.assertEqual(code, grab.EXIT_SUCCESS)
            # --out 优先，不应出现自动命名提示
            self.assertNotIn("自动标题命名", out_buf.getvalue())
            self.assertTrue(os.path.isfile(out_path))


class TestAutoTitleWechatNoUrl(unittest.TestCase):
    """回归测试：微信标题在无 URL 场景下的提取"""

    def test_wechat_html_feature_without_url(self):
        """无 URL 时也能通过 HTML 特征检测微信页面并提取标题"""
        html = (
            '<html><head><title>微信</title>'
            '<meta property="og:title" content="元标题测试">'
            '</head><body>'
            '<div class="rich_media_content"><p>正文</p></div>'
            '</body></html>'
        )
        # url 为空，但 HTML 包含微信特征 → 应走 extract_wechat_title
        title = grab._extract_title_for_filename(html, "")
        self.assertEqual(title, "元标题测试")

    def test_wechat_html_feature_local_html_auto_title(self):
        """--local-html --auto-title 无 --base-url 时微信标题正确提取"""
        html = (
            '<html><head><title>公众号</title>'
            '<meta property="og:title" content="离线微信文章">'
            '</head><body>'
            '<div class="rich_media_content"><p>正文</p></div>'
            '</body></html>'
        )
        with tempfile.TemporaryDirectory() as td:
            html_path = os.path.join(td, "wechat.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            original_cwd = os.getcwd()
            try:
                os.chdir(td)
                out_buf = io.StringIO()
                err_buf = io.StringIO()
                with redirect_stdout(out_buf), redirect_stderr(err_buf):
                    code = grab.main([
                        "--local-html", html_path,
                        "--auto-title",
                        "--overwrite",
                    ])
                self.assertEqual(code, grab.EXIT_SUCCESS)
                stdout_text = out_buf.getvalue()
                self.assertIn("离线微信文章", stdout_text)
            finally:
                os.chdir(original_cwd)


class TestValidateUrlDecoding(unittest.TestCase):
    """回归测试：validate_markdown 对 URL 编码路径的解码"""

    def test_validate_decodes_percent_encoded_space(self):
        """带空格文件名经 %20 编码后校验不应误报缺失"""
        with tempfile.TemporaryDirectory() as td:
            md_path = os.path.join(td, "out.md")
            img_dir = os.path.join(td, "images")
            os.makedirs(img_dir, exist_ok=True)
            # 创建带空格的图片文件
            open(os.path.join(img_dir, "a 1.png"), "wb").write(b"\x89PNG")
            # Markdown 中用 %20 编码引用
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("![x](images/a%201.png)\n")
            result = grab.validate_markdown(md_path, img_dir)
            self.assertEqual(result.missing_files, [],
                             f"Should not report missing for %20-encoded path, got: {result.missing_files}")

    def test_validate_decodes_percent_encoded_parens(self):
        """带括号文件名经 %28/%29 编码后校验不应误报缺失"""
        with tempfile.TemporaryDirectory() as td:
            md_path = os.path.join(td, "out.md")
            img_dir = os.path.join(td, "images")
            os.makedirs(img_dir, exist_ok=True)
            open(os.path.join(img_dir, "fig (1).png"), "wb").write(b"\x89PNG")
            with open(md_path, "w", encoding="utf-8") as f:
                # 注意：括号在 Markdown 中需要编码才能正确解析
                f.write("![x](images/fig%20%281%29.png)\n")
            result = grab.validate_markdown(md_path, img_dir)
            self.assertEqual(result.missing_files, [],
                             f"Should not report missing for encoded parens, got: {result.missing_files}")

    def test_validate_literal_percent20_filename_not_decoded(self):
        """文件名本身含字面 %20 时不应被错误解码为空格"""
        with tempfile.TemporaryDirectory() as td:
            md_path = os.path.join(td, "out.md")
            img_dir = os.path.join(td, "images")
            os.makedirs(img_dir, exist_ok=True)
            # 文件名字面包含 %20（不是空格）
            open(os.path.join(img_dir, "foo%20bar.png"), "wb").write(b"\x89PNG")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("![x](images/foo%20bar.png)\n")
            result = grab.validate_markdown(md_path, img_dir)
            self.assertEqual(result.missing_files, [],
                             f"Literal %20 in filename should be found by raw path check, got: {result.missing_files}")


class TestHttpErrorGuidance(unittest.TestCase):
    def _run_http_error_case(self, status_code: int) -> tuple[int, str]:
        response = mock.Mock()
        response.status_code = status_code
        error = requests.exceptions.HTTPError(f"{status_code} error", response=response)

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            out_md = str(pathlib.Path(td) / "out.md")
            with mock.patch.object(grab, "fetch_html", side_effect=error):
                with redirect_stdout(out_buf), redirect_stderr(err_buf):
                    code = grab.main(["https://example.com/p", "--out", out_md, "--overwrite"])
        return code, err_buf.getvalue()

    def test_main_http_403_shows_local_html_guidance(self):
        code, stderr_text = self._run_http_error_case(403)
        self.assertEqual(code, grab.EXIT_ERROR)
        self.assertIn("错误：请求失败（HTTP 403）", stderr_text)
        self.assertIn("可能触发了站点的反爬或访问频控", stderr_text)
        self.assertIn("--local-html", stderr_text)

    def test_main_http_429_shows_local_html_guidance(self):
        code, stderr_text = self._run_http_error_case(429)
        self.assertEqual(code, grab.EXIT_ERROR)
        self.assertIn("错误：请求失败（HTTP 429）", stderr_text)
        self.assertIn("可能触发了站点的反爬或访问频控", stderr_text)
        self.assertIn("--local-html", stderr_text)


# ---------------------------------------------------------------------------
#  编码检测 —— _detect_meta_charset
# ---------------------------------------------------------------------------
class TestDetectMetaCharset(unittest.TestCase):
    """确保 fetch_html 内部的 _detect_meta_charset 能正确识别 <meta> 声明的编码。"""

    @classmethod
    def setUpClass(cls):
        root = pathlib.Path(__file__).resolve().parents[1]
        hc_path = root / "skills" / "webpage-to-md" / "scripts" / "webpage_to_md" / "http_client.py"
        spec = importlib.util.spec_from_file_location("http_client", hc_path)
        cls.hc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.hc)

    def test_sjis_without_quotes(self):
        raw = b'<html><head><meta http-equiv=Content-Type content="text/html;charset=sjis">'
        self.assertEqual(self.hc._detect_meta_charset(raw), "shift_jis")

    def test_shift_jis_with_quotes(self):
        raw = b'<META http-equiv="Content-TYPE" content="text/html; charset=Shift_JIS">'
        self.assertEqual(self.hc._detect_meta_charset(raw), "shift_jis")

    def test_euc_jp(self):
        raw = b'<META HTTP-EQUIV="Content-type" CONTENT="text/html; charset=euc-jp">'
        self.assertEqual(self.hc._detect_meta_charset(raw), "euc_jp")

    def test_html5_utf8(self):
        raw = b'<html><head><meta charset="utf-8">'
        self.assertEqual(self.hc._detect_meta_charset(raw), "utf-8")

    def test_gb2312(self):
        raw = b'<meta http-equiv="Content-Type" content="text/html; charset=gb2312">'
        self.assertEqual(self.hc._detect_meta_charset(raw), "gb2312")

    def test_no_charset_returns_none(self):
        raw = b'<html><head><title>no charset</title></head>'
        self.assertIsNone(self.hc._detect_meta_charset(raw))

    def test_unknown_charset_returns_none(self):
        raw = b'<meta charset="not-a-real-encoding">'
        self.assertIsNone(self.hc._detect_meta_charset(raw))


# ============================================================================
# SSR 提取模块测试
# ============================================================================

def _load_ssr_module():
    """动态加载 ssr_extract 模块。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    mod_path = root / "skills" / "webpage-to-md" / "scripts" / "webpage_to_md" / "ssr_extract.py"
    spec = importlib.util.spec_from_file_location("webpage_to_md.ssr_extract", mod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ssr = _load_ssr_module()


class TestSSRDetection(unittest.TestCase):
    """测试 SSR 类型检测和统一入口。"""

    def test_no_ssr_returns_none(self):
        """普通 HTML 页面应返回 None。"""
        html = "<html><head><title>普通页面</title></head><body><p>Hello</p></body></html>"
        self.assertIsNone(ssr.try_ssr_extract(html))

    def test_detects_nextjs(self):
        """含 __NEXT_DATA__ 的 HTML 应触发 Next.js 提取。"""
        pm_content = json.dumps({
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 2}, "content": [
                    {"type": "text", "text": "测试标题"}
                ]},
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "这是一段正文内容，长度需要超过50个字符才能通过验证。这是额外的填充文字。"}
                ]},
            ]
        })
        article_data = json.dumps({
            "articleInfo": {
                "title": "测试文章",
                "content": pm_content,
            }
        })
        next_data = json.dumps({
            "props": {
                "pageProps": {
                    "fallback": {
                        "/api/article/detail?id=123": json.loads(article_data),
                    }
                }
            }
        })
        html = (
            f'<html><head></head><body>'
            f'<script id="__NEXT_DATA__" type="application/json">{next_data}</script>'
            f'</body></html>'
        )
        result = ssr.try_ssr_extract(html)
        self.assertIsNotNone(result)
        self.assertEqual(result.source_type, "nextjs")
        self.assertEqual(result.title, "测试文章")
        self.assertFalse(result.is_markdown)
        self.assertIn("测试标题", result.body)
        self.assertIn("正文内容", result.body)

    def test_detects_modernjs_mdcontent(self):
        """含 _ROUTER_DATA + MDContent 的 HTML 应触发 Modern.js 提取。"""
        md_content = (
            "## 变更历史\n\n"
            "这是一段足够长的 Markdown 正文内容，确保长度超过50个字符的阈值。\n\n"
            "### 步骤一\n\n"
            "安装依赖包...\n\n"
            "![示例图片](https://example.com/img1.png)\n"
        )
        router_data = json.dumps({
            "loaderData": {
                "docs/(libid)/(docid$)/page": {
                    "curDoc": {
                        "Title": "快速部署指南",
                        "MDContent": md_content,
                        "Content": "{}",
                    }
                }
            }
        })
        html = (
            f'<html><head></head><body>'
            f'<script>window._ROUTER_DATA = {router_data};</script>'
            f'</body></html>'
        )
        result = ssr.try_ssr_extract(html)
        self.assertIsNotNone(result)
        self.assertEqual(result.source_type, "modernjs")
        self.assertEqual(result.title, "快速部署指南")
        self.assertTrue(result.is_markdown)
        self.assertIn("变更历史", result.body)
        self.assertIn("安装依赖包", result.body)


class TestProseMirrorToHtml(unittest.TestCase):
    """测试 ProseMirror JSON → HTML 转换（通过通用转换器）。"""

    def test_paragraph(self):
        doc = {"type": "doc", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<p>Hello world</p>", html)

    def test_heading_levels(self):
        for level in [1, 2, 3, 4, 5, 6]:
            doc = {"type": "doc", "content": [
                {"type": "heading", "attrs": {"level": level}, "content": [
                    {"type": "text", "text": f"H{level}"}
                ]}
            ]}
            html = ssr.richtext_json_to_html(doc)
            self.assertIn(f"<h{level}>H{level}</h{level}>", html)

    def test_bold_italic_code_marks(self):
        doc = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "bold", "marks": [{"type": "bold"}]},
                {"type": "text", "text": " "},
                {"type": "text", "text": "italic", "marks": [{"type": "italic"}]},
                {"type": "text", "text": " "},
                {"type": "text", "text": "code", "marks": [{"type": "code"}]},
            ]}
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<em>italic</em>", html)
        self.assertIn("<code>code</code>", html)

    def test_link_mark(self):
        doc = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "click here",
                 "marks": [{"type": "link", "attrs": {"href": "https://example.com"}}]},
            ]}
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn('<a href="https://example.com">click here</a>', html)

    def test_bullet_list(self):
        doc = {"type": "doc", "content": [
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "item 1"}]}
                ]},
                {"type": "listItem", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "item 2"}]}
                ]},
            ]}
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<ul>", html)
        self.assertIn("<li>", html)
        self.assertIn("item 1", html)
        self.assertIn("item 2", html)

    def test_code_block(self):
        doc = {"type": "doc", "content": [
            {"type": "codeBlock", "attrs": {"language": "python"},
             "content": [{"type": "text", "text": "print('hello')"}]}
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn('<code class="language-python">', html)
        self.assertIn("print(&#x27;hello&#x27;)", html)

    def test_image(self):
        doc = {"type": "doc", "content": [
            {"type": "image", "attrs": {
                "src": "https://example.com/img.png",
                "alt": "示例图"
            }}
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn('src="https://example.com/img.png"', html)
        self.assertIn('alt="示例图"', html)

    def test_table(self):
        doc = {"type": "doc", "content": [
            {"type": "table", "content": [
                {"type": "tableRow", "content": [
                    {"type": "tableHeader", "attrs": {}, "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "名称"}]}
                    ]},
                    {"type": "tableHeader", "attrs": {}, "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "说明"}]}
                    ]},
                ]},
                {"type": "tableRow", "content": [
                    {"type": "tableCell", "attrs": {}, "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "foo"}]}
                    ]},
                    {"type": "tableCell", "attrs": {}, "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "bar"}]}
                    ]},
                ]},
            ]}
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<table>", html)
        self.assertIn("<th>", html)
        self.assertIn("<td>", html)
        self.assertIn("名称", html)
        self.assertIn("foo", html)

    def test_html_escape(self):
        """特殊字符应被正确转义。"""
        doc = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "<script>alert('xss')</script>"}
            ]}
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_hard_break_and_hr(self):
        doc = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "before"},
                {"type": "hardBreak"},
                {"type": "text", "text": "after"},
            ]},
            {"type": "horizontalRule"},
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<br>", html)
        self.assertIn("<hr>", html)


class TestSlateToHtml(unittest.TestCase):
    """测试 Slate.js JSON Schema → HTML 转换。"""

    def test_paragraph_with_bold(self):
        """Slate 风格：children 数组 + 扁平布尔属性标记格式。"""
        doc = [
            {"type": "paragraph", "children": [
                {"text": "normal "},
                {"text": "bold text", "bold": True},
                {"text": " end"},
            ]}
        ]
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<p>", html)
        self.assertIn("<strong>bold text</strong>", html)
        self.assertIn("normal ", html)

    def test_heading_with_level(self):
        doc = [{"type": "heading", "level": 3, "children": [{"text": "Slate H3"}]}]
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<h3>Slate H3</h3>", html)

    def test_bulleted_list(self):
        doc = [
            {"type": "bulleted-list", "children": [
                {"type": "list-item", "children": [{"text": "apple"}]},
                {"type": "list-item", "children": [{"text": "banana"}]},
            ]}
        ]
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<ul>", html)
        self.assertIn("<li>", html)
        self.assertIn("apple", html)
        self.assertIn("banana", html)

    def test_code_block(self):
        doc = [{"type": "code-block", "children": [{"text": "x = 1"}]}]
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<pre><code>", html)
        self.assertIn("x = 1", html)

    def test_image(self):
        doc = [{"type": "image", "url": "https://img.example.com/pic.jpg",
                "children": [{"text": ""}]}]
        html = ssr.richtext_json_to_html(doc)
        self.assertIn('src="https://img.example.com/pic.jpg"', html)

    def test_italic_and_strikethrough(self):
        doc = [{"type": "paragraph", "children": [
            {"text": "styled", "italic": True, "strikethrough": True}
        ]}]
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<em>", html)
        self.assertIn("<s>", html)


class TestEditorJsToHtml(unittest.TestCase):
    """测试 Editor.js blocks JSON Schema → HTML 转换。"""

    def test_basic_blocks(self):
        data = {
            "blocks": [
                {"type": "header", "data": {"text": "Editor.js Title", "level": 2}},
                {"type": "paragraph", "data": {"text": "A paragraph of text."}},
                {"type": "code", "data": {"code": "console.log('hi')"}},
            ]
        }
        html = ssr.richtext_json_to_html(data)
        self.assertIn("<h2>Editor.js Title</h2>", html)
        self.assertIn("<p>A paragraph of text.</p>", html)
        self.assertIn("<pre><code>console.log(&#x27;hi&#x27;)</code></pre>", html)

    def test_list_block(self):
        data = {
            "blocks": [
                {"type": "list", "data": {
                    "style": "ordered",
                    "items": ["first", "second", "third"]
                }},
            ]
        }
        html = ssr.richtext_json_to_html(data)
        self.assertIn("<ol>", html)
        self.assertIn("<li>first</li>", html)
        self.assertIn("<li>third</li>", html)

    def test_image_block(self):
        data = {
            "blocks": [
                {"type": "image", "data": {
                    "file": {"url": "https://cdn.example.com/photo.png"},
                    "caption": "A photo"
                }},
            ]
        }
        html = ssr.richtext_json_to_html(data)
        self.assertIn('src="https://cdn.example.com/photo.png"', html)
        self.assertIn('alt="A photo"', html)

    def test_delimiter_block(self):
        data = {"blocks": [{"type": "delimiter", "data": {}}]}
        html = ssr.richtext_json_to_html(data)
        self.assertIn("<hr>", html)

    def test_quote_block(self):
        data = {"blocks": [{"type": "quote", "data": {"text": "To be or not to be"}}]}
        html = ssr.richtext_json_to_html(data)
        self.assertIn("<blockquote>To be or not to be</blockquote>", html)

    def test_table_block(self):
        data = {
            "blocks": [
                {"type": "table", "data": {
                    "content": [
                        ["Name", "Age"],
                        ["Alice", "30"],
                    ]
                }}
            ]
        }
        html = ssr.richtext_json_to_html(data)
        self.assertIn("<table>", html)
        self.assertIn("<td>Name</td>", html)
        self.assertIn("<td>Alice</td>", html)

    def test_html_in_text_preserved(self):
        """Editor.js data.text 中的 HTML 格式标记应被保留。"""
        data = {
            "blocks": [
                {"type": "paragraph", "data": {"text": "This is <b>bold</b> and <i>italic</i>."}},
            ]
        }
        html = ssr.richtext_json_to_html(data)
        # HTML 格式标记应原样保留，不应被双重转义
        self.assertIn("<b>bold</b>", html)
        self.assertIn("<i>italic</i>", html)
        # 不应出现转义后的标签
        self.assertNotIn("&lt;b&gt;", html)

    def test_html_in_list_items_preserved(self):
        """Editor.js list items 中的 HTML 格式标记应被保留。"""
        data = {
            "blocks": [
                {"type": "list", "data": {
                    "style": "unordered",
                    "items": ["<b>bold item</b>", "plain item"]
                }},
            ]
        }
        html = ssr.richtext_json_to_html(data)
        self.assertIn("<b>bold item</b>", html)
        self.assertNotIn("&lt;b&gt;", html)

    def test_dangerous_tags_stripped(self):
        """Editor.js 内容中的危险标签应被移除。"""
        data = {
            "blocks": [
                {"type": "paragraph", "data": {
                    "text": 'Safe text<script>alert("xss")</script> end'
                }},
            ]
        }
        html = ssr.richtext_json_to_html(data)
        self.assertNotIn("<script>", html)
        self.assertIn("Safe text", html)
        self.assertIn("end", html)

    def test_event_attrs_stripped(self):
        """Editor.js 内容中的带引号事件属性应被移除。"""
        data = {
            "blocks": [
                {"type": "paragraph", "data": {
                    "text": '<a href="https://example.com" onclick="alert(1)">link</a>'
                }},
            ]
        }
        html = ssr.richtext_json_to_html(data)
        self.assertIn("https://example.com", html)
        self.assertNotIn("onclick", html)
        self.assertIn("link</a>", html)

    def test_unquoted_event_attrs_stripped(self):
        """无引号事件属性也应被移除。"""
        data = {
            "blocks": [
                {"type": "paragraph", "data": {
                    "text": '<a onclick=alert(1) href="https://x.com">x</a>'
                }},
            ]
        }
        html = ssr.richtext_json_to_html(data)
        self.assertNotIn("onclick", html)
        self.assertIn("https://x.com", html)

    def test_unquoted_js_href_stripped(self):
        """无引号 javascript: 协议应被清除。"""
        data = {
            "blocks": [
                {"type": "paragraph", "data": {
                    "text": '<a href=javascript:alert(1)>x</a>'
                }},
            ]
        }
        html = ssr.richtext_json_to_html(data)
        self.assertNotIn("javascript:", html)
        self.assertIn("x</a>", html)

    def test_img_onerror_stripped(self):
        """img 标签上的无引号 onerror 事件应被移除。"""
        data = {
            "blocks": [
                {"type": "paragraph", "data": {
                    "text": '<img src=x onerror=alert(1)>'
                }},
            ]
        }
        html = ssr.richtext_json_to_html(data)
        self.assertNotIn("onerror", html)
        self.assertIn("<img src=x>", html)


class TestLexicalToHtml(unittest.TestCase):
    """测试 Lexical JSON Schema → HTML 转换。"""

    def test_basic_document(self):
        """Lexical 风格：root 节点 + children + format 位掩码。"""
        doc = {"type": "root", "children": [
            {"type": "paragraph", "children": [
                {"type": "text", "text": "normal "},
                {"type": "text", "text": "bold", "format": 1},
                {"type": "text", "text": " "},
                {"type": "text", "text": "italic", "format": 2},
            ]},
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<p>", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<em>italic</em>", html)

    def test_heading_with_tag(self):
        """Lexical 使用 tag 字段表示标题级别。"""
        doc = {"type": "root", "children": [
            {"type": "heading", "tag": "h3", "children": [
                {"type": "text", "text": "Lexical H3"}
            ]},
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<h3>Lexical H3</h3>", html)

    def test_linebreak(self):
        doc = {"type": "root", "children": [
            {"type": "paragraph", "children": [
                {"type": "text", "text": "line 1"},
                {"type": "linebreak"},
                {"type": "text", "text": "line 2"},
            ]},
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<br>", html)
        self.assertIn("line 1", html)
        self.assertIn("line 2", html)

    def test_combined_format_bitmask(self):
        """format=3 表示 bold(1) + italic(2)。"""
        doc = {"type": "root", "children": [
            {"type": "paragraph", "children": [
                {"type": "text", "text": "bold-italic", "format": 3},
            ]},
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<strong>", html)
        self.assertIn("<em>", html)
        self.assertIn("bold-italic", html)

    def test_code_format(self):
        """format=16 表示 inline code。"""
        doc = {"type": "root", "children": [
            {"type": "paragraph", "children": [
                {"type": "text", "text": "variable", "format": 16},
            ]},
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<code>variable</code>", html)

    def test_quote(self):
        doc = {"type": "root", "children": [
            {"type": "quote", "children": [
                {"type": "text", "text": "wise words"},
            ]},
        ]}
        html = ssr.richtext_json_to_html(doc)
        self.assertIn("<blockquote>wise words</blockquote>", html)


class TestQuillDeltaConvert(unittest.TestCase):
    """测试 Quill Delta ops → HTML 转换。"""

    def test_basic_ops(self):
        ops = [
            {"insert": "Hello "},
            {"insert": "bold", "attributes": {"bold": True}},
            {"insert": "\n\nSecond paragraph\n"},
        ]
        html = ssr._convert_quill_ops(ops)
        self.assertIsNotNone(html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("Hello", html)

    def test_image_insert(self):
        ops = [
            {"insert": "text before\n"},
            {"insert": {"image": "https://example.com/photo.jpg"}},
        ]
        html = ssr._convert_quill_ops(ops)
        self.assertIsNotNone(html)
        self.assertIn('src="https://example.com/photo.jpg"', html)

    def test_header_attribute(self):
        ops = [
            {"insert": "Title", "attributes": {"header": 2}},
        ]
        html = ssr._convert_quill_ops(ops)
        self.assertIsNotNone(html)
        self.assertIn("<h2>Title</h2>", html)


class TestModernJsCleanup(unittest.TestCase):
    """测试 Modern.js MDContent 清理。"""

    def test_clean_admonition(self):
        md = ":::warning\n内容\n:::\n其他"
        cleaned = ssr._clean_md_content(md)
        self.assertNotIn(":::", cleaned)
        self.assertIn("> **warning**:", cleaned)
        self.assertIn("内容", cleaned)

    def test_clean_span_anchors(self):
        md = '<span id="abc123"></span>\n## 标题'
        cleaned = ssr._clean_md_content(md)
        self.assertNotIn("<span", cleaned)
        self.assertIn("## 标题", cleaned)

    def test_clean_jsx_residual(self):
        md = "正文内容\n\n```\n代码\n```\n\n}></RenderMd></Tabs.TabPane></Tabs>);"
        cleaned = ssr._clean_md_content(md)
        self.assertNotIn("RenderMd", cleaned)
        self.assertIn("正文内容", cleaned)


class TestCollectMdImageUrls(unittest.TestCase):
    """测试从 Markdown 中提取图片 URL。"""

    def test_basic_images(self):
        md = "![alt](https://example.com/a.png)\n![](https://example.com/b.jpg)"
        urls = ssr.collect_md_image_urls(md)
        self.assertEqual(len(urls), 2)
        self.assertIn("https://example.com/a.png", urls)
        self.assertIn("https://example.com/b.jpg", urls)

    def test_relative_urls_without_base(self):
        """无 base_url 时，相对 URL 应被忽略。"""
        md = "![alt](./local.png)\n![alt](https://example.com/remote.png)"
        urls = ssr.collect_md_image_urls(md)
        self.assertEqual(len(urls), 1)
        self.assertEqual(urls[0], "https://example.com/remote.png")

    def test_relative_urls_with_base(self):
        """提供 base_url 时，相对 URL 应被解析为绝对 URL。"""
        md = "![img](/assets/a.png)\n![img](images/b.jpg)\n![abs](https://cdn.example.com/c.png)"
        urls = ssr.collect_md_image_urls(md, base_url="https://docs.example.com/page/123")
        self.assertEqual(len(urls), 3)
        self.assertEqual(urls[0], "https://docs.example.com/assets/a.png")
        self.assertEqual(urls[1], "https://docs.example.com/page/images/b.jpg")
        self.assertEqual(urls[2], "https://cdn.example.com/c.png")

    def test_data_uri_ignored_with_base(self):
        """data: URI 不应被解析。"""
        md = "![img](data:image/png;base64,iVBOR...)"
        urls = ssr.collect_md_image_urls(md, base_url="https://example.com/")
        self.assertEqual(urls, [])

    def test_title_stripped_from_url(self):
        """标准 Markdown 图片 title 不应污染 URL。"""
        md = '![alt](https://example.com/a.png "img title")\n![ok](https://example.com/b.png)'
        urls = ssr.collect_md_image_urls(md)
        self.assertEqual(len(urls), 2)
        self.assertEqual(urls[0], "https://example.com/a.png")
        self.assertEqual(urls[1], "https://example.com/b.png")

    def test_title_with_single_quotes(self):
        """单引号 title 也应正确剔除。"""
        md = "![alt](https://example.com/pic.jpg 'hover text')"
        urls = ssr.collect_md_image_urls(md)
        self.assertEqual(len(urls), 1)
        self.assertEqual(urls[0], "https://example.com/pic.jpg")

    def test_title_and_size_hint_combined(self):
        """同时有 title 和 size hint 时都应剔除。"""
        md = '![alt](https://example.com/photo.png "title" =800x)'
        urls = ssr.collect_md_image_urls(md)
        # 优先剔除 title，再剔除 size hint（如果有）
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("https://example.com/photo.png"))
        self.assertNotIn('"title"', urls[0])

    def test_no_images(self):
        md = "# Title\n\nJust text, no images."
        urls = ssr.collect_md_image_urls(md)
        self.assertEqual(urls, [])


class TestExtractJsonObject(unittest.TestCase):
    """测试从 HTML 中提取嵌套 JSON 对象。"""

    def test_simple_object(self):
        html = 'window.x = {"a": 1, "b": "hello"};'
        start = html.index("{")
        result = ssr._extract_json_object_str(html, start)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["a"], 1)
        self.assertEqual(parsed["b"], "hello")

    def test_nested_object(self):
        html = 'var x = {"outer": {"inner": [1, 2, 3]}};'
        start = html.index("{")
        result = ssr._extract_json_object_str(html, start)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["outer"]["inner"], [1, 2, 3])

    def test_string_with_braces(self):
        html = 'var x = {"text": "a { b } c"};'
        start = html.index("{")
        result = ssr._extract_json_object_str(html, start)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["text"], "a { b } c")


class TestResolveRelativeMdImages(unittest.TestCase):
    """测试 Markdown 中相对图片 URL 解析为绝对 URL。"""

    def test_absolute_slash_path(self):
        md = "text\n![img](/assets/a.png)\nmore"
        result = ssr.resolve_relative_md_images(md, "https://example.com/docs/page")
        self.assertIn("![img](https://example.com/assets/a.png)", result)

    def test_relative_path(self):
        md = "![pic](images/photo.jpg)"
        result = ssr.resolve_relative_md_images(md, "https://example.com/docs/page")
        self.assertIn("![pic](https://example.com/docs/images/photo.jpg)", result)

    def test_absolute_url_unchanged(self):
        md = "![img](https://cdn.example.com/pic.png)"
        result = ssr.resolve_relative_md_images(md, "https://example.com/")
        self.assertIn("![img](https://cdn.example.com/pic.png)", result)

    def test_data_uri_unchanged(self):
        md = "![img](data:image/png;base64,abc123)"
        result = ssr.resolve_relative_md_images(md, "https://example.com/")
        self.assertIn("data:image/png;base64,abc123", result)

    def test_no_base_url_unchanged(self):
        md = "![img](/assets/a.png)"
        result = ssr.resolve_relative_md_images(md, "")
        self.assertEqual(md, result)

    def test_mixed_urls(self):
        md = (
            "![a](/local/a.png)\n"
            "![b](https://cdn.com/b.jpg)\n"
            "![c](relative/c.gif)"
        )
        result = ssr.resolve_relative_md_images(md, "https://example.com/page/1")
        self.assertIn("https://example.com/local/a.png", result)
        self.assertIn("https://cdn.com/b.jpg", result)
        self.assertIn("https://example.com/page/relative/c.gif", result)


class TestBatchSSRBypassJsChallenge(unittest.TestCase):
    """测试批量模式下 SSR 数据可用时应绕过 JS 反爬拦截（P1 修复验证）。"""

    def test_ssr_available_bypasses_js_challenge(self):
        """含 __NEXT_DATA__ + noscript 标签的页面在批量模式下不应被拦截。"""
        pm_content = json.dumps({
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "这是一段正文内容，长度需要超过50个字符才能通过验证。这是额外的填充文字确保够长。再多加一些文字确保 HTML 输出超过阈值。"}
                ]},
            ]
        })
        article_data = {
            "articleInfo": {
                "title": "测试文章",
                "content": pm_content,
            }
        }
        next_data = json.dumps({
            "props": {"pageProps": {"fallback": {
                "/api/article/detail?id=1": article_data,
            }}}
        })
        # 构造带 noscript 的 HTML（会触发 JS 反爬检测）
        html = (
            '<html><head></head><body>'
            '<noscript>请启用 JavaScript</noscript>'
            '<div id="root"></div>'
            f'<script id="__NEXT_DATA__" type="application/json">{next_data}</script>'
            '</body></html>'
        )
        config = grab.BatchConfig(download_images=False, no_ssr=False, force=False)
        with unittest.mock.patch.object(grab, 'fetch_html', return_value=html):
            result = grab.process_single_url(
                session=object(), url='https://example.com/article/1', config=config
            )
        # SSR 数据可用，不应被 JS 反爬拦截
        self.assertTrue(result.success)
        self.assertIn("正文内容", result.md_content)

    def test_no_ssr_still_blocked(self):
        """无 SSR 数据的 JS 反爬页面在非 force 模式下仍应被拦截。"""
        html = (
            '<html><head></head><body>'
            '<noscript>请启用 JavaScript</noscript>'
            '<div id="root">Loading...</div>'
            '</body></html>'
        )
        config = grab.BatchConfig(download_images=False, no_ssr=False, force=False)
        with unittest.mock.patch.object(grab, 'fetch_html', return_value=html):
            result = grab.process_single_url(
                session=object(), url='https://example.com/spa', config=config
            )
        self.assertFalse(result.success)
        self.assertIn("反爬", result.error or "")


class TestSSRTitleExtraction(unittest.TestCase):
    """测试 _extract_title_for_filename 与 SSR 集成。"""

    def test_ssr_title_takes_priority(self):
        """SSR 标题应优先于 HTML 标题。"""
        html = "<html><head><title>HTML 标题</title></head><body><h1>H1 标题</h1></body></html>"
        ssr_content = ssr.SSRContent(
            title="SSR 标题",
            body="<p>content</p>",
            source_type="nextjs",
            is_markdown=False,
        )
        title = grab._extract_title_for_filename(html, ssr_result=ssr_content)
        self.assertEqual(title, "SSR 标题")

    def test_fallback_without_ssr(self):
        """无 SSR 时回退到 H1 → title 标签。"""
        html = "<html><head><title>网页标题</title></head><body><h1>主标题</h1></body></html>"
        title = grab._extract_title_for_filename(html)
        self.assertEqual(title, "主标题")


if __name__ == "__main__":
    unittest.main()
