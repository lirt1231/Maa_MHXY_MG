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


class _BlockingOutput:
    def __init__(self, exited: threading.Event):
        self._exited = exited

    def __iter__(self):
        return self

    def __next__(self):
        self._exited.wait()
        raise StopIteration


class _FakeProcess:
    def __init__(self, pid: int = 43210):
        self.pid = pid
        self.exited = threading.Event()
        self.return_code = 0
        self.stdout = _BlockingOutput(self.exited)

    def poll(self):
        return self.return_code if self.exited.is_set() else None

    def wait(self):
        self.exited.wait()
        return self.return_code

    def exit(self, return_code: int = -signal.SIGTERM):
        self.return_code = return_code
        self.exited.set()


class RunnerStopBarrierTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "dashboard.sqlite3")
        self.manager = RunnerManager(
            self.database,
            EventBroker(),
            stop_term_timeout=0.03,
            stop_kill_timeout=0.2,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _start(self):
        return self.manager.start("zhuogui", {"rounds": 1}, checklist_confirmed=True)

    def _wait_for_status(self, expected: str, timeout: float = 1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.manager.current
            if current and current["status"] == expected:
                return current
            time.sleep(0.002)
        self.fail(f"runner did not reach {expected!r}; current={self.manager.current!r}")

    def test_stop_before_spawn_prevents_process_from_starting(self):
        command_entered = threading.Event()
        release_command = threading.Event()

        def delayed_command(*_args):
            command_entered.set()
            release_command.wait()
            return ["unused"]

        self.manager._command = delayed_command
        with patch("dashboard.runner.subprocess.Popen") as popen:
            run = self._start()
            self.assertTrue(command_entered.wait(1))
            result = {}
            stop_thread = threading.Thread(
                target=lambda: result.update(self.manager.stop(run["id"])),
                daemon=True,
            )
            stop_thread.start()
            self._wait_for_status("stopping")
            release_command.set()
            stop_thread.join(1)

        self.assertFalse(stop_thread.is_alive())
        self.assertEqual(result["status"], "cancelled")
        popen.assert_not_called()
        self.assertEqual(self.database.recent_runs(1)[0]["status"], "cancelled")

    def test_stop_waits_for_sigterm_exit_and_cancelled_persistence(self):
        process = _FakeProcess()
        signals = []

        def killpg(pid, sig):
            signals.append((pid, sig))
            process.exit(-sig)

        with (
            patch("dashboard.runner.subprocess.Popen", return_value=process),
            patch("dashboard.runner.os.killpg", side_effect=killpg),
        ):
            run = self._start()
            self._wait_for_status("running")
            stopped = self.manager.stop(run["id"])

        self.assertEqual(signals, [(process.pid, signal.SIGTERM)])
        self.assertEqual(stopped["status"], "cancelled")
        self.assertEqual(stopped["stage"], "已停止")
        self.assertIn("lastOperationAt", stopped)
        self.assertEqual(self.database.recent_runs(1)[0]["status"], "cancelled")

    def test_stop_escalates_to_sigkill_after_term_timeout(self):
        process = _FakeProcess()
        signals = []

        def killpg(pid, sig):
            signals.append((pid, sig))
            if sig == signal.SIGKILL:
                process.exit(-sig)

        with (
            patch("dashboard.runner.subprocess.Popen", return_value=process),
            patch("dashboard.runner.os.killpg", side_effect=killpg),
        ):
            run = self._start()
            self._wait_for_status("running")
            stopped = self.manager.stop(run["id"])

        self.assertEqual(
            signals,
            [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)],
        )
        self.assertEqual(stopped["status"], "cancelled")
        events = self.database.events_after(0)
        kill_events = [event for event in events if event["payload"].get("phase") == "kill"]
        self.assertEqual(len(kill_events), 1)
        self.assertEqual(kill_events[0]["payload"]["signal"], "SIGKILL")


if __name__ == "__main__":
    unittest.main()
