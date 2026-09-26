"""http.open_url 必须给 urllib 传 certifi SSL 上下文。

回归背景: macOS python.org Python 未跑 "Install Certificates.command" 时,
标准库 ssl 系统证书库为空, urllib 请求 MOEX 必报 CERTIFICATE_VERIFY_FAILED,
而 requests(自带 certifi)正常 —— 表现为"官价能抓、盘价全挂"。
"""
import ssl
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import http


class SslContextTest(unittest.TestCase):
    def test_uses_certifi_bundle_when_available(self):
        import certifi
        with mock.patch.object(http.ssl, "create_default_context",
                               wraps=ssl.create_default_context) as m:
            http._ssl_context()
        m.assert_called_once_with(cafile=certifi.where())

    def test_falls_back_to_default_context_without_certifi(self):
        real_create = ssl.create_default_context
        with mock.patch.dict(sys.modules, {"certifi": None}), \
                mock.patch.object(ssl, "create_default_context",
                                  side_effect=real_create) as m:
            ctx = http._ssl_context()
        m.assert_called_once_with()
        self.assertIsInstance(ctx, ssl.SSLContext)

    def test_open_url_passes_ssl_context_to_urlopen(self):
        sentinel_ctx = ssl.create_default_context()
        with mock.patch.object(http, "_ssl_context", return_value=sentinel_ctx), \
                mock.patch.object(http.urllib.request, "urlopen") as uo:
            http.open_url("https://example.com", timeout=5)
        _, kwargs = uo.call_args
        self.assertIs(kwargs["context"], sentinel_ctx)


if __name__ == "__main__":
    unittest.main()
