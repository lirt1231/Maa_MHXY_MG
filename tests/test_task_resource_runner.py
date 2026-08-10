import json
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard.database import Database
from dashboard.events import EventBroker
from dashboard.runner import RunnerManager
from tools.resource_snapshots import ResourceSnapshot, ResourceValues


class FinishedProcess:
    next_pid = 3000

    def __init__(self, lines, return_code=0):
        type(self).next_pid += 1
        self.pid = type(self).next_pid
        self.stdout = lines
        self.return_code = return_code

    def poll(self):
        return self.return_code

    def wait(self):
        return self.return_code


class BlockingProcess:
    def __init__(self):
        self.pid = 4001
        self.stdout = self
        self.return_code = 0
        self.exited = threading.Event()

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


def snapshot_process(
    phase,
    *,
    captured_at,
    level=69,
    experience=1000,
    experience_required=5000,
    gold=10000,
    silver=20000,
    error=None,
):
    snapshot = ResourceSnapshot(
        target_id="local-primary",
        phase=phase,
        captured_at=captured_at,
        values=ResourceValues(
            level=level,
            experience=experience,
            experience_required=experience_required,
            gold=gold,
            silver=silver,
        ),
        error=error,
    )
    return FinishedProcess([
        "resource_snapshot_json="
        + json.dumps(snapshot.to_payload(), ensure_ascii=False)
    ])


def task_process(status="completed", *, task_id="sanjieqiyuan", settlement_reward=None):
    payload = (
        {"task_id": task_id, "status": status, "actions": 2, "clicks": 2}
        if task_id == "baotu"
        else {
            "task_id": task_id,
            "status": status,
            "answers": 15,
            "clicks": 15,
            "limit": 20,
            **({"settlement_reward": settlement_reward} if settlement_reward is not None else {}),
        }
        if task_id == "keju_huishi"
        else {
            "task_id": task_id,
            "status": status,
            "questions": 10,
            "clicks": 10,
        }
    )
    if status == "safe_stopped":
        payload = {
            "task_id": "sanjieqiyuan",
            "status": status,
            "reason": "未识别到明确完成状态",
        }
    return FinishedProcess([
        "result_json=" + json.dumps(payload, ensure_ascii=False)
    ])


def recovery_process():
    payload = {
        "status": "confirmed",
        "outcome": "recovered",
        "scene": "main_screen",
        "reason": None,
        "actions": 1,
        "clicks": 1,
        "confirmedAt": "2026-08-05T02:09:59+00:00",
        "evidence": {
            "targetId": "local-primary",
            "firstFrameId": "frame-1",
            "lastFrameId": "frame-2",
            "anchors": ["task", "team", "bag"],
        },
    }
    return FinishedProcess([
        "home_recovery_json=" + json.dumps(payload, ensure_ascii=False)
    ])


class TaskResourceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "dashboard.sqlite3")
        self.manager = RunnerManager(
            self.database,
            EventBroker(),
            resource_tracking=True,
            stop_term_timeout=0.05,
            stop_kill_timeout=0.2,
        )

    def tearDown(self):
        self.temp.cleanup()

    def wait_for(self, predicate, timeout=1):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.manager.current
            if current and predicate(current):
                return current
            time.sleep(0.002)
        self.fail(f"run did not reach expected state: {self.manager.current}")

    def start(self):
        return self.manager.start(
            "sanjieqiyuan", {}, checklist_confirmed=True
        )

    def start_baotu(self):
        return self.manager.start("baotu", {}, checklist_confirmed=True)

    def start_huishi(self):
        with patch("dashboard.runner.ensure_task_available"):
            return self.manager.start(
                "keju_huishi", {"waitForRoundOne": True}, checklist_confirmed=True
            )

    def test_completed_task_records_before_after_and_net_changes(self):
        processes = [
            snapshot_process("before", captured_at="2026-08-05T02:00:00+00:00"),
            task_process(),
            recovery_process(),
            snapshot_process(
                "after",
                captured_at="2026-08-05T02:10:00+00:00",
                experience=1400,
                gold=10120,
                silver=20250,
            ),
        ]
        with patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen:
            self.start()
            current = self.wait_for(
                lambda run: run["status"] in {"succeeded", "failed", "cancelled"}
            )

        self.assertEqual(popen.call_count, 4)
        self.assertEqual(current["status"], "succeeded")
        self.assertEqual(current["resourceYield"]["attributionStatus"], "reliable")
        self.assertEqual(current["resourceYield"]["experienceDelta"], 400)
        self.assertEqual(current["resourceYield"]["goldDelta"], 120)
        self.assertEqual(current["resourceYield"]["silverDelta"], 250)
        stored = self.database.recent_runs(1)[0]
        self.assertEqual(stored["resourceYield"], current["resourceYield"])

    def test_already_completed_does_not_capture_after_or_report_zero(self):
        processes = [
            snapshot_process("before", captured_at="2026-08-05T02:00:00+00:00"),
            task_process("already_completed"),
            recovery_process(),
        ]
        with patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen:
            self.start()
            current = self.wait_for(lambda run: run["status"] == "succeeded")

        self.assertEqual(popen.call_count, 3)
        self.assertEqual(current["resourceYield"]["attributionStatus"], "not_run")
        self.assertIsNone(current["resourceYield"]["after"])
        self.assertIsNone(current["resourceYield"]["goldDelta"])

    def test_baotu_skips_resource_snapshots_and_yield_recording(self):
        processes = [task_process(task_id="baotu"), recovery_process()]
        with patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen:
            self.start_baotu()
            current = self.wait_for(lambda run: run["status"] == "succeeded")

        self.assertEqual(popen.call_count, 2)
        self.assertTrue(all(
            not any("run_resource_snapshot_macos.py" in part for part in call.args[0])
            for call in popen.call_args_list
        ))
        self.assertIsNone(current["resourceYield"])
        self.assertIsNone(self.database.recent_runs(1)[0]["resourceYield"])

    def test_fun_news_skips_character_resource_tracking(self):
        self.assertFalse(self.manager._tracks_task_resources("fun_news"))

    def test_huishi_records_confirmed_settlement_rewards_without_snapshots(self):
        settlement_reward = {
            "status": "confirmed",
            "experience": 12500,
            "silver": 30000,
            "gold": 12500,
            "evidence": {
                "sourcePage": "huishi_settlement",
                "experience": {"text": "12,500", "box": [470, 400, 76, 28]},
                "silver": {"text": "30,000", "box": [470, 440, 76, 28]},
                "gold": {"text": "12,500", "box": [470, 490, 76, 28]},
            },
        }
        processes = [
            task_process(task_id="keju_huishi", settlement_reward=settlement_reward),
        ]
        with patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen:
            self.start_huishi()
            current = self.wait_for(lambda run: run["status"] == "succeeded")

        self.assertEqual(popen.call_count, 1)
        self.assertTrue(all(
            not any("run_resource_snapshot_macos.py" in part for part in call.args[0])
            for call in popen.call_args_list
        ))
        self.assertEqual(current["resourceYield"]["goldDelta"], 12500)
        self.assertEqual(current["resourceYield"]["experienceDelta"], 12500)
        self.assertEqual(current["resourceYield"]["silverDelta"], 30000)
        self.assertEqual(
            current["resourceYield"]["measurementSource"], "settlement_reward"
        )
        self.assertIsNone(current["resourceYield"]["before"])
        self.assertEqual(current["homeRecovery"]["status"], "not_requested")

    def test_safe_stop_never_captures_after(self):
        processes = [
            snapshot_process("before", captured_at="2026-08-05T02:00:00+00:00"),
            task_process("safe_stopped"),
        ]
        with patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen:
            self.start()
            current = self.wait_for(lambda run: run["status"] == "failed")

        self.assertEqual(popen.call_count, 2)
        self.assertEqual(current["resourceYield"]["attributionStatus"], "unavailable")
        self.assertIsNone(current["resourceYield"]["after"])

    def test_failed_before_capture_retries_once_before_starting_task(self):
        processes = [
            snapshot_process(
                "before",
                captured_at="2026-08-05T02:00:00+00:00",
                level=None,
                experience=None,
                experience_required=None,
                gold=None,
                silver=None,
                error="当前页面无法安全返回主界面",
            ),
            snapshot_process("before", captured_at="2026-08-05T02:00:05+00:00"),
            task_process(),
            recovery_process(),
            snapshot_process("after", captured_at="2026-08-05T02:10:00+00:00"),
        ]
        with patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen:
            self.start()
            current = self.wait_for(lambda run: run["status"] == "succeeded")

        self.assertEqual(popen.call_count, 5)
        self.assertEqual(current["resourceYield"]["attributionStatus"], "reliable")
        self.assertTrue(all(
            "--phase" in call.args[0] and "before" in call.args[0]
            for call in popen.call_args_list[:2]
        ))

    def test_failed_before_capture_continues_after_one_retry(self):
        failed = lambda captured_at: snapshot_process(
            "before", captured_at=captured_at, level=None, experience=None,
            experience_required=None, gold=None, silver=None,
            error="等待包裹页面打开超时",
        )
        processes = [
            failed("2026-08-05T02:00:00+00:00"),
            failed("2026-08-05T02:00:05+00:00"),
            task_process(),
            recovery_process(),
            snapshot_process("after", captured_at="2026-08-05T02:10:00+00:00"),
        ]
        with patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen:
            self.start()
            current = self.wait_for(lambda run: run["status"] == "succeeded")

        self.assertEqual(popen.call_count, 5)
        self.assertEqual(current["resourceYield"]["attributionStatus"], "unavailable")

    def test_kaogu_resource_snapshot_command_collects_only_silver(self):
        command = self.manager._resource_snapshot_command("kaogu", "before")

        self.assertEqual(command[-2:], ["--fields", "silver"])

    def test_stop_during_after_capture_cancels_capture_and_spawns_nothing_else(self):
        after = BlockingProcess()
        processes = [
            snapshot_process("before", captured_at="2026-08-05T02:00:00+00:00"),
            task_process(),
            recovery_process(),
            after,
        ]

        def killpg(pid, sig):
            self.assertEqual(pid, after.pid)
            after.exit(-sig)

        with (
            patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen,
            patch("dashboard.runner.os.killpg", side_effect=killpg),
        ):
            run = self.start()
            self.wait_for(
                lambda current: current["stage"] == "正在记录任务完成后资源"
            )
            current = self.manager.stop(run["id"])

        self.assertEqual(popen.call_count, 4)
        self.assertEqual(current["status"], "cancelled")
        self.assertEqual(current["result"]["status"], "completed")
        self.assertEqual(current["resourceYield"]["attributionStatus"], "unavailable")
        self.assertIn("sanjieqiyuan", self.database.today_task_completions())


if __name__ == "__main__":
    unittest.main()
