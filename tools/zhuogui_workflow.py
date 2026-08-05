"""Platform-neutral bounded workflow for the 抓鬼 task."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Protocol

try:
    from .automation_actions import (
        ActionContext,
        ActionMetrics,
        ActionResult,
        AtomicAction,
    )
    from .automation_geometry import relative_point
    from .automation_workflows import WorkflowContext, WorkflowResult
    from .zhuogui_domain import RuntimeBudget, round_completion_action
except ImportError:  # Direct execution through tools/run_zhuogui_macos.py.
    from automation_actions import (
        ActionContext,
        ActionMetrics,
        ActionResult,
        AtomicAction,
    )
    from automation_geometry import relative_point
    from automation_workflows import WorkflowContext, WorkflowResult
    from zhuogui_domain import RuntimeBudget, round_completion_action


MAX_TARGET_LEVEL_SWIPES = 64
MAX_TASKBAR_CLICK_ATTEMPTS = 3


class ZhuoguiObservationSource(Protocol):
    def observe(self): ...


@dataclass
class WorkflowState:
    phase: str = "ensure_team"
    completed_rounds: int = 0
    target_daily_clicked: bool = False
    target_ghost_clicked: bool = False
    target_high_range_clicked: bool = False
    target_level_swipes: int = 0
    target_configured: bool = False
    target_description_focused: bool = False
    target_description_cleared: bool = False
    target_keyboard_confirmed: bool = False
    target_input_focus_polls: int = 0
    target_confirm_attempts: int = 0
    formation_configured: bool = False
    formation_selection_attempts: int = 0
    formation_save_attempts: int = 0
    double_claims_this_round: int = 0
    unknown_count: int = 0
    round_started_at: float | None = None
    last_task_click_at: float | None = None
    taskbar_click_attempts: int = 0
    navigate_not_before: float | None = None
    steps: int = 0
    last_ghost_progress: int | None = None


def level_drag_end(current_level: int, target_level: int) -> float:
    difference = current_level - target_level
    if difference == 0:
        raise ValueError("current level already matches target")
    if difference > 0:
        return 0.75 if difference > 15 else 0.45
    return 0.10 if difference < -15 else 0.34


def _point(value, image_size):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x, y = value
    width, height = image_size
    if not all(
        isinstance(item, (int, float))
        and not isinstance(item, bool)
        and math.isfinite(item)
        for item in (x, y, width, height)
    ):
        return None
    if width <= 0 or height <= 0 or not (0 <= x < width and 0 <= y < height):
        return None
    return x, y


def _box_center(box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    x, y, width, height = box
    if width <= 0 or height <= 0:
        return None
    return x + width / 2, y + height / 2


class ZhuoguiClickAction(AtomicAction):
    name = "zhuogui_evidence_click"
    statuses = frozenset(("click",))

    def execute(self, context: ActionContext) -> ActionResult:
        point = _point(context.decision.get("point"), context.scene.image_size)
        if point is None:
            raise RuntimeError(
                f"{context.decision.get('reason', '捉鬼动作')} 缺少经过识别的点击位置"
            )
        context.input.click(point, context.scene, str(context.decision["reason"]))
        result = ActionResult(self.name, "click", 1, clicks=1)
        context.emit(
            "action_completed",
            action=str(context.decision.get("semantic", self.name)),
            task=context.task_label,
            decision="click",
            clicks=1,
            drags=0,
        )
        return result


class AdjustTargetLevelAction(AtomicAction):
    name = "adjust_zhuogui_minimum_level"
    statuses = frozenset(("adjust_level",))

    def execute(self, context: ActionContext) -> ActionResult:
        current = context.decision.get("current_level")
        target = context.decision.get("target_level")
        if not isinstance(current, int) or not isinstance(target, int):
            raise RuntimeError("调整捉鬼最低等级缺少可靠等级证据")
        end_y = level_drag_end(current, target)
        start = relative_point((0.623, 0.38), context.scene.image_size)
        end = relative_point((0.623, end_y), context.scene.image_size)
        context.input.drag(start, end, context.scene, f"调整最低等级至{target}")
        result = ActionResult(self.name, "adjust_level", 1, drags=1)
        context.emit(
            "action_completed",
            action=self.name,
            task=context.task_label,
            decision="adjust_level",
            currentLevel=current,
            targetLevel=target,
            clicks=0,
            drags=1,
        )
        return result


@dataclass(frozen=True)
class ZhuoguiWorkflow:
    source: ZhuoguiObservationSource
    target_rounds: int = 2
    target_min_level: int = 90
    target_max_level: int = 115
    level_range_label: str = "90-115"
    formation: str = "current"
    max_steps: int = 600
    poll_interval: float = 1.2
    recruit_timeout: float = 180.0
    max_rebuilds: int = 3
    clock: Callable[[], float] = time.monotonic

    def execute(
        self,
        context: WorkflowContext,
        state: WorkflowState | None = None,
    ) -> WorkflowResult:
        state = state or WorkflowState()
        state.formation_configured = self.formation == "current"
        budget = RuntimeBudget(
            recruitment_timeout=self.recruit_timeout,
            max_rebuilds=self.max_rebuilds,
        )
        metrics = ActionMetrics()
        click_action = ZhuoguiClickAction()
        level_action = AdjustTargetLevelAction()

        def click(point, scene, reason, semantic, *, box=False):
            if box:
                point = _box_center(point)
            result = click_action.execute(
                context.action_context(
                    scene,
                    {
                        "status": "click",
                        "point": point,
                        "reason": reason,
                        "semantic": semantic,
                    },
                    "捉鬼",
                )
            )
            metrics.record(result)

        for step in range(1, self.max_steps + 1):
            state.steps = step
            now = self.clock()
            observation = self.source.observe()
            scene = observation.scene
            decision = observation.decision
            status = decision["status"]
            progress = decision.get("progress")
            if isinstance(progress, int) and not isinstance(progress, bool) and progress >= 0:
                state.last_ghost_progress = progress
            context.emit(
                "scene",
                step=step,
                phase=state.phase,
                status=status,
                completed_rounds=state.completed_rounds,
                ghost_progress=progress,
            )
            if status != "unknown":
                state.unknown_count = 0

            if status == "already_completed":
                return self._result("already_completed", state, metrics)
            if status == "double_points_prompt":
                if state.double_claims_this_round >= 1:
                    raise RuntimeError("本轮重复出现双倍领取弹窗，停止避免重复消耗")
                click(decision.get("confirm"), scene, "领取100点双倍", "claim_double_points")
                state.double_claims_this_round += 1
                context.waiter.wait(self.poll_interval)
                continue
            if status == "not_enough_members":
                click(decision.get("cancel"), scene, "取消人数不足弹窗", "cancel_member_prompt")
                state.phase = "recruit"
                if budget.recruitment_started_at is None:
                    budget.recruitment_started_at = now
                context.waiter.wait(self.poll_interval)
                continue
            if status == "invite_members_overlay":
                click(decision.get("close"), scene, "关闭邀请成员窗口", "close_invite", box=True)
                context.waiter.wait(self.poll_interval)
                continue
            if status == "welfare_overlay":
                click(decision.get("close"), scene, "关闭已知福利弹窗", "close_welfare")
                context.waiter.wait(self.poll_interval)
                continue
            if status == "round_complete":
                action = round_completion_action(state.completed_rounds, self.target_rounds)
                state.completed_rounds += 1
                context.emit("round_complete", round=state.completed_rounds, action=action)
                if action == "finish":
                    click(decision.get("cancel"), scene, "达到目标轮数，取消继续", "finish_rounds")
                    return self._result("completed", state, metrics)
                click(decision.get("confirm"), scene, "继续下一轮", "continue_rounds")
                state.phase = "recruit"
                state.double_claims_this_round = 0
                state.round_started_at = None
                budget.recruitment_started_at = now
                context.waiter.wait(self.poll_interval)
                continue
            if status == "battle":
                state.taskbar_click_attempts = 0
                if budget.battle_started_at is None:
                    budget.battle_started_at = now
                elif budget.battle_expired(now):
                    raise TimeoutError("单场战斗超过180秒，已停止")
                context.waiter.wait(self.poll_interval)
                continue
            if status != "unknown":
                budget.battle_started_at = None

            budget.round_started_at = state.round_started_at
            if budget.round_expired(now):
                raise TimeoutError("单轮抓鬼超过20分钟，已停止")
            if status in {"ghost_task", "auto_pathing"}:
                state.phase = "run_round"
                if state.round_started_at is None:
                    state.round_started_at = now
                if status == "ghost_task":
                    if state.last_task_click_at is None or now - state.last_task_click_at >= 8.0:
                        if state.taskbar_click_attempts >= MAX_TASKBAR_CLICK_ATTEMPTS:
                            raise RuntimeError("连续三次点击右侧捉鬼任务后仍未开始寻路，已安全停止")
                        click(decision.get("click"), scene, "点击抓鬼任务栏", "continue_ghost_task")
                        state.last_task_click_at = now
                        state.taskbar_click_attempts += 1
                else:
                    state.taskbar_click_attempts = 0
                context.waiter.wait(self.poll_interval)
                continue
            if status == "zhongkui_dialog":
                click(decision.get("click"), scene, "接取捉鬼任务", "accept_ghost_task")
                state.phase = "run_round"
                state.round_started_at = now
                state.last_task_click_at = None
                state.taskbar_click_attempts = 0
                context.waiter.wait(self.poll_interval)
                continue
            if status in {"map_zhongkui", "world_map_changan"}:
                if state.phase == "run_round":
                    raise RuntimeError("捉鬼任务已接取，但画面仍停留在寻人地图，已停止避免重复找钟馗")
                click(decision.get("click"), scene, status, status)
                state.phase = "navigate_zhongkui"
                context.waiter.wait(2.0)
                continue
            if status == "target_dialog":
                self._handle_target_dialog(
                    context,
                    state,
                    budget,
                    metrics,
                    click,
                    level_action,
                    scene,
                    decision,
                    now,
                )
                context.waiter.wait(self.poll_interval)
                continue
            if status == "formation_menu":
                if decision.get("selected_formation") == self.formation:
                    save = decision.get("save")
                    if save is None:
                        raise RuntimeError("已选中目标阵法，但未识别到保存阵法按钮")
                    if state.formation_save_attempts >= 2:
                        raise RuntimeError("保存阵法重复超过2次，已安全停止")
                    click(save, scene, f"保存{self.formation}", "save_formation")
                    state.formation_save_attempts += 1
                else:
                    target = decision.get("formations", {}).get(self.formation)
                    if target is None:
                        raise RuntimeError(f"阵法菜单中未找到{self.formation}，已安全停止")
                    if state.formation_selection_attempts >= 2:
                        raise RuntimeError("阵法选择重复超过2次，已安全停止")
                    click(target, scene, f"选择{self.formation}", "select_formation")
                    state.formation_selection_attempts += 1
                context.waiter.wait(self.poll_interval)
                continue
            if status in {"team_page", "remove_member_menu"}:
                self._handle_team_page(context, state, budget, click, scene, decision, status, now)
                context.waiter.wait(self.poll_interval)
                continue
            if status == "team_quick_panel":
                click(decision.get("create"), scene, "从快捷面板创建队伍", "create_team")
                state.phase = "ensure_team"
                context.waiter.wait(self.poll_interval)
                continue
            if status == "team_created_panel":
                click(decision.get("team"), scene, "从队长面板进入完整队伍页", "open_team_page")
                context.waiter.wait(self.poll_interval)
                continue
            if status == "channel_picker":
                click(decision.get("click"), scene, "选择当前频道", "choose_channel")
                context.waiter.wait(self.poll_interval)
                continue
            if status == "main_screen":
                if state.phase in {"ensure_team", "recruit"}:
                    click(decision.get("team"), scene, "打开队伍界面", "open_team")
                elif state.phase == "navigate_zhongkui":
                    if state.navigate_not_before and now < state.navigate_not_before:
                        context.waiter.wait(self.poll_interval)
                        continue
                    click(scene.templates.get("small_map"), scene, "打开小地图寻找钟馗", "open_small_map", box=True)
                elif state.phase == "run_round":
                    context.waiter.wait(self.poll_interval)
                    continue
                else:
                    raise RuntimeError(f"主界面与当前阶段不一致：{state.phase}")
                context.waiter.wait(self.poll_interval)
                continue

            state.unknown_count += 1
            if state.unknown_count >= 3:
                raise RuntimeError("连续三次无法识别当前画面，已停止")
            context.waiter.wait(self.poll_interval)

        raise RuntimeError(
            f"达到最大状态步骤 {self.max_steps}，仅完成 "
            f"{state.completed_rounds}/{self.target_rounds} 轮，已安全停止"
        )

    def _handle_target_dialog(
        self,
        context,
        state,
        budget,
        metrics,
        click,
        level_action,
        scene,
        decision,
        now,
    ):
        if decision.get("target_is_ghost"):
            state.target_daily_clicked = True
            state.target_ghost_clicked = True
        if not state.target_ghost_clicked and decision.get("ghost"):
            click(decision["ghost"], scene, "选择捉鬼任务", "select_ghost_target")
            state.target_ghost_clicked = True
        elif not state.target_daily_clicked and decision.get("daily"):
            click(decision["daily"], scene, "展开日常任务", "open_daily_targets")
            state.target_daily_clicked = True
        elif self.level_range_label == "90-115" and not state.target_high_range_clicked and decision.get("range_90_115"):
            click(decision["range_90_115"], scene, "选择90-115预设区间", "select_level_preset")
            state.target_high_range_clicked = True
        elif (
            decision.get("current_min_level") == self.target_min_level
            and decision.get("current_max_level") == self.target_max_level
        ):
            confirm = decision.get("confirm")
            if confirm is None:
                raise RuntimeError("等级范围已设置，但未识别到目标设置确定按钮")
            if state.target_confirm_attempts >= 2:
                raise RuntimeError("确认队伍目标后弹窗仍未关闭，已安全停止")
            click(
                confirm,
                scene,
                f"确认{self.level_range_label}抓鬼目标",
                "confirm_target",
            )
            state.target_confirm_attempts += 1
            state.target_configured = True
            state.phase = "recruit"
            budget.recruitment_started_at = now
        elif state.target_level_swipes < MAX_TARGET_LEVEL_SWIPES:
            current_min = decision.get("current_min_level")
            if current_min is None:
                raise RuntimeError("未能读取最低等级，停止避免盲目拖动")
            metrics.record(
                level_action.execute(
                    context.action_context(
                        scene,
                        {
                            "status": "adjust_level",
                            "current_level": current_min,
                            "target_level": self.target_min_level,
                        },
                        "捉鬼",
                    )
                )
            )
            state.target_level_swipes += 1
        else:
            raise RuntimeError(
                f"调整{self.level_range_label}等级范围超过{MAX_TARGET_LEVEL_SWIPES}次，停止"
            )

    def _handle_team_page(self, context, state, budget, click, scene, decision, status, now):
        state.phase = "recruit"
        if decision.get("target_is_ghost"):
            state.target_configured = True
        if budget.recruitment_started_at is None:
            budget.recruitment_started_at = now
        if budget.recruitment_expired(now):
            raise TimeoutError("招募超过180秒，已停止并保留当前队伍")
        if status == "remove_member_menu":
            if not budget.can_rebuild():
                raise RuntimeError("队伍重组已达最大次数，停止并保留当前队伍")
            click(decision.get("click"), scene, "请离一个异常成员", "confirm_remove_member")
            budget.record_rebuild()
        elif decision.get("create"):
            # The formation screen is visible before a team exists, but its save
            # button does not take effect until the team has been created.
            click(decision["create"], scene, "创建队伍", "create_team")
        elif not state.formation_configured:
            if decision.get("current_formation") == self.formation:
                state.formation_configured = True
            elif decision.get("formation"):
                click(decision["formation"], scene, "打开阵法选择", "open_formation")
            else:
                raise RuntimeError("队伍页未识别到当前阵法入口，已安全停止")
        elif decision.get("remove"):
            if not budget.can_rebuild():
                raise RuntimeError("队伍重组已达最大次数，停止并保留当前队伍")
            click(decision["remove"], scene, "请离一个异常成员", "open_remove_member")
            budget.record_rebuild()
        elif decision.get("offline"):
            click(decision["offline"], scene, "选择离线或暂离成员", "select_offline_member")
        elif decision.get("current_channel"):
            click(decision["current_channel"], scene, "选择当前频道", "select_current_channel")
        elif not state.target_configured and decision.get("target_icon"):
            click(decision["target_icon"], scene, "打开调整目标", "open_target", box=True)
        elif decision.get("full"):
            click(decision.get("close"), scene, "满员后关闭队伍界面", "close_full_team", box=True)
            state.phase = "navigate_zhongkui"
            state.navigate_not_before = now + 8.0
            budget.recruitment_started_at = None
        elif decision.get("auto_match"):
            click(decision["auto_match"], scene, "开启自动匹配", "auto_match")
        elif decision.get("shout") and budget.can_shout(now):
            click(decision["shout"], scene, "一键喊话", "recruit_shout")
            budget.record_shout(now)

    def _result(self, status, state, metrics):
        payload = {
            "rounds": state.completed_rounds,
            "target_rounds": self.target_rounds,
            "steps": state.steps,
            "actions": metrics.actions,
            "clicks": metrics.clicks,
            "drags": metrics.drags,
        }
        if state.last_ghost_progress is not None:
            payload["ghost_progress"] = state.last_ghost_progress
        return WorkflowResult(status, payload)


__all__ = [
    "AdjustTargetLevelAction",
    "MAX_TARGET_LEVEL_SWIPES",
    "MAX_TASKBAR_CLICK_ATTEMPTS",
    "WorkflowState",
    "ZhuoguiClickAction",
    "ZhuoguiObservationSource",
    "ZhuoguiWorkflow",
    "level_drag_end",
]
