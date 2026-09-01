import unittest

from scripts.openai_ads_lib.conversions import dedupe_key, hash_identifier, validate_batch
from scripts.openai_ads_lib.errors import PolicyError, ValidationError


NOW = 1_800_000_000_000


def event():
    return {
        "id": "order_123",
        "type": "order_created",
        "timestamp_ms": NOW,
        "source_url": "https://Shop.Example/path?email=user@example.com#receipt",
        "action_source": "web",
        "user": {"emails_sha256": ["a" * 64]},
        "data": {"type": "contents", "amount": 1000, "currency": "USD"},
    }


class ConversionTests(unittest.TestCase):
    def test_normalization_and_hashing(self):
        self.assertEqual(hash_identifier("email", " User@Example.COM "), hash_identifier("email", "user@example.com"))
        self.assertEqual(hash_identifier("phone", "+1 (415) 555-2671"), hash_identifier("phone", "14155552671"))
        self.assertEqual(hash_identifier("first_name", "Mary Jane"), hash_identifier("first_name", "maryjane"))

    def test_consent_and_url_sanitization(self):
        with self.assertRaises(PolicyError):
            validate_batch({"events": [event()]}, now_ms=NOW)
        result = validate_batch({"events": [event()]}, now_ms=NOW, consent_confirmed=True)
        self.assertEqual(result["events"][0]["source_url"], "https://shop.example/path")

    def test_batch_timestamp_and_raw_pii_validation(self):
        too_old = event()
        too_old["timestamp_ms"] = NOW - 8 * 24 * 60 * 60 * 1000
        with self.assertRaises(ValidationError):
            validate_batch({"events": [too_old]}, now_ms=NOW, consent_confirmed=True)
        raw = event()
        raw["user"] = {"email": "private@example.com"}
        with self.assertRaises(PolicyError):
            validate_batch({"events": [raw]}, now_ms=NOW, consent_confirmed=True)

    def test_dedupe_key_uses_pixel_event_name_and_id(self):
        self.assertEqual(dedupe_key("pixel_1", event()), ("pixel_1", "order_created", "order_123"))

    def test_batch_limit(self):
        with self.assertRaises(ValidationError):
            validate_batch({"events": [event()] * 1001}, now_ms=NOW, consent_confirmed=True)

    def test_event_data_shape_and_minor_units(self):
        wrong_shape = event()
        wrong_shape["data"] = {"type": "customer_action"}
        with self.assertRaises(ValidationError):
            validate_batch({"events": [wrong_shape]}, now_ms=NOW, consent_confirmed=True)
        decimal_amount = event()
        decimal_amount["data"]["amount"] = 10.5
        with self.assertRaises(ValidationError):
            validate_batch({"events": [decimal_amount]}, now_ms=NOW, consent_confirmed=True)


if __name__ == "__main__":
    unittest.main()
