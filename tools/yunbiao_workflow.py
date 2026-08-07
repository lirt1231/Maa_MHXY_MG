"""Platform-neutral ordinary escort task workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

try:
    from .automation_actions import (
        ActionMetrics,
    )
    from .automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from .daily_activity_actions import DailyActivityActions
    from .daily_workflow_common import DAILY_TASK_TEMPLATES
    from .game_atomic_actions import (
        ConfirmEscortAction,
        ContinueTrackedTaskAction,
        SelectEscortModeAction,
    )
    from .yunbiao_recognition import classify_yunbiao_scene
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import (
        ActionMetrics,
    )
    from automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from daily_activity_actions import DailyActivityActions
    from daily_workflow_common import DAILY_TASK_TEMPLATES
    from game_atomic_actions import (
        ConfirmEscortAction,
        ContinueTrackedTaskAction,
        SelectEscortModeAction,
    )
    from yunbiao_recognition import classify_yunbiao_scene


@dataclass(frozen=True)
class YunbiaoWorkflow:
    rounds: int = 3
    max_steps: int = 240
    final_main_confirmations: int = 2
    final_confirmation_wait_seconds: float = 3.0
    classifier: Callable[..., Mapping[str, Any]] = field(
        default=classify_yunbiao_scene,
        repr=False,
        compare=False,
    )

    def __post_init__(self):
        if not 1 <= self.rounds <= 3:
            raise ValueError("普通运镖轮数必须在 1-3 之间")
        if self.max_steps <= 0:
            raise ValueError("普通运镖最大识别步骤必须是正整数")
        if self.final_main_confirmations <= 0:
            raise ValueError("普通运镖最终主界面确认次数必须是正整数")
        if self.final_confirmation_wait_seconds < 0:
            raise ValueError("普通运镖最终确认等待时间不能为负数")

    def execute(self, context: WorkflowContext, *, inspect: bool = False) -> WorkflowOutcome:
        phase = "enter"
        metrics = ActionMetrics()
        accepted_rounds = 0
        completed_rounds = 0
        unknown_polls = 0
        confirmed_final_main_polls = 0
        daily_actions = DailyActivityActions("普通运镖")
        continue_task = ContinueTrackedTaskAction("普通运镖任务", wait_seconds=2.0)
        select_normal = SelectEscortModeAction(
            signature_factory=lambda action_context: (
                accepted_rounds + 1,
                tuple(action_context.decision["box"]),
            ),
        )
        confirm_normal = ConfirmEscortAction()

        for step in range(1, self.max_steps + 1):
            scene = context.runtime.read_scene(DAILY_TASK_TEMPLATES)
            decision = self.classifier(
                scene.texts,
                scene.templates,
                scene.image_size,
                phase,
                daily_tab_confirmed=daily_actions.daily_tab_confirmed,
            )
            status = decision["status"]
            reported_status = (
                "final_delivery_main_candidate"
                if (
                    accepted_rounds >= self.rounds
                    and phase == "delivering"
                    and status == "open_activity"
                )
                else status
            )
            context.emit(
                "scene",
                step=step,
                phase=phase,
                status=reported_status,
                acceptedRounds=accepted_rounds,
                completedRounds=completed_rounds,
            )
            if inspect:
                return WorkflowInspection.from_scene(
                    scene,
                    decision,
                    context.inspection_evidence(scene, decision),
                )

            if status in {"completed", "already_completed"}:
                result_status = "completed" if completed_rounds or accepted_rounds else status
                return WorkflowResult(
                    result_status,
                    {
                        "rounds": max(completed_rounds, accepted_rounds),
                        "target_rounds": self.rounds,
                        "actions": metrics.actions,
                        "clicks": metrics.clicks,
                    },
                )

            if accepted_rounds >= self.rounds:
                if status == "delivery_active":
                    phase = "delivering"
                    confirmed_final_main_polls = 0
                    unknown_polls = 0
                    context.waiter.wait(2.0)
                    continue
                if phase == "delivering" and status == "select_normal":
                    completed_rounds = accepted_rounds
                    return WorkflowResult(
                        "completed",
                        {
                            "rounds": completed_rounds,
                            "target_rounds": self.rounds,
                            "actions": metrics.actions,
                            "clicks": metrics.clicks,
                        },
                    )
                if phase == "delivering" and status == "open_activity":
                    confirmed_final_main_polls += 1
                    context.emit(
                        "final_delivery_main_candidate",
                        confirmation=confirmed_final_main_polls,
                        required=self.final_main_confirmations,
                    )
                    if confirmed_final_main_polls >= self.final_main_confirmations:
                        completed_rounds = accepted_rounds
                        return WorkflowResult(
                            "completed",
                            {
                                "rounds": completed_rounds,
                                "target_rounds": self.rounds,
                                "actions": metrics.actions,
                                "clicks": metrics.clicks,
                            },
                        )
                    context.waiter.wait(self.final_confirmation_wait_seconds)
                    continue
                confirmed_final_main_polls = 0
                context.emit(
                    "final_delivery_wait",
                    reason=(
                        "等待最后一轮普通运镖进入进行中"
                        if phase != "delivering"
                        else "等待最后一轮普通运镖结束"
                    ),
                )
                context.waiter.wait(2.0)
                continue
            action_context = context.action_context(scene, decision, "普通运镖")
            if status == "blocked_resource":
                raise RuntimeError(f"普通运镖资源条件不满足：{decision['reason']}")
            if status == "unsupported_delivery":
                raise RuntimeError(
                    f"只允许普通运镖，当前未识别到普通镖选项：{decision['reason']}"
                )
            if status == "delivery_active":
                if phase != "delivering":
                    accepted_rounds = max(accepted_rounds, completed_rounds + 1)
                    phase = "delivering"
                unknown_polls = 0
                context.waiter.wait(2.0)
                continue
            if status == "select_normal":
                daily_actions.reset_page_state()
                if phase == "delivering":
                    completed_rounds += 1
                    if completed_rounds >= self.rounds:
                        return WorkflowResult(
                            "completed",
                            {
                                "rounds": completed_rounds,
                                "target_rounds": self.rounds,
                                "actions": metrics.actions,
                                "clicks": metrics.clicks,
                            },
                        )
                    phase = "enter"
                if accepted_rounds >= self.rounds:
                    raise RuntimeError("普通运镖达到目标轮数后仍出现领取选项，已保留现场")
                metrics.record(select_normal.execute(action_context))
                phase = "confirm_normal"
                unknown_polls = 0
                continue
            if status == "confirm_normal":
                daily_actions.reset_page_state()
                metrics.record(confirm_normal.execute(action_context))
                accepted_rounds += 1
                phase = "await_delivery"
                unknown_polls = 0
                continue
            if status == "continue_task":
                daily_actions.reset_page_state()
                metrics.record(continue_task.execute(action_context))
                unknown_polls = 0
                continue
            navigation_result = daily_actions.execute(action_context)
            if navigation_result is not None:
                metrics.record(navigation_result)
                unknown_polls = 0
                continue
            if status == "wait":
                unknown_polls = 0
                context.waiter.wait(2.0)
                continue

            unknown_polls += 1
            unknown_limit = 6 if phase in {"await_delivery", "delivering"} else 3
            if unknown_polls >= unknown_limit:
                raise RuntimeError("连续多次无法识别普通运镖页面，已停止以避免无证据点击")
            context.waiter.wait(1.0)

        raise RuntimeError("普通运镖超过最大识别步骤，已安全停止")


__all__ = ["YunbiaoWorkflow"]
