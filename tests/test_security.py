import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.openai_ads_lib.errors import AuthError
from scripts.openai_ads_lib.security import (
    CONFIG_ENV,
    atomic_write,
    audit_event,
    confirmation_hash,
    consume_confirmation_plan,
    ensure_secure_dir,
    get_confirmation_plan,
    load_credentials,
    redact,
    save_confirmation_plan,
    save_credentials,
)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / "config"
        self.patch = mock.patch.dict(os.environ, {CONFIG_ENV: str(self.config)})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_atomic_permissions_and_profile_isolation(self):
        save_credentials({"profiles": {"a": {"api_key": "ads-secret-a"}, "b": {"api_key": "ads-secret-b"}}})
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o700)
        target = self.config / "credentials.json"
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        data = load_credentials()
        self.assertEqual(data["profiles"]["a"]["api_key"], "ads-secret-a")
        self.assertNotEqual(data["profiles"]["a"], data["profiles"]["b"])
        self.assertFalse(any(path.name.startswith(".credentials.json.") for path in self.config.iterdir()))

    def test_refuses_wide_permissions(self):
        ensure_secure_dir()
        os.chmod(self.config, 0o755)
        with self.assertRaises(AuthError):
            load_credentials()

    def test_refuses_symlinked_file(self):
        ensure_secure_dir()
        real = Path(self.temp.name) / "real.json"
        real.write_text("{}", encoding="utf-8")
        (self.config / "credentials.json").symlink_to(real)
        with self.assertRaises(AuthError):
            load_credentials()

    def test_redaction_and_audit_exclude_secrets_and_pii(self):
        value = {"Authorization": "Bearer ads-supersecret", "email": "a@example.com", "nested": {"title": "safe"}}
        self.assertEqual(redact(value)["Authorization"], "[REDACTED]")
        message = redact("Failed for user@example.com at 203.0.113.1 and +1 (415) 555-2671")
        self.assertNotIn("user@example.com", message)
        self.assertNotIn("203.0.113.1", message)
        self.assertNotIn("555-2671", message)
        self.assertEqual(redact("2026-08-31"), "2026-08-31")
        audit_event({
            "operation": "test", "method": "POST", "path": "/ads", "body_hash": "hash",
            "diff": value, "idempotency_key": "idem-private", "request_id": "req_1",
        })
        raw = (self.config / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("supersecret", raw)
        self.assertNotIn("a@example.com", raw)
        self.assertNotIn("idem-private", raw)
        self.assertIn("req_1", raw)

    def test_archive_confirmation_binds_resource(self):
        token = confirmation_hash("POST", "/campaigns/cmpn_123/archive", {}, "acct_1")
        self.assertTrue(token.endswith(":cmpn_123"))

    def test_confirmation_binds_query_idempotency_and_nonce(self):
        common = {
            "method": "POST",
            "path": "/campaigns",
            "body": {"name": "Campaign"},
            "account_id": "acct_1",
            "before_hash": "before",
            "plan_nonce": "nonce-1",
        }
        first = confirmation_hash(query={"mode": "one"}, idempotency_key="idem-1", **common)
        changed_query = confirmation_hash(query={"mode": "two"}, idempotency_key="idem-1", **common)
        changed_key = confirmation_hash(query={"mode": "one"}, idempotency_key="idem-2", **common)
        changed_nonce = confirmation_hash(
            query={"mode": "one"}, idempotency_key="idem-1", **{**common, "plan_nonce": "nonce-2"}
        )
        self.assertEqual(len({first, changed_query, changed_key, changed_nonce}), 4)

    def test_confirmation_plan_is_permission_restricted_and_single_use(self):
        record = save_confirmation_plan("token", {
            "request_hash": "request-hash",
            "idempotency_key": "local-idempotency-key",
            "plan_nonce": "nonce",
        })
        self.assertGreater(record["expires_at"], record["created_at"])
        self.assertEqual(stat.S_IMODE((self.config / "pending-plans.json").stat().st_mode), 0o600)
        self.assertIsNotNone(get_confirmation_plan("token"))
        self.assertTrue(consume_confirmation_plan("token", "request-hash"))
        self.assertIsNone(get_confirmation_plan("token"))
        self.assertFalse(consume_confirmation_plan("token", "request-hash"))


if __name__ == "__main__":
    unittest.main()
