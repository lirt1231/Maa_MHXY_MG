"""Platform-neutral low-risk sect task workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

try:
    from .automation_actions import ActionBudget, ActionMetrics, EvidenceClickAction
    from .automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from .daily_activity_actions import DailyActivityActions
    from .daily_workflow_common import DAILY_TASK_TEMPLATES
    from .shimen_recognition import classify_shimen_scene
    from .game_driven_task_monitor import (
        GameDrivenTaskMonitor,
        StableMainCompletionTracker,
    )
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import ActionBudget, ActionMetrics, EvidenceClickAction
    from automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from daily_activity_actions import DailyActivityActions
    from daily_workflow_common import DAILY_TASK_TEMPLATES
    from shimen_recognition import classify_shimen_scene
    from game_driven_task_monitor import (
        GameDrivenTaskMonitor,
        StableMainCompletionTracker,
    )


@dataclass(frozen=True)
class ShimenWorkflow:
    max_steps: int = 360
    max_task_clicks: int = 40
    max_daily_loading_polls: int = 8
    max_daily_reselects: int = 2
    classifier: Callable[..., Mapping[str, Any]] = field(
        default=classify_shimen_scene,
        repr=False,
        compare=False,
    )

    def __post_init__(self):
        limits = (
            self.max_steps,
            self.max_task_clicks,
            self.max_daily_loading_polls,
            self.max_daily_reselects,
        )
        if any(value <= 0 for value in limits):
            raise ValueError("师门 Workflow 上限必须是正整数")

    def execute(self, context: WorkflowContext, *, inspect: bool = False) -> WorkflowOutcome:
        metrics = ActionMetrics()
        unknown_polls = 0
        daily_loading_polls = 0
        daily_reselects = 0
        final_assessment_started = False
        daily_actions = DailyActivityActions("师门任务")
        monitor = GameDrivenTaskMonitor(
            "师门任务",
            max_observations=self.max_steps,
            max_unknown_polls=12,
        )
        completion_tracker = StableMainCompletionTracker()
        task_budget = ActionBudget(
            self.max_task_clicks,
            "师门推进点击达到上限，已停止以避免循环",
        )
        start_task = EvidenceClickAction(
            name="start_shimen_task",
            statuses=("start_task",),
            reason="前往师父领取师门任务",
            wait_seconds=3.0,
            budget=task_budget,
            repeat_limit=2,
            repeat_error="师门领取入口点击后页面仍未变化，已安全停止",
        )
        continue_task = EvidenceClickAction(
            name="continue_shimen_task",
            statuses=("continue_task",),
            reason="继续右侧师门任务",
            wait_seconds=7.0,
            budget=task_budget,
            repeat_limit=self.max_task_clicks,
        )
        final_assessment = EvidenceClickAction(
            name="start_shimen_final_assessment",
            statuses=("final_assessment",),
            reason="前往师父接受师门最终考核",
            wait_seconds=3.0,
            budget=task_budget,
            repeat_limit=1,
            repeat_error="师门最终考核入口点击后页面仍未变化，已安全停止",
        )
        progress_task = EvidenceClickAction(
            name="progress_shimen_task",
            statuses=("accept_task_dialogue", "progress_task"),
            reason=lambda action_context: {
                "accept_task_dialogue": "接取师门 NPC 对话任务",
                "progress_task": "执行师门去完成按钮",
            }[action_context.decision_status],
            wait_seconds=2.0,
            budget=task_budget,
        )
        recent_statuses = []

        for step in range(1, self.max_steps + 1):
            scene = context.runtime.read_scene(DAILY_TASK_TEMPLATES)
            decision = self.classifier(
                scene.texts,
                scene.templates,
                scene.image_size,
                daily_tab_confirmed=daily_actions.daily_tab_confirmed,
            )
            status = decision["status"]
            if not monitor.active:
                recent_statuses.append(status)
                recent_statuses = recent_statuses[-10:]
                if (
                    len(recent_statuses) == 10
                    and recent_statuses[:5] == recent_statuses[5:]
                ):
                    raise RuntimeError("师门重复相同的五步页面序列，已停止以避免导航循环")
            context.emit(
                "scene",
                step=step,
                status=status,
                reason=decision.get("reason"),
                monitoring=monitor.active,
            )
            if inspect:
                return WorkflowInspection.from_scene(
                    scene,
                    decision,
                    context.inspection_evidence(scene, decision),
                )

            if status in {"completed", "already_completed"}:
                return WorkflowResult(
                    status,
                    {"actions": metrics.actions, "clicks": metrics.clicks},
                )
            if monitor.active:
                completed_on_main = completion_tracker.observe(
                    status,
                    decision.get("reason"),
                )
                if completion_tracker.main_screen_polls:
                    context.emit(
                        "completion_candidate",
                        task="师门任务",
                        evidence="stable_main_after_task_disappeared",
                        poll=completion_tracker.main_screen_polls,
                        limit=completion_tracker.required_polls,
                    )
                if completed_on_main:
                    return WorkflowResult(
                        "completed",
                        {
                            "actions": metrics.actions,
                            "clicks": metrics.clicks,
                            "completionEvidence": "stable_main_after_task_disappeared",
                            "confirmationPolls": completion_tracker.main_screen_polls,
                        },
                    )
            if monitor.observe(context, status, decision.get("reason")):
                continue
            if status == "unsupported_asset_branch":
                raise RuntimeError(
                    f"师门尚未进入只读监控，无法确认资产页面属于当前任务：{decision['reason']}"
                )
            if status == "final_assessment" and not final_assessment_started:
                action_context = context.action_context(scene, decision, "师门任务")
                metrics.record(final_assessment.execute(action_context))
                final_assessment_started = True
                monitor.activate(context, "师门已进入最终考核，交由游戏自动完成")
                unknown_polls = 0
                continue
            action_context = context.action_context(scene, decision, "师门任务")
            if status == "start_task":
                daily_actions.reset_page_state()
                daily_loading_polls = 0
                daily_reselects = 0
                metrics.record(start_task.execute(action_context))
                unknown_polls = 0
                continue
            if status == "continue_task":
                daily_actions.reset_page_state()
                daily_loading_polls = 0
                daily_reselects = 0
                metrics.record(continue_task.execute(action_context))
                monitor.activate(context, "已点击右侧师门任务，交由游戏自动完成")
                unknown_polls = 0
                continue
            if status in {"accept_task_dialogue", "progress_task"}:
                daily_actions.reset_page_state()
                daily_loading_polls = 0
                daily_reselects = 0
                metrics.record(progress_task.execute(action_context))
                unknown_polls = 0
                continue
            navigation_result = daily_actions.execute(action_context)
            if navigation_result is not None:
                daily_loading_polls = 0
                if status in {"scroll_daily", "join_task"}:
                    daily_reselects = 0
                metrics.record(navigation_result)
                unknown_polls = 0
                continue
            if status == "wait":
                unknown_polls = 0
                if decision.get("reason") == "daily_page_loading":
                    daily_loading_polls += 1
                    if daily_loading_polls >= self.max_daily_loading_polls:
                        if daily_reselects >= self.max_daily_reselects:
                            raise RuntimeError(
                                "师门日常活动页两次重新选择后仍未加载，已安全停止"
                            )
                        daily_actions.reset_page_state()
                        daily_loading_polls = 0
                        daily_reselects += 1
                        context.emit(
                            "daily_reselect",
                            attempt=daily_reselects,
                            reason="daily_page_loading_timeout",
                        )
                    context.waiter.wait(1.0)
                    continue
                daily_loading_polls = 0
                context.waiter.wait(2.0)
                continue

            unknown_polls += 1
            context.emit(
                "unknown_evidence",
                reason=decision.get("reason"),
                texts=[item.get("text", "")[:80] for item in scene.texts[:24]],
            )
            if unknown_polls >= 5:
                raise RuntimeError("连续五次无法识别师门任务页面，已停止以避免无证据点击")
            context.waiter.wait(1.0)

        raise RuntimeError("师门任务超过最大识别步骤，已安全停止")


__all__ = ["ShimenWorkflow"]
