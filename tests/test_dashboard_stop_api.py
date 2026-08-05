from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.app import create_app


class ControlledProcess:
    """A subprocess stand-in that stays alive until the test releases it."""

    _next_pid = 41000

    def __init__(self) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.stdout = self
        self.entered = threading.Event()
        self.release = threading.Event()
        self.returncode: int | None = None

    def __iter__(self):
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("fake task process was not released")
        return iter(())

    def poll(self) -> int | None:
        return self.returncode

    def wait(self) -> int:
        self.release.wait(timeout=5)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def finish(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.release.set()


class DashboardStopApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.processes: list[ControlledProcess] = []

        def create_process(*_args, **_kwargs) -> ControlledProcess:
            process = ControlledProcess()
            self.processes.append(process)
            return process

        self.popen_patch = patch("dashboard.runner.subprocess.Popen", side_effect=create_process)
        self.kill_patch = patch("dashboard.runner.os.killpg")
        self.popen_patch.start()
        self.killpg = self.kill_patch.start()
        self.app = create_app(Path(self.temp.name) / "dashboard.sqlite3")
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        for process in self.processes:
            process.finish()
        self.client_context.__exit__(None, None, None)
        self.kill_patch.stop()
        self.popen_patch.stop()
        self.temp.cleanup()

    def _start_task(self, task_id: str = "zhuogui") -> dict:
        previous_process_count = len(self.processes)
        options = {"rounds": 1} if task_id == "zhuogui" else {}
        response = self.client.post(
            "/api/runs",
            json={"taskId": task_id, "options": options, "checklistConfirmed": True},
        )
        self.assertEqual(response.status_code, 202, response.text)
        process = self._wait_for(
            lambda: self.processes[-1]
            if len(self.processes) > previous_process_count
            else None
        )
        self.assertTrue(process.entered.wait(timeout=2), "fake process did not enter its output loop")
        return response.json()

    def _request_stop_in_background(self, run_id: str):
        responses = []

        def request_stop() -> None:
            responses.append(self.client.post(f"/api/runs/{run_id}/stop"))

        thread = threading.Thread(target=request_stop, daemon=True)
        thread.start()
        self._wait_for(
            lambda: self.app.state.runner.current
            if self.app.state.runner.current
            and self.app.state.runner.current["status"] == "stopping"
            else None
        )
        return thread, responses

    def _wait_for(self, lookup, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = lookup()
            if value:
                return value
            time.sleep(0.01)
        self.fail("timed out waiting for dashboard state")

    def _run_from_history(self, run_id: str) -> dict | None:
        response = self.client.get("/api/runs")
        self.assertEqual(response.status_code, 200)
        return next((item for item in response.json()["items"] if item["id"] == run_id), None)

    def test_stop_unknown_run_returns_not_found(self) -> None:
        response = self.client.post("/api/runs/does-not-exist/stop")

        self.assertEqual(response.status_code, 404)
        self.assertIn("不存在", response.json()["detail"])

    def test_running_stop_becomes_cancelled_and_is_persisted(self) -> None:
        run = self._start_task()

        stop_thread, stop_responses = self._request_stop_in_background(run["id"])
        self.killpg.assert_called_once()

        self.processes[0].finish(returncode=-15)
        stop_thread.join(timeout=2)
        self.assertFalse(stop_thread.is_alive(), "stop endpoint did not wait for safe completion")
        self.assertEqual(stop_responses[0].status_code, 202)
        self.assertEqual(stop_responses[0].json()["status"], "cancelled")
        persisted = self._wait_for(
            lambda: (
                item
                if (item := self._run_from_history(run["id"]))
                and item["status"] == "cancelled"
                else None
            )
        )
        self.assertEqual(persisted["status"], "cancelled")
        self.assertIsNotNone(persisted["finishedAt"])
        self.assertIsNotNone(persisted["durationMs"])
        self.assertIsNone(persisted["error"])

    def test_duplicate_start_is_rejected_while_stop_is_in_progress(self) -> None:
        run = self._start_task()
        stop_thread, stop_responses = self._request_stop_in_background(run["id"])

        duplicate = self.client.post(
            "/api/runs",
            json={"taskId": "keju", "options": {}, "checklistConfirmed": True},
        )

        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("已有任务", duplicate.json()["detail"])
        self.assertEqual(len(self.processes), 1)
        self.processes[0].finish(returncode=-15)
        stop_thread.join(timeout=2)
        self.assertEqual(stop_responses[0].json()["status"], "cancelled")

    def test_next_task_can_start_after_stop_finishes(self) -> None:
        first = self._start_task()
        stop_thread, stop_responses = self._request_stop_in_background(first["id"])
        self.processes[0].finish(returncode=-15)
        stop_thread.join(timeout=2)
        self.assertEqual(stop_responses[0].json()["status"], "cancelled")
        self._wait_for(
            lambda: (
                item
                if (item := self._run_from_history(first["id"]))
                and item["status"] == "cancelled"
                else None
            )
        )

        second = self._start_task("fuli_qiandao")

        self.assertNotEqual(second["id"], first["id"])
        self.assertIn(second["status"], {"preparing", "running"})
        self.assertEqual(len(self.processes), 2)
        self.processes[1].finish()


if __name__ == "__main__":
    unittest.main()
