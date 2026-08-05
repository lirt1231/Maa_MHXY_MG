import unittest
from unittest.mock import patch

from dashboard.metrics import parse_backpack_resources
from tools.run_resource_snapshot_macos import build_snapshot


class ResourceSnapshotCaptureTests(unittest.TestCase):
    def test_fixed_bag_currency_slots_distinguish_gold_and_silver(self):
        results = [
            {"text": "795.565", "box": [175, 587, 69, 23]},
            {"text": "15589", "box": [319, 545, 55, 24]},
            {"text": "24.896.692", "box": [347, 586, 97, 23]},
        ]

        parsed = parse_backpack_resources(results)

        self.assertEqual(parsed["gold"], 795565)
        self.assertEqual(parsed["silver"], 24896692)
        self.assertEqual(parsed["goldItem"]["box"], [175, 587, 69, 23])
        self.assertEqual(parsed["silverItem"]["box"], [347, 586, 97, 23])

    def test_capture_failure_becomes_a_failed_snapshot(self):
        with patch(
            "tools.run_resource_snapshot_macos.capture_visible_metrics",
            side_effect=RuntimeError("当前不是主界面"),
        ):
            snapshot = build_snapshot("before")

        self.assertEqual(snapshot.status, "failed")
        self.assertEqual(snapshot.phase, "before")
        self.assertIn("当前不是主界面", snapshot.error)

    def test_partial_capture_preserves_available_fields_and_evidence(self):
        captured = {
            "gold": 100,
            "silver": None,
            "level": 69,
            "experience": 200,
            "experienceRequired": 500,
            "error": "未能识别字段：silver",
            "resourceEvidence": {
                "gold": {
                    "status": "captured",
                    "sourcePage": "backpack",
                    "rawText": "100",
                    "confidence": None,
                    "box": [175, 587, 69, 23],
                    "frameId": "frame-1",
                },
                "silver": {
                    "status": "missing",
                    "sourcePage": "backpack",
                    "rawText": None,
                    "confidence": None,
                    "box": None,
                    "frameId": "frame-1",
                },
            },
        }
        with patch(
            "tools.run_resource_snapshot_macos.capture_visible_metrics",
            return_value=captured,
        ):
            snapshot = build_snapshot("after")

        self.assertEqual(snapshot.status, "partial")
        self.assertEqual(snapshot.values.gold, 100)
        self.assertIsNone(snapshot.values.silver)
        self.assertEqual(snapshot.evidence["silver"].status, "missing")


if __name__ == "__main__":
    unittest.main()
