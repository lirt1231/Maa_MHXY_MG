import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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
            {
                "sanjieqiyuan", "keju", "keju_huishi", "zhuogui",
                "fuli_qiandao", "bangpai_qiandao", "baotu",
            },
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

    def test_game_data_api_filters_category_and_limit(self):
        database = self.client.app.state.database
        for captured_at, category, data in (
            ("2026-07-24T00:00:00+00:00", "character_resources", {"gold": 100}),
            ("2026-07-25T00:00:00+00:00", "character_resources", {"gold": 200}),
            ("2026-07-26T00:00:00+00:00", "daily_tasks", {"tasks": []}),
        ):
            database.add_game_data_snapshot({
                "capturedAt": captured_at,
                "category": category,
                "sourcePage": "test_page",
                "status": "success",
                "data": data,
                "evidence": {},
            })

        response = self.client.get(
            "/api/game-data?category=character_resources&limit=1"
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["targetId"], "local-primary")
        self.assertEqual(body["items"][0]["data"], {"gold": 200})
        self.assertEqual(
            body["latestByCategory"]["character_resources"], body["items"][0]
        )
        self.assertEqual(
            body["latestAttemptByCategory"], body["latestByCategory"]
        )
        self.assertEqual(
            body["latestUsableByCategory"]["character_resources"], body["items"][0]
        )
        self.assertEqual(
            self.client.get("/api/game-data?limit=0").status_code, 422
        )

    def test_game_data_api_isolates_targets(self):
        database = self.client.app.state.database
        database.add_game_data_snapshot({
            "targetId": "secondary-window",
            "category": "character_resources",
            "sourcePage": "backpack",
            "status": "success",
            "data": {"gold": 999},
            "evidence": {},
        })

        primary = self.client.get("/api/game-data").json()
        secondary = self.client.get(
            "/api/game-data?targetId=secondary-window"
        ).json()

        self.assertEqual(primary["items"], [])
        self.assertEqual(secondary["targetId"], "secondary-window")
        self.assertEqual(secondary["items"][0]["data"], {"gold": 999})
        self.assertEqual(
            self.client.get("/api/game-data?category=Bad%20Category").status_code,
            422,
        )

    @patch("dashboard.app.collect_daily_task_data")
    def test_daily_task_capture_persists_structured_data_without_run_history(
        self, collect
    ):
        collect.return_value = {
            "category": "daily_tasks",
            "source_page": "daily_schedule",
            "status": "success",
            "data": {"tasks": [{
                "taskId": "daily-shimen",
                "name": "师门任务",
                "completed": 10,
                "target": 20,
                "status": "available",
                "rewardStatus": "unknown",
            }]},
            "evidence": {"image_size": [1020, 718], "ocr_item_count": 32},
        }
        before_runs = self.client.get("/api/runs").json()["items"]

        response = self.client.post("/api/game-data/daily-tasks/capture")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["category"], "daily_tasks")
        self.assertEqual(response.json()["data"]["tasks"][0]["name"], "师门任务")
        self.assertEqual(self.client.get("/api/runs").json()["items"], before_runs)
        latest = self.client.get("/api/game-data").json()["latestByCategory"]
        self.assertEqual(latest["daily_tasks"], response.json())
        self.assertFalse(self.client.app.state.runner.inspecting)

    @patch(
        "dashboard.app.collect_daily_task_data",
        side_effect=TimeoutError("日程数据采集超过 15 秒，已停止采集"),
    )
    def test_daily_task_capture_timeout_is_recorded_and_releases_lock(self, collect):
        publish = Mock(wraps=self.client.app.state.broker.publish)
        self.client.app.state.broker.publish = publish

        response = self.client.post("/api/game-data/daily-tasks/capture")

        self.assertEqual(response.status_code, 504)
        latest = self.client.get(
            "/api/game-data?category=daily_tasks"
        ).json()["latestByCategory"]["daily_tasks"]
        self.assertEqual(latest["status"], "failed")
        self.assertIn("15 秒", latest["error"])
        self.assertFalse(self.client.app.state.runner.inspecting)
        event = publish.call_args.args[0]
        self.assertEqual(event["type"], "game_data")
        self.assertEqual(event["level"], "error")
        self.assertEqual(event["payload"], latest)

    @patch("dashboard.app.collect_daily_task_data")
    def test_daily_task_capture_is_rejected_during_another_read_only_operation(
        self, collect
    ):
        runner = self.client.app.state.runner
        runner.begin_read_only()
        try:
            response = self.client.post("/api/game-data/daily-tasks/capture")
        finally:
            runner.end_read_only()

        self.assertEqual(response.status_code, 409)
        collect.assert_not_called()

    @patch("dashboard.app.capture_visible_metrics")
    def test_metric_capture_syncs_sanitized_character_resources(self, capture):
        capture.return_value = {
            "status": "success",
            "gold": 7654321,
            "level": 69,
            "experience": 123456,
            "experienceRequired": 987654,
            "rawText": "背包 OCR 原始文本，不应进入游戏数据快照",
        }

        metric_response = self.client.post("/api/metrics/capture")
        game_data_response = self.client.get(
            "/api/game-data?category=character_resources"
        )

        self.assertEqual(metric_response.status_code, 202)
        self.assertEqual(game_data_response.status_code, 200)
        item = game_data_response.json()["items"][0]
        self.assertEqual(item["status"], "success")
        self.assertEqual(item["sourcePage"], "backpack_profile")
        self.assertEqual(item["data"], {
            "gold": 7654321,
            "level": 69,
            "experience": 123456,
            "experienceRequired": 987654,
        })
        self.assertEqual(item["evidence"], {
            "metricSnapshotId": metric_response.json()["id"],
            "capturedFields": [
                "gold", "level", "experience", "experienceRequired"
            ],
            "rawOcrStored": False,
            "fields": {
                "gold": {
                    "status": "captured",
                    "confidence": None,
                    "sourcePage": "backpack",
                },
                "level": {
                    "status": "captured",
                    "confidence": None,
                    "sourcePage": "character_profile",
                },
                "experience": {
                    "status": "captured",
                    "confidence": None,
                    "sourcePage": "character_profile",
                },
                "experienceRequired": {
                    "status": "captured",
                    "confidence": None,
                    "sourcePage": "character_profile",
                },
            },
        })
        self.assertNotIn("rawText", item)
        self.assertNotIn("OCR 原始文本", str(item))

    @patch("dashboard.app.capture_visible_metrics", side_effect=RuntimeError("截图失败"))
    def test_failed_metric_capture_also_creates_failed_game_data(self, capture):
        metric_response = self.client.post("/api/metrics/capture")
        game_data_response = self.client.get(
            "/api/game-data?category=character_resources"
        )

        self.assertEqual(metric_response.status_code, 202)
        item = game_data_response.json()["latestByCategory"]["character_resources"]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["error"], "截图失败")
        self.assertEqual(item["data"], {
            "gold": None,
            "level": None,
            "experience": None,
            "experienceRequired": None,
        })
        self.assertEqual(item["evidence"]["capturedFields"], [])
        self.assertFalse(item["evidence"]["rawOcrStored"])

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
