import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

from scripts.openai_ads_lib.cli import _campaign_pacing
from scripts.openai_ads_lib.client import ApiResponse
from scripts.openai_ads_lib.reporting import completed_window, summarize


class ReportingTests(unittest.TestCase):
    def test_account_local_current_day_is_excluded(self):
        now = datetime(2026, 9, 1, 1, 30, tzinfo=timezone.utc)
        window = completed_window(7, "America/Los_Angeles", now)
        self.assertEqual(window.end.isoformat(), "2026-08-30")
        self.assertEqual(window.start.isoformat(), "2026-08-24")
        self.assertEqual(window.previous_start.isoformat(), "2026-08-17")

    def test_exact_formulas_and_view_separation(self):
        metrics = summarize(
            [{"impressions": 1000, "clicks": 50, "spend": 125}, {"impressions": None}],
            [{"conversions": 5, "view_through_conversions": 3}],
        )
        self.assertEqual(metrics["ctr"], 0.05)
        self.assertEqual(metrics["cpc"], 2.5)
        self.assertEqual(metrics["cpm"], 125.0)
        self.assertEqual(metrics["cvr"], 0.1)
        self.assertEqual(metrics["cpa"], 25.0)
        self.assertEqual(metrics["view_through_conversions"], 3.0)

    def test_zero_denominators_return_null(self):
        metrics = summarize([], [])
        self.assertIsNone(metrics["ctr"])
        self.assertIsNone(metrics["cpc"])
        self.assertIsNone(metrics["cpm"])
        self.assertIsNone(metrics["cvr"])
        self.assertIsNone(metrics["cpa"])

    def test_unavailable_conversion_metrics_are_not_fake_zeroes(self):
        metrics = summarize([{"impressions": 10, "clicks": 2, "spend": 4}], [], conversions_available=False)
        self.assertIsNone(metrics["click_through_conversions"])
        self.assertIsNone(metrics["view_through_conversions"])
        self.assertIsNone(metrics["cvr"])
        self.assertIsNone(metrics["cpa"])

    def test_campaign_even_pacing_is_evidence_not_threshold(self):
        class Client:
            def request(self, *args, **kwargs):
                return ApiResponse(200, {"data": [{"campaign_id": "cmpn_1", "spend": 50, "impressions": 100}]}, {}, "req", pages=1)

        window = SimpleNamespace(start=date(2026, 9, 1), end=date(2026, 9, 7))
        campaign = {
            "id": "cmpn_1", "name": "Test", "status": "active",
            "start_time": int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()),
            "end_time": int(datetime(2026, 9, 11, tzinfo=timezone.utc).timestamp()),
            "budget": {"lifetime_spend_limit_micros": 100_000_000},
        }
        result = _campaign_pacing(Client(), [campaign], window, "UTC")
        self.assertEqual(result["rows"][0]["expected_even_pace_spend"], 70.0)
        self.assertAlmostEqual(result["rows"][0]["actual_to_even_pace_ratio"], 50 / 70)
        self.assertIn("not automatic", result["interpretation"])


if __name__ == "__main__":
    unittest.main()
