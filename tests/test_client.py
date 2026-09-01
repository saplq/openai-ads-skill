import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from scripts.openai_ads_lib.client import AdsClient
from scripts.openai_ads_lib.errors import AdsManagerError


def response(status, payload, headers=None):
    return status, headers or {}, json.dumps(payload).encode()


class ClientTests(unittest.TestCase):
    def test_exhausts_pagination(self):
        calls = []

        def transport(request, timeout):
            calls.append(request.full_url)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            if "after" not in query:
                return response(200, {"data": [{"id": "1"}], "last_id": "1", "has_more": True})
            return response(200, {"data": [{"id": "2"}], "last_id": "2", "has_more": False})

        result = AdsClient("secret", transport=transport).request("GET", "/campaigns", all_pages=True)
        self.assertEqual([item["id"] for item in result.data["data"]], ["1", "2"])
        self.assertEqual(result.pages, 2)
        self.assertEqual(len(calls), 2)

    def test_429_honors_retry_after_for_read(self):
        calls = []
        sleeps = []

        def transport(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                return response(429, {"error": {"message": "slow"}}, {"Retry-After": "2"})
            return response(200, {"data": []})

        result = AdsClient("secret", transport=transport, sleep=sleeps.append).request("GET", "/ads")
        self.assertEqual(result.status, 200)
        self.assertEqual(sleeps, [2.0])

    def test_non_idempotent_write_is_not_retried(self):
        calls = []

        def transport(request, timeout):
            calls.append(request)
            return response(503, {"error": "unavailable"})

        with self.assertRaises(AdsManagerError):
            AdsClient("secret", transport=transport).request("POST", "/campaigns", body={"name": "x"})
        self.assertEqual(len(calls), 1)

    def test_timeout_retries_only_safe_read(self):
        calls = []

        def transport(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise TimeoutError("private transport details")
            return response(200, {"data": []})

        result = AdsClient("secret", transport=transport, sleep=lambda _: None).request("GET", "/ads")
        self.assertEqual(result.status, 200)
        self.assertEqual(len(calls), 2)

    def test_idempotent_write_retries(self):
        calls = []

        def transport(request, timeout):
            calls.append(request)
            return response(503 if len(calls) == 1 else 200, {"data": {"id": "cmpn_1"}})

        result = AdsClient("secret", transport=transport, sleep=lambda _: None).request(
            "POST", "/campaigns", body={"name": "x"}, idempotency_key="idem_1"
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(len(calls), 2)

    def test_secret_response_redacted_by_default(self):
        client = AdsClient("secret", transport=lambda *_: response(200, {"api_key": "capi-secret-value"}))
        self.assertEqual(client.request("GET", "/ad_account").data["api_key"], "[REDACTED]")

    def test_binary_upload_is_streamed_as_multipart(self):
        observed = {}

        def transport(request, timeout):
            observed["content_type"] = request.headers["Content-type"]
            observed["content_length"] = int(request.headers["Content-length"])
            observed["body"] = b"".join(request.data)
            return response(200, {"file_id": "file_1"})

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "image.png"
            path.write_bytes(b"PNG-DATA")
            result = AdsClient("secret", transport=transport).upload_file("/upload", path, purpose="account_favicon")
        self.assertEqual(result.data["file_id"], "file_1")
        self.assertIn("multipart/form-data", observed["content_type"])
        self.assertEqual(observed["content_length"], len(observed["body"]))
        self.assertIn(b"PNG-DATA", observed["body"])


if __name__ == "__main__":
    unittest.main()
