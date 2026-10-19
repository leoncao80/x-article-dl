"""Tests for proxy resolution and Netscape cookie parsing (pitfalls A2/A4/A8/A9)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from x_article_dl import auth  # noqa: E402

CT0 = "c" * 160


class ProxyTests(unittest.TestCase):
    ENV_KEYS = ("XADL_PROXY", "HTTPS_PROXY", "https_proxy")

    def setUp(self):
        clean = {k: v for k, v in os.environ.items() if k not in self.ENV_KEYS}
        self.env = mock.patch.dict(os.environ, clean, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_default_is_direct(self):
        self.assertIsNone(auth.resolve_proxy())

    def test_cli_flag_wins(self):
        os.environ["XADL_PROXY"] = "http://env:1111"
        self.assertEqual(auth.resolve_proxy("http://cli:2222"), "http://cli:2222")

    def test_xadl_proxy_env(self):
        os.environ["XADL_PROXY"] = "http://env:1111"
        self.assertEqual(auth.resolve_proxy(), "http://env:1111")

    def test_standard_https_proxy_env(self):
        os.environ["HTTPS_PROXY"] = "http://std:3333"
        self.assertEqual(auth.resolve_proxy(), "http://std:3333")

    def test_no_proxy_forces_direct(self):
        os.environ["XADL_PROXY"] = "http://env:1111"
        self.assertIsNone(auth.resolve_proxy(no_proxy=True))

    def test_default_proxy_constant_is_none(self):
        self.assertIsNone(auth.DEFAULT_PROXY)


class CookieTests(unittest.TestCase):
    def _write(self, content: str) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return Path(tmp.name)

    def test_httponly_auth_token_survives_and_foreign_domains_dropped(self):
        # netflix.com contains the substring "x.com"; the old filter accepted it (regression)
        path = self._write(
            "# Netscape HTTP Cookie File\n"
            "#HttpOnly_.x.com\tTRUE\t/\tTRUE\t0\tauth_token\ttok123\n"
            ".x.com\tTRUE\t/\tTRUE\t0\tct0\t%s\n"
            ".netflix.com\tTRUE\t/\tFALSE\t0\tNetflixId\tnf\n"
            "evilx.com\tTRUE\t/\tFALSE\t0\tbad\t1\n" % CT0
        )
        cookies = auth.load_cookies(path)
        self.assertEqual(cookies, {"auth_token": "tok123", "ct0": CT0})

    def test_expired_cookie_is_dropped(self):
        path = self._write(
            "#HttpOnly_.x.com\tTRUE\t/\tTRUE\t1\tauth_token\texpired\n"
            ".x.com\tTRUE\t/\tTRUE\t0\tct0\t%s\n" % CT0
        )
        with self.assertRaises(auth.ConfigError):
            auth.load_cookies(path)

    def test_missing_ct0_raises_config_error(self):
        path = self._write(
            "#HttpOnly_.x.com\tTRUE\t/\tTRUE\t0\tauth_token\ttok\n"
        )
        with self.assertRaises(auth.ConfigError):
            auth.load_cookies(path)

    def test_env_path_is_not_silently_ignored(self):
        with mock.patch.dict(
            os.environ, {"XADL_COOKIES": "/tmp/xadl-not-here.txt"}, clear=False
        ):
            self.assertEqual(
                auth.default_cookie_path(), Path("/tmp/xadl-not-here.txt")
            )
            with self.assertRaises(auth.ConfigError):
                auth.load_cookies()


if __name__ == "__main__":
    unittest.main()
