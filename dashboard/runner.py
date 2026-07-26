from __future__ import annotations

import json
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
INSPECTION_TIMEOUT_SECONDS = 15.0
RESULT_PREFIX = "result_json="
TASK_SUCCESS_RESULTS = {"completed", "already_completed", "limit_reached"}
TASK_RESULT_STATUSES = TASK_SUCCESS_RESULTS | {"safe_stopped", "cancelled"}
TASK_RESULT_NUMERIC_FIELDS = {
    "questions", "answers", "clicks", "limit", "rounds", "target_rounds",
    "actions", "steps", "ghost_progress", "max_steps"
}
TASK_RESULT_SCHEMAS = {
    "sanjieqiyuan": {
        "statuses": {"completed", "already_completed", "limit_reached", "safe_stopped", "cancelled"},
        "fields": {"task_id", "status", "reason", "questions", "clicks", "limit"},
        "required": {
            "completed": {"questions", "clicks"},
            "already_completed": {"questions", "clicks"},
            "limit_reached": {"questions", "clicks", "limit"},
        },
    },
    "keju": {
        "statuses": {"completed", "already_completed", "limit_reached", "safe_stopped", "cancelled"},
        "fields": {"task_id", "status", "reason", "answers", "clicks", "limit"},
        "required": {
            status: {"answers", "clicks", "limit"}
            for status in TASK_RESULT_STATUSES
        },
    },
    "zhuogui": {
        "statuses": {"completed", "already_completed", "limit_reached", "safe_stopped", "cancelled"},
        "fields": {
            "task_id", "status", "reason", "rounds", "target_rounds", "steps",
            "ghost_progress", "max_steps",
        },
        "required": {
            status: {"rounds", "target_rounds", "steps"}
            for status in TASK_RESULT_STATUSES
        },
    },
    "fuli_qiandao": {
        "statuses": {"completed", "already_completed", "safe_stopped", "cancelled"},
        "fields": {"task_id", "status", "reason", "actions", "clicks"},
        "required": {
            "completed": {"actions", "clicks"},
            "already_completed": {"actions", "clicks"},
        },
    },
    "bangpai_qiandao": {
        "statuses": {"completed", "already_completed", "safe_stopped", "cancelled"},
        "fields": {"task_id", "status", "reason", "actions", "clicks"},
        "required": {
            "completed": {"actions", "clicks"},
            "already_completed": {"actions", "clicks"},
        },
    },
}

SCENE_LABELS = {
    "answering": "三界奇缘答题页",
    "formal_question_ready": "科举正式题目页",
    "waiting_for_question": "科举等待页",
    "exam_page_without_question": "科举活动页",
    "open_welfare": "福利入口",
    "open_scratch": "每日刮刮乐入口",
    "scratch_ready": "刮奖页面",
    "confirm_reward": "签到奖励确认",
    "expand_menu": "主界面折叠菜单",
    "open_clan": "帮派入口",
    "sign_in": "帮派福利签到页",
    "battle": "战斗中",
    "auto_pathing": "自动寻路中",
    "main_screen": "游戏主界面",
    "unknown": "未识别页面",
    "completed": "任务已完成",
    "already_completed": "今日已完成",
    "no_clan": "未加入帮派",
}

ACTION_LABELS = {
    "answering": "识别当前题目后选择可靠答案",
    "formal_question_ready": "识别题目与选项后选择可靠答案",
    "open_welfare": "点击经过识别的福利入口",
    "open_scratch": "点击经过识别的刮刮乐入口",
    "scratch_ready": "在经过识别的刮奖区域内滑动",
    "confirm_reward": "确认签到奖励",
    "expand_menu": "展开主界面菜单",
    "open_clan": "打开帮派页面",
    "sign_in": "点击一次帮派签到按钮",
    "wait": "等待当前游戏状态结束",
    "open_team": "打开队伍面板",
    "create_team": "创建捉鬼队伍",
    "open_full_team_page": "打开完整队伍页面",
    "evaluate_team": "检查队伍成员状态",
    "configure_ghost_target": "配置捉鬼队伍目标",
    "choose_current_channel": "选择当前频道",
    "click_zhongkui": "前往钟馗",
    "travel_to_changan": "前往长安城",
    "accept_ghost_task": "领取捉鬼任务",
    "click_ghost_task": "继续当前捉鬼任务",
    "apply_exact_round_decision": "按设定轮数决定继续或结束",
    "claim_100_double_points": "确认领取 100 点双倍点数",
    "return_to_recruitment": "返回队伍招募",
    "close_invite_overlay": "关闭邀请成员弹窗",
    "close_known_welfare_overlay": "关闭已识别的福利弹窗",
    "remove_one_abnormal_member": "移除一个异常队员",
    "dismiss_known_popup": "关闭已识别的提示弹窗",
    "close_bag_overlay": "关闭背包遮挡",
    "open_battle_menu": "打开战斗活动菜单",
    "click_join": "点击经过识别的参加按钮",
    "click_daily": "打开日常活动列表",
    "click_activity": "打开活动页面",
    "click_activity_toolbar": "点击经过识别的活动工具栏入口",
    "click_activity_template": "点击经过模板确认的活动入口",
}


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
        self._inspecting = False
        self._completion = threading.Event()

    @property
    def current(self) -> dict | None:
        with self._lock:
            return dict(self._current) if self._current else None

    @property
    def inspecting(self) -> bool:
        with self._lock:
            return self._inspecting

    def begin_read_only(self) -> None:
        with self._lock:
            if self._inspecting:
                raise RuntimeError("已有只读采集正在进行")
            if self._current and self._current["status"] not in TERMINAL_STATES:
                raise RuntimeError("任务运行期间不能执行只读采集")
            self._inspecting = True

    def end_read_only(self) -> None:
        with self._lock:
            self._inspecting = False

    def start(self, task_id: str, options: dict | None, checklist_confirmed: bool) -> dict:
        if not checklist_confirmed:
            raise ValueError("请先确认全部运行前检查项")
        normalized = validate_options(task_id, options)
        with self._lock:
            if self._inspecting:
                raise RuntimeError("正在检测当前画面，请等待检测完成")
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

    def inspect(self, task_id: str, options: dict | None) -> dict:
        normalized = validate_options(task_id, options)
        self.begin_read_only()
        try:
            command = self._inspection_command(task_id, normalized)
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
            try:
                output, _ = process.communicate(timeout=INSPECTION_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                self._terminate_inspection_process(process)
                raise TimeoutError("画面检测超过 15 秒，已停止检测") from exc
            if process.returncode != 0:
                raise RuntimeError(
                    self._friendly_error(task_id, process.returncode, output)
                )
            return self._parse_inspection(task_id, normalized, output)
        finally:
            self.end_read_only()

    @staticmethod
    def _terminate_inspection_process(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()

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
        if task_id == "fuli_qiandao":
            return [sys.executable, str(ROOT / "tools/run_fuli_qiandao_macos.py")]
        if task_id == "bangpai_qiandao":
            return [sys.executable, str(ROOT / "tools/run_bangpai_qiandao_macos.py")]
        raise ValueError("不支持的任务")

    def _inspection_command(self, task_id: str, options: dict) -> list[str]:
        scripts = {
            "sanjieqiyuan": "run_sanjieqiyuan_macos.py",
            "keju": "run_keju_macos.py",
            "zhuogui": "run_zhuogui_macos.py",
            "fuli_qiandao": "run_fuli_qiandao_macos.py",
            "bangpai_qiandao": "run_bangpai_qiandao_macos.py",
        }
        command = [sys.executable, str(ROOT / "tools" / scripts[task_id]), "--inspect-scene"]
        if task_id == "sanjieqiyuan":
            command.extend(["--input-method", "global"])
        elif task_id == "zhuogui":
            command.extend(["--rounds", "1"])
        return command

    @staticmethod
    def _parse_inspection(task_id: str, options: dict, output: str) -> dict:
        prefix = "preflight_json="
        protocol_lines = [line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)]
        if len(protocol_lines) != 1:
            raise RuntimeError("画面检测没有返回唯一的结构化结果")
        try:
            raw = json.loads(protocol_lines[0])
        except json.JSONDecodeError as exc:
            raise RuntimeError("画面检测返回了无效的结构化结果") from exc

        if not isinstance(raw, dict):
            raise RuntimeError("画面检测返回了无效的结构化结果")
        decision = raw.get("decision")
        if not isinstance(decision, dict):
            raise RuntimeError("画面检测结果缺少有效的场景判断")
        scene = decision.get("status", "unknown")
        if not isinstance(scene, str) or not scene:
            raise RuntimeError("画面检测结果缺少有效的场景状态")
        proposed = raw.get("proposed_action")
        if proposed is not None and not isinstance(proposed, str):
            raise RuntimeError("画面检测结果包含无效的下一步动作")

        ready = False
        if task_id == "sanjieqiyuan":
            manual = options.get("navigation") == "manual"
            actionable = {
                "answering", "dismiss_known_popup", "close_bag_overlay",
                "open_battle_menu", "click_join", "click_daily", "click_activity",
                "click_activity_toolbar", "click_activity_template",
            }
            ready = scene == "answering" if manual else scene in actionable
        elif task_id == "keju":
            ready = scene == "formal_question_ready"
        elif task_id == "zhuogui":
            ready = bool(proposed and proposed != "stop")
        elif task_id == "fuli_qiandao":
            ready = scene in {"open_welfare", "open_scratch", "scratch_ready", "confirm_reward"}
        elif task_id == "bangpai_qiandao":
            ready = scene in {"expand_menu", "open_clan", "open_welfare", "sign_in"}

        action_key = proposed if proposed else scene
        if ready:
            summary = "当前画面支持启动此任务"
            reason = None
        elif scene in {"completed", "already_completed"}:
            summary = "当前画面显示今日任务已经完成"
            reason = "无需再次启动"
        elif scene == "no_clan":
            summary = "当前角色未加入帮派"
            reason = "加入帮派后再检测"
        elif scene == "waiting_for_question":
            summary = "科举尚未进入正式题目"
            reason = "等待正式题目出现后重新检测"
        else:
            summary = "当前画面不支持直接启动此任务"
            reason = "请按任务说明切换到主界面或指定页面后重新检测"

        evidence = raw.get("evidence") or {}
        if not isinstance(evidence, dict):
            raise RuntimeError("画面检测结果包含无效的识别证据")
        texts = raw.get("texts")
        templates = raw.get("templates")
        if texts is not None and not isinstance(texts, list):
            raise RuntimeError("画面检测结果包含无效的 OCR 证据")
        if templates is not None and not isinstance(templates, dict):
            raise RuntimeError("画面检测结果包含无效的模板证据")
        ocr_count = len(texts) if texts is not None else evidence.get("ocr_item_count", 0)
        matched_templates = (
            [name for name, box in templates.items() if box]
            if templates is not None
            else evidence.get("matched_templates", [])
        )
        if not isinstance(ocr_count, int) or ocr_count < 0:
            raise RuntimeError("画面检测结果包含无效的 OCR 计数")
        if not isinstance(matched_templates, list) or not all(
            isinstance(name, str) for name in matched_templates
        ):
            raise RuntimeError("画面检测结果包含无效的模板列表")

        action_label = ACTION_LABELS.get(action_key)
        if task_id == "bangpai_qiandao" and action_key == "open_welfare":
            action_label = "打开帮派福利页面"
        return {
            "taskId": task_id,
            "status": "ready" if ready else "unsupported",
            "canStart": ready,
            "scene": scene,
            "sceneLabel": SCENE_LABELS.get(scene, scene.replace("_", " ")),
            "summary": summary,
            "proposedAction": action_label,
            "reason": reason,
            "evidence": {
                "imageSize": raw.get("image_size"),
                "ocrItemCount": ocr_count,
                "matchedTemplates": matched_templates,
            },
            "observedAt": utc_now(),
        }

    @staticmethod
    def _parse_task_result(task_id: str, output_lines: list[str]) -> dict:
        """Parse the single terminal protocol emitted by every formal task run."""
        protocol_lines = [
            line[len(RESULT_PREFIX):]
            for line in output_lines
            if line.startswith(RESULT_PREFIX)
        ]
        if len(protocol_lines) != 1:
            raise RuntimeError("任务没有返回唯一的结构化最终结果")
        try:
            result = json.loads(protocol_lines[0])
        except json.JSONDecodeError as exc:
            raise RuntimeError("任务返回了无效的结构化最终结果") from exc
        if not isinstance(result, dict):
            raise RuntimeError("任务返回了无效的结构化最终结果")
        if result.get("task_id") != task_id:
            raise RuntimeError("任务最终结果的任务标识不匹配")
        schema = TASK_RESULT_SCHEMAS[task_id]
        status = result.get("status")
        if status not in schema["statuses"]:
            raise RuntimeError("任务最终结果包含未知状态")
        unknown_fields = set(result) - schema["fields"]
        if unknown_fields:
            raise RuntimeError("任务最终结果包含未声明字段")
        missing_fields = schema["required"].get(status, set()) - set(result)
        if missing_fields:
            raise RuntimeError("任务最终结果缺少必要计数")
        for field in TASK_RESULT_NUMERIC_FIELDS:
            value = result.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise RuntimeError(f"任务最终结果包含无效的 {field}")
        reason = result.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise RuntimeError("任务最终结果包含无效的停止原因")
        if status == "safe_stopped" and reason is None:
            raise RuntimeError("任务安全停止结果缺少原因")
        if (
            task_id == "zhuogui"
            and status == "completed"
            and result["rounds"] != result["target_rounds"]
        ):
            raise RuntimeError("捉鬼完成轮数与目标轮数不一致")
        return result

    @classmethod
    def _cancelled_task_result(cls, task_id: str, output_lines: list[str]) -> dict | None:
        """Keep valid cancellation progress, while allowing forced exits without a result."""
        try:
            result = cls._parse_task_result(task_id, output_lines)
        except RuntimeError:
            return None
        return result if result["status"] == "cancelled" else None

    @staticmethod
    def _task_result_message(task_id: str, result: dict | None) -> str | None:
        if result is None:
            return None
        task_name = TASKS[task_id]["name"]
        status = result.get("status")
        if status == "already_completed":
            return f"{task_name}今日已经完成"
        if status == "limit_reached":
            return {
                "sanjieqiyuan": "三界奇缘已达到本次最多答题数",
                "keju": "科举乡试已达到本次最多答题数",
                "zhuogui": "捉鬼任务已达到目标轮数",
            }.get(task_id, f"{task_name}已达到本次执行上限")
        if status == "completed":
            return {
                "sanjieqiyuan": "三界奇缘答题完成",
                "keju": "科举乡试答题完成",
                "zhuogui": "捉鬼任务已完成目标轮数",
                "fuli_qiandao": "福利签到完成",
                "bangpai_qiandao": "帮派签到完成",
            }[task_id]
        return None

    def _execute(self, run: dict, completion: threading.Event) -> None:
        error = None
        status = "failed"
        output_lines = []
        task_result = None
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
                    output_lines.append(message)
                    self._emit("info", "log", message, self._progress_payload(run["taskId"], message))
            return_code = process.wait()
            with self._lock:
                stopped = self._stop_requested
            if stopped:
                status = "cancelled"
                task_result = self._cancelled_task_result(
                    run["taskId"], output_lines
                )
            else:
                task_result = self._parse_task_result(run["taskId"], output_lines)
                result_status = task_result["status"]
                if result_status in TASK_SUCCESS_RESULTS and return_code == 0:
                    status = "succeeded"
                else:
                    status = "failed"
                    error = (
                        task_result.get("reason")
                        if result_status == "safe_stopped"
                        else self._friendly_error(
                            run["taskId"], return_code, "\n".join(output_lines)
                        )
                    )
        except Exception as exc:
            with self._lock:
                status = "cancelled" if self._stop_requested else "failed"
            error = None if status == "cancelled" else str(exc)
        finally:
            try:
                with self._lock:
                    # Once stop() accepts a request, cancellation wins over a
                    # result parsed concurrently after the subprocess exits.
                    if self._stop_requested:
                        status = "cancelled"
                        error = None
                        if not task_result or task_result.get("status") != "cancelled":
                            task_result = self._cancelled_task_result(
                                run["taskId"], output_lines
                            )
                    self._process = None
                    if self._current and self._current["id"] == run["id"]:
                        self._current["status"] = status
                        self._current["stage"] = {
                            "succeeded": "运行完成",
                            "failed": "运行失败",
                            "cancelled": "已停止",
                        }[status]
                        self._current["error"] = error
                        self._current["result"] = task_result
                        self._current["lastOperationAt"] = utc_now()
                    # Keep final persistence and publication inside the same state
                    # transition, so start() cannot replace the run in between.
                    self.database.update_run(run["id"], status, error, task_result)
                    level = "error" if status == "failed" else (
                        "warning" if status == "cancelled" else "info"
                    )
                    message = error or self._task_result_message(
                        run["taskId"], task_result
                    ) or {
                        "succeeded": "任务运行完成",
                        "cancelled": "任务已安全停止",
                    }.get(status, "任务运行失败")
                    self._emit(
                        level,
                        "status",
                        message,
                        {
                            "status": status,
                            "error": error,
                            "result": task_result,
                            "lastOperationAt": run.get("lastOperationAt"),
                        },
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
        elif task_id == "fuli_qiandao" and '"event": "scene"' in line:
            payload["stage"] = "正在识别福利签到页面"
        elif task_id == "fuli_qiandao" and line.startswith(RESULT_PREFIX):
            payload["stage"] = RunnerManager._sign_in_result_stage(line, clan=False)
        elif task_id == "bangpai_qiandao" and '"event": "scene"' in line:
            payload["stage"] = "正在识别帮派签到页面"
        elif task_id == "bangpai_qiandao" and line.startswith(RESULT_PREFIX):
            payload["stage"] = RunnerManager._sign_in_result_stage(line, clan=True)
        return payload

    @staticmethod
    def _sign_in_result_stage(line: str, *, clan: bool) -> str:
        prefix = "帮派签到" if clan else "签到"
        try:
            status = json.loads(line[len(RESULT_PREFIX):]).get("status")
        except (json.JSONDecodeError, AttributeError):
            return f"{prefix}流程正在结束"
        return {
            "completed": f"{prefix}流程完成",
            "already_completed": f"{prefix}今日已完成",
            "safe_stopped": f"{prefix}流程已安全停止",
            "cancelled": f"{prefix}流程已取消",
        }.get(status, f"{prefix}流程正在结束")

    @staticmethod
    def _friendly_error(task_id: str, return_code: int, output: str = "") -> str:
        if task_id == "sanjieqiyuan" and any(
            marker in output
            for marker in ("screencap failed", "截图失败", "截图为空", "Failed to get cached image")
        ):
            return "游戏窗口截图失败，请确认梦幻西游窗口未最小化，并检查控制台的屏幕录制权限。"
        hints = {
            "sanjieqiyuan": "三界奇缘未能继续，请确认活动可参与且当前页面符合所选进入方式。",
            "keju": "科举答题已停止，请确认正式题目页面可见并检查模型或本地题库配置。",
            "zhuogui": "捉鬼流程已停止，请检查组队状态、弹窗和当前游戏页面。",
            "fuli_qiandao": "福利签到未能继续，请保持主界面或福利页面完整可见后重试。",
            "bangpai_qiandao": "帮派签到未能继续，请确认角色已加入帮派并保持相关页面完整可见。",
        }
        return f"{hints[task_id]}（进程代码 {return_code}）"
