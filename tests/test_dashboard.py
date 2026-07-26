import tempfile
import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dashboard.database import Database
from dashboard.metrics import (
    parse_backpack_gold,
    parse_number,
    parse_profile_metrics,
    parse_visible_metrics,
)
from dashboard.registry import TASKS, task_list, validate_options
from dashboard.system import _game_is_running


class TaskRegistryTests(unittest.TestCase):
    def test_only_verified_macos_tasks_are_exposed(self):
        self.assertEqual(
            set(TASKS),
            {"sanjieqiyuan", "keju", "zhuogui", "fuli_qiandao", "bangpai_qiandao"},
        )
        self.assertTrue(all(task["checklist"] for task in task_list()))
        self.assertTrue(all(task["steps"] for task in task_list()))
        self.assertTrue(all(task["risk"]["level"] in {"low", "medium", "high"} for task in task_list()))

    def test_defaults_and_integer_bounds_are_applied(self):
        self.assertEqual(validate_options("zhuogui", {}), {"rounds": 2})
        with self.assertRaisesRegex(ValueError, "1–100"):
            validate_options("zhuogui", {"rounds": 0})
        with self.assertRaisesRegex(ValueError, "未知参数"):
            validate_options("keju", {"apiKey": "must-not-be-accepted"})

    def test_task_specific_defaults(self):
        self.assertEqual(validate_options("fuli_qiandao", {}), {})
        self.assertEqual(validate_options("bangpai_qiandao", {}), {})
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


class SystemInspectionTests(unittest.TestCase):
    @patch("dashboard.system.subprocess.run")
    def test_game_process_detection_does_not_require_window_capture(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout='ASN:0x0-0x1-"梦幻西游":\n')
        self.assertTrue(_game_is_running())
        run.assert_called_once_with(
            ["/usr/bin/lsappinfo", "find", "bundleID=com.netease.my"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )

    @patch("dashboard.system.subprocess.run")
    def test_missing_game_process_returns_false(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="\n")
        self.assertFalse(_game_is_running())


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

    def test_structured_run_result_round_trips(self):
        self.database.create_run({
            "id": "structured", "taskId": "sanjieqiyuan", "taskName": "三界奇缘",
            "status": "preparing", "options": {}, "startedAt": "2026-07-26T00:00:00+00:00",
        })
        result = {
            "task_id": "sanjieqiyuan", "status": "safe_stopped",
            "reason": "未识别到明确完成状态",
        }
        self.database.update_run("structured", "failed", result["reason"], result)

        restored = self.database.recent_runs(1)[0]
        self.assertEqual(restored["result"], result)

    def test_existing_database_is_migrated_for_structured_results(self):
        path = Path(self.temp.name) / "legacy.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute(
                """CREATE TABLE task_runs (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_name TEXT NOT NULL,
                status TEXT NOT NULL, options_json TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, duration_ms INTEGER, error TEXT,
                benefit_status TEXT NOT NULL DEFAULT 'not_captured')"""
            )

        migrated = Database(path)
        with migrated.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(task_runs)")}
            game_data_columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(game_data_snapshots)")
            }
        self.assertIn("result_json", columns)
        self.assertEqual(game_data_columns, {
            "id",
            "captured_at",
            "category",
            "source_page",
            "status",
            "data_json",
            "evidence_json",
            "error",
        })

    def test_game_data_snapshots_serialize_filter_and_select_latest(self):
        first = self.database.add_game_data_snapshot({
            "capturedAt": "2026-07-25T08:00:00+00:00",
            "category": "character_resources",
            "sourcePage": "backpack_profile",
            "status": "partial",
            "data": {"gold": 123, "level": None},
            "evidence": {"capturedFields": ["gold"], "note": "背包"},
            "error": "等级未识别",
        })
        latest = self.database.add_game_data_snapshot({
            "capturedAt": "2026-07-26T08:00:00+00:00",
            "category": "character_resources",
            "sourcePage": "backpack_profile",
            "status": "success",
            "data": {"gold": 456, "level": 69},
            "evidence": {"capturedFields": ["gold", "level"]},
        })
        self.database.add_game_data_snapshot({
            "capturedAt": "2026-07-27T08:00:00+00:00",
            "category": "daily_tasks",
            "sourcePage": "activity_list",
            "status": "success",
            "data": {"tasks": [{"name": "三界奇缘", "completed": True}]},
            "evidence": {},
        })

        resources = self.database.game_data_snapshots(
            category="character_resources", limit=1
        )

        self.assertEqual(resources, [latest])
        self.assertEqual(
            self.database.latest_game_data("character_resources"), latest
        )
        latest_by_category = self.database.latest_game_data_by_category()
        self.assertEqual(latest_by_category["character_resources"], latest)
        self.assertEqual(
            latest_by_category["daily_tasks"]["data"]["tasks"][0]["name"],
            "三界奇缘",
        )
        self.assertEqual(first["evidence"]["note"], "背包")
        self.assertEqual(len(self.database.game_data_snapshots(limit=10)), 3)

    def test_game_data_snapshot_rejects_invalid_status_and_shapes(self):
        base = {
            "category": "character_resources",
            "sourcePage": "backpack_profile",
            "status": "success",
            "data": {},
            "evidence": {},
        }
        for changes in (
            {"status": "unknown"},
            {"data": []},
            {"evidence": []},
            {"category": ""},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.database.add_game_data_snapshot({**base, **changes})

    def test_incomplete_runs_are_recovered_after_restart(self):
        self.database.create_run({
            "id": "abandoned", "taskId": "zhuogui", "taskName": "捉鬼任务",
            "status": "preparing", "options": {"rounds": 2}, "startedAt": "2026-07-26T00:00:00+00:00",
        })
        self.assertEqual(self.database.recover_incomplete_runs(), 1)
        recovered = self.database.recent_runs(1)[0]
        self.assertEqual(recovered["status"], "failed")
        self.assertIn("意外退出", recovered["error"])
        self.assertIsNotNone(recovered["durationMs"])
        events = self.database.events_after(0)
        self.assertEqual(events[-1]["runId"], "abandoned")
        self.assertTrue(events[-1]["payload"]["recovered"])

    def test_events_can_resume_after_last_received_id(self):
        self.database.create_run({
            "id": "run-events", "taskId": "keju", "taskName": "科举乡试",
            "status": "preparing", "options": {}, "startedAt": "2026-07-26T00:00:00+00:00",
        })
        first = self.database.add_event("run-events", "info", "status", "准备")
        second = self.database.add_event("run-events", "info", "log", "第一题")
        resumed = self.database.events_after(first["id"])
        self.assertEqual([event["id"] for event in resumed], [second["id"]])

    def test_successful_snapshot_satisfies_daily_reminder(self):
        self.assertFalse(self.database.metric_collected_today())
        self.database.add_metric({"status": "failed", "error": "no value"})
        self.assertFalse(self.database.metric_collected_today())
        self.database.add_metric({"status": "success", "gold": 100})
        self.assertTrue(self.database.metric_collected_today())

    def test_metric_statistics_distinguish_values_failures_and_corrections(self):
        now = datetime.now(timezone.utc)
        self.database.add_metric({
            "capturedAt": (now - timedelta(days=2)).isoformat(),
            "status": "success", "gold": 100,
        })
        self.database.add_metric({
            "capturedAt": (now - timedelta(days=1)).isoformat(),
            "status": "failed", "error": "OCR failed",
        })
        self.database.add_metric({
            "capturedAt": now.isoformat(),
            "status": "success", "gold": 160, "corrected": True,
        })

        metrics = self.database.metrics(7, "gold")

        self.assertEqual(metrics["rangeDays"], 7)
        self.assertEqual(metrics["current"], 160)
        self.assertEqual(metrics["change"], 60)
        self.assertEqual(metrics["stats"], {
            "attempts": 3,
            "successfulCaptures": 2,
            "failedCaptures": 1,
            "correctedCaptures": 1,
            "captureSuccessRate": 66.7,
            "validValues": 2,
            "minimum": 100,
            "maximum": 160,
            "average": 130,
            "latestChange": 60,
            "averageChangePerCapture": 60,
            "daysWithData": 2,
        })


if __name__ == "__main__":
    unittest.main()
