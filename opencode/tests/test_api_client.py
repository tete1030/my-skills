import io
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from opencode_api_client import OpenCodeApiError, OpenCodeClient, build_opencode_session_ui_url, encode_workspace_for_ui  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class OpenCodeApiClientTests(unittest.TestCase):
    def test_http_error_preserves_status_headers_and_body_preview(self):
        client = OpenCodeClient(base_url="http://127.0.0.1:4096", timeout=5)
        http_error = HTTPError(
            url="http://127.0.0.1:4096/session/status",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "30", "X-Request-Id": "req_demo"},
            fp=io.BytesIO(b'{"error":"rate_limited","detail":"quota exceeded"}'),
        )
        self.addCleanup(http_error.close)

        with mock.patch("opencode_api_client.urlopen", side_effect=http_error):
            with self.assertRaises(OpenCodeApiError) as ctx:
                client.session_status(workspace="/tmp/demo-workspace")

        data = ctx.exception.to_dict()
        self.assertEqual(data["status"], 429)
        self.assertEqual(data["retryAfter"], "30")
        self.assertEqual(data["requestId"], "req_demo")
        self.assertIn("quota exceeded", data["bodyPreview"])
        self.assertIn("HTTP 429", data["message"])

    def test_encode_workspace_for_ui_uses_base64url_without_padding(self):
        encoded = encode_workspace_for_ui("/mnt/vault/teslausb-video-sum")
        self.assertEqual(encoded, "L21udC92YXVsdC90ZXNsYXVzYi12aWRlby1zdW0")
        self.assertNotIn("=", encoded)

    def test_build_opencode_session_ui_url_uses_workspace_prefix_route(self):
        url = build_opencode_session_ui_url(
            "http://100.126.131.48:4096",
            "/mnt/vault/teslausb-video-sum",
            "ses_demo",
        )
        self.assertEqual(url, "http://100.126.131.48:4096/L21udC92YXVsdC90ZXNsYXVzYi12aWRlby1zdW0/session/ses_demo")

    def test_abort_session_posts_to_verified_abort_endpoint_with_directory(self):
        client = OpenCodeClient(base_url="http://127.0.0.1:4096", timeout=5)
        captured = {}

        def fake_urlopen(req, timeout):
            captured["method"] = req.get_method()
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            return _FakeResponse(b"true")

        with mock.patch("opencode_api_client.urlopen", side_effect=fake_urlopen):
            result = client.abort_session("ses_demo", directory="/tmp/demo-workspace")

        self.assertTrue(result)
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["timeout"], 5)
        self.assertEqual(captured["url"], "http://127.0.0.1:4096/session/ses_demo/abort?directory=%2Ftmp%2Fdemo-workspace")


if __name__ == "__main__":
    unittest.main()
