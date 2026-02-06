import importlib.util
import io
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


if __name__ == "__main__":
    unittest.main()
