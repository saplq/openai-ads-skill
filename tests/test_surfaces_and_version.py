import hashlib
import json
import unittest
from pathlib import Path

from scripts.openai_ads_lib import VERSION
from scripts.openai_ads_lib.errors import PolicyError, ValidationError
from scripts.openai_ads_lib.client import capability_enabled
from scripts.openai_ads_lib.surfaces import authorize_surface, validate_mutation


ROOT = Path(__file__).resolve().parents[1]


class SurfaceAndVersionTests(unittest.TestCase):
    def test_versions_are_synchronized_but_distinct(self):
        manifest = json.loads((ROOT / "references" / "compatibility.json").read_text())
        plugin = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())
        marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text())
        skill = (ROOT / "SKILL.md").read_text()
        packaged_skill = (ROOT / "skills" / "openai-ads-manager" / "SKILL.md").read_text()
        self.assertEqual(VERSION, "0.4.1")
        self.assertEqual(manifest["skill_version"], VERSION)
        self.assertEqual(plugin["version"], VERSION)
        self.assertIn('version: "0.4.1"', skill)
        self.assertIn('version: "0.4.1"', packaged_skill)
        self.assertEqual(marketplace["plugins"][0]["name"], plugin["name"])
        self.assertEqual(marketplace["plugins"][0]["source"]["url"], "https://github.com/saplq/openai-ads-skill.git")
        self.assertTrue((ROOT / "assets" / "openai-ads-manager-social-preview.png").is_file())
        self.assertEqual(manifest["ads_api"]["major"], "v1")
        self.assertEqual(manifest["openapi"]["document_version"], "2.3.0")
        self.assertEqual(len(manifest["openapi"]["operation_surface_sha256"]), 64)
        self.assertEqual(manifest["policy"]["version"], "1.5")

    def test_preview_and_oauth_routing(self):
        with self.assertRaises(ValidationError):
            authorize_surface("/bulk_mutation_jobs", "documented", "POST")
        self.assertEqual(authorize_surface("/bulk_mutation_jobs", "bulk_preview", "POST"), "bulk_preview")
        with self.assertRaises(ValidationError):
            authorize_surface("/oauth/token", "spec_preview")
        with self.assertRaises(PolicyError):
            authorize_surface("/conversions/api_keys", "documented")

    def test_new_ad_is_forced_paused(self):
        body = {"name": "safe"}
        warnings = validate_mutation("POST", "/ads", body)
        self.assertEqual(body["status"], "paused")
        self.assertTrue(warnings)
        with self.assertRaises(PolicyError):
            validate_mutation("POST", "/ads", {"status": "active"})

    def test_preview_requires_explicit_live_capability(self):
        self.assertFalse(capability_enabled({"features": []}, "bulk_preview"))
        self.assertTrue(capability_enabled({"features": {"bulk_preview": True}}, "bulk_preview"))

    def test_feed_delta_is_documented_but_feed_admin_is_preview(self):
        self.assertEqual(authorize_surface("/feeds/feed_1/products", "documented", "PATCH"), "documented")
        with self.assertRaises(ValidationError):
            authorize_surface("/feeds", "documented", "GET")
        with self.assertRaises(PolicyError):
            authorize_surface("/feeds/feed_1/sftp_access", "spec_preview", "POST")

    def test_allowlist_requires_exact_method_and_path_template(self):
        self.assertEqual(authorize_surface("/campaigns/cmpn_1", "documented", "POST"), "documented")
        with self.assertRaises(ValidationError):
            authorize_surface("/campaigns/cmpn_1", "documented", "DELETE")
        with self.assertRaises(ValidationError):
            authorize_surface("/campaigns/cmpn_1/future-dangerous-action", "documented", "POST")

    def test_pinned_operation_surface_is_complete_and_stable(self):
        surface = json.loads((ROOT / "references" / "api-surface.json").read_text())
        openapi_operations = (
            surface["documented"] + surface["spec_preview"] + surface["oauth_only"] + surface["secret"]
        )
        self.assertEqual(len(openapi_operations), 88)
        self.assertEqual(len(openapi_operations), len(set(openapi_operations)))
        digest = hashlib.sha256(("\n".join(sorted(openapi_operations)) + "\n").encode()).hexdigest()
        self.assertEqual(digest, surface["operation_surface_sha256"])
        self.assertEqual(digest, json.loads((ROOT / "references" / "compatibility.json").read_text())["openapi"]["operation_surface_sha256"])

    def test_update_ad_title_allows_one_character_but_create_requires_three(self):
        validate_mutation("POST", "/ads/ad_1", {"creative": {"title": "A", "body": "Body"}})
        with self.assertRaises(ValidationError):
            validate_mutation("POST", "/ads", {"creative": {"title": "A", "body": "Body"}})

    def test_budget_and_targeting_changes_are_high_risk(self):
        warnings = validate_mutation("PATCH", "/campaigns/cmpn_1", {"daily_budget": 100, "targeting": {"locations": []}})
        self.assertTrue(any("spend" in warning for warning in warnings))



if __name__ == "__main__":
    unittest.main()
