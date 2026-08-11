import unittest
from unittest.mock import Mock, call, patch

from dashboard.metrics import (
    BACKPACK_ENTRY_POINT,
    OCR_SIZE,
    PROFILE_ENTRY_POINT,
    _is_backpack,
    _is_huishi_settlement,
    _is_main,
    _ensure_main,
    _is_social_messages,
    metric_capture_status,
    parse_backpack_resources,
)
from tools.run_resource_snapshot_macos import build_snapshot


class ResourceSnapshotCaptureTests(unittest.TestCase):
    def test_resource_capture_reuses_cross_region_main_screen_evidence(self):
        results = [
            {"text": "任务", "box": [780, 80, 45, 25]},
            {"text": "队伍", "box": [930, 95, 45, 25]},
            {"text": "福利", "box": [10, 135, 45, 25]},
            {"text": "当前", "box": [8, 640, 45, 25]},
        ]

        self.assertTrue(_is_main(results))

    def test_resource_capture_rejects_sparse_toolbar_text(self):
        self.assertFalse(_is_main([
            {"text": "任务", "box": [780, 80, 45, 25]},
            {"text": "活动", "box": [260, 45, 50, 22]},
        ]))

    @patch("dashboard.metrics.time.sleep")
    @patch("dashboard.metrics._wait_for")
    def test_resource_capture_waits_without_input_for_main_screen(self, wait_for, sleep):
        battle = [{"text": "取消", "box": [961, 680, 44, 25]}]
        main = [
            {"text": "任务", "box": [780, 80, 45, 25]},
            {"text": "队伍", "box": [930, 95, 45, 25]},
            {"text": "福利", "box": [10, 135, 45, 25]},
            {"text": "当前", "box": [8, 640, 45, 25]},
        ]
        wait_for.side_effect = [(None, battle), (None, main), (None, main)]
        native_input = Mock()

        self.assertIs(_ensure_main(object(), native_input, 1), main)
        native_input.click.assert_not_called()
        self.assertEqual(sleep.call_args_list, [call(1), call(0.6)])

    @patch("dashboard.metrics.time.sleep")
    @patch("dashboard.metrics._wait_for")
    def test_resource_capture_stops_after_bounded_unknown_frames(self, wait_for, _sleep):
        wait_for.return_value = (None, [{"text": "取消", "box": [961, 680, 44, 25]}])

        with self.assertRaisesRegex(RuntimeError, "等待安全主界面超时"):
            _ensure_main(object(), Mock(), 1)
        self.assertEqual(wait_for.call_count, 12)

    def test_fixed_resource_icons_are_normalized_to_the_game_canvas(self):
        self.assertEqual(BACKPACK_ENTRY_POINT, (985, 630))
        self.assertEqual(PROFILE_ENTRY_POINT, (978, 55))
        self.assertEqual(OCR_SIZE, (1020, 718))

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

    def test_live_bag_ocr_drift_still_requires_cross_region_identity(self):
        results = [
            {"text": "包裹", "box": [464, 106, 211, 31]},
            {"text": "物评分15670", "box": [234, 532, 140, 22]},
            {"text": "661.363", "box": [175, 571, 68, 19]},
            {"text": "6.091.849", "box": [353, 571, 87, 19]},
            {"text": "物品", "box": [563, 166, 59, 28]},
            {"text": "任 务", "box": [753, 166, 60, 28]},
            {"text": "整理", "box": [756, 560, 71, 32]},
            {"text": "礼包", "box": [547, 559, 71, 31]},
            {"text": "仓库", "box": [899, 307, 29, 46]},
        ]

        self.assertTrue(_is_backpack(results))
        self.assertFalse(_is_backpack([
            {"text": "包裹", "box": [464, 106, 211, 31]},
            {"text": "661.363", "box": [175, 571, 68, 19]},
            {"text": "6.091.849", "box": [353, 571, 87, 19]},
        ]))

    def test_huishi_settlement_requires_completion_and_current_reward_evidence(self):
        settlement = [
            {"text": "科举会试", "box": [451, 137, 120, 33]},
            {"text": "恭喜通关", "box": [522, 223, 245, 55]},
            {"text": "本次会获得奖金", "box": [372, 315, 152, 24]},
        ]

        self.assertTrue(_is_huishi_settlement(settlement))
        self.assertFalse(_is_huishi_settlement(settlement[:-1]))

    def test_social_messages_requires_title_navigation_and_message_content(self):
        social_messages = [
            {"text": "我的好友", "box": [449, 94, 98, 28]},
            {"text": "最近", "box": [130, 152, 51, 29]},
            {"text": "群组", "box": [212, 151, 53, 33]},
            {"text": "联系人", "box": [286, 150, 75, 35]},
            {"text": "邮件", "box": [382, 152, 51, 30]},
            {"text": "系统消息", "box": [180, 210, 79, 26]},
        ]

        self.assertTrue(_is_social_messages(social_messages))
        self.assertFalse(_is_social_messages(social_messages[:2]))

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

    def test_silver_only_capture_is_complete_for_its_declared_scope(self):
        captured = {
            "gold": None,
            "silver": 20000,
            "level": None,
            "experience": None,
            "experienceRequired": None,
            "error": None,
            "resourceEvidence": {
                "silver": {
                    "status": "captured", "sourcePage": "backpack",
                    "rawText": "20.000", "confidence": None,
                    "box": [350, 571, 88, 19], "frameId": "frame-1",
                },
            },
        }
        with patch(
            "tools.run_resource_snapshot_macos.capture_visible_metrics",
            return_value=captured,
        ) as capture:
            snapshot = build_snapshot("before", captured_fields=frozenset({"silver"}))

        capture.assert_called_once_with(captured_fields=frozenset({"silver"}))
        self.assertEqual(snapshot.status, "success")
        self.assertEqual(snapshot.captured_fields, frozenset({"silver"}))
        self.assertEqual(snapshot.values.silver, 20000)

    def test_complete_metric_capture_status_is_success(self):
        self.assertEqual(metric_capture_status([]), "success")
        self.assertEqual(metric_capture_status(["gold"]), "partial")


if __name__ == "__main__":
    unittest.main()
