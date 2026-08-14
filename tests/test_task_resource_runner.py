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
        if task_id in {"baotu", "shimen"}
        else {
            "task_id": task_id,
            "status": status,
            "answers": 15,
            "clicks": 15,
            "limit": 20,
            **({"settlement_reward": settlement_reward} if settlement_reward is not None else {}),
        }
        if task_id in {"keju", "keju_huishi"}
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


def kaogu_task_process(*, cleanup_status="completed", cleanup_warning=None):
    return FinishedProcess([
        "result_json=" + json.dumps({
            "task_id": "kaogu",
            "status": "completed",
            "digs": 10,
            "target_digs": 10,
            "purchases": 1,
            "sale_clicks": 2,
            "sale_batches": 1,
            "sale_outcome": (
                "cleanup_incomplete"
                if cleanup_status == "incomplete"
                else "all_non_genuine_sold_genuine_retained"
            ),
            "genuine_protected": True,
            "cleanup_status": cleanup_status,
            "cleanup_warning": cleanup_warning,
            "actions": 18,
            "clicks": 14,
            "drags": 3,
        }, ensure_ascii=False)
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


def failed_recovery_process():
    return FinishedProcess([
        "home_recovery_json=" + json.dumps({
            "status": "failed", "outcome": None, "scene": None,
            "reason": "恢复停止：no_known_close", "actions": 4, "clicks": 2,
        }, ensure_ascii=False)
    ], return_code=1)


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

    def start_kaogu(self):
        return self.manager.start("kaogu", {}, checklist_confirmed=True)

    def start_shimen(self):
        return self.manager.start("shimen", {}, checklist_confirmed=True)

    def start_huishi(self):
        with patch("dashboard.runner.ensure_task_available"):
            return self.manager.start(
                "keju_huishi", {"waitForRoundOne": True}, checklist_confirmed=True
            )

    def start_xiangshi(self):
        with patch("dashboard.runner.ensure_task_available"):
            return self.manager.start("keju", {}, checklist_confirmed=True)

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

    def test_confirmed_shimen_recovery_records_after_resources(self):
        processes = [
            snapshot_process(
                "before", captured_at="2026-08-13T06:04:46+00:00",
                experience=152243979, gold=822658, silver=5441209,
            ),
            task_process(task_id="shimen"),
            recovery_process(),
            snapshot_process(
                "after", captured_at="2026-08-13T06:10:54+00:00",
                experience=153021599, gold=822658, silver=5446183,
            ),
        ]
        with patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen:
            self.start_shimen()
            current = self.wait_for(lambda run: run["status"] == "succeeded")

        self.assertEqual(popen.call_count, 4)
        self.assertEqual(current["homeRecovery"]["status"], "confirmed")
        self.assertEqual(current["resourceYield"]["attributionStatus"], "reliable")
        self.assertEqual(current["resourceYield"]["experienceDelta"], 777620)
        self.assertEqual(current["resourceYield"]["silverDelta"], 4974)
        self.assertEqual(current["resourceYield"]["goldDelta"], 0)

    def test_shimen_rechecks_main_read_only_then_records_after_resources(self):
        processes = [
            snapshot_process(
                "before", captured_at="2026-08-14T00:04:56+00:00",
                experience=161891345, gold=825276, silver=6568946,
            ),
            task_process(task_id="shimen"),
            failed_recovery_process(),
            recovery_process(),
            snapshot_process(
                "after", captured_at="2026-08-14T00:11:20+00:00",
                experience=162668965, gold=825276, silver=6573920,
            ),
        ]
        with patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen:
            self.start_shimen()
            current = self.wait_for(lambda run: run["status"] == "succeeded")

        self.assertEqual(popen.call_count, 5)
        self.assertIn("--confirm-only", popen.call_args_list[3].args[0])
        self.assertEqual(current["homeRecovery"]["status"], "confirmed")
        self.assertEqual(current["resourceYield"]["attributionStatus"], "reliable")
        self.assertEqual(current["resourceYield"]["experienceDelta"], 777620)
        self.assertEqual(current["resourceYield"]["silverDelta"], 4974)

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

    def test_huishi_records_confirmed_settlement_gold_without_snapshots(self):
        settlement_reward = {
            "status": "confirmed",
            "gold": 12500,
            "evidence": {
                "sourcePage": "huishi_settlement",
                "amount": {"text": "12,500", "box": [470, 490, 76, 28]},
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
        self.assertIsNone(current["resourceYield"]["experienceDelta"])
        self.assertIsNone(current["resourceYield"]["silverDelta"])
        self.assertEqual(
            current["resourceYield"]["measurementSource"], "settlement_reward"
        )
        self.assertIsNone(current["resourceYield"]["before"])
        self.assertEqual(current["homeRecovery"]["status"], "not_requested")

    def test_xiangshi_records_settlement_experience_and_silver_without_snapshots(self):
        settlement_reward = {
            "status": "confirmed", "experience": 142923, "silver": 7347,
            "evidence": {
                "sourcePage": "xiangshi_settlement",
                "experience": {"text": "142923", "box": [585, 538, 58, 22]},
                "silver": {"text": "7347", "box": [584, 564, 41, 20]},
            },
        }
        processes = [
            task_process(task_id="keju", settlement_reward=settlement_reward), recovery_process(),
        ]
        with patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen:
            self.start_xiangshi()
            current = self.wait_for(lambda run: run["status"] == "succeeded")
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(current["resourceYield"]["experienceDelta"], 142923)
        self.assertEqual(current["resourceYield"]["silverDelta"], 7347)
        self.assertIsNone(current["resourceYield"]["goldDelta"])

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

    def test_kaogu_cleanup_warning_recovers_and_records_silver_delta(self):
        processes = [
            snapshot_process(
                "before", captured_at="2026-08-05T02:00:00+00:00", silver=20000,
            ),
            kaogu_task_process(
                cleanup_status="incomplete",
                cleanup_warning="capture geometry changed during cleanup",
            ),
            recovery_process(),
            snapshot_process(
                "after", captured_at="2026-08-05T02:10:00+00:00", silver=20880,
            ),
        ]
        with patch("dashboard.runner.subprocess.Popen", side_effect=processes) as popen:
            self.start_kaogu()
            current = self.wait_for(lambda run: run["status"] == "succeeded")

        self.assertEqual(popen.call_count, 4)
        self.assertEqual(current["result"]["cleanup_status"], "incomplete")
        self.assertEqual(
            current["result"]["cleanup_warning"],
            "capture geometry changed during cleanup",
        )
        self.assertEqual(current["homeRecovery"]["status"], "confirmed")
        self.assertEqual(current["resourceYield"]["attributionStatus"], "reliable")
        self.assertEqual(current["resourceYield"]["silverDelta"], 880)
        final_event = self.database.events_after(0)[-1]
        self.assertEqual(final_event["level"], "warning")
        self.assertIn("古董整理未完整完成", final_event["message"])

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
