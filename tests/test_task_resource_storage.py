import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.database import Database
from tools.resource_snapshots import (
    ResourceFieldEvidence,
    ResourceSnapshot,
    ResourceValues,
    ResourceYield,
    calculate_resource_yield,
)


class TaskResourceStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "dashboard.sqlite3"
        self.database = Database(self.path)
        self.database.create_run({
            "id": "run-1",
            "taskId": "baotu",
            "taskName": "宝图任务",
            "status": "preparing",
            "options": {},
            "startedAt": "2026-08-05T02:00:00+00:00",
        })

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def snapshot(phase: str, captured_at: str, **changes) -> ResourceSnapshot:
        values = {
            "level": 69,
            "experience": 1000,
            "experience_required": 5000,
            "gold": 10000,
            "silver": 20000,
            **changes,
        }
        return ResourceSnapshot(
            target_id="local-primary",
            character_id="character-1",
            phase=phase,
            captured_at=captured_at,
            values=ResourceValues(**values),
            evidence={
                "gold": ResourceFieldEvidence(
                    "captured", "backpack", raw_text=str(values["gold"])
                )
            },
        )

    def test_snapshots_and_net_changes_round_trip_with_run_history(self):
        before = self.database.add_resource_snapshot(
            "run-1", self.snapshot("before", "2026-08-05T02:00:01+00:00")
        )
        after = self.database.add_resource_snapshot(
            "run-1",
            self.snapshot(
                "after",
                "2026-08-05T02:05:00+00:00",
                experience=1350,
                gold=10120,
                silver=19800,
            ),
        )
        result = calculate_resource_yield(
            before, after, task_status="completed"
        )

        stored = self.database.add_resource_yield("run-1", "baotu", result)
        run = self.database.recent_runs(1)[0]

        self.assertEqual(stored["attributionStatus"], "reliable")
        self.assertEqual(stored["experienceDelta"], 350)
        self.assertEqual(stored["goldDelta"], 120)
        self.assertEqual(stored["silverDelta"], -200)
        self.assertEqual(stored["before"]["values"]["gold"], 10000)
        self.assertEqual(stored["after"]["values"]["silver"], 19800)
        self.assertEqual(run["benefitStatus"], "reliable")
        self.assertEqual(run["resourceYield"], stored)

    def test_missing_yield_is_null_in_api_instead_of_zero(self):
        with TestClient(create_app(self.path)) as client:
            response = client.get("/api/runs")

        self.assertEqual(response.status_code, 200)
        run = response.json()["items"][0]
        self.assertIsNone(run["resourceYield"])
        self.assertEqual(run["benefitStatus"], "not_captured")

    def test_already_completed_persists_not_run_without_after_snapshot(self):
        before = self.database.add_resource_snapshot(
            "run-1", self.snapshot("before", "2026-08-05T02:00:01+00:00")
        )
        result = calculate_resource_yield(
            before, None, task_status="already_completed"
        )

        stored = self.database.add_resource_yield("run-1", "baotu", result)

        self.assertEqual(stored["attributionStatus"], "not_run")
        self.assertIsNone(stored["after"])
        self.assertIsNone(stored["goldDelta"])

    def test_existing_database_adds_resource_tables(self):
        legacy_path = Path(self.temp.name) / "legacy.sqlite3"
        legacy_path.unlink(missing_ok=True)
        with self.database.connect() as source:
            schema = source.execute(
                "SELECT sql FROM sqlite_master WHERE name='task_runs'"
            ).fetchone()["sql"]
        with sqlite3.connect(legacy_path) as legacy:
            legacy.execute(schema)

        migrated = Database(legacy_path)
        with migrated.connect() as db:
            tables = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

        self.assertIn("resource_snapshots", tables)
        self.assertIn("task_resource_yields", tables)

    def test_task_yield_statistics_use_per_metric_reliability(self):
        before = self.database.add_resource_snapshot(
            "run-1", self.snapshot("before", "2026-08-05T02:00:01+00:00")
        )
        after = self.database.add_resource_snapshot(
            "run-1",
            self.snapshot(
                "after", "2026-08-05T02:05:00+00:00",
                experience=1400, gold=10120, silver=20200,
            ),
        )
        self.database.add_resource_yield(
            "run-1", "baotu",
            calculate_resource_yield(before, after, task_status="completed"),
        )

        self.database.create_run({
            "id": "run-2", "taskId": "baotu", "taskName": "宝图任务",
            "status": "succeeded", "options": {},
            "startedAt": "2026-08-05T03:00:00+00:00",
        })
        second_before = self.database.add_resource_snapshot(
            "run-2", self.snapshot("before", "2026-08-05T03:00:01+00:00")
        )
        second_after = self.database.add_resource_snapshot(
            "run-2",
            self.snapshot(
                "after", "2026-08-05T03:05:00+00:00",
                level=70, experience=200, gold=10060, silver=20300,
            ),
        )
        self.database.add_resource_yield(
            "run-2", "baotu",
            ResourceYield(
                task_status="completed",
                attribution_status="cross_level_unknown",
                experience_delta=None,
                gold_delta=60,
                silver_delta=300,
                reasons=("level_requirement_missing",),
                before_snapshot_id=second_before.snapshot_id,
                after_snapshot_id=second_after.snapshot_id,
            ),
        )

        statistics = self.database.task_yield_statistics(30)
        task = statistics["tasks"][0]

        self.assertEqual(statistics["rangeDays"], 30)
        self.assertEqual(task["taskId"], "baotu")
        self.assertEqual(task["completedRuns"], 2)
        self.assertEqual(task["reliableRuns"], 1)
        self.assertEqual(task["coverageRate"], 50.0)
        self.assertEqual(task["metrics"]["experience"], {
            "average": 400, "total": 400, "sampleCount": 1,
            "minimum": 400, "maximum": 400,
        })
        self.assertEqual(task["metrics"]["gold"]["average"], 90)
        self.assertEqual(task["metrics"]["gold"]["sampleCount"], 2)
        self.assertEqual(task["metrics"]["silver"]["total"], 500)
        self.assertEqual(task["points"][1]["validMetrics"], ["gold", "silver"])

    def test_task_yield_statistics_api_defaults_to_thirty_days(self):
        with TestClient(create_app(self.path)) as client:
            response = client.get("/api/task-yields")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"rangeDays": 30, "tasks": []})


if __name__ == "__main__":
    unittest.main()
