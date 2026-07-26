import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from dashboard.app import create_app


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


if __name__ == "__main__":
    unittest.main()
