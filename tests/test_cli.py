import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.openai_ads_lib.cli import command_api, command_capi, command_doctor
from scripts.openai_ads_lib.client import ApiResponse
from scripts.openai_ads_lib.errors import AdsManagerError


class FakeClient:
    def __init__(self):
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == "POST":
            return ApiResponse(200, {"id": "cmpn_1"}, {"x-request-id": "req_write"}, "req_write")
        return ApiResponse(200, {"id": "cmpn_1", "status": "paused"}, {}, "req_read")


def api_args(body_file, **changes):
    values = {
        "profile": "main", "method": "POST", "path": "/campaigns", "surface": "documented",
        "query_file": None, "body_file": body_file, "upload_file": None, "purpose": None,
        "all_pages": False, "idempotency_key": "idem_1", "apply": False, "confirm": None,
        "policy_reviewed": False, "first_party_confirmed": False, "non_eea_confirmed": False,
    }
    values.update(changes)
    return argparse.Namespace(**values)


class CliTests(unittest.TestCase):
    def test_mutation_confirmation_and_readback(self):
        fake = FakeClient()
        with tempfile.TemporaryDirectory() as temp:
            body = Path(temp) / "body.json"
            body.write_text('{"name":"Campaign"}', encoding="utf-8")
            with mock.patch("scripts.openai_ads_lib.cli._profile_client", return_value=({"account_id": "acct_1"}, fake)):
                dry = command_api(api_args(str(body)))
                token = dry["data"]["plan"]["confirmation_hash"]
                with self.assertRaises(AdsManagerError):
                    command_api(api_args(str(body), apply=True, confirm="stale"))
                with mock.patch("scripts.openai_ads_lib.cli.audit_preflight"), mock.patch("scripts.openai_ads_lib.cli.audit_event"):
                    applied = command_api(api_args(str(body), apply=True, confirm=token))
        self.assertTrue(applied["data"]["applied"])
        self.assertEqual(applied["data"]["readback"]["status"], "paused")
        self.assertEqual([call[0] for call in fake.calls], ["POST", "GET"])

    def test_update_confirmation_binds_before_read(self):
        class StatefulClient:
            def __init__(self):
                self.budget = 10
                self.write_calls = 0

            def request(self, method, path, **kwargs):
                if method == "GET":
                    return ApiResponse(200, {"id": "cmpn_1", "daily_budget": self.budget}, {}, "req_read")
                self.write_calls += 1
                return ApiResponse(200, {"id": "cmpn_1"}, {}, "req_write")

        client = StatefulClient()
        with tempfile.TemporaryDirectory() as temp:
            body = Path(temp) / "body.json"
            body.write_text('{"daily_budget":20}', encoding="utf-8")
            args = api_args(str(body), method="PATCH", path="/campaigns/cmpn_1")
            with mock.patch("scripts.openai_ads_lib.cli._profile_client", return_value=({"account_id": "acct_1"}, client)):
                dry = command_api(args)
                self.assertEqual(dry["data"]["plan"]["diff"]["before"]["daily_budget"], 10)
                client.budget = 11
                args.apply = True
                args.confirm = dry["data"]["plan"]["confirmation_hash"]
                with self.assertRaises(AdsManagerError):
                    command_api(args)
        self.assertEqual(client.write_calls, 0)

    def test_update_fails_closed_when_before_read_fails(self):
        class BrokenReadClient:
            def request(self, method, path, **kwargs):
                raise AdsManagerError("unavailable")

        with tempfile.TemporaryDirectory() as temp:
            body = Path(temp) / "body.json"
            body.write_text('{"daily_budget":20}', encoding="utf-8")
            args = api_args(str(body), method="PATCH", path="/campaigns/cmpn_1")
            with mock.patch("scripts.openai_ads_lib.cli._profile_client", return_value=({"account_id": "acct_1"}, BrokenReadClient())):
                with self.assertRaises(AdsManagerError) as caught:
                    command_api(args)
        self.assertIn("no confirmation hash", caught.exception.message)

    def test_audience_diff_does_not_expose_identifiers(self):
        fake = FakeClient()
        with tempfile.TemporaryDirectory() as temp:
            body = Path(temp) / "audience.json"
            body.write_text('{"identifiers":["opaque-person-123"],"revision":2}', encoding="utf-8")
            args = api_args(
                str(body), path="/custom_audiences/aud_1/add",
                first_party_confirmed=True, non_eea_confirmed=True,
            )
            with mock.patch("scripts.openai_ads_lib.cli._profile_client", return_value=({"account_id": "acct_1"}, fake)):
                result = command_api(args)
        rendered = json.dumps(result)
        self.assertNotIn("opaque-person-123", rendered)
        self.assertTrue(result["data"]["plan"]["diff"]["after"]["audience_data_redacted"])

    def test_policy_error_keeps_account_context(self):
        fake = FakeClient()
        with tempfile.TemporaryDirectory() as temp:
            body = Path(temp) / "ad.json"
            body.write_text('{"status":"active","creative":{"title":"Valid title","body":"Valid body"}}', encoding="utf-8")
            args = api_args(str(body), path="/ads", policy_reviewed=True)
            with mock.patch("scripts.openai_ads_lib.cli._profile_client", return_value=({"account_id": "acct_1"}, fake)):
                with self.assertRaises(AdsManagerError) as caught:
                    command_api(args)
        self.assertEqual(caught.exception.account_id, "acct_1")
        self.assertIsInstance(caught.exception.warnings, list)

    def test_capi_secret_never_enters_output(self):
        class SecretClient:
            def request(self, *args, **kwargs):
                return ApiResponse(200, {"name": "prod", "api_key": "capi-super-private"}, {}, "req_secret")

        base = argparse.Namespace(
            profile="main", capi_command="key", key_command="create", name="production",
            idempotency_key="idem", apply=False, confirm=None,
        )
        with mock.patch("scripts.openai_ads_lib.cli._profile_client", return_value=({"account_id": "acct_1"}, SecretClient())):
            dry = command_capi(base)
            base.apply = True
            base.confirm = dry["data"]["plan"]["confirmation_hash"]
            with mock.patch("scripts.openai_ads_lib.cli._load_capi_secrets", return_value={"version": 1, "profiles": {}}), \
                 mock.patch("scripts.openai_ads_lib.cli.atomic_write", return_value=Path("/secure/capi-secrets.json")) as writer, \
                 mock.patch("scripts.openai_ads_lib.cli.audit_preflight"), \
                 mock.patch("scripts.openai_ads_lib.cli.audit_event"):
                result = command_capi(base)
        rendered = json.dumps(result)
        self.assertNotIn("capi-super-private", rendered)
        self.assertIn("key_fingerprint", rendered)
        saved = writer.call_args.args[1]
        self.assertEqual(saved["profiles"]["main"]["production"]["api_key"], "capi-super-private")

    def test_doctor_detects_openapi_docs_and_policy_drift(self):
        args = argparse.Namespace(offline=False, check_updates=True)
        with mock.patch("scripts.openai_ads_lib.cli.load_credentials", return_value={"profiles": {}}), \
             mock.patch("scripts.openai_ads_lib.cli._fetch_json", return_value={"info": {"version": "2.4.0"}, "paths": {"/new_surface": {}}}), \
             mock.patch("scripts.openai_ads_lib.cli._fetch_text", side_effect=["no pinned changelog", "Policy v1.6"]):
            result = command_doctor(args)
        self.assertTrue(result["data"]["drift"]["version_changed"])
        self.assertTrue(result["data"]["drift"]["operation_surface_changed"])
        self.assertIn("/new_surface", result["data"]["drift"]["unclassified_paths"])
        self.assertTrue(result["warnings"])


if __name__ == "__main__":
    unittest.main()
