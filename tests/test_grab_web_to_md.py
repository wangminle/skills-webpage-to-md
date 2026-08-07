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

    def test_out_bare_filename_not_wrapped(self):
        """显式 --out bare.md 应保持原路径，不自动包同名目录"""
        html = "<html><head><title>Ignored</title></head><body><h1>Hello</h1><p>content</p></body></html>"
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
                        "--out", "article.md",
                        "--overwrite",
                    ])
                self.assertEqual(code, grab.EXIT_SUCCESS)
                self.assertTrue(os.path.isfile(os.path.join(td, "article.md")))
                self.assertFalse(os.path.exists(os.path.join(td, "article", "article.md")))
            finally:
                os.chdir(original_cwd)

    def test_output_alias_works_like_out(self):
        """--output 作为 --out 别名可用"""
        html = "<html><head><title>Ignored</title></head><body><h1>Hello</h1><p>content</p></body></html>"
        with tempfile.TemporaryDirectory() as td:
            html_path = str(pathlib.Path(td) / "page.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)

            out_buf = io.StringIO()
            err_buf = io.StringIO()
            out_path = str(pathlib.Path(td) / "via-output.md")
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                code = grab.main([
                    "--local-html", html_path,
                    "--output", out_path,
                    "--overwrite",
                ])
            self.assertEqual(code, grab.EXIT_SUCCESS)
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


class TestPdfFeatureRemoved(unittest.TestCase):
    """PDF 生成交给专门的 PDF/文档 skill，本 CLI 不再暴露 PDF 参数。"""

    def test_help_does_not_expose_pdf_options(self):
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                grab.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        help_text = out_buf.getvalue()
        self.assertNotIn("--with-pdf", help_text)
        self.assertNotIn("--pdf-allow-file-access", help_text)


class TestCliGuidance(unittest.TestCase):
    def test_js_challenge_suggestion_uses_python3(self):
        result = grab.JSChallengeResult(
            is_challenge=True,
            confidence="high",
            signals=["challenge"],
        )
        suggestions = "\n".join(result.get_suggestions("https://example.com/p"))
        self.assertIn("python3 grab_web_to_md.py", suggestions)
        self.assertNotIn("python grab_web_to_md.py", suggestions)


class TestRegressionSuiteMissingFile(unittest.TestCase):
    def test_missing_regression_suite_reports_error(self):
        import importlib.util

        root = pathlib.Path(__file__).resolve().parents[1]
        runner_path = root / "tests" / "run_regression_suite.py"
        spec = importlib.util.spec_from_file_location("run_regression_suite", runner_path)
        assert spec and spec.loader
        runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runner
        spec.loader.exec_module(runner)

        args = runner.build_parser().parse_args([
            "--suite", str(pathlib.Path("/private/tmp/definitely-missing-regression-suite.md")),
            "--dry-run",
        ])
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            code = runner.run(args)
        self.assertEqual(code, 2)
        self.assertIn("回归测试清单不存在", err_buf.getvalue())


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


# ============================================================================
#  微信异步渲染文章（图文笔记/小绿书）提取测试
# ============================================================================

def _load_extractors_module():
    root = pathlib.Path(__file__).resolve().parents[1]
    mod_path = root / "skills" / "webpage-to-md" / "scripts" / "webpage_to_md" / "extractors.py"
    spec = importlib.util.spec_from_file_location("webpage_to_md.extractors", mod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ext = _load_extractors_module()

# 最小的 window.cgiDataNew HTML 样例，模拟微信图文笔记格式
_WECHAT_ASYNC_HTML = (
    "<html><head><title>微信</title></head><body>"
    "<script>"
    "window.cgiDataNew = {"
    "  is_async: '1',"
    "  item_show_type: '10',"
    "  title: JsDecode('\\x5fae\\x4fe1\\x56fe\\x6587\\x7b14\\x8bb0'),"
    "  nick_name: JsDecode('\\x6d4b\\x8bd5\\x516c\\x4f17\\x53f7'),"
    "  signature: JsDecode('\\x8fd9\\x662f\\x7b7e\\x540d'),"
    "  cdn_url: JsDecode('https://example.com/img.jpg'),"
    "  source_url: JsDecode(''),"
    "  create_time: JsDecode('1709280000'),"
    "  text_page_info: ["
    "    {"
    "      content_noencode: JsDecode('\\x8fd9\\x662f\\x6b63\\x6587\\x5185\\x5bb9\\x3c br\\x3e\\x7b2c\\x4e8c\\x884c'),"
    "    }"
    "  ],"
    "};"
    "</script>"
    "</body></html>"
)

# 传统微信文章 HTML（有 rich_media_content）——不应被误判为异步格式
_WECHAT_TRADITIONAL_HTML = (
    '<html><head><title>微信</title></head><body>'
    '<h1 class="rich_media_title">传统文章</h1>'
    '<div class="rich_media_content" id="js_content"><p>传统正文</p></div>'
    '</body></html>'
)


class TestWechatJsDecode(unittest.TestCase):
    """测试 _wechat_jsdecode 转义还原。"""

    def test_basic_escapes(self):
        self.assertEqual(ext._wechat_jsdecode("a\\x26b"), "a&b")
        self.assertEqual(ext._wechat_jsdecode("\\x3ctag\\x3e"), "<tag>")
        self.assertEqual(ext._wechat_jsdecode("line1\\x0aline2"), "line1\nline2")

    def test_backslash(self):
        self.assertEqual(ext._wechat_jsdecode("a\\x5cb"), "a\\b")

    def test_no_escapes(self):
        self.assertEqual(ext._wechat_jsdecode("plain text"), "plain text")


class TestIsWechatAsyncArticle(unittest.TestCase):
    """测试异步微信文章检测。"""

    def test_detects_async_article(self):
        self.assertTrue(ext.is_wechat_async_article(_WECHAT_ASYNC_HTML))

    def test_traditional_not_detected(self):
        self.assertFalse(ext.is_wechat_async_article(_WECHAT_TRADITIONAL_HTML))

    def test_plain_html_not_detected(self):
        self.assertFalse(ext.is_wechat_async_article("<html><body>hello</body></html>"))

    def test_cgidata_without_async_flag(self):
        html = "<html><body><script>window.cgiDataNew = { is_async: '0' };</script></body></html>"
        self.assertFalse(ext.is_wechat_async_article(html))

    def test_cgidata_with_rich_media(self):
        html = (
            '<html><body>'
            '<div class="rich_media_content"><p>有传统正文</p></div>'
            "<script>window.cgiDataNew = { is_async: '1' };</script>"
            '</body></html>'
        )
        self.assertFalse(ext.is_wechat_async_article(html))

    def test_double_quoted_is_async(self):
        html = (
            '<html><body><script>'
            'window.cgiDataNew = { is_async: "1", item_show_type: "10" };'
            '</script></body></html>'
        )
        self.assertTrue(ext.is_wechat_async_article(html))

    def test_unquoted_is_async(self):
        html = (
            '<html><body><script>'
            'window.cgiDataNew = { is_async: 1, item_show_type: 10 };'
            '</script></body></html>'
        )
        self.assertTrue(ext.is_wechat_async_article(html))


class TestExtractWechatAsyncContent(unittest.TestCase):
    """测试从 cgiDataNew 中提取内容。"""

    def test_extracts_title(self):
        info = ext.extract_wechat_async_content(_WECHAT_ASYNC_HTML)
        self.assertIsNotNone(info)
        self.assertTrue(len(info["title"]) > 0)

    def test_extracts_content(self):
        info = ext.extract_wechat_async_content(_WECHAT_ASYNC_HTML)
        self.assertIsNotNone(info)
        self.assertTrue(len(info["content"]) > 0)

    def test_extracts_nick_name(self):
        info = ext.extract_wechat_async_content(_WECHAT_ASYNC_HTML)
        self.assertIsNotNone(info)
        self.assertTrue(len(info["nick_name"]) > 0)

    def test_returns_none_without_cgidata(self):
        self.assertIsNone(ext.extract_wechat_async_content("<html><body>plain</body></html>"))

    def test_returns_none_without_title(self):
        html = (
            "<html><body><script>"
            "window.cgiDataNew = { nick_name: JsDecode('test') };"
            "</script></body></html>"
        )
        self.assertIsNone(ext.extract_wechat_async_content(html))

    def test_long_content_beyond_80kb(self):
        """内容超过 80KB 时不应被截断导致提取失败。"""
        long_text = "A" * 100_000
        html = (
            "<html><body><script>"
            "window.cgiDataNew = {"
            "  is_async: '1',"
            f"  title: JsDecode('LongTitle'),"
            f"  nick_name: JsDecode('Author'),"
            f"  content_noencode: JsDecode('{long_text}'),"
            "};"
            "</script></body></html>"
        )
        info = ext.extract_wechat_async_content(html)
        self.assertIsNotNone(info)
        self.assertEqual(info["title"], "LongTitle")
        self.assertEqual(len(info["content"]), 100_000)


class TestWechatAsyncToMarkdown(unittest.TestCase):
    """测试异步微信内容转 Markdown。"""

    def test_formats_with_nick_name(self):
        info = {
            "title": "标题",
            "nick_name": "测试号",
            "signature": "签名",
            "content": "第一行\n第二行",
        }
        md = ext.wechat_async_to_markdown(info)
        self.assertIn("> 公众号：**测试号**", md)
        self.assertIn("> 签名", md)
        self.assertIn("第一行", md)
        self.assertIn("第二行", md)

    def test_formats_without_nick_name(self):
        info = {"title": "标题", "nick_name": "", "content": "正文", "signature": ""}
        md = ext.wechat_async_to_markdown(info)
        self.assertNotIn("公众号", md)
        self.assertIn("正文", md)

    def test_empty_content(self):
        info = {"title": "标题", "nick_name": "", "content": "", "signature": ""}
        md = ext.wechat_async_to_markdown(info)
        self.assertEqual(md, "")


class TestWechatAsyncIntegration(unittest.TestCase):
    """集成测试：process_single_url 对异步微信文章的处理。"""

    def test_batch_mode_extracts_async_wechat(self):
        config = grab.BatchConfig(download_images=False)
        with mock.patch.object(grab, "fetch_html", return_value=_WECHAT_ASYNC_HTML):
            result = grab.process_single_url(
                session=object(),
                url="https://mp.weixin.qq.com/s/test_async",
                config=config,
            )
        self.assertTrue(result.success)
        self.assertTrue(len(result.md_content) > 0)
        self.assertEqual(result.image_urls, [])

    def test_batch_mode_traditional_wechat_still_works(self):
        config = grab.BatchConfig(download_images=False)
        with mock.patch.object(grab, "fetch_html", return_value=_WECHAT_TRADITIONAL_HTML):
            result = grab.process_single_url(
                session=object(),
                url="https://mp.weixin.qq.com/s/test_traditional",
                config=config,
            )
        self.assertTrue(result.success)
        self.assertIn("传统正文", result.md_content)

    def test_main_mode_extracts_async_wechat(self):
        """单页 main() 模式也能正确提取异步微信文章。"""
        with tempfile.TemporaryDirectory() as td:
            out_md = os.path.join(td, "out.md")
            with mock.patch.object(grab, "fetch_html", return_value=_WECHAT_ASYNC_HTML):
                with mock.patch.object(grab, "detect_js_challenge") as mock_js:
                    mock_js.return_value = mock.Mock(is_challenge=False)
                    out_buf = io.StringIO()
                    err_buf = io.StringIO()
                    with redirect_stdout(out_buf), redirect_stderr(err_buf):
                        code = grab.main([
                            "https://mp.weixin.qq.com/s/test_async",
                            "--out", out_md,
                            "--overwrite",
                            "--no-map-json",
                        ])
            self.assertEqual(code, grab.EXIT_SUCCESS)
            self.assertTrue(os.path.isfile(out_md))
            with open(out_md, encoding="utf-8") as f:
                content = f.read()
            self.assertTrue(len(content) > 50)


# ═══════════════════════════════════════════════════════════════════════
# Notion 模块测试
# ═══════════════════════════════════════════════════════════════════════

from webpage_to_md.notion import (
    _blocks_to_html,
    _extract_page_id,
    _render_notion_table,
    _rich_text_to_html,
    is_notion_url,
)


class TestNotionURLDetection(unittest.TestCase):
    def test_notion_url_positive(self):
        self.assertTrue(is_notion_url("https://www.notion.so/Kiro-29cbd3b8020080d5a1e5f7cd300576dd"))
        self.assertTrue(is_notion_url("https://notion.so/Some-Page-abcdef0123456789abcdef0123456789"))

    def test_notion_site_url_positive(self):
        self.assertTrue(is_notion_url("https://team.notion.site/Test-29cbd3b8020080d5a1e5f7cd300576dd"))
        self.assertTrue(is_notion_url("https://my-org.notion.site/Page-abcdef0123456789abcdef0123456789"))

    def test_notion_url_negative(self):
        self.assertFalse(is_notion_url("https://example.com/page"))
        self.assertFalse(is_notion_url("https://www.notion.so/"))
        self.assertFalse(is_notion_url(""))

    def test_notion_url_no_page_id_negative(self):
        """notion.so 子路径但无 32 位 page ID 的普通页面不应识别为 Notion 公开页面"""
        self.assertFalse(is_notion_url("https://www.notion.so/help/guides"))
        self.assertFalse(is_notion_url("https://www.notion.so/product/docs"))
        self.assertFalse(is_notion_url("https://www.notion.so/about"))
        self.assertFalse(is_notion_url("https://www.notion.so/pricing"))

    def test_extract_page_id(self):
        pid = _extract_page_id("https://www.notion.so/Kiro-29cbd3b8020080d5a1e5f7cd300576dd")
        self.assertEqual(pid, "29cbd3b8-0200-80d5-a1e5-f7cd300576dd")

    def test_extract_page_id_no_title_prefix(self):
        pid = _extract_page_id("https://www.notion.so/29cbd3b8020080d5a1e5f7cd300576dd")
        self.assertEqual(pid, "29cbd3b8-0200-80d5-a1e5-f7cd300576dd")

    def test_extract_page_id_invalid(self):
        self.assertIsNone(_extract_page_id("https://www.notion.so/"))
        self.assertIsNone(_extract_page_id("https://example.com"))


class TestNotionRichText(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(_rich_text_to_html([["Hello"]]), "Hello")

    def test_bold_italic(self):
        result = _rich_text_to_html([["Bold", [["b"]]], [" and "], ["Italic", [["i"]]]])
        self.assertIn("<b>Bold</b>", result)
        self.assertIn("<i>Italic</i>", result)

    def test_link(self):
        result = _rich_text_to_html([["click", [["a", "https://example.com"]]]])
        self.assertIn('href="https://example.com"', result)
        self.assertIn("click", result)

    def test_code(self):
        result = _rich_text_to_html([["code()", [["c"]]]])
        self.assertIn("<code>code()</code>", result)

    def test_html_escape(self):
        result = _rich_text_to_html([["<script>alert(1)</script>"]])
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)


class TestNotionBlocksToHTML(unittest.TestCase):
    def _make_block(self, block_id, block_type, title_text="", children=None, **extra):
        block = {
            "id": block_id,
            "type": block_type,
            "properties": {"title": [[title_text]]} if title_text else {},
        }
        if children:
            block["content"] = children
        block.update(extra)
        return block

    def test_basic_page(self):
        page_id = "page-1"
        blocks = {
            page_id: self._make_block(page_id, "page", "Test Page", children=["b1", "b2"]),
            "b1": self._make_block("b1", "header", "Section 1"),
            "b2": self._make_block("b2", "text", "Hello world"),
        }
        html, title = _blocks_to_html(blocks, page_id)
        self.assertEqual(title, "Test Page")
        self.assertIn("<h1>Test Page</h1>", html)
        self.assertIn("<h1>Section 1</h1>", html)
        self.assertIn("<p>Hello world</p>", html)

    def test_nested_lists(self):
        page_id = "page-1"
        blocks = {
            page_id: self._make_block(page_id, "page", "List Page", children=["l1", "l2"]),
            "l1": self._make_block("l1", "bulleted_list", "Item A"),
            "l2": self._make_block("l2", "bulleted_list", "Item B"),
        }
        html, _ = _blocks_to_html(blocks, page_id)
        self.assertIn("<ul>", html)
        self.assertIn("<li>Item A</li>", html)
        self.assertIn("<li>Item B</li>", html)

    def test_code_block(self):
        page_id = "page-1"
        blocks = {
            page_id: self._make_block(page_id, "page", "Code", children=["c1"]),
            "c1": {
                "id": "c1", "type": "code",
                "properties": {
                    "title": [["print('hello')"]],
                    "language": [["python"]],
                },
            },
        }
        html, _ = _blocks_to_html(blocks, page_id)
        self.assertIn("language-python", html)
        self.assertIn("print(&#x27;hello&#x27;)", html)

    def test_toggle_block(self):
        page_id = "page-1"
        blocks = {
            page_id: self._make_block(page_id, "page", "Toggle", children=["t1"]),
            "t1": self._make_block("t1", "toggle", "Click me", children=["t1c"]),
            "t1c": self._make_block("t1c", "text", "Hidden content"),
        }
        html, _ = _blocks_to_html(blocks, page_id)
        self.assertIn("<details>", html)
        self.assertIn("<summary>Click me</summary>", html)
        self.assertIn("Hidden content", html)

    def test_quote_block(self):
        page_id = "page-1"
        blocks = {
            page_id: self._make_block(page_id, "page", "Q", children=["q1"]),
            "q1": self._make_block("q1", "quote", "A wise saying"),
        }
        html, _ = _blocks_to_html(blocks, page_id)
        self.assertIn("<blockquote>A wise saying</blockquote>", html)

    def test_nested_todo(self):
        """嵌套待办项：父 to_do 包含子 to_do，子项不应丢失。"""
        page_id = "page-1"
        blocks = {
            page_id: self._make_block(page_id, "page", "Todos", children=["t1"]),
            "t1": {
                "id": "t1", "type": "to_do",
                "properties": {"title": [["Parent task"]], "checked": [["No"]]},
                "content": ["t2"],
            },
            "t2": {
                "id": "t2", "type": "to_do",
                "properties": {"title": [["Child task"]], "checked": [["Yes"]]},
            },
        }
        html, _ = _blocks_to_html(blocks, page_id)
        self.assertIn("Parent task", html)
        self.assertIn("Child task", html)
        self.assertIn("☐", html)
        self.assertIn("☑", html)

    def test_divider(self):
        page_id = "page-1"
        blocks = {
            page_id: self._make_block(page_id, "page", "D", children=["d1"]),
            "d1": self._make_block("d1", "divider"),
        }
        html, _ = _blocks_to_html(blocks, page_id)
        self.assertIn("<hr/>", html)


class TestNotionNoNotionFlag(unittest.TestCase):
    """测试 --no-notion 参数能正确禁用 Notion 检测。"""

    def test_no_notion_skips_api(self):
        with tempfile.TemporaryDirectory() as td:
            out_md = os.path.join(td, "out.md")
            html = "<html><head><title>Test</title></head><body><p>Content</p></body></html>"
            with mock.patch.object(grab, "fetch_html", return_value=html):
                with mock.patch.object(grab, "detect_js_challenge") as mock_js:
                    mock_js.return_value = mock.Mock(is_challenge=False)
                    with mock.patch("webpage_to_md.notion.fetch_notion_page") as mock_notion:
                        out_buf = io.StringIO()
                        err_buf = io.StringIO()
                        with redirect_stdout(out_buf), redirect_stderr(err_buf):
                            code = grab.main([
                                "https://www.notion.so/Test-abcdef0123456789abcdef0123456789",
                                "--out", out_md,
                                "--overwrite",
                                "--no-map-json",
                                "--no-notion",
                            ])
                        mock_notion.assert_not_called()
            self.assertEqual(code, grab.EXIT_SUCCESS)


class TestNotionAPIFailureNoFallback(unittest.TestCase):
    """Notion URL 识别后 API 失败，不应静默回退到 HTTP，应报错。"""

    def test_batch_notion_api_failure_returns_error(self):
        """批量模式：Notion API 失败应返回 success=False，不应静默成功。"""
        config = grab.BatchConfig(
            timeout=10,
            retries=1,
            max_html_bytes=5_000_000,
            no_notion=False,
        )
        with mock.patch.object(grab, "fetch_notion_page", side_effect=RuntimeError("API 500")):
            result = grab.process_single_url(
                url="https://www.notion.so/Test-abcdef0123456789abcdef0123456789",
                config=config,
                session=mock.MagicMock(),
            )
            self.assertFalse(result.success)
            self.assertIn("Notion API", result.error)

    def test_single_page_notion_api_failure_exits(self):
        """单页模式：Notion API 失败应返回 EXIT_ERROR，不应回退到 HTTP。"""
        with tempfile.TemporaryDirectory() as td:
            out_md = os.path.join(td, "out.md")
            with mock.patch("webpage_to_md.notion.fetch_notion_page", side_effect=RuntimeError("API 500")):
                out_buf = io.StringIO()
                err_buf = io.StringIO()
                with redirect_stdout(out_buf), redirect_stderr(err_buf):
                    code = grab.main([
                        "https://www.notion.so/Test-abcdef0123456789abcdef0123456789",
                        "--out", out_md,
                        "--overwrite",
                        "--no-map-json",
                    ])
                self.assertEqual(code, grab.EXIT_ERROR)
                self.assertIn("Notion API", err_buf.getvalue())


# ============================================================================
# P1 Bug 回归测试
# ============================================================================

class TestP1BugFixes(unittest.TestCase):
    """9 个 P1 bug 修复的回归测试，防止未来再次引入。"""

    # ── Bug1: clean_wechat_noise 误删正文中的「取消/允许」 ──────────────
    def test_bug1_wechat_noise_preserves_body_text(self):
        """微信噪音清理不应删除正文句子中的「允许/取消」等同名词。"""
        md = "该设置允许用户取消订阅。\n\n正文继续"
        result = grab.clean_wechat_noise(md)
        self.assertIn("允许用户取消订阅", result)

    def test_bug1_wechat_noise_strips_standalone_buttons(self):
        """独占整行的按钮噪音（Cancel/Allow/Share 等）应被清理。"""
        md = "正文\n\nCancel\n\nAllow\n\nShare\n\n正文继续"
        result = grab.clean_wechat_noise(md)
        self.assertNotIn("Cancel", result)
        self.assertNotIn("Share", result)
        self.assertIn("正文", result)

    def test_bug1_wechat_noise_strips_button_link_form(self):
        """链接形式的按钮 [阅读原文](url) 应被清理。"""
        md = "正文\n\n[阅读原文](https://mp.weixin.qq.com/s?xxx)\n\n正文继续"
        result = grab.clean_wechat_noise(md)
        self.assertNotIn("阅读原文", result)

    # ── Bug2: 合并模式标题降级破坏代码块 ──────────────────────────────
    def test_bug2_merge_demote_preserves_code_block_comments(self):
        """合并模式标题降级不应把代码块内的 # 注释行改成标题。"""
        md_content = "```bash\n# 安装依赖\npip install foo\n```\n\n# 页面标题"
        result_obj = grab.BatchPageResult(
            url="https://docs.example.com/x", title="Test",
            md_content=md_content, success=True,
        )
        merged, _ = grab.generate_merged_markdown(
            [result_obj], include_toc=False, main_title="Doc", redact_urls=False,
        )
        self.assertIn("# 安装依赖", merged)
        self.assertNotIn("### 安装依赖", merged)
        # 页面标题应被降级（# → ###）
        self.assertIn("### 页面标题", merged)

    def test_bug2_html_to_markdown_preserves_code_fence_content(self):
        """html_to_markdown 后处理不应折叠/删除代码块内的空行和注释。"""
        html = "<pre><code># comment\n\n\nblank above</code></pre>"
        md = grab.html_to_markdown(html, base_url="https://x.com/", url_to_local={})
        self.assertIn("# comment", md)

    # ── Bug3: 目标容器 void 元素泄漏页脚 ──────────────────────────────
    def test_bug3_target_extractor_void_no_leak(self):
        """void 元素（br/img/hr）不应使深度计数器永不归零，避免泄漏页脚。"""
        html = (
            '<div id="content"><p>正文</p><br><img src="x.png"><hr></div>'
            "<footer><p>页脚不应出现</p></footer>"
        )
        result = grab.extract_target_html(html, target_id="content", target_class=None)
        self.assertIsNotNone(result)
        self.assertNotIn("页脚", result)
        self.assertIn("正文", result)

    def test_bug3_target_extractor_nested_div_closes(self):
        """嵌套 div 应正确归零，不泄漏后续内容。"""
        html = '<div id="c"><div><p>深层</p></div></div><footer>页脚</footer>'
        result = grab.extract_target_html(html, target_id="c", target_class=None)
        self.assertNotIn("页脚", result)
        self.assertIn("深层", result)

    # ── Bug4: <a> 包 <img> 多出垃圾链接 ────────────────────────────────
    def test_bug4_a_wrapping_img_no_bare_link(self):
        """<a> 内仅含图片时不应输出回退裸链接。"""
        html = '<a href="https://x.com/page"><img src="https://x.com/img.png" alt="pic"></a>'
        md = grab.html_to_markdown(html, base_url="https://x.com/", url_to_local={})
        # 应只有图片，无裸链接 [https://x.com/page](https://x.com/page)
        self.assertEqual(md.count("]("), 1)
        self.assertIn("![pic]", md)

    def test_bug4_a_with_img_and_text(self):
        """<a> 内含图片+文字时两者都应保留。"""
        html = '<a href="https://x.com/p"><img src="https://x.com/i.png" alt="img">点击</a>'
        md = grab.html_to_markdown(html, base_url="https://x.com/", url_to_local={})
        self.assertIn("![img]", md)
        self.assertIn("点击", md)

    # ── Bug5: Editor.js header level 非数字崩溃 ────────────────────────
    def test_bug5_editorjs_header_non_numeric_level(self):
        """Editor.js header 的 level 为非数字字符串时不应崩溃。"""
        blocks = [{"type": "header", "data": {"text": "标题", "level": "high"}}]
        html = ssr._convert_editorjs_blocks(blocks)
        self.assertIn("标题", html)
        self.assertIn("<h2>", html)  # 回退到默认 2

    def test_bug5_editorjs_header_none_level(self):
        blocks = [{"type": "header", "data": {"text": "标题", "level": None}}]
        html = ssr._convert_editorjs_blocks(blocks)
        self.assertIn("标题", html)

    # ── Bug6: Quill Delta header 转换错误 ──────────────────────────────
    def test_bug6_quill_header_correct(self):
        """Quill header 是行级属性，应正确产出 <hN> 而非空标题。"""
        ops = [
            {"insert": "标题", "attributes": {"header": 2}},
            {"insert": "\n", "attributes": {"header": 2}},
            {"insert": "段落\n"},
        ]
        html = ssr._convert_quill_ops(ops)
        self.assertIn("<h2>标题</h2>", html)
        self.assertIn("<p>段落</p>", html)
        self.assertNotIn("<h2>\n</h2>", html)

    def test_bug6_quill_list(self):
        """Quill list 属性应正确产出 <ul>/<ol>。"""
        ops = [
            {"insert": "a"}, {"insert": "\n", "attributes": {"list": "bullet"}},
            {"insert": "b"}, {"insert": "\n", "attributes": {"list": "bullet"}},
        ]
        html = ssr._convert_quill_ops(ops)
        self.assertIn("<ul>", html)
        self.assertIn("<li>a</li>", html)

    # ── Bug7: Notion 原生表格丢失 ──────────────────────────────────────
    def test_bug7_notion_table_rendered(self):
        """Notion table + table_row 应渲染为 <table>，不丢失数据。"""
        all_blocks = {
            "t": {
                "id": "t", "type": "table",
                "content": ["r1", "r2"],
                "format": {"table_block_column_header": True},
            },
            "r1": {"id": "r1", "type": "table_row", "properties": {
                "c0": [["Name"]], "c1": [["Age"]],
            }},
            "r2": {"id": "r2", "type": "table_row", "properties": {
                "c0": [["Alice"]], "c1": [["30"]],
            }},
        }
        html = _render_notion_table(all_blocks, all_blocks["t"])
        self.assertIn("<table>", html)
        self.assertIn("Alice", html)
        self.assertIn("30", html)

    def test_bug7_notion_table_empty(self):
        """空表格应返回空字符串。"""
        all_blocks = {"t": {"id": "t", "type": "table", "content": []}}
        self.assertEqual(_render_notion_table(all_blocks, all_blocks["t"]), "")

    # ── Bug8: JS 反爬检测误杀正常页面 ──────────────────────────────────
    def test_bug8_challenge_title_not_false_positive(self):
        """标题含 'Challenge' 的正常页面（正文足够长）不应被判为反爬。"""
        body = "<article><p>" + "正常内容" * 60 + "</p></article>"
        html = f"<html><head><title>Weekly Challenge 314 - LeetCode</title></head><body>{body}</body></html>"
        result = grab.detect_js_challenge(html)
        self.assertFalse(result.is_challenge)

    def test_bug8_real_cloudflare_still_detected(self):
        """真实 Cloudflare 挑战页仍应被检测为 high。"""
        html = (
            '<html><head><title>Just a moment...</title></head>'
            '<body>Checking<div></div>'
            '<script>__cf_chl_opt={};</script></body></html>'
        )
        result = grab.detect_js_challenge(html)
        self.assertTrue(result.is_challenge)
        self.assertEqual(result.confidence, "high")

    def test_bug8_spa_placeholder_still_detected(self):
        """SPA 占位页（短正文 + noscript）仍应被检测。"""
        html = (
            '<html><head></head><body>'
            '<noscript>请启用 JavaScript</noscript>'
            '<div id="root">Loading...</div></body></html>'
        )
        result = grab.detect_js_challenge(html)
        self.assertTrue(result.is_challenge)

    # ── Bug9: 批量模式 --browser-fetch 不可用 ──────────────────────────
    def test_bug9_batch_browser_fetch_skips_js_detection(self):
        """browser-fetch 模式下不应因 noscript 触发 JS 反爬拦截。"""
        html = (
            '<html><head><title>文章</title></head><body>'
            '<noscript>请启用 JavaScript</noscript>'
            '<article><p>正文内容，足够长以通过质量检查' + "填充" * 30 + '</p></article>'
            '</body></html>'
        )
        config = grab.BatchConfig(
            browser_fetch=True, no_ssr=True, no_notion=True, force=False,
        )
        with mock.patch.object(grab, "browser_fetch_html", return_value=html):
            result = grab.process_single_url(
                session=mock.MagicMock(), url="https://example.com/a", config=config,
            )
        self.assertTrue(result.success, f"browser-fetch 被误拦截: {result.error}")

    # ── Bug6 残留: 无尾部 \n 的 Quill header 不应双重输出 ──────────────
    def test_bug6_quill_header_without_trailing_newline(self):
        """块属性直接挂在文本 op、无尾部 \\n 时，只应产出一次标题。"""
        ops = [{"insert": "Title", "attributes": {"header": 2}}]
        html = ssr._convert_quill_ops(ops)
        self.assertEqual(html.count("Title"), 1)
        self.assertIn("<h2>Title</h2>", html)
        self.assertNotIn("<p>Title</p>", html)


# ============================================================================
# P2 高价值修复回归测试
# ============================================================================

class TestP2BugFixes(unittest.TestCase):
    """高价值 P2：阈值统一 / local-html 编码 / HttpOnly cookie / str.find / js:空白。"""

    def test_auto_detect_threshold_unified(self):
        """单页与批量 auto-detect 应共用同一置信度阈值常量。"""
        self.assertTrue(hasattr(grab, "AUTO_DETECT_CONFIDENCE_THRESHOLD"))
        self.assertEqual(grab.AUTO_DETECT_CONFIDENCE_THRESHOLD, 0.6)

    def test_batch_auto_detect_respects_unified_threshold(self):
        """批量模式置信度 0.55 时不应应用预设（与单页 0.6 对齐）。"""
        html = (
            '<html><head><title>Docs</title></head><body>'
            '<div class="theme-doc-markdown markdown">'
            '<p>' + ("正文内容" * 40) + '</p></div></body></html>'
        )
        config = grab.BatchConfig(
            auto_detect=True, no_ssr=True, no_notion=True, download_images=False,
        )
        with mock.patch.object(grab, "fetch_html", return_value=html):
            with mock.patch.object(
                grab, "detect_docs_framework",
                return_value=("docusaurus", 0.55, ["signal"]),
            ):
                with mock.patch.object(grab, "detect_js_challenge") as mock_js:
                    mock_js.return_value = grab.JSChallengeResult(
                        is_challenge=False, confidence="none", signals=[],
                    )
                    with mock.patch.object(
                        grab, "extract_target_html_multi",
                    ) as mock_target:
                        result = grab.process_single_url(
                            session=mock.MagicMock(),
                            url="https://docs.example.com/page",
                            config=config,
                        )
        self.assertTrue(result.success, result.error)
        # 置信度 0.55 < 0.6：不应套用 docusaurus 的 target 提取
        mock_target.assert_not_called()

    def test_decode_html_bytes_respects_meta_charset(self):
        """本地 HTML 应按 meta charset 解码（Shift_JIS），而非强制 UTF-8。"""
        from webpage_to_md.http_client import decode_html_bytes

        body = "日本語テスト"
        raw = (
            b'<html><head><meta charset="Shift_JIS"></head><body>'
            + body.encode("shift_jis")
            + b"</body></html>"
        )
        text = decode_html_bytes(raw)
        self.assertIn(body, text)
        # 若误用 UTF-8 replace，日文会变成替换字符
        self.assertNotIn("\ufffd", text)

    def test_read_local_html_file_shift_jis(self):
        """read_local_html_file 应正确读取 Shift_JIS 存档页。"""
        from webpage_to_md.http_client import read_local_html_file

        body = "ShiftJIS本文"
        raw = (
            b'<html><head><meta http-equiv="Content-Type" '
            b'content="text/html; charset=Shift_JIS"></head><body>'
            + body.encode("shift_jis")
            + b"</body></html>"
        )
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            f.write(raw)
            path = f.name
        try:
            text = read_local_html_file(path)
            self.assertIn(body, text)
        finally:
            os.unlink(path)

    def test_httponly_cookies_loaded(self):
        """cookies.txt 中 #HttpOnly_ 前缀条目应被加载，而非当注释丢弃。"""
        from webpage_to_md.http_client import _parse_cookies_file

        content = (
            "# Netscape HTTP Cookie File\n"
            ".example.com\tTRUE\t/\tFALSE\t0\tnormal\tabc\n"
            "#HttpOnly_.example.com\tTRUE\t/\tTRUE\t0\tsession\tsecret\n"
            "# This is a real comment\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(content)
            path = f.name
        try:
            cookies = _parse_cookies_file(path)
            self.assertEqual(cookies.get("normal"), "abc")
            self.assertEqual(cookies.get("session"), "secret")
        finally:
            os.unlink(path)

    def test_iter_script_bodies_uses_str_find(self):
        """script 扫描应基于 str.find，正确提取多个 script 正文。"""
        html = (
            "<html><body>"
            "<script type='application/json'>{\"a\":1}</script>"
            "<script>var x = 2;</script>"
            "<SCRIPT>UPPER</SCRIPT>"
            "</body></html>"
        )
        bodies = list(ssr._iter_script_bodies(html))
        self.assertEqual(len(bodies), 3)
        self.assertIn('{"a":1}', bodies[0])
        self.assertIn("var x = 2;", bodies[1])
        self.assertIn("UPPER", bodies[2])

    def test_scan_scripts_no_catastrophic_backtracking_regex(self):
        """确认 _scan_scripts_for_richtext 不再依赖带 .*? 的全局 script 正则。"""
        self.assertFalse(hasattr(ssr, "_SCRIPT_TAG_RE") and ssr._SCRIPT_TAG_RE is not None
                         and ".*?" in getattr(ssr._SCRIPT_TAG_RE, "pattern", ""))
        # 超大近似 script 内容仍应能完成扫描（不挂起）
        big = "<script>" + ("{" * 5000) + ("}" * 5000) + "</script>"
        result = ssr._scan_scripts_for_richtext(big)
        self.assertIsNone(result)

    def test_sanitize_js_href_leading_whitespace(self):
        """href 值前导空白的 javascript: 协议也应被清除。"""
        dirty = '<a href=" javascript:alert(1)">x</a>'
        clean = ssr._sanitize_editorjs_html(dirty)
        self.assertNotIn("javascript:", clean.lower())
        self.assertIn("href=", clean)

    # ── P2-3: 孤儿标题正则误删无关标题 ─────────────────────────────────
    def test_p2_3_consecutive_titles_not_deleted(self):
        """剥离锚点列表后，连续的章节标题不应被孤儿清理误删。"""
        links = "".join(f"- [l{i}](u{i})\n" for i in range(1, 22))
        md = f"正文。\n\n{links}\n### 参考资料\n\n#### 官方文档\n\n参考内容。"
        result, _ = grab.strip_anchor_lists(md, threshold=20)
        self.assertIn("### 参考资料", result)
        self.assertIn("#### 官方文档", result)
        self.assertIn("参考内容", result)

    def test_p2_3_title_before_hr_not_deleted(self):
        """标题后跟 --- 不应被当孤儿删除。"""
        links = "".join(f"- [l{i}](u{i})\n" for i in range(1, 22))
        md = f"{links}\n### 重要章节\n\n---\n\n后续内容"
        result, _ = grab.strip_anchor_lists(md, threshold=20)
        self.assertIn("### 重要章节", result)
        self.assertIn("---", result)

    def test_p2_3_real_orphan_at_eof_deleted(self):
        """文档末尾的真正孤儿标题（标题+空行+EOF）应被清理。"""
        links = "".join(f"- [l{i}](u{i})\n" for i in range(1, 22))
        md = f"正文。\n\n{links}\n### 孤立标题\n\n\n"
        result, _ = grab.strip_anchor_lists(md, threshold=20)
        self.assertNotIn("### 孤立标题", result)

    # ── P2-4: 懒加载图 src=data: 时 data-src 被丢弃 ────────────────────
    def test_p2_4_lazy_image_data_src_collected(self):
        """src=data: 占位时 data-src 真实 URL 应被 ImageURLCollector 收集。"""
        html = ('<img src="data:image/gif;base64,R0lGODlh" '
                'data-src="https://example.com/real.jpg" alt="r">')
        c = grab.ImageURLCollector(base_url="https://example.com/")
        c.feed(html)
        self.assertIn("https://example.com/real.jpg", c.image_urls)

    def test_p2_4_lazy_image_markdown_uses_data_src(self):
        """html_to_markdown 中 data: 占位图应回退到 data-src。"""
        html = ('<img src="data:image/gif;base64,xxx" '
                'data-src="https://example.com/real.jpg" alt="图">')
        md = grab.html_to_markdown(html, base_url="https://example.com/", url_to_local={})
        self.assertIn("https://example.com/real.jpg", md)
        self.assertNotIn("data:image/gif", md)

    def test_p2_4_normal_src_unchanged(self):
        """正常 src（非 data:）不应受懒加载回退影响。"""
        html = '<img src="https://example.com/normal.png" alt="n">'
        md = grab.html_to_markdown(html, base_url="https://example.com/", url_to_local={})
        self.assertIn("https://example.com/normal.png", md)

    # ── P2-7: 4xx 客户端错误不重试（确定性失败） ────────────────────────
    def test_p2_7_404_not_retried(self):
        """404 等确定性 4xx 错误不应被重试。"""
        import requests as req_mod
        from webpage_to_md.http_client import fetch_html

        call_count = [0]
        fake_resp = mock.MagicMock()
        fake_resp.status_code = 404
        fake_resp.raise_for_status.side_effect = req_mod.exceptions.HTTPError(
            response=fake_resp,
        )

        def fake_get(*a, **kw):
            call_count[0] += 1
            return fake_resp

        session = mock.MagicMock()
        session.get.side_effect = fake_get
        with self.assertRaises(req_mod.exceptions.HTTPError):
            fetch_html(session, "https://x.com/missing", timeout_s=5, retries=3)
        self.assertEqual(call_count[0], 1, "404 不应被重试")

    def test_p2_7_429_retried(self):
        """429 限流应被重试（临时性错误）。"""
        import requests as req_mod
        from webpage_to_md.http_client import fetch_html

        call_count = [0]
        fake_resp_429 = mock.MagicMock()
        fake_resp_429.status_code = 429
        fake_resp_429.raise_for_status.side_effect = req_mod.exceptions.HTTPError(
            response=fake_resp_429,
        )
        fake_resp_ok = mock.MagicMock()
        fake_resp_ok.status_code = 200
        fake_resp_ok.headers = {}
        fake_resp_ok.raise_for_status.return_value = None
        fake_resp_ok.iter_content.return_value = iter([b"<html>OK</html>"])
        fake_resp_ok.encoding = "utf-8"

        def fake_get(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return fake_resp_429
            return fake_resp_ok

        session = mock.MagicMock()
        session.get.side_effect = fake_get
        result = fetch_html(session, "https://x.com/rate", timeout_s=5, retries=3)
        self.assertGreater(call_count[0], 1, "429 应被重试")
        self.assertIn("OK", result)

    # ── P2-8: redact_url 剥离 userinfo 凭据 ────────────────────────────
    def test_p2_8_redact_strips_userinfo(self):
        """URL 中的 user:password@ 凭据应被脱敏剥离。"""
        r = grab.redact_url("https://user:password@host.com/path?secret=1")
        self.assertNotIn("password", r)
        self.assertNotIn("user", r)
        self.assertEqual(r, "https://host.com/path")

    def test_p2_8_redact_preserves_port(self):
        """剥离 userinfo 时应保留端口号。"""
        r = grab.redact_url("http://admin:pass@host.com:8080/p?q=1")
        self.assertNotIn("admin", r)
        self.assertIn(":8080", r)

    def test_p2_8_redact_only_username(self):
        """仅有 username 也应被剥离。"""
        r = grab.redact_url("https://token@host.com/p")
        self.assertNotIn("token", r)
        self.assertEqual(r, "https://host.com/p")

    # ── P2-6: relpath 跨盘符回退 ────────────────────────────────────────
    def test_p2_6_relpath_cross_drive_fallback(self):
        """跨盘符时 relpath 回退为绝对路径，不抛 ValueError。"""
        from webpage_to_md.output import batch_save_individual
        from webpage_to_md.models import BatchPageResult

        result = BatchPageResult(
            url="https://x.com/a", title="Test",
            md_content="![img](test.assets/01.png)", success=True,
        )
        with tempfile.TemporaryDirectory() as td:
            # 模拟跨盘符场景：shared_assets_dir 无法 relpath 到 output_dir
            # 使用 mock 让 relpath 抛 ValueError
            with mock.patch("os.path.relpath", side_effect=ValueError("cross-drive")):
                saved = batch_save_individual(
                    results=[result],
                    output_dir=os.path.join(td, "out"),
                    shared_assets_dir="D:/shared.assets",
                    redact_urls=False,
                )
            self.assertEqual(len(saved), 1)
            with open(saved[0], "r", encoding="utf-8") as f:
                content = f.read()
            # 图片路径应被改写为绝对路径（而非丢失）
            self.assertIn("shared.assets/01.png", content)

    # ── BUG-013: 合并模式文件存在检查应在抓取前提前 ─────────────────────
    def test_p2_013_merge_exists_check_before_fetch(self):
        """合并模式输出文件已存在时，应在抓取开始前提前返回，不浪费抓取配额。"""
        with tempfile.TemporaryDirectory() as td:
            urls_file = os.path.join(td, "urls.txt")
            with open(urls_file, "w") as f:
                f.write("https://example.com/page1\n")
            merged = os.path.join(td, "merged.md")
            with open(merged, "w") as f:
                f.write("existing content")

            fetch_called = [False]
            def fake_fetch(**kw):
                fetch_called[0] = True
                return "<html></html>"

            out_buf = io.StringIO()
            err_buf = io.StringIO()
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                with mock.patch.object(grab, "fetch_html", side_effect=fake_fetch):
                    code = grab.main([
                        "--urls-file", urls_file,
                        "--merge", "--merge-output", merged,
                    ])
            self.assertEqual(code, grab.EXIT_FILE_EXISTS)
            # 关键断言：fetch_html 不应被调用（提前退出了）
            self.assertFalse(fetch_called[0], "文件已存在时不应发起抓取")

    def test_p2_013_merge_overwrite_proceeds(self):
        """--overwrite 时合并模式应正常进行（不因文件存在而退出）。"""
        with tempfile.TemporaryDirectory() as td:
            urls_file = os.path.join(td, "urls.txt")
            with open(urls_file, "w") as f:
                f.write("https://example.com/page1\n")
            merged = os.path.join(td, "out", "merged.md")
            os.makedirs(os.path.dirname(merged), exist_ok=True)
            with open(merged, "w") as f:
                f.write("old")

            html = "<html><head><title>T</title></head><body><article><p>正文</p></article></body></html>"
            out_buf = io.StringIO()
            err_buf = io.StringIO()
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                with mock.patch.object(grab, "fetch_html", return_value=html):
                    with mock.patch.object(grab, "detect_js_challenge") as mj:
                        mj.return_value = grab.JSChallengeResult(
                            is_challenge=False, confidence="none", signals=[])
                        code = grab.main([
                            "--urls-file", urls_file,
                            "--merge", "--merge-output", merged,
                            "--overwrite",
                        ])
            self.assertEqual(code, grab.EXIT_SUCCESS)

    # ── BUG-030: _append_text 空格启发式割裂 CJK 文本 ──────────────────
    def test_p2_030_cjk_no_space_insertion(self):
        """CJK 字符之间的内联标签不应插入空格（你好<span>世界 → 你好世界）。"""
        html = "<article><p>你好<strong>世界</strong></p></article>"
        md = grab.html_to_markdown(html, base_url="https://x.com/", url_to_local={})
        self.assertIn("你好**世界**", md)
        self.assertNotIn("你好 **世界**", md)

    def test_p2_030_english_keeps_space(self):
        """英文单词之间的内联标签仍应插入空格（hello <b>world）。"""
        html = "<article><p>hello <strong>world</strong></p></article>"
        md = grab.html_to_markdown(html, base_url="https://x.com/", url_to_local={})
        self.assertIn("hello **world**", md)

    def test_p2_030_cjk_english_boundary(self):
        """CJK 与英文交界处仍可插空格（不割裂任一侧）。"""
        html = "<article><p>中文<strong>English</strong></p></article>"
        md = grab.html_to_markdown(html, base_url="https://x.com/", url_to_local={})
        # CJK(中文) + English 交界：不应割裂中文，但 English 前可有空格
        self.assertIn("中文", md)
        self.assertIn("**English**", md)
        self.assertNotIn("中 文", md)

    # ── BUG-035: _attrs_to_str / <a> 协议过滤绕过 ─────────────────────
    def test_p2_035_javascript_protocol_blocked(self):
        """<a href='javascript:...'> 的脚本协议不应出现在输出中。"""
        html = '<a href="javascript:alert(1)">x</a>'
        md = grab.html_to_markdown(html, base_url="https://x.com/", url_to_local={})
        self.assertNotIn("javascript", md.lower())

    def test_p2_035_control_char_variant_blocked(self):
        """含控制字符的 java\\tscript: 变体也应被拦截。"""
        html = '<a href="java\tscript:alert(1)">x</a>'
        md = grab.html_to_markdown(html, base_url="https://x.com/", url_to_local={})
        # 去除控制字符后不应含 javascript
        clean = md.replace("\t", "").replace("\n", "").lower()
        self.assertNotIn("javascript", clean)

    def test_p2_035_data_text_html_blocked(self):
        """data:text/html 在 href 中可执行脚本，应被拦截。"""
        html = '<a href="data:text/html,<script>x</script>">y</a>'
        md = grab.html_to_markdown(html, base_url="https://x.com/", url_to_local={})
        self.assertNotIn("data:text/html", md.lower())

    def test_p2_035_table_link_filtered(self):
        """表格内的不安全协议链接也应被过滤。"""
        html = '<table><tr><td><a href="javascript:alert(1)">bad</a></td></tr></table>'
        md = grab.html_to_markdown(html, base_url="https://x.com/", url_to_local={})
        self.assertNotIn("javascript", md.lower())

    def test_p2_035_normal_link_preserved(self):
        """正常 https 链接不应受协议过滤影响。"""
        html = '<a href="https://example.com/page">正常</a>'
        md = grab.html_to_markdown(html, base_url="https://x.com/", url_to_local={})
        self.assertIn("https://example.com/page", md)

    # ── BUG-036: sniff_ext SVG 启发式误判 HTML ─────────────────────────
    def test_p2_036_html_with_svg_not_misdetected(self):
        """开头含 <svg 的 HTML 错误页不应被误判为 .svg。"""
        from webpage_to_md.images import sniff_ext
        data = b'<svg onload="x"></svg>\n<html><body>error</body></html>'
        self.assertNotEqual(sniff_ext(data), ".svg")

    def test_p2_036_real_svg_detected(self):
        """真正的 SVG 文件应被识别为 .svg。"""
        from webpage_to_md.images import sniff_ext
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        self.assertEqual(sniff_ext(svg), ".svg")

    def test_p2_036_xml_svg_detected(self):
        """XML 声明 + SVG 应被识别。"""
        from webpage_to_md.images import sniff_ext
        xml_svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        self.assertEqual(sniff_ext(xml_svg), ".svg")


if __name__ == "__main__":
    unittest.main()
