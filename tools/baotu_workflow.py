"""Platform-neutral 宝图 task workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


TASK_ENTRY_OBSERVATION_POLLS = 6
TASK_ENTRY_TRANSITION_STATUSES = frozenset(
    {"open_activity", "open_daily", "join_task", "scroll_daily", "unknown"}
)

try:
    from .automation_actions import ActionMetrics
    from .automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from .baotu_recognition import classify_baotu_scene
    from .daily_activity_actions import DailyActivityActions
    from .daily_workflow_common import DAILY_TASK_TEMPLATES
    from .game_driven_task_monitor import (
        GameDrivenTaskMonitor,
        StableMainCompletionTracker,
    )
    from .game_atomic_actions import AcceptIdentifiedDialogueAction
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import ActionMetrics
    from automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from baotu_recognition import classify_baotu_scene
    from daily_activity_actions import DailyActivityActions
    from daily_workflow_common import DAILY_TASK_TEMPLATES
    from game_driven_task_monitor import (
        GameDrivenTaskMonitor,
        StableMainCompletionTracker,
    )
    from game_atomic_actions import AcceptIdentifiedDialogueAction


@dataclass(frozen=True)
class BaotuWorkflow:
    max_steps: int = 180
    classifier: Callable[..., Mapping[str, Any]] = field(
        default=classify_baotu_scene,
        repr=False,
        compare=False,
    )

    def __post_init__(self):
        if self.max_steps <= 0:
            raise ValueError("宝图任务最大识别步骤必须是正整数")

    def execute(self, context: WorkflowContext, *, inspect: bool = False) -> WorkflowOutcome:
        metrics = ActionMetrics()
        unknown_polls = 0
        task_entry_observation_polls = 0
        awaiting_task_entry = False
        daily_actions = DailyActivityActions("宝图任务")
        monitor = GameDrivenTaskMonitor(
            "宝图任务",
            max_observations=self.max_steps,
            max_unknown_polls=12,
        )
        completion_tracker = StableMainCompletionTracker()
        accept_dialogue = AcceptIdentifiedDialogueAction("宝图任务", wait_seconds=1.0)

        for step in range(1, self.max_steps + 1):
            scene = context.runtime.read_scene(DAILY_TASK_TEMPLATES)
            decision = self.classifier(
                scene.texts,
                scene.templates,
                scene.image_size,
                daily_tab_confirmed=daily_actions.daily_tab_confirmed,
            )
            status = decision["status"]
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

            if status == "continue_task" and not monitor.active:
                monitor.activate(context, "已识别到游戏正在自动执行宝图任务")
            if monitor.active:
                completed_on_main = completion_tracker.observe(
                    status,
                    decision.get("reason"),
                )
                if completion_tracker.main_screen_polls:
                    context.emit(
                        "completion_candidate",
                        task="宝图任务",
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

            if awaiting_task_entry and status in TASK_ENTRY_TRANSITION_STATUSES:
                task_entry_observation_polls += 1
                if task_entry_observation_polls <= TASK_ENTRY_OBSERVATION_POLLS:
                    unknown_polls = 0
                    context.emit(
                        "task_entry_wait",
                        poll=task_entry_observation_polls,
                        status=status,
                    )
                    context.waiter.wait(2.0)
                    continue
                awaiting_task_entry = False

            action_context = context.action_context(scene, decision, "宝图任务")
            navigation_result = daily_actions.execute(action_context)
            if navigation_result is not None:
                metrics.record(navigation_result)
                if status == "join_task":
                    awaiting_task_entry = True
                    task_entry_observation_polls = 0
                unknown_polls = 0
                continue
            if status == "accept_dialogue":
                awaiting_task_entry = False
                daily_actions.reset_page_state()
                metrics.record(accept_dialogue.execute(action_context))
                monitor.activate(context, "宝图任务已接取，交由游戏自动完成")
                unknown_polls = 0
                continue
            if status == "wait":
                unknown_polls = 0
                context.waiter.wait(2.0)
                continue

            unknown_polls += 1
            if unknown_polls >= 3:
                raise RuntimeError("连续三次无法识别宝图任务页面，已停止以避免无证据点击")
            context.waiter.wait(1.0)

        raise RuntimeError("宝图任务超过最大识别步骤，已安全停止")


__all__ = [
    "BaotuWorkflow",
    "TASK_ENTRY_OBSERVATION_POLLS",
]
