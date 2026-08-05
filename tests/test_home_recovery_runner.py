import json
import sqlite3
import signal
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard.database import Database
from dashboard.events import EventBroker
from dashboard.runner import RunnerManager


class FinishedProcess:
    def __init__(self, lines, return_code=0):
        self.pid = 1001
        self.stdout = lines
        self.return_code = return_code

    def poll(self):
        return self.return_code

    def wait(self):
        return self.return_code


class BlockingProcess:
    def __init__(self):
        self.pid = 2002
        self.exited = threading.Event()
        self.return_code = 0
        self.stdout = self

    def __iter__(self):
        self.exited.wait()
        return iter([])

    def poll(self):
        return self.return_code if self.exited.is_set() else None

    def wait(self):
        self.exited.wait()
        return self.return_code

    def exit(self, return_code=-signal.SIGTERM):
        self.return_code = return_code
        self.exited.set()


def task_process(status="completed"):
    payload = {
        "task_id": "sanjieqiyuan",
        "status": status,
        "questions": 10,
        "clicks": 10,
    }
    if status == "safe_stopped":
        payload = {
            "task_id": "sanjieqiyuan",
            "status": status,
            "reason": "未识别到明确完成状态",
        }
    return FinishedProcess(["result_json=" + json.dumps(payload, ensure_ascii=False)])


def recovery_process(status="confirmed"):
    payload = {
        "status": status,
        "outcome": "recovered" if status == "confirmed" else None,
        "scene": "main_screen" if status == "confirmed" else None,
        "reason": None if status == "confirmed" else "未知页面",
        "actions": 3,
        "clicks": 1,
    }
    if status == "confirmed":
        payload["confirmedAt"] = "2026-08-03T01:00:00+00:00"
        payload["evidence"] = {
            "targetId": "local-primary",
            "firstFrameId": "frame-1",
            "lastFrameId": "frame-2",
            "anchors": ["task", "team", "bag"],
            "imageSize": [1020, 718],
            "captureMethod": "maa",
        }
    return FinishedProcess(
        ["home_recovery_json=" + json.dumps(payload, ensure_ascii=False)],
        0 if status == "confirmed" else 1,
    )


class RunnerHomeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "dashboard.sqlite3")
        self.manager = RunnerManager(self.database, EventBroker())

    def tearDown(self):
        self.temp.cleanup()

    def wait_terminal(self):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            current = self.manager.current
            if current and current["status"] in {"succeeded", "failed", "cancelled"}:
                return current
            time.sleep(0.002)
        self.fail(f"run did not finish: {self.manager.current}")

    def test_completed_task_runs_recovery_and_persists_confirmation(self):
        with patch(
            "dashboard.runner.subprocess.Popen",
            side_effect=[task_process(), recovery_process()],
        ) as popen:
            self.manager.start("sanjieqiyuan", {}, checklist_confirmed=True)
            current = self.wait_terminal()
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(current["status"], "succeeded")
        self.assertEqual(current["homeRecovery"]["status"], "confirmed")
        self.assertIsNone(current["failurePhase"])
        stored = self.database.recent_runs(1)[0]
        self.assertEqual(stored["stage"], "已确认回到主界面")
        self.assertEqual(stored["homeRecovery"]["evidence"]["lastFrameId"], "frame-2")

    def test_already_completed_task_also_requires_home_confirmation(self):
        with patch(
            "dashboard.runner.subprocess.Popen",
            side_effect=[task_process("already_completed"), recovery_process()],
        ) as popen:
            self.manager.start("sanjieqiyuan", {}, checklist_confirmed=True)
            current = self.wait_terminal()
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(current["status"], "succeeded")
        self.assertEqual(current["result"]["status"], "already_completed")
        self.assertEqual(current["homeRecovery"]["status"], "confirmed")

    def test_recovery_failure_preserves_task_success_and_today_completion(self):
        with patch(
            "dashboard.runner.subprocess.Popen",
            side_effect=[task_process(), recovery_process("failed")],
        ):
            self.manager.start("sanjieqiyuan", {}, checklist_confirmed=True)
            current = self.wait_terminal()
        self.assertEqual(current["status"], "failed")
        self.assertEqual(current["failurePhase"], "recovery")
        self.assertEqual(current["result"]["status"], "completed")
        self.assertEqual(current["homeRecovery"]["status"], "failed")
        self.assertIn("sanjieqiyuan", self.database.today_task_completions())

    def test_safe_stopped_task_never_starts_recovery(self):
        with patch(
            "dashboard.runner.subprocess.Popen", return_value=task_process("safe_stopped")
        ) as popen:
            self.manager.start("sanjieqiyuan", {}, checklist_confirmed=True)
            current = self.wait_terminal()
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(current["status"], "failed")
        self.assertEqual(current["failurePhase"], "task")
        self.assertEqual(current["homeRecovery"]["status"], "not_requested")

    def test_stop_during_recovery_cancels_input_and_preserves_task_result(self):
        recovery = BlockingProcess()

        def killpg(pid, sig):
            self.assertEqual(pid, recovery.pid)
            recovery.exit(-sig)

        with (
            patch(
                "dashboard.runner.subprocess.Popen",
                side_effect=[task_process(), recovery],
            ),
            patch("dashboard.runner.os.killpg", side_effect=killpg),
        ):
            run = self.manager.start("sanjieqiyuan", {}, checklist_confirmed=True)
            deadline = time.monotonic() + 1
            while self.manager.current["status"] != "recovering" and time.monotonic() < deadline:
                time.sleep(0.002)
            current = self.manager.stop(run["id"])
        self.assertEqual(current["status"], "cancelled")
        self.assertEqual(current["result"]["status"], "completed")
        self.assertEqual(current["homeRecovery"]["status"], "cancelled")
        self.assertIn("sanjieqiyuan", self.database.today_task_completions())


class HomeRecoveryMigrationTests(unittest.TestCase):
    def test_old_database_adds_recovery_columns_and_defaults_to_unconfirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute(
                    """CREATE TABLE task_runs (
                    id TEXT PRIMARY KEY, task_id TEXT, task_name TEXT, status TEXT,
                    options_json TEXT, started_at TEXT, finished_at TEXT,
                    duration_ms INTEGER, error TEXT, result_json TEXT,
                    benefit_status TEXT DEFAULT 'not_captured'
                    )"""
                )
                db.execute(
                    "INSERT INTO task_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("old", "baotu", "宝图", "succeeded", "{}",
                     datetime.now(timezone.utc).isoformat(), None, None, None,
                     '{"status":"completed"}', "not_captured"),
                )
            database = Database(path)
            run = database.recent_runs(1)[0]
            self.assertEqual(run["homeRecovery"], {"status": "not_requested"})
            self.assertIsNone(run["failurePhase"])

    def test_recovering_run_is_failed_without_replaying_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "dashboard.sqlite3")
            now = datetime.now(timezone.utc).isoformat()
            with database.connect() as db:
                db.execute(
                    """INSERT INTO task_runs(
                    id, task_id, task_name, status, options_json, started_at,
                    result_json, stage, home_recovery_json
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    ("recovering", "baotu", "宝图", "recovering", "{}", now,
                     '{"status":"completed"}', "正在返回主界面",
                     '{"status":"running"}'),
                )
            self.assertEqual(database.recover_incomplete_runs(), 1)
            run = database.recent_runs(1)[0]
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["failurePhase"], "recovery")
            self.assertEqual(run["result"]["status"], "completed")
            self.assertIn("未自动重放", run["homeRecovery"]["reason"])


if __name__ == "__main__":
    unittest.main()
