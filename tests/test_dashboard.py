import tempfile
import unittest
from pathlib import Path

from dashboard.database import Database
from dashboard.metrics import (
    parse_backpack_gold,
    parse_number,
    parse_profile_metrics,
    parse_visible_metrics,
)
from dashboard.registry import TASKS, task_list, validate_options


class TaskRegistryTests(unittest.TestCase):
    def test_only_verified_macos_tasks_are_exposed(self):
        self.assertEqual(set(TASKS), {"sanjieqiyuan", "keju", "zhuogui"})
        self.assertTrue(all(task["checklist"] for task in task_list()))
        self.assertTrue(all(task["steps"] for task in task_list()))

    def test_defaults_and_integer_bounds_are_applied(self):
        self.assertEqual(validate_options("zhuogui", {}), {"rounds": 2})
        with self.assertRaisesRegex(ValueError, "1–100"):
            validate_options("zhuogui", {"rounds": 0})
        with self.assertRaisesRegex(ValueError, "未知参数"):
            validate_options("keju", {"apiKey": "must-not-be-accepted"})

    def test_task_specific_defaults(self):
        self.assertEqual(
            validate_options("sanjieqiyuan", {}),
            {"maxSteps": 10, "navigation": "auto"},
        )
        self.assertEqual(
            validate_options("keju", {}),
            {"maxSteps": 20, "answerSource": "configured", "waitForRoundOne": True},
        )


class MetricParsingTests(unittest.TestCase):
    def test_chinese_punctuation_and_full_width_digits(self):
        self.assertEqual(parse_number("金币 １，２３４，５６７"), 1234567)

    def test_visible_metric_labels_are_parsed(self):
        values = parse_visible_metrics(["人物等级 115级", "当前经验 12,345 / 98,765", "金币", "8,765,432"])
        self.assertEqual(values["level"], 115)
        self.assertEqual(values["experience"], 12345)
        self.assertEqual(values["experienceRequired"], 98765)
        self.assertEqual(values["gold"], 8765432)

    def test_macos_bag_footer_uses_left_currency_as_gold(self):
        results = [
            {"text": "795.565", "box": [175, 587, 69, 23]},
            {"text": "15589", "box": [319, 545, 55, 24]},
            {"text": "24.896.692", "box": [347, 586, 97, 23]},
        ]
        self.assertEqual(parse_backpack_gold(results), 795565)

    def test_macos_profile_reads_level_and_experience_fraction(self):
        results = [
            {"text": "独角兽11269级", "box": [241, 145, 147, 22]},
            {"text": "319248657/43527489", "box": [610, 544, 165, 19]},
            {"text": "3722/3722", "box": [690, 154, 83, 19]},
        ]
        self.assertEqual(
            parse_profile_metrics(results),
            {"level": 69, "experience": 319248657, "experienceRequired": 43527489},
        )


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "dashboard.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_run_summary_and_metric_correction(self):
        run = {
            "id": "run-1", "taskId": "keju", "taskName": "科举乡试", "status": "preparing",
            "options": {}, "startedAt": "2026-07-26T00:00:00+00:00",
        }
        self.database.create_run(run)
        self.database.update_run("run-1", "succeeded")
        self.assertEqual(self.database.summary()["successRate"], 100.0)

        metric = self.database.add_metric({"status": "failed", "error": "OCR failed"})
        corrected = self.database.update_metric(metric["id"], {"gold": 1234, "experience": 88})
        self.assertEqual(corrected["status"], "success")
        self.assertTrue(corrected["corrected"])
        self.assertEqual(corrected["gold"], 1234)

    def test_incomplete_runs_are_recovered_after_restart(self):
        self.database.create_run({
            "id": "abandoned", "taskId": "zhuogui", "taskName": "捉鬼任务",
            "status": "preparing", "options": {"rounds": 2}, "startedAt": "2026-07-26T00:00:00+00:00",
        })
        self.assertEqual(self.database.recover_incomplete_runs(), 1)
        recovered = self.database.recent_runs(1)[0]
        self.assertEqual(recovered["status"], "failed")
        self.assertIn("意外退出", recovered["error"])

    def test_successful_snapshot_satisfies_daily_reminder(self):
        self.assertFalse(self.database.metric_collected_today())
        self.database.add_metric({"status": "failed", "error": "no value"})
        self.assertFalse(self.database.metric_collected_today())
        self.database.add_metric({"status": "success", "gold": 100})
        self.assertTrue(self.database.metric_collected_today())


if __name__ == "__main__":
    unittest.main()
