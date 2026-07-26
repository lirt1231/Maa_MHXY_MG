import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

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
            {"sanjieqiyuan", "keju", "zhuogui", "fuli_qiandao", "bangpai_qiandao"},
        )
        self.assertTrue(
            all(
                item["risk"]["level"] in {"low", "medium", "high"}
                and item["risk"]["label"]
                and item["risk"]["summary"]
                for item in response.json()["items"]
            )
        )

    def test_start_requires_checklist_confirmation(self):
        response = self.client.post(
            "/api/runs",
            json={"taskId": "keju", "options": {}, "checklistConfirmed": False},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("确认", response.json()["detail"])

    def test_inspection_is_transient_and_returns_structured_result(self):
        database = self.client.app.state.database
        self.client.app.state.runner.inspect = Mock(return_value={
            "taskId": "fuli_qiandao", "status": "ready", "canStart": True,
            "scene": "open_welfare", "sceneLabel": "福利入口",
            "summary": "当前画面支持启动此任务", "proposedAction": "打开福利",
            "reason": None, "evidence": {}, "observedAt": "2026-07-26T00:00:00+00:00",
        })

        response = self.client.post(
            "/api/tasks/fuli_qiandao/inspect", json={"options": {}}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scene"], "open_welfare")
        self.assertEqual(database.recent_runs(), [])
        self.assertEqual(database.events_after(0), [])

    def test_inspection_timeout_is_actionable(self):
        self.client.app.state.runner.inspect = Mock(
            side_effect=TimeoutError("画面检测超过 15 秒，已停止检测")
        )
        response = self.client.post("/api/tasks/keju/inspect", json={"options": {}})
        self.assertEqual(response.status_code, 504)
        self.assertIn("15 秒", response.json()["detail"])

    def test_inspection_is_rejected_while_task_is_running(self):
        runner = self.client.app.state.runner
        runner._current = {"id": "busy", "status": "running"}
        inspect = Mock(wraps=runner.inspect)
        runner.inspect = inspect

        response = self.client.post("/api/tasks/keju/inspect", json={"options": {}})
        runner._current = None

        self.assertEqual(response.status_code, 409)
        self.assertIn("任务运行期间", response.json()["detail"])

    def test_inspection_blocks_other_game_changing_console_actions(self):
        runner = self.client.app.state.runner
        runner._inspecting = True
        try:
            game_response = self.client.post("/api/game/start")
            capture_response = self.client.post("/api/metrics/capture")
        finally:
            runner._inspecting = False

        self.assertEqual(game_response.status_code, 409)
        self.assertIn("画面检测期间", game_response.json()["detail"])
        self.assertEqual(capture_response.status_code, 409)
        self.assertIn("画面检测期间", capture_response.json()["detail"])

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
