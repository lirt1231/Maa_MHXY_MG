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
        self.assertEqual(stored["trackedFields"], ["experience", "gold", "silver"])
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

    def test_settlement_reward_round_trips_without_a_before_snapshot(self):
        reward = self.database.add_resource_snapshot(
            "run-1",
            ResourceSnapshot(
                target_id="local-primary",
                phase="reward",
                captured_at="2026-08-05T02:05:00+00:00",
                values=ResourceValues(gold=12500),
                evidence={
                    "gold": ResourceFieldEvidence(
                        "captured", "huishi_settlement", raw_text="12,500"
                    )
                },
                captured_fields=frozenset({"gold"}),
            ),
        )
        stored = self.database.add_resource_yield(
            "run-1", "keju_huishi",
            ResourceYield(
                task_status="completed",
                attribution_status="reliable",
                gold_delta=12500,
                after_snapshot_id=reward.snapshot_id,
                tracked_fields=frozenset({"gold"}),
                measurement_source="settlement_reward",
            ),
        )

        self.assertIsNone(stored["before"])
        self.assertEqual(stored["goldDelta"], 12500)
        self.assertEqual(stored["measurementSource"], "settlement_reward")

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

    def test_existing_yields_migrate_to_the_original_measurement_source(self):
        legacy_path = Path(self.temp.name) / "legacy-yields.sqlite3"
        with sqlite3.connect(legacy_path) as legacy:
            legacy.executescript(
                """
                CREATE TABLE task_runs (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    task_name TEXT NOT NULL, status TEXT NOT NULL,
                    options_json TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT, duration_ms INTEGER, error TEXT,
                    result_json TEXT, benefit_status TEXT NOT NULL DEFAULT 'not_captured'
                );
                CREATE TABLE resource_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    target_id TEXT NOT NULL, character_id TEXT, phase TEXT NOT NULL,
                    captured_at TEXT NOT NULL, status TEXT NOT NULL, level INTEGER,
                    experience INTEGER, experience_required INTEGER, gold INTEGER,
                    silver INTEGER, captured_fields_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL, error TEXT
                );
                CREATE TABLE task_resource_yields (
                    run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    before_snapshot_id INTEGER NOT NULL, after_snapshot_id INTEGER,
                    task_status TEXT NOT NULL, attribution_status TEXT NOT NULL,
                    experience_delta INTEGER, gold_delta INTEGER, silver_delta INTEGER,
                    tracked_fields_json TEXT NOT NULL, reasons_json TEXT NOT NULL,
                    calculation_version INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                INSERT INTO task_runs(id, task_id, task_name, status, options_json, started_at)
                VALUES('legacy-run', 'baotu', '宝图任务', 'succeeded', '{}', '2026-08-05T02:00:00+00:00');
                INSERT INTO resource_snapshots(
                    id, run_id, target_id, phase, captured_at, status, gold,
                    captured_fields_json, evidence_json
                ) VALUES(
                    1, 'legacy-run', 'local-primary', 'before',
                    '2026-08-05T02:00:01+00:00', 'success', 100,
                    '["gold"]', '{}'
                );
                INSERT INTO task_resource_yields(
                    run_id, task_id, before_snapshot_id, after_snapshot_id,
                    task_status, attribution_status, experience_delta, gold_delta,
                    silver_delta, tracked_fields_json, reasons_json,
                    calculation_version, created_at
                ) VALUES(
                    'legacy-run', 'baotu', 1, NULL, 'completed', 'partial',
                    NULL, 12, NULL, '["gold"]', '[]', 1,
                    '2026-08-05T02:05:00+00:00'
                );
                """
            )

        migrated = Database(legacy_path)
        with migrated.connect() as db:
            column = next(
                row for row in db.execute("PRAGMA table_info(task_resource_yields)")
                if row["name"] == "before_snapshot_id"
            )
            row = db.execute(
                "SELECT gold_delta, measurement_source FROM task_resource_yields "
                "WHERE run_id='legacy-run'"
            ).fetchone()

        self.assertFalse(column["notnull"])
        self.assertEqual(dict(row), {
            "gold_delta": 12,
            "measurement_source": "resource_delta",
        })

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

    def test_partial_remainder_run_is_not_a_full_task_sample(self):
        self.database.update_run(
            "run-1", "succeeded",
            result={
                "task_id": "yunbiao", "status": "completed",
                "rounds": 2, "target_rounds": 3,
            },
        )
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
            "run-1", "yunbiao",
            calculate_resource_yield(before, after, task_status="completed"),
        )

        task = self.database.task_yield_statistics(30)["tasks"][0]

        self.assertEqual(task["attempts"], 1)
        self.assertEqual(task["completedRuns"], 0)
        self.assertEqual(task["reliableRuns"], 0)
        self.assertEqual(task["metrics"]["gold"]["sampleCount"], 0)
        self.assertEqual(task["points"], [])


if __name__ == "__main__":
    unittest.main()
