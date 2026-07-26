import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from dashboard.app import _sse_event, create_app
from dashboard.database import Database


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client_context = TestClient(create_app(Path(self.temp.name) / "dashboard.sqlite3"))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def test_task_api_only_returns_macos_whitelist(self):
        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item["id"] for item in response.json()["items"]},
            {"sanjieqiyuan", "keju", "zhuogui"},
        )

    def test_start_requires_checklist_confirmation(self):
        response = self.client.post(
            "/api/runs",
            json={"taskId": "keju", "options": {}, "checklistConfirmed": False},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("确认", response.json()["detail"])

    def test_metric_history_starts_empty(self):
        response = self.client.get("/api/metrics?range=7d&metric=experience")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["points"], [])
        self.assertIsNone(response.json()["current"])

    def test_sse_events_include_resume_id(self):
        payload = {
            "id": 42, "runId": "run", "createdAt": "2026-07-26T00:00:00+00:00",
            "level": "info", "type": "status", "message": "恢复", "payload": {"status": "running"},
        }
        encoded = _sse_event(payload)
        self.assertTrue(encoded.startswith("id: 42\nevent: status\n"))
        self.assertIn('"message": "恢复"', encoded)

class DashboardRestartTests(unittest.TestCase):
    def test_lifespan_recovers_abandoned_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard.sqlite3"
            database = Database(path)
            database.create_run({
                "id": "from-old-process", "taskId": "keju", "taskName": "科举乡试",
                "status": "running", "options": {}, "startedAt": "2026-07-26T00:00:00+00:00",
            })
            with TestClient(create_app(path)) as client:
                response = client.get("/api/runs")
            recovered = response.json()["items"][0]
            self.assertEqual(recovered["status"], "failed")
            self.assertIn("意外退出", recovered["error"])
            self.assertIsNotNone(recovered["finishedAt"])


if __name__ == "__main__":
    unittest.main()
