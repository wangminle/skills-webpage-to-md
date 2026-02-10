import importlib.util
import io
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


if __name__ == "__main__":
    unittest.main()
