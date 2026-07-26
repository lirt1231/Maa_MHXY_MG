from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Callable

from .database import Database, utc_now
from .events import EventBroker
from .registry import TASKS, validate_options


ROOT = Path(__file__).resolve().parents[1]
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
STOP_TERM_TIMEOUT_SECONDS = 5.0
STOP_KILL_TIMEOUT_SECONDS = 2.0


class RunnerManager:
    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        *,
        stop_term_timeout: float = STOP_TERM_TIMEOUT_SECONDS,
        stop_kill_timeout: float = STOP_KILL_TIMEOUT_SECONDS,
    ):
        self.database = database
        self.broker = broker
        self._stop_term_timeout = stop_term_timeout
        self._stop_kill_timeout = stop_kill_timeout
        self._lock = threading.RLock()
        self._current: dict | None = None
        self._process: subprocess.Popen | None = None
        self._stop_requested = False
        self._completion = threading.Event()

    @property
    def current(self) -> dict | None:
        with self._lock:
            return dict(self._current) if self._current else None

    def start(self, task_id: str, options: dict | None, checklist_confirmed: bool) -> dict:
        if not checklist_confirmed:
            raise ValueError("请先确认全部运行前检查项")
        normalized = validate_options(task_id, options)
        with self._lock:
            if self._current and self._current["status"] not in TERMINAL_STATES:
                raise RuntimeError("已有任务正在运行")
            task = TASKS[task_id]
            run = {
                "id": uuid.uuid4().hex,
                "taskId": task_id,
                "taskName": task["name"],
                "status": "preparing",
                "stage": "准备运行环境",
                "options": normalized,
                "startedAt": utc_now(),
                "benefitStatus": "not_captured",
            }
            self._current = run
            self._stop_requested = False
            completion = threading.Event()
            self._completion = completion
            self.database.create_run(run)
            self._emit("info", "status", f"正在准备{task['name']}", {"status": "preparing"})
            threading.Thread(
                target=self._execute,
                args=(run, completion),
                daemon=True,
                name=f"dashboard-{task_id}",
            ).start()
            return dict(run)

    def stop(self, run_id: str) -> dict:
        with self._lock:
            if not self._current or self._current["id"] != run_id:
                raise LookupError("任务不存在或已经结束")
            if self._current["status"] in TERMINAL_STATES:
                return dict(self._current)
            target_run = self._current
            completion = self._completion
            already_stopping = target_run["status"] == "stopping"
            process = None
            if not already_stopping:
                self._stop_requested = True
                target_run["status"] = "stopping"
                target_run["stage"] = "正在发送停止信号"
                target_run["lastOperationAt"] = utc_now()
                process = self._process
                self._emit(
                    "warning",
                    "status",
                    "收到停止请求，正在停止自动化",
                    {
                        "status": "stopping",
                        "phase": "requested",
                        "lastOperationAt": target_run["lastOperationAt"],
                    },
                )
        if already_stopping:
            if not completion.wait(self._stop_term_timeout + self._stop_kill_timeout):
                raise RuntimeError("自动化进程仍在停止中")
            return dict(target_run)
        if process and process.poll() is None:
            self._signal_process(target_run, process, signal.SIGTERM, "term")

        if not completion.wait(self._stop_term_timeout):
            with self._lock:
                process = self._process
            if process and process.poll() is None:
                self._signal_process(target_run, process, signal.SIGKILL, "kill")
            if not completion.wait(self._stop_kill_timeout):
                raise RuntimeError("自动化进程在强制停止后仍未退出")

        # The worker sets completion only after the process has exited and the
        # final cancelled state has been persisted.
        return dict(target_run)

    def _signal_process(
        self,
        run: dict,
        process: subprocess.Popen,
        sig: signal.Signals,
        phase: str,
    ) -> None:
        operation_at = utc_now()
        with self._lock:
            if self._current is run:
                run["stage"] = "正在等待进程退出" if phase == "term" else "正在强制结束进程"
                run["lastOperationAt"] = operation_at
                self._emit(
                    "warning",
                    "status",
                    "已发送安全停止信号" if phase == "term" else "安全停止超时，已发送强制停止信号",
                    {
                        "status": "stopping",
                        "phase": phase,
                        "signal": sig.name,
                        "lastOperationAt": operation_at,
                    },
                )
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            # Exiting between poll() and killpg() is a successful stop race.
            pass

    def _command(self, task_id: str, options: dict) -> list[str]:
        if task_id == "sanjieqiyuan":
            command = [sys.executable, str(ROOT / "tools/run_sanjieqiyuan_macos.py"), "--max-steps", str(options["maxSteps"]), "--input-method", "native"]
            if options["navigation"] == "auto":
                command.append("--navigate")
            return command
        if task_id == "keju":
            command = [sys.executable, str(ROOT / "tools/run_keju_macos.py"), "--max-steps", str(options["maxSteps"])]
            if options["answerSource"] == "codex":
                command.append("--use-codex")
            if options["waitForRoundOne"]:
                command.append("--wait-for-round-one")
            return command
        if task_id == "zhuogui":
            return [sys.executable, str(ROOT / "tools/run_zhuogui_macos.py"), "--rounds", str(options["rounds"])]
        raise ValueError("不支持的任务")

    def _execute(self, run: dict, completion: threading.Event) -> None:
        error = None
        status = "failed"
        try:
            command = self._command(run["taskId"], run["options"])
            with self._lock:
                if self._stop_requested:
                    status = "cancelled"
                    return
                self._current["status"] = "running"
                self._current["stage"] = "自动化运行中"
                self._emit("info", "status", f"{run['taskName']}已开始", {"status": "running"})
                self._process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    start_new_session=True,
                )
                process = self._process
            assert process.stdout is not None
            for line in process.stdout:
                message = line.strip()
                if message:
                    self._emit("info", "log", message, self._progress_payload(run["taskId"], message))
            return_code = process.wait()
            with self._lock:
                status = "cancelled" if self._stop_requested else ("succeeded" if return_code == 0 else "failed")
            if return_code != 0 and status != "cancelled":
                error = self._friendly_error(run["taskId"], return_code)
        except Exception as exc:
            with self._lock:
                status = "cancelled" if self._stop_requested else "failed"
            error = None if status == "cancelled" else str(exc)
        finally:
            try:
                with self._lock:
                    self._process = None
                    if self._current and self._current["id"] == run["id"]:
                        self._current["status"] = status
                        self._current["stage"] = {
                            "succeeded": "运行完成",
                            "failed": "运行失败",
                            "cancelled": "已停止",
                        }[status]
                        self._current["error"] = error
                        self._current["lastOperationAt"] = utc_now()
                    # Keep final persistence and publication inside the same state
                    # transition, so start() cannot replace the run in between.
                    self.database.update_run(run["id"], status, error)
                    level = "error" if status == "failed" else (
                        "warning" if status == "cancelled" else "info"
                    )
                    message = error or {
                        "succeeded": "任务运行完成",
                        "cancelled": "任务已安全停止",
                    }.get(status, "任务运行失败")
                    self._emit(
                        level,
                        "status",
                        message,
                        {"status": status, "error": error, "lastOperationAt": run.get("lastOperationAt")},
                    )
            finally:
                completion.set()

    def _emit(self, level: str, event_type: str, message: str, payload: dict | None = None) -> None:
        run_id = self._current["id"] if self._current else "system"
        event = self.database.add_event(run_id, level, event_type, message, payload) if run_id != "system" else {
            "runId": run_id, "createdAt": utc_now(), "level": level, "type": event_type, "message": message, "payload": payload or {}
        }
        self.broker.publish(event)

    @staticmethod
    def _progress_payload(task_id: str, line: str) -> dict:
        payload = {"taskId": task_id}
        if "step=" in line:
            payload["stage"] = "正在识别并处理题目"
        elif "round_complete" in line:
            payload["stage"] = "完成一轮捉鬼"
        elif "navigation" in line or "entry_status" in line:
            payload["stage"] = "正在进入活动"
        return payload

    @staticmethod
    def _friendly_error(task_id: str, return_code: int) -> str:
        hints = {
            "sanjieqiyuan": "三界奇缘未能继续，请确认活动可参与且当前页面符合所选进入方式。",
            "keju": "科举答题已停止，请确认正式题目页面可见并检查模型或本地题库配置。",
            "zhuogui": "捉鬼流程已停止，请检查组队状态、弹窗和当前游戏页面。",
        }
        return f"{hints[task_id]}（进程代码 {return_code}）"
