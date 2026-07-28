import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.database import Database


class TaskCompletionOverrideDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(
            Path(self.temporary_directory.name) / "dashboard.sqlite3"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_manual_pending_overrides_automatic_completion(self):
        completed_at = "2026-07-28T03:00:00+00:00"
        with self.database.connect() as db:
            db.execute(
                """INSERT INTO task_runs(
                id, task_id, task_name, status, options_json, started_at,
                finished_at, result_json
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    "completed-run", "yunbiao", "普通运镖", "succeeded", "{}",
                    completed_at, completed_at, json.dumps({"status": "completed"}),
                ),
            )

        self.database.set_task_completion_override(
            "yunbiao", "pending", local_date=date(2026, 7, 28)
        )

        completions = self.database.today_task_completions(
            local_date=date(2026, 7, 28)
        )
        self.assertEqual(completions["yunbiao"], {
            "status": "pending",
            "completedAt": None,
            "resultStatus": None,
            "source": "manual",
        })

    def test_manual_completion_only_applies_to_selected_date(self):
        saved = self.database.set_task_completion_override(
            "shimen", "completed", local_date=date(2026, 7, 28)
        )

        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["source"], "manual")
        self.assertIsNotNone(saved["completedAt"])
        self.assertEqual(
            self.database.today_task_completions(local_date=date(2026, 7, 29)),
            {},
        )

    def test_rejects_invalid_override_values(self):
        with self.assertRaisesRegex(ValueError, "任务 ID 不能为空"):
            self.database.set_task_completion_override("", "completed")
        with self.assertRaisesRegex(ValueError, "completed 或 pending"):
            self.database.set_task_completion_override("yunbiao", "unknown")


class TaskCompletionOverrideApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        app = create_app(Path(self.temporary_directory.name) / "dashboard.sqlite3")
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temporary_directory.cleanup()

    def test_can_mark_task_completed_and_pending(self):
        completed = self.client.patch(
            "/api/tasks/yunbiao/today-status", json={"status": "completed"}
        )

        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["todayStatus"]["status"], "completed")
        self.assertEqual(completed.json()["todayStatus"]["source"], "manual")
        tasks = {
            task["id"]: task for task in self.client.get("/api/tasks").json()["items"]
        }
        self.assertEqual(tasks["yunbiao"]["todayStatus"]["status"], "completed")

        pending = self.client.patch(
            "/api/tasks/yunbiao/today-status", json={"status": "pending"}
        )
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["todayStatus"], {
            "status": "pending",
            "completedAt": None,
            "resultStatus": None,
            "source": "manual",
        })

    def test_rejects_unknown_task_and_status(self):
        unknown_task = self.client.patch(
            "/api/tasks/not-a-task/today-status", json={"status": "completed"}
        )
        invalid_status = self.client.patch(
            "/api/tasks/yunbiao/today-status", json={"status": "unknown"}
        )

        self.assertEqual(unknown_task.status_code, 404)
        self.assertEqual(invalid_status.status_code, 422)


if __name__ == "__main__":
    unittest.main()
