from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Callable

from tools.resource_snapshots import (
    ResourceFieldEvidence,
    ResourceSnapshot,
    ResourceValues,
    ResourceYield,
    YIELD_FIELDS,
    calculate_resource_yield,
)

from .database import Database, utc_now
from .events import EventBroker
from .registry import TASKS, ensure_task_available, validate_options
from .user_progress import user_progress


ROOT = Path(__file__).resolve().parents[1]
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
STOP_TERM_TIMEOUT_SECONDS = 5.0
STOP_KILL_TIMEOUT_SECONDS = 2.0
INSPECTION_TIMEOUT_SECONDS = 15.0
RESULT_PREFIX = "result_json="
HOME_RECOVERY_PREFIX = "home_recovery_json="
RESOURCE_SNAPSHOT_PREFIX = "resource_snapshot_json="
MANUAL_REVIEW_PREFIX = "manual_review_request_json="
BEFORE_RESOURCE_CAPTURE_ATTEMPTS = 2
RESOURCE_CAPTURE_FIELDS_BY_TASK = {
    "kaogu": frozenset({"silver"}),
}
RESOURCE_YIELD_FIELDS_BY_TASK = {
    "kaogu": frozenset({"silver"}),
}
# 宝图领取后、趣闻点赞时均没有可归因的角色资源变化；会试单独从结算页读取金币。
# 跳过边界快照与收益记录，而不是将未发生的变化写成 0。
RESOURCE_TRACKING_DISABLED_TASKS = frozenset({"baotu", "fun_news", "keju", "keju_huishi"})
# 会试的最终结算页是金币收益的唯一可信来源，必须保留给结算识别与用户复核。
TASKS_RETAIN_RESULT_SCREEN = frozenset({"keju_huishi"})
TASK_SUCCESS_RESULTS = {"completed", "already_completed"}
TASK_RESULT_STATUSES = TASK_SUCCESS_RESULTS | {"safe_stopped", "cancelled"}
TASK_RESULT_NUMERIC_FIELDS = {
    "questions", "answers", "clicks", "limit", "rounds", "target_rounds",
    "actions", "steps", "ghost_progress", "max_steps", "battles", "max_battles",
    "digs", "target_digs", "purchases", "sale_clicks", "sale_batches", "likes",
    "scrolls", "drags",
    "confirmationPolls",
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
        "fields": {"task_id", "status", "reason", "answers", "clicks", "limit", "settlement_reward"},
        "required": {
            status: {"answers", "clicks", "limit"}
            for status in TASK_RESULT_STATUSES
        },
    },
    "keju_huishi": {
        "statuses": {
            "completed", "already_completed", "limit_reached", "safe_stopped",
            "cancelled",
        },
        "fields": {
            "task_id", "status", "reason", "answers", "clicks", "limit",
            "settlement_reward",
        },
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
    "baotu": {
        "statuses": {"completed", "already_completed", "safe_stopped", "cancelled"},
        "fields": {
            "task_id", "status", "reason", "actions", "clicks",
            "completionEvidence", "confirmationPolls",
        },
        "required": {
            "completed": {"actions", "clicks"},
            "already_completed": {"actions", "clicks"},
        },
    },
    "fun_news": {
        "statuses": {"completed", "already_completed", "safe_stopped", "cancelled"},
        "fields": {"task_id", "status", "reason", "likes", "scrolls", "actions", "clicks"},
        "required": {
            status: {"likes", "scrolls", "actions", "clicks"}
            for status in {"completed", "already_completed"}
        },
    },
    "yunbiao": {
        "statuses": {"completed", "already_completed", "safe_stopped", "cancelled"},
        "fields": {
            "task_id", "status", "reason", "rounds", "target_rounds", "actions", "clicks",
        },
        "required": {
            status: {"rounds", "target_rounds", "actions", "clicks"}
            for status in {"completed", "already_completed"}
        },
    },
    "shimen": {
        "statuses": {"completed", "already_completed", "safe_stopped", "cancelled"},
        "fields": {
            "task_id", "status", "reason", "actions", "clicks",
            "completionEvidence", "confirmationPolls",
        },
        "required": {
            "completed": {"actions", "clicks"},
            "already_completed": {"actions", "clicks"},
        },
    },
    "mijingxiangyao": {
        "statuses": {
            "completed", "already_completed", "limit_reached", "safe_stopped",
            "cancelled",
        },
        "fields": {
            "task_id", "status", "reason", "battles", "actions", "clicks",
            "max_battles",
        },
        "required": {
            "completed": {"battles", "actions", "clicks", "max_battles"},
            "already_completed": {"battles", "actions", "clicks", "max_battles"},
            "limit_reached": {"battles", "actions", "clicks", "max_battles"},
        },
    },
    "kaogu": {
        "statuses": {"completed", "already_completed", "safe_stopped", "cancelled"},
        "fields": {
            "task_id", "status", "reason", "digs", "target_digs", "purchases",
            "sale_clicks", "sale_batches", "sale_outcome", "genuine_protected",
            "cleanup_status", "cleanup_warning", "actions", "clicks", "drags",
        },
        "required": {
            status: {
                "digs", "target_digs", "purchases", "sale_clicks",
                "sale_outcome", "genuine_protected", "actions", "clicks",
                "cleanup_status", "cleanup_warning",
            }
            for status in {"completed", "already_completed"}
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
    "claim_dividend": "可领取帮派分红",
    "confirm_dividend": "帮派分红确认页",
    "battle": "战斗中",
    "auto_pathing": "自动寻路中",
    "main_screen": "游戏主界面",
    "unknown": "未识别页面",
    "completed": "任务已完成",
    "already_completed": "今日已完成",
    "scroll_daily": "日常活动列表",
    "no_clan": "未加入帮派",
    "open_activity": "活动入口",
    "open_daily": "日常活动入口",
    "join_task": "日常任务卡片",
    "like": "趣闻动态点赞控件",
    "refresh_feed": "趣闻动态列表",
    "accept_dialogue": "宝图任务领取对话",
    "accept_task_dialogue": "师门任务领取对话",
    "start_task": "师门任务待启动",
    "continue_task": "当前任务栏",
    "select_normal": "普通镖领取对话",
    "confirm_normal": "普通镖确认页",
    "delivery_active": "普通运镖进行中",
    "blocked_resource": "普通运镖条件不足",
    "unsupported_delivery": "非普通镖选项",
    "progress_task": "师门任务推进页",
    "unsupported_asset_branch": "资产操作分支",
    "select_mode": "秘境模式选择页",
    "confirm_mode": "秘境模式确认页",
    "start_challenge": "秘境挑战页",
    "select_stage": "秘境关卡列表",
    "enter_battle": "秘境战斗确认页",
    "challenge_failed": "秘境挑战失败页",
    "mode_selection_required": "秘境模式选择页",
    "enter_dungeon": "当前秘境入口",
    "open_skills": "技能入口",
    "open_auxiliary": "辅助技能页签",
    "open_archaeology": "工坊考古入口",
    "open_production": "技能生产页签",
    "switch_tier": "考古阶数切换",
    "open_tool_selection": "考古工具选择入口",
    "open_shovel_shop": "洛阳铲购买入口",
    "buy_shovel": "洛阳铲购买确认页",
    "close_shop": "杂货铺关闭按钮",
    "select_shovel": "洛阳铲工具",
    "use_shovel": "洛阳铲使用按钮",
    "start_archaeology": "考古开始按钮",
    "dig": "考古挖掘按钮",
    "close_temporary_bag": "临时道具栏",
    "wait_for_dig": "等待下一次挖掘",
    "manual_tool_required": "未检测到已选工具",
    "open_inventory_for_sale": "准备出售非真品古董",
    "normalize_inventory": "包裹物品列表归顶",
    "organize_inventory": "包裹整理按钮",
    "scan_inventory": "浏览包裹中的非真品古董",
    "activate_antique": "包裹中的非真品古董",
    "use_antique": "古董使用按钮",
    "wait_for_sale_pathing": "前往古董商途中",
    "sell_antique": "非真品古董出售按钮",
    "sale_candidate_absent": "古董商待售列表复核",
    "cancel_genuine_sale": "真品出售确认页",
    "antique_sale_finished": "非真品古董已售完",
    "close_sale_shop": "古董商页面关闭按钮",
    "antique_sale_main_confirmed": "售卖后主界面",
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
    "claim_dividend": "领取当前可用的帮派分红",
    "confirm_dividend": "确认领取帮派分红",
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
    "scroll_daily": "向下浏览活动列表",
    "click_activity": "打开活动页面",
    "click_activity_toolbar": "点击经过识别的活动工具栏入口",
    "click_activity_template": "点击经过模板确认的活动入口",
    "open_activity": "打开活动页面",
    "open_daily": "打开日常活动列表",
    "join_task": "点击同一卡片内的任务参加按钮",
    "like": "点击经过模板确认的心形点赞控件",
    "refresh_feed": "下滑加载趣闻动态",
    "accept_dialogue": "选择经过识别的“听听无妨”",
    "accept_task_dialogue": "选择经过识别的师门 NPC 任务",
    "start_task": "点击右侧师门任务正式开始",
    "continue_task": "点击经过识别的任务栏",
    "select_normal": "选择经过识别的“押送普通镖银”",
    "confirm_normal": "确认押送普通镖银",
    "delivery_active": "等待本轮普通运镖结束",
    "progress_task": "点击经过识别的“去完成”或“继续任务”",
    "start_challenge": "点击经过识别的继续挑战按钮",
    "select_stage": "选择经过识别的当前可挑战关卡",
    "enter_battle": "点击经过识别的进入战斗按钮",
    "enter_dungeon": "进入游戏当前已选择的秘境模式",
    "open_skills": "打开技能页面",
    "open_auxiliary": "切换到辅助技能",
    "open_archaeology": "进入工坊考古",
    "open_production": "切换到技能生产",
    "switch_tier": "切换到较低考古阶数",
    "open_tool_selection": "打开考古工具选择",
    "open_shovel_shop": "打开洛阳铲购买页",
    "buy_shovel": "购买一个洛阳铲",
    "close_shop": "关闭杂货铺",
    "select_shovel": "选择洛阳铲",
    "use_shovel": "使用洛阳铲",
    "start_archaeology": "开始考古",
    "dig": "执行本次挖掘",
    "close_temporary_bag": "关闭临时道具栏",
    "open_inventory_for_sale": "打开包裹查找非真品古董",
    "normalize_inventory": "将包裹物品列表下拉到顶部",
    "organize_inventory": "整理包裹中的考古物品",
    "scan_inventory": "继续浏览包裹中的非真品古董",
    "activate_antique": "双击经过模板确认的非真品古董",
    "use_antique": "使用非真品古董并启动自动寻路",
    "sell_antique": "出售当前非真品古董",
    "cancel_genuine_sale": "取消真品出售并保留真品",
    "close_sale_shop": "关闭古董商页面",
}


class RunnerManager:
    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        *,
        stop_term_timeout: float = STOP_TERM_TIMEOUT_SECONDS,
        stop_kill_timeout: float = STOP_KILL_TIMEOUT_SECONDS,
        resource_tracking: bool = False,
    ):
        self.database = database
        self.broker = broker
        self._stop_term_timeout = stop_term_timeout
        self._stop_kill_timeout = stop_kill_timeout
        self._resource_tracking = resource_tracking
        self._lock = threading.RLock()
        self._current: dict | None = None
        self._process: subprocess.Popen | None = None
        self._stop_requested = False
        self._inspecting = False
        self._completion = threading.Event()
        self._manual_review: dict | None = None

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
            ensure_task_available(task_id)
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
                "resourceYield": None,
                "homeRecovery": {"status": "not_requested"},
                "failurePhase": None,
            }
            self._current = run
            self._stop_requested = False
            self._manual_review = None
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

    def submit_manual_review(self, run_id: str, request_id: str, answer: str) -> dict:
        answer = str(answer or "").upper()
        with self._lock:
            current = self._current
            pending = self._manual_review
            if (
                not current or current["id"] != run_id or current["taskId"] != "keju"
                or current["status"] != "running" or not pending
            ):
                raise RuntimeError("当前没有可提交的乡试人工复核")
            if pending.get("requestId") != request_id or answer not in {"A", "B", "C", "D"}:
                raise ValueError("人工复核请求已过期或选项无效")
            process = self._process
            if process is None or process.stdin is None:
                raise RuntimeError("人工复核输入通道不可用")
            process.stdin.write(json.dumps({"requestId": request_id, "answer": answer}) + "\n")
            process.stdin.flush()
            self._manual_review = None
            current["stage"] = "正在复核人工选择"
            self.database.update_run_stage(run_id, "running", current["stage"])
            self._emit("info", "manual_review", "已提交人工选择，正在复核当前题面", {
                "requestId": request_id, "answer": answer, "status": "submitted",
            })
            return {"runId": run_id, "requestId": request_id, "answer": answer, "status": "submitted"}

    def _handle_manual_review_request(self, run: dict, line: str) -> bool:
        if run["taskId"] != "keju" or not line.startswith(MANUAL_REVIEW_PREFIX):
            return False
        try:
            payload = json.loads(line[len(MANUAL_REVIEW_PREFIX):])
        except json.JSONDecodeError as exc:
            raise RuntimeError("乡试人工复核请求不是有效 JSON") from exc
        required = {"requestId", "question", "options", "review"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise RuntimeError("乡试人工复核请求字段无效")
        if not isinstance(payload["requestId"], str) or not payload["requestId"]:
            raise RuntimeError("乡试人工复核请求编号无效")
        if not isinstance(payload["question"], str) or not isinstance(payload["options"], dict):
            raise RuntimeError("乡试人工复核题面无效")
        if not {"A", "B"}.issubset(payload["options"]) or not set(payload["options"]).issubset({"A", "B", "C", "D"}) or not all(
            isinstance(value, str) and value for value in payload["options"].values()
        ):
            raise RuntimeError("乡试人工复核选项不完整")
        if not isinstance(payload["review"], dict):
            raise RuntimeError("乡试人工复核材料无效")
        with self._lock:
            if self._stop_requested:
                return True
            self._manual_review = payload
            run["stage"] = "等待人工选择乡试答案"
            self.database.update_run_stage(run["id"], "running", run["stage"])
            self._emit("warning", "manual_review", "乡试需要人工选择答案", payload)
        return True

    def inspect(self, task_id: str, options: dict | None) -> dict:
        normalized = validate_options(task_id, options)
        self.begin_read_only()
        try:
            ensure_task_available(task_id)
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
            return [
                sys.executable,
                str(ROOT / "tools/run_sanjieqiyuan_macos.py"),
                "--max-steps",
                "10",
                "--input-method",
                "native",
                "--navigate",
            ]
        if task_id in {"keju", "keju_huishi"}:
            exam_type = "xiangshi" if task_id == "keju" else "huishi"
            command = [
                sys.executable,
                str(ROOT / "tools/run_keju_macos.py"),
                "--exam-type",
                exam_type,
                "--max-steps",
                "10" if task_id == "keju" else "20",
            ]
            if options.get("waitForRoundOne", False):
                command.append("--wait-for-round-one")
            if task_id == "keju":
                command.append("--navigate-from-main")
            else:
                command.append("--wait-for-entry")
            return command
        if task_id == "zhuogui":
            return [
                sys.executable,
                str(ROOT / "tools/run_zhuogui_macos.py"),
                "--rounds",
                str(options["rounds"]),
                "--level-range",
                str(options["levelRange"]),
                "--formation",
                str(options["formation"]),
            ]
        if task_id == "fuli_qiandao":
            return [sys.executable, str(ROOT / "tools/run_fuli_qiandao_macos.py")]
        if task_id == "bangpai_qiandao":
            return [sys.executable, str(ROOT / "tools/run_bangpai_qiandao_macos.py")]
        if task_id == "baotu":
            return [sys.executable, str(ROOT / "tools/run_baotu_macos.py")]
        if task_id == "fun_news":
            return [sys.executable, str(ROOT / "tools/run_fun_news_macos.py")]
        if task_id == "yunbiao":
            return [
                sys.executable,
                str(ROOT / "tools/run_yunbiao_macos.py"),
                "--rounds",
                "3",
            ]
        if task_id == "shimen":
            return [sys.executable, str(ROOT / "tools/run_shimen_macos.py")]
        if task_id == "mijingxiangyao":
            return [sys.executable, str(ROOT / "tools/run_mijingxiangyao_macos.py")]
        if task_id == "kaogu":
            return [
                sys.executable,
                str(ROOT / "tools/run_kaogu_macos.py"),
                "--tool-mode",
                str(options.get("toolMode", "selected")),
            ]
        raise ValueError("不支持的任务")

    @staticmethod
    def _recovery_command(task_id: str) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "tools/run_return_to_main_macos.py"),
            "--task-id",
            task_id,
        ]

    @staticmethod
    def _resource_snapshot_command(task_id: str, phase: str) -> list[str]:
        fields = RESOURCE_CAPTURE_FIELDS_BY_TASK.get(task_id)
        command = [
            sys.executable,
            str(ROOT / "tools/run_resource_snapshot_macos.py"),
            "--phase",
            phase,
        ]
        if fields is not None:
            command.extend(("--fields", ",".join(sorted(fields))))
        return command

    def _tracks_task_resources(self, task_id: str) -> bool:
        return self._resource_tracking and task_id not in RESOURCE_TRACKING_DISABLED_TASKS

    @staticmethod
    def _parse_resource_snapshot(
        output_lines: list[str], expected_phase: str
    ) -> ResourceSnapshot:
        protocol = [
            line[len(RESOURCE_SNAPSHOT_PREFIX):]
            for line in output_lines
            if line.startswith(RESOURCE_SNAPSHOT_PREFIX)
        ]
        if len(protocol) != 1:
            raise RuntimeError("资源采集进程没有返回唯一的结构化结果")
        try:
            payload = json.loads(protocol[0])
        except json.JSONDecodeError as exc:
            raise RuntimeError("资源采集进程返回了无效结果") from exc
        if not isinstance(payload, dict) or payload.get("phase") != expected_phase:
            raise RuntimeError("资源采集结果阶段不匹配")
        values = payload.get("values")
        evidence_payload = payload.get("evidence", {})
        if not isinstance(values, dict) or not isinstance(evidence_payload, dict):
            raise RuntimeError("资源采集结果缺少字段数据")
        evidence = {}
        try:
            for name, item in evidence_payload.items():
                if not isinstance(item, dict):
                    raise ValueError("资源字段证据无效")
                evidence[name] = ResourceFieldEvidence(
                    status=item.get("status"),
                    source_page=item.get("sourcePage"),
                    raw_text=item.get("rawText"),
                    confidence=item.get("confidence"),
                    box=None if item.get("box") is None else tuple(item["box"]),
                    frame_id=item.get("frameId"),
                )
            return ResourceSnapshot(
                target_id=payload.get("targetId"),
                character_id=payload.get("characterId"),
                phase=payload.get("phase"),
                captured_at=payload.get("capturedAt"),
                values=ResourceValues(
                    level=values.get("level"),
                    experience=values.get("experience"),
                    experience_required=values.get("experienceRequired"),
                    gold=values.get("gold"),
                    silver=values.get("silver"),
                ),
                evidence=evidence,
                status=payload.get("status"),
                error=payload.get("error"),
                captured_fields=frozenset(payload.get("capturedFields") or {
                    "level", "experience", "experience_required", "gold", "silver"
                }),
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError("资源采集结果不符合快照契约") from exc

    @staticmethod
    def _failed_resource_snapshot(
        phase: str, reason: str, *, captured_fields: frozenset[str]
    ) -> ResourceSnapshot:
        return ResourceSnapshot(
            target_id="local-primary",
            phase=phase,
            captured_at=utc_now(),
            values=ResourceValues(),
            error=reason,
            captured_fields=captured_fields,
        )

    def _capture_resource_snapshot(
        self, run: dict, phase: str, *, attempt: int = 1
    ) -> ResourceSnapshot | None:
        label = "任务前" if phase == "before" else "任务完成后"
        captured_fields = RESOURCE_CAPTURE_FIELDS_BY_TASK.get(
            run["taskId"],
            frozenset({"level", "experience", "experience_required", "gold", "silver"}),
        )
        output_lines: list[str] = []
        process = None
        try:
            with self._lock:
                if self._stop_requested:
                    return None
                stage = f"正在记录{label}资源"
                run["stage"] = stage
                if self._current and self._current["id"] == run["id"]:
                    self._current["stage"] = stage
                    self.database.update_run_stage(
                        run["id"], self._current["status"], stage,
                        result=run.get("result"),
                        home_recovery=run.get("homeRecovery"),
                        failure_phase=run.get("failurePhase"),
                    )
                self._emit(
                    "info", "resource", f"正在记录{label}的金币、银币和经验",
                {"phase": phase, "status": "running", "attempt": attempt},
                )
                self._process = subprocess.Popen(
                    self._resource_snapshot_command(run["taskId"], phase),
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
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
            return_code = process.wait()
            with self._lock:
                stopped = self._stop_requested
                if self._process is process:
                    self._process = None
            if stopped:
                return None
            if return_code != 0:
                raise RuntimeError(f"资源采集进程异常退出（{return_code}）")
            snapshot = self._parse_resource_snapshot(output_lines, phase)
        except Exception as exc:
            with self._lock:
                if self._stop_requested:
                    return None
                if process is not None and self._process is process:
                    self._process = None
            snapshot = self._failed_resource_snapshot(
                phase, str(exc), captured_fields=captured_fields
            )

        stored = self.database.add_resource_snapshot(run["id"], snapshot)
        level = "info" if stored.status == "success" else "warning"
        message = (
            f"{label}资源记录完成"
            if stored.status == "success"
            else f"{label}资源记录不完整，任务仍会继续"
        )
        self._emit(
            level,
            "resource",
            message,
            {"phase": phase, "attempt": attempt, "snapshot": stored.to_payload()},
        )
        return stored

    def _capture_before_resource_snapshot(self, run: dict) -> ResourceSnapshot | None:
        """Retry one fully failed pre-task capture after its subprocess has restored state."""
        snapshot = self._capture_resource_snapshot(run, "before")
        if snapshot is None or snapshot.status != "failed":
            return snapshot
        with self._lock:
            if self._stop_requested:
                return None
        self._emit(
            "info",
            "resource",
            "任务前资源记录失败，正在重试一次",
            {
                "phase": "before",
                "attempt": 2,
                "maxAttempts": BEFORE_RESOURCE_CAPTURE_ATTEMPTS,
                "reason": snapshot.error,
            },
        )
        return self._capture_resource_snapshot(run, "before", attempt=2)

    def _store_resource_yield(
        self,
        run: dict,
        before: ResourceSnapshot,
        after: ResourceSnapshot | None,
        task_status: str,
    ) -> dict:
        calculated = calculate_resource_yield(
            before,
            after,
            task_status=task_status,
            tracked_fields=RESOURCE_YIELD_FIELDS_BY_TASK.get(
                run["taskId"], YIELD_FIELDS
            ),
        )
        stored = self.database.add_resource_yield(
            run["id"], run["taskId"], calculated
        )
        with self._lock:
            run["resourceYield"] = stored
            run["benefitStatus"] = stored["attributionStatus"]
            if self._current and self._current["id"] == run["id"]:
                self._current["resourceYield"] = stored
                self._current["benefitStatus"] = stored["attributionStatus"]
        messages = {
            "reliable": "本次任务资源净变化已记录",
            "not_run": "任务本次未重复执行，不统计资源变化",
            "partial": "本次任务仅记录到部分资源变化",
            "cross_level_unknown": "任务期间角色升级，经验变化待确认",
            "interrupted": "本次资源变化存在干扰，仅供参考",
            "unavailable": "本次任务资源变化未能完整记录",
        }
        self._emit(
            "info" if stored["attributionStatus"] in {"reliable", "not_run"} else "warning",
            "resource",
            messages[stored["attributionStatus"]],
            {"resourceYield": stored},
        )
        return stored

    def _store_keju_settlement_reward(
        self, run: dict, result: dict, *, fields: tuple[str, ...], source_page: str, label: str
    ) -> dict | None:
        reward = result.get("settlement_reward")
        if not isinstance(reward, dict) or reward.get("status") != "confirmed":
            reason = reward.get("reason") if isinstance(reward, dict) else "missing"
            self._emit(
                "warning", "resource", f"{label}结算页未识别到奖励，本次不记录收益",
                {"reason": reason},
            )
            return None
        evidence_payload = reward["evidence"]
        evidence = {}
        values = {"experience": None, "silver": None, "gold": None}
        deltas = {"experience": None, "silver": None, "gold": None}
        for field in fields:
            amount = evidence_payload["amount"] if field == "gold" else evidence_payload[field]
            values[field] = reward[field]
            deltas[field] = reward[field]
            evidence[field] = ResourceFieldEvidence(
                status="captured", source_page=source_page,
                raw_text=amount["text"], box=tuple(amount["box"]),
                frame_id=evidence_payload.get("frameId"),
            )
        captured = ResourceSnapshot(
            target_id="local-primary",
            phase="reward",
            captured_at=utc_now(),
            values=ResourceValues(**values), evidence=evidence, captured_fields=frozenset(fields),
        )
        stored_snapshot = self.database.add_resource_snapshot(run["id"], captured)
        direct_reward = ResourceYield(
            task_status="completed",
            attribution_status="reliable",
            experience_delta=deltas["experience"], gold_delta=deltas["gold"], silver_delta=deltas["silver"],
            after_snapshot_id=stored_snapshot.snapshot_id,
            tracked_fields=frozenset(fields),
            measurement_source="settlement_reward",
        )
        stored = self.database.add_resource_yield(
            run["id"], run["taskId"], direct_reward
        )
        with self._lock:
            run["resourceYield"] = stored
            run["benefitStatus"] = stored["attributionStatus"]
            if self._current and self._current["id"] == run["id"]:
                self._current["resourceYield"] = stored
                self._current["benefitStatus"] = stored["attributionStatus"]
        self._emit(
            "info", "resource", f"{label}结算奖励已记录",
            {"resourceYield": stored},
        )
        return stored

    @staticmethod
    def _parse_home_recovery(output_lines: list[str]) -> dict:
        protocol = [
            line[len(HOME_RECOVERY_PREFIX):]
            for line in output_lines
            if line.startswith(HOME_RECOVERY_PREFIX)
        ]
        if len(protocol) != 1:
            raise RuntimeError("返回主界面进程没有返回唯一的结构化结果")
        try:
            result = json.loads(protocol[0])
        except json.JSONDecodeError as exc:
            raise RuntimeError("返回主界面进程返回了无效结果") from exc
        if not isinstance(result, dict) or result.get("status") not in {
            "confirmed", "failed", "cancelled"
        }:
            raise RuntimeError("返回主界面结果包含未知状态")
        for field in ("actions", "clicks"):
            value = result.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeError(f"返回主界面结果包含无效的 {field}")
        if result["status"] == "confirmed":
            evidence = result.get("evidence")
            if (
                result.get("scene") != "main_screen"
                or result.get("outcome") not in {"already_main", "recovered"}
                or not isinstance(evidence, dict)
                or not all(evidence.get(key) for key in (
                    "targetId", "firstFrameId", "lastFrameId", "anchors"
                ))
                or evidence["firstFrameId"] == evidence["lastFrameId"]
            ):
                raise RuntimeError("返回主界面确认结果缺少双帧证据")
        return result

    def _inspection_command(self, task_id: str, options: dict) -> list[str]:
        scripts = {
            "sanjieqiyuan": "run_sanjieqiyuan_macos.py",
            "keju": "run_keju_macos.py",
            "keju_huishi": "run_keju_macos.py",
            "zhuogui": "run_zhuogui_macos.py",
            "fuli_qiandao": "run_fuli_qiandao_macos.py",
            "bangpai_qiandao": "run_bangpai_qiandao_macos.py",
            "baotu": "run_baotu_macos.py",
            "fun_news": "run_fun_news_macos.py",
            "yunbiao": "run_yunbiao_macos.py",
            "shimen": "run_shimen_macos.py",
            "mijingxiangyao": "run_mijingxiangyao_macos.py",
            "kaogu": "run_kaogu_macos.py",
        }
        command = [sys.executable, str(ROOT / "tools" / scripts[task_id]), "--inspect-scene"]
        if task_id == "sanjieqiyuan":
            command.extend(["--input-method", "global"])
        elif task_id in {"keju", "keju_huishi"}:
            command.extend([
                "--exam-type",
                "xiangshi" if task_id == "keju" else "huishi",
            ])
            if task_id == "keju":
                command.append("--navigate-from-main")
        elif task_id == "zhuogui":
            command.extend(["--rounds", "1"])
        elif task_id == "yunbiao":
            command.extend(["--rounds", str(options.get("rounds", 3))])
        elif task_id == "kaogu":
            command.extend([
                "--tool-mode",
                str(options.get("toolMode", "selected")),
            ])
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
            actionable = {
                "answering", "dismiss_known_popup", "close_bag_overlay",
                "open_battle_menu", "click_join", "click_daily", "scroll_daily",
                "click_activity",
                "click_activity_toolbar", "click_activity_template",
            }
            ready = scene in actionable
        elif task_id == "keju":
            ready = scene in {
                "exam_ready", "open_activity", "open_daily", "join_task", "scroll_daily", "wait"
            } or (
                scene == "formal_question_ready" and decision.get("examType") == "xiangshi"
            )
        elif task_id == "keju_huishi":
            ready = (
                scene == "formal_question_ready"
                and decision.get("examType") == "huishi"
            )
        elif task_id == "zhuogui":
            ready = bool(proposed and proposed != "stop")
        elif task_id == "fuli_qiandao":
            ready = scene in {"open_welfare", "open_scratch", "scratch_ready", "confirm_reward"}
        elif task_id == "bangpai_qiandao":
            ready = scene in {
                "expand_menu", "open_clan", "open_welfare", "sign_in",
                "claim_dividend", "confirm_dividend", "close_known_welfare_overlay",
            }
        elif task_id == "baotu":
            ready = scene in {
                "open_activity", "open_daily", "scroll_daily", "join_task",
                "accept_dialogue", "continue_task", "battle", "auto_pathing",
            }
        elif task_id == "fun_news":
            ready = scene in {
                "open_activity", "open_daily", "scroll_daily", "join_task",
                "like", "refresh_feed",
            }
        elif task_id == "yunbiao":
            ready = scene in {
                "open_activity", "open_daily", "scroll_daily", "join_task",
                "select_normal", "confirm_normal", "continue_task",
                "delivery_active", "battle", "auto_pathing",
            }
        elif task_id == "shimen":
            ready = scene in {
                "open_activity", "open_daily", "scroll_daily", "join_task",
                "accept_task_dialogue", "start_task", "continue_task",
                "progress_task", "battle", "auto_pathing",
            }
        elif task_id == "mijingxiangyao":
            ready = scene in {
                "open_activity", "open_daily", "scroll_daily", "join_task",
                "continue_task", "enter_dungeon", "start_challenge", "select_stage",
                "enter_battle", "battle", "auto_pathing", "wait",
            }
        elif task_id == "kaogu":
            ready = scene in {
                "expand_menu", "open_skills", "open_auxiliary", "open_archaeology",
                "open_production", "switch_tier", "open_tool_selection",
                "open_shovel_shop", "buy_shovel", "close_shop", "select_shovel",
                "use_shovel", "start_archaeology", "dig", "close_temporary_bag",
                "wait_for_dig",
            }

        action_key = proposed if proposed else scene
        expected_exam_type = {
            "keju": "xiangshi",
            "keju_huishi": "huishi",
        }.get(task_id)
        observed_exam_type = decision.get("examType")
        if ready:
            summary = "当前画面支持启动此任务"
            reason = None
        elif (
            expected_exam_type
            and observed_exam_type in {"xiangshi", "huishi"}
            and observed_exam_type != expected_exam_type
        ):
            expected_label = "乡试" if expected_exam_type == "xiangshi" else "会试"
            observed_label = "乡试" if observed_exam_type == "xiangshi" else "会试"
            summary = f"当前是科举{observed_label}，不能启动科举{expected_label}"
            reason = f"请切换到科举{expected_label}正式题目页后重新检测"
        elif expected_exam_type and scene in {
            "formal_question_ready",
            "waiting_for_question",
            "exam_page_without_question",
        } and observed_exam_type not in {"xiangshi", "huishi"}:
            expected_label = "乡试" if expected_exam_type == "xiangshi" else "会试"
            summary = f"无法确认当前是否为科举{expected_label}"
            reason = f"请让科举{expected_label}标题和正式题目同时清晰可见后重新检测"
        elif scene in {"completed", "already_completed"}:
            summary = "当前画面显示今日任务已经完成"
            reason = "无需再次启动"
        elif scene == "no_clan":
            summary = "当前角色未加入帮派"
            reason = "加入帮派后再检测"
        elif scene == "waiting_for_question":
            summary = "科举尚未进入正式题目"
            reason = "等待正式题目出现后重新检测"
        elif scene == "unsupported_asset_branch":
            task_label = "秘境" if task_id == "mijingxiangyao" else "师门"
            summary = f"当前{task_label}分支需要资产操作，自动化不会继续"
            reason = f"请手动处理后重新检测；识别内容：{decision.get('reason', '未知')}"
        elif scene == "challenge_failed" and task_id == "mijingxiangyao":
            summary = "当前秘境挑战已经失败，自动化不会执行付费恢复"
            reason = "请手动处理失败页面、药品或助战后重新检测"
        elif scene in {
            "select_mode", "confirm_mode", "mode_selection_required",
        } and task_id == "mijingxiangyao":
            summary = "当前需要手动选择或确认秘境模式"
            reason = "请选择所需模式并进入当前挑战页后重新检测；自动化不会切换模式"
        elif scene == "manual_tool_required" and task_id == "kaogu":
            summary = "还没有检测到已手动选好的考古工具"
            reason = decision.get("reason") or "请按提示图准备考古生产页后重新检测"
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

        coordinate_evidence = {}
        for key, length in {
            "windowBoundsPoints": 4,
            "contentRectPoints": 4,
            "recognizedBoxCenterPixels": 2,
            "predictedScreenPoint": 2,
        }.items():
            if key not in evidence:
                continue
            value = evidence[key]
            if not isinstance(value, (list, tuple)) or len(value) != length or not all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(item)
                for item in value
            ):
                raise RuntimeError(f"画面检测结果包含无效的坐标证据：{key}")
            coordinate_evidence[key] = list(value)
        if "scaleFactor" in evidence:
            scale_factor = evidence["scaleFactor"]
            if (
                not isinstance(scale_factor, (int, float))
                or isinstance(scale_factor, bool)
                or not math.isfinite(scale_factor)
                or scale_factor <= 0
            ):
                raise RuntimeError("画面检测结果包含无效的缩放证据")
            coordinate_evidence["scaleFactor"] = scale_factor
        for key in ("frameId", "captureMethod", "anchorValidation"):
            if key not in evidence:
                continue
            value = evidence[key]
            if not isinstance(value, str) or not value:
                raise RuntimeError(f"画面检测结果包含无效的标定证据：{key}")
            coordinate_evidence[key] = value

        action_label = ACTION_LABELS.get(action_key)
        if task_id == "bangpai_qiandao" and action_key == "open_welfare":
            action_label = "打开帮派福利页面"
        task_action_overrides = {
            ("baotu", "join_task"): "点击同一卡片内的宝图任务参加按钮",
            ("baotu", "continue_task"): "点击经过识别的宝图任务栏",
            ("yunbiao", "join_task"): "点击同一卡片内的运镖参加按钮",
            ("yunbiao", "continue_task"): "点击经过识别的运镖任务栏",
            ("shimen", "join_task"): "点击同一卡片内的师门任务参加按钮",
            ("shimen", "accept_task_dialogue"): "选择经过识别的师门 NPC 任务",
            ("shimen", "start_task"): "点击右侧师门任务正式开始",
            ("shimen", "continue_task"): "点击经过识别的师门任务栏",
            ("mijingxiangyao", "join_task"): "点击同一卡片内的秘境降妖参加按钮",
            ("mijingxiangyao", "continue_task"): "点击经过识别的秘境降妖任务栏",
        }
        action_label = task_action_overrides.get((task_id, action_key), action_label)
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
                **coordinate_evidence,
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
        if "completionEvidence" in result:
            if (
                result["completionEvidence"] != "stable_main_after_task_disappeared"
                or result.get("confirmationPolls", 0) < 2
            ):
                raise RuntimeError("任务最终结果包含无效的自动完成证据")
        elif "confirmationPolls" in result:
            raise RuntimeError("任务最终结果缺少自动完成证据")
        if task_id in {"keju", "keju_huishi"} and "settlement_reward" in result:
            reward = result["settlement_reward"]
            if not isinstance(reward, dict) or reward.get("status") not in {
                "confirmed", "unavailable",
            }:
                raise RuntimeError("科举结算奖励结果无效")
            if reward["status"] == "confirmed":
                evidence = reward.get("evidence")
                fields = ("experience", "silver") if task_id == "keju" else ("gold",)
                if (
                    not isinstance(evidence, dict)
                    or any(
                        isinstance(reward.get(field), bool)
                        or not isinstance(reward.get(field), int)
                        or reward[field] < 0
                        or not isinstance(evidence.get("amount" if field == "gold" else field), dict)
                        or not isinstance(evidence["amount" if field == "gold" else field].get("text"), str)
                        or not isinstance(evidence["amount" if field == "gold" else field].get("box"), list)
                        or len(evidence["amount" if field == "gold" else field]["box"]) != 4
                        for field in fields
                    )
                ):
                    raise RuntimeError("科举结算奖励证据无效")
            elif not isinstance(reward.get("reason"), str) or not reward["reason"]:
                raise RuntimeError("科举结算奖励缺少未采集原因")
        if (
            task_id == "zhuogui"
            and status == "completed"
            and result["rounds"] != result["target_rounds"]
        ):
            raise RuntimeError("捉鬼完成轮数与目标轮数不一致")
        if (
            task_id == "kaogu"
            and status == "completed"
            and result["digs"] != result["target_digs"]
        ):
            raise RuntimeError("考古完成次数与目标次数不一致")
        if task_id == "kaogu" and result.get("purchases", 0) > 2:
            raise RuntimeError("考古结果中的洛阳铲购买次数超过上限")
        if task_id == "kaogu" and result.get("sale_clicks", 0) > 12:
            raise RuntimeError("考古结果中的古董出售次数超过上限")
        if task_id == "kaogu" and status == "completed":
            if result.get("genuine_protected") is not True:
                raise RuntimeError("考古结果未确认真品保护")
            cleanup_status = result.get("cleanup_status")
            cleanup_warning = result.get("cleanup_warning")
            if cleanup_status not in {"completed", "incomplete"}:
                raise RuntimeError("考古结果缺少有效的收尾状态")
            if cleanup_status == "completed" and cleanup_warning is not None:
                raise RuntimeError("考古结果的已完成收尾不应包含警告")
            if cleanup_status == "incomplete" and (
                not isinstance(cleanup_warning, str) or not cleanup_warning.strip()
            ):
                raise RuntimeError("考古结果的未完成收尾缺少原因")
            valid_sale_outcomes = {
                "all_non_genuine_sold",
                "all_non_genuine_sold_genuine_retained",
            }
            if cleanup_status == "incomplete":
                valid_sale_outcomes.add("cleanup_incomplete")
            if result.get("sale_outcome") not in valid_sale_outcomes:
                raise RuntimeError("考古结果缺少有效的古董售卖结论")
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
                "keju_huishi": "科举会试已达到本次最多答题数",
                "zhuogui": "捉鬼任务已达到目标轮数",
                "mijingxiangyao": "秘境降妖已达到本次战斗场数上限",
            }.get(task_id, f"{task_name}已达到本次执行上限")
        if status == "completed":
            if task_id == "kaogu" and result.get("cleanup_status") == "incomplete":
                return (
                    "工坊考古完成 10 次挖掘，但古董整理未完整完成："
                    f"{result['cleanup_warning']}"
                )
            return {
                "sanjieqiyuan": "三界奇缘答题完成",
                "keju": "科举乡试答题完成",
                "keju_huishi": "科举会试答题完成",
                "zhuogui": "捉鬼任务已完成目标轮数",
                "fuli_qiandao": "福利签到完成",
                "bangpai_qiandao": "帮派签到与分红领取完成",
                "baotu": "宝图任务完成",
                "fun_news": "趣闻鉴赏完成",
                "yunbiao": "普通运镖完成",
                "shimen": "师门任务已启动，后续由游戏自动完成",
                "mijingxiangyao": "秘境降妖完成",
                "kaogu": "工坊考古完成 10 次挖掘，非真品古董已兑换为银币",
            }[task_id]
        return None

    def _execute(self, run: dict, completion: threading.Event) -> None:
        error = None
        status = "failed"
        output_lines = []
        task_result = None
        home_recovery = {"status": "not_requested"}
        failure_phase = "task"
        recovery_started = False
        before_snapshot = None
        after_snapshot = None
        resource_yield_stored = False
        try:
            if self._tracks_task_resources(run["taskId"]):
                before_snapshot = self._capture_before_resource_snapshot(run)
            command = self._command(run["taskId"], run["options"])
            with self._lock:
                if self._stop_requested:
                    status = "cancelled"
                    return
                self._current["status"] = "running"
                self._current["stage"] = "自动化运行中"
                self.database.update_run_stage(
                    run["id"], "running", self._current["stage"],
                    home_recovery=home_recovery,
                    failure_phase=None,
                )
                self._emit("info", "status", f"{run['taskName']}已开始", {"status": "running"})
                self._process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    start_new_session=True,
                )
                process = self._process
            assert process.stdout is not None
            last_progress_message = None
            for line in process.stdout:
                message = line.strip()
                if message:
                    output_lines.append(message)
                    if self._handle_manual_review_request(run, message):
                        continue
                    progress = user_progress(run["taskId"], message)
                    if progress and progress["message"] != last_progress_message:
                        with self._lock:
                            if self._current and self._current["id"] == run["id"]:
                                self._current["stage"] = progress["payload"]["stage"]
                                self.database.update_run_stage(
                                    run["id"], "running", self._current["stage"],
                                    home_recovery=home_recovery,
                                    failure_phase=None,
                                )
                        self._emit(
                            progress["level"],
                            "log",
                            progress["message"],
                            progress["payload"],
                        )
                        last_progress_message = progress["message"]
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
                    if run["taskId"] == "keju" and task_result["status"] == "completed":
                        self._store_keju_settlement_reward(
                            run, task_result, fields=("experience", "silver"),
                            source_page="xiangshi_settlement", label="科举乡试",
                        )
                    elif run["taskId"] == "keju_huishi" and task_result["status"] == "completed":
                        self._store_keju_settlement_reward(
                            run, task_result, fields=("gold",),
                            source_page="huishi_settlement", label="科举会试",
                        )
                    with self._lock:
                        if self._stop_requested:
                            status = "cancelled"
                            task_result = None
                        elif run["taskId"] in TASKS_RETAIN_RESULT_SCREEN:
                            status = "succeeded"
                            failure_phase = None
                            home_recovery = {
                                "status": "not_requested",
                                "outcome": None,
                                "scene": "huishi_settlement",
                                "reason": "会试结算页已保留",
                                "actions": 0,
                                "clicks": 0,
                            }
                            run["status"] = status
                            run["stage"] = "会试答题完成，结算页已保留"
                            run["result"] = task_result
                            run["homeRecovery"] = home_recovery
                            self.database.update_run_stage(
                                run["id"], status, run["stage"],
                                result=task_result,
                                home_recovery=home_recovery,
                                failure_phase=None,
                            )
                            self._emit(
                                "info", "status", "会试答题完成，结算页已保留",
                                {"status": status, "result": task_result,
                                 "homeRecovery": home_recovery},
                            )
                        else:
                            recovery_started = True
                            failure_phase = None
                            home_recovery = {
                                "status": "running",
                                "outcome": None,
                                "scene": None,
                                "reason": None,
                                "actions": 0,
                                "clicks": 0,
                            }
                            run["status"] = "recovering"
                            run["stage"] = "正在返回主界面"
                            run["result"] = task_result
                            run["homeRecovery"] = home_recovery
                            self.database.update_run_stage(
                                run["id"], "recovering", run["stage"],
                                result=task_result,
                                home_recovery=home_recovery,
                                failure_phase=None,
                            )
                            self._emit(
                                "info", "status", "任务已完成，正在返回主界面",
                                {"status": "recovering", "result": task_result,
                                 "homeRecovery": home_recovery},
                            )
                            self._process = subprocess.Popen(
                                self._recovery_command(run["taskId"]),
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
                    if (
                        status != "cancelled"
                        and run["taskId"] not in TASKS_RETAIN_RESULT_SCREEN
                    ):
                        recovery_lines = []
                        assert process.stdout is not None
                        for line in process.stdout:
                            message = line.strip()
                            if message:
                                recovery_lines.append(message)
                        recovery_return_code = process.wait()
                        with self._lock:
                            stopped = self._stop_requested
                        if stopped:
                            status = "cancelled"
                            parsed = None
                            try:
                                parsed = self._parse_home_recovery(recovery_lines)
                            except RuntimeError:
                                pass
                            home_recovery = parsed or {
                                "status": "cancelled",
                                "outcome": None,
                                "scene": None,
                                "reason": "返回主界面期间收到停止请求",
                                "actions": 0,
                                "clicks": 0,
                            }
                            home_recovery["status"] = "cancelled"
                            failure_phase = None
                        else:
                            home_recovery = self._parse_home_recovery(recovery_lines)
                            if home_recovery["status"] == "confirmed" and recovery_return_code == 0:
                                status = "succeeded"
                                failure_phase = None
                                run["homeRecovery"] = home_recovery
                                if (
                                    self._tracks_task_resources(run["taskId"])
                                    and task_result["status"] == "completed"
                                ):
                                    after_snapshot = self._capture_resource_snapshot(
                                        run, "after"
                                    )
                            else:
                                status = "failed"
                                failure_phase = "recovery"
                                error = "任务已完成，但未能返回主界面"
                else:
                    status = "failed"
                    failure_phase = "task"
                    error = (
                        task_result.get("reason")
                        if result_status == "safe_stopped"
                        else "任务达到内部上限，但缺少明确完成证据"
                        if result_status == "limit_reached"
                        else self._friendly_error(
                            run["taskId"], return_code, "\n".join(output_lines)
                        )
                    )
        except Exception as exc:
            with self._lock:
                status = "cancelled" if self._stop_requested else "failed"
            error = None if status == "cancelled" else str(exc)
            if status == "failed":
                failure_phase = "recovery" if recovery_started else "task"
                if recovery_started:
                    home_recovery = {
                        "status": "failed",
                        "outcome": None,
                        "scene": None,
                        "reason": str(exc),
                        "actions": home_recovery.get("actions", 0),
                        "clicks": home_recovery.get("clicks", 0),
                    }
        finally:
            try:
                if (
                    self._tracks_task_resources(run["taskId"])
                    and before_snapshot is not None
                    and not resource_yield_stored
                ):
                    resource_task_status = (
                        task_result.get("status")
                        if isinstance(task_result, dict)
                        else "cancelled" if status == "cancelled" else "failed"
                    )
                    try:
                        self._store_resource_yield(
                            run,
                            before_snapshot,
                            after_snapshot,
                            resource_task_status,
                        )
                        resource_yield_stored = True
                    except Exception as exc:
                        self._emit(
                            "warning", "resource", "资源净变化计算失败",
                            {"reason": str(exc)},
                        )
                with self._lock:
                    # Once stop() accepts a request, cancellation wins over a
                    # result parsed concurrently after the subprocess exits.
                    if self._stop_requested:
                        status = "cancelled"
                        error = None
                        failure_phase = None
                        if recovery_started:
                            home_recovery = {
                                **home_recovery,
                                "status": "cancelled",
                                "reason": home_recovery.get("reason") or "返回主界面期间收到停止请求",
                            }
                        elif not task_result or task_result.get("status") != "cancelled":
                            task_result = self._cancelled_task_result(
                                run["taskId"], output_lines
                            )
                    self._process = None
                    self._manual_review = None
                    if self._current and self._current["id"] == run["id"]:
                        self._current["status"] = status
                        self._current["stage"] = {
                            "succeeded": (
                                "会试答题完成，结算页已保留"
                                if run["taskId"] in TASKS_RETAIN_RESULT_SCREEN
                                and not recovery_started
                                else "已确认回到主界面"
                            ),
                            "failed": (
                                "任务完成但返回主界面失败"
                                if failure_phase == "recovery" else "运行失败"
                            ),
                            "cancelled": "已停止",
                        }[status]
                        self._current["error"] = error
                        self._current["result"] = task_result
                        self._current["homeRecovery"] = home_recovery
                        self._current["failurePhase"] = failure_phase
                        self._current["lastOperationAt"] = utc_now()
                    # Keep final persistence and publication inside the same state
                    # transition, so start() cannot replace the run in between.
                    self.database.update_run(
                        run["id"], status, error, task_result,
                        stage=run.get("stage"),
                        home_recovery=home_recovery,
                        failure_phase=failure_phase,
                    )
                    level = "error" if status == "failed" else (
                        "warning"
                        if status == "cancelled" or (
                            isinstance(task_result, dict)
                            and task_result.get("cleanup_status") == "incomplete"
                        )
                        else "info"
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
                            "homeRecovery": home_recovery,
                            "failurePhase": failure_phase,
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
            payload["stage"] = "正在识别帮派签到与分红页面"
        elif task_id == "bangpai_qiandao" and line.startswith(RESULT_PREFIX):
            payload["stage"] = RunnerManager._sign_in_result_stage(line, clan=True)
        elif task_id == "baotu" and '"event": "scene"' in line:
            payload["stage"] = "正在识别并推进宝图任务"
        elif task_id == "baotu" and line.startswith(RESULT_PREFIX):
            payload["stage"] = RunnerManager._bounded_task_result_stage(line, "宝图任务")
        elif task_id == "fun_news" and '"event": "scene"' in line:
            payload["stage"] = "正在识别趣闻动态"
        elif task_id == "fun_news" and line.startswith(RESULT_PREFIX):
            payload["stage"] = RunnerManager._bounded_task_result_stage(line, "趣闻鉴赏")
        elif task_id == "yunbiao" and '"event": "scene"' in line:
            payload["stage"] = "正在识别并推进普通运镖"
        elif task_id == "yunbiao" and line.startswith(RESULT_PREFIX):
            payload["stage"] = RunnerManager._bounded_task_result_stage(line, "普通运镖")
        elif task_id == "shimen" and '"event": "scene"' in line:
            payload["stage"] = "正在领取并启动师门任务"
        elif task_id == "shimen" and line.startswith(RESULT_PREFIX):
            result_stage = RunnerManager._bounded_task_result_stage(line, "师门任务")
            payload["stage"] = (
                "师门任务已启动，后续由游戏自动完成"
                if result_stage == "师门任务完成"
                else result_stage
            )
        elif task_id == "mijingxiangyao" and '"event": "scene"' in line:
            payload["stage"] = "正在识别并推进秘境降妖"
        elif task_id == "mijingxiangyao" and line.startswith(RESULT_PREFIX):
            payload["stage"] = RunnerManager._bounded_task_result_stage(line, "秘境降妖")
        elif task_id == "kaogu" and '"event": "scene"' in line:
            payload["stage"] = "正在识别并推进工坊考古"
        elif task_id == "kaogu" and line.startswith(RESULT_PREFIX):
            payload["stage"] = RunnerManager._bounded_task_result_stage(line, "工坊考古")
        return payload

    @staticmethod
    def _bounded_task_result_stage(line: str, label: str) -> str:
        try:
            status = json.loads(line[len(RESULT_PREFIX):]).get("status")
        except (json.JSONDecodeError, AttributeError):
            return f"{label}正在结束"
        return {
            "completed": f"{label}完成",
            "already_completed": f"{label}今日已完成",
            "limit_reached": f"{label}已达到本次执行上限",
            "safe_stopped": f"{label}已安全停止",
            "cancelled": f"{label}已取消",
        }.get(status, f"{label}正在结束")

    @staticmethod
    def _sign_in_result_stage(line: str, *, clan: bool) -> str:
        prefix = "帮派签到与分红" if clan else "签到"
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
            "sanjieqiyuan": "三界奇缘未能继续，请确认活动可参与且当前画面是无遮挡的游戏主界面。",
            "keju": "科举答题已停止，请确认正式题目页面可见并检查模型或本地题库配置。",
            "keju_huishi": "科举会试答题已停止，请确认会试正式题目页面可见并检查模型或本地题库配置。",
            "zhuogui": "捉鬼流程已停止，请检查组队状态、弹窗和当前游戏页面。",
            "fuli_qiandao": "福利签到未能继续，请保持主界面或福利页面完整可见后重试。",
            "bangpai_qiandao": "帮派签到未能继续，请确认角色已加入帮派并保持相关页面完整可见。",
            "baotu": "宝图任务未能继续，请保持主界面、日常活动页或宝图任务过程完整可见。",
            "yunbiao": "普通运镖未能继续，请检查活力、镖种和当前任务页面。",
            "shimen": "师门任务未能启动，请保持活动入口、领取对话或右侧师门任务完整可见。",
            "mijingxiangyao": "秘境降妖未能继续，请检查战斗状态；药品、助战购买和付费恢复需要手动处理。",
            "kaogu": "工坊考古未能继续，请检查技能生产页面、角色活力和洛阳铲购买状态。",
        }
        return f"{hints[task_id]}（进程代码 {return_code}）"
