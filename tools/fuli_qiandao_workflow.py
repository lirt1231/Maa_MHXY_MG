"""Platform-neutral 福利签到 workflow and domain actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

try:
    from .automation_actions import (
        ActionContext,
        ActionMetrics,
        ActionResult,
        AtomicAction,
    )
    from .automation_geometry import valid_box
    from .automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from .fuli_qiandao_recognition import classify_welfare_scene, scratch_gestures
    from .game_atomic_actions import (
        ConfirmFreeRewardAction,
        OpenScratchCardAction,
        OpenWelfareAction,
    )
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import (
        ActionContext,
        ActionMetrics,
        ActionResult,
        AtomicAction,
    )
    from automation_geometry import valid_box
    from automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from fuli_qiandao_recognition import classify_welfare_scene, scratch_gestures
    from game_atomic_actions import (
        ConfirmFreeRewardAction,
        OpenScratchCardAction,
        OpenWelfareAction,
    )


WELFARE_SIGN_IN_TEMPLATES = {
    "welfare_header": ["qiandao/fuli.png"],
    "scratch": ["qiandao/qiandaohuadong.png"],
}


class ScratchWelfareCardAction(AtomicAction):
    name = "scratch_welfare_card"
    statuses = frozenset({"scratch_ready"})

    def __init__(self, *, max_attempts: int = 2):
        if max_attempts <= 0:
            raise ValueError("福利刮卡次数上限必须是正整数")
        self.max_attempts = max_attempts
        self.attempts = 0

    def execute(self, context: ActionContext) -> ActionResult:
        if not self.accepts(context):
            raise RuntimeError(f"动作 {self.name} 不接受状态 {context.decision_status or 'missing'}")
        box = valid_box({"box": context.decision.get("box")})
        if box is None:
            raise RuntimeError("福利刮卡缺少经过识别的有效区域")
        if self.attempts >= self.max_attempts:
            raise RuntimeError("刮奖区域连续两次未发生变化，已安全停止")
        self.attempts += 1
        gestures = scratch_gestures(box)
        for index, (start, end) in enumerate(gestures, 1):
            context.input.drag(start, end, context.scene, f"刮奖区域第 {index} 行")
            context.waiter.wait(0.15)
        context.waiter.wait(0.8)
        result = ActionResult(
            name=self.name,
            decision_status=context.decision_status,
            actions=len(gestures),
            drags=len(gestures),
            wait_seconds=len(gestures) * 0.15 + 0.8,
        )
        context.emit(
            "action_completed",
            action=self.name,
            task=context.task_label,
            decision=context.decision_status,
            clicks=0,
            drags=result.drags,
        )
        return result


@dataclass(frozen=True)
class WelfareSignInWorkflow:
    max_steps: int = 20
    classifier: Callable[..., Mapping[str, Any]] = field(
        default=classify_welfare_scene,
        repr=False,
        compare=False,
    )

    def __post_init__(self):
        if self.max_steps <= 0:
            raise ValueError("福利签到最大识别步骤必须是正整数")

    def execute(self, context: WorkflowContext, *, inspect: bool = False) -> WorkflowOutcome:
        phase = "enter"
        idle_polls = 0
        unknown_polls = 0
        metrics = ActionMetrics()
        actions = {
            "open_welfare": OpenWelfareAction(),
            "open_scratch": OpenScratchCardAction(),
            "scratch_ready": ScratchWelfareCardAction(),
            "confirm_reward": ConfirmFreeRewardAction(),
        }

        for step in range(1, self.max_steps + 1):
            scene = context.runtime.read_scene(WELFARE_SIGN_IN_TEMPLATES)
            decision = self.classifier(scene.texts, scene.templates, phase, scene.image_size)
            status = decision["status"]
            context.emit("scene", step=step, phase=phase, status=status)
            if inspect:
                return WorkflowInspection.from_scene(
                    scene,
                    decision,
                    context.inspection_evidence(scene, decision),
                )

            if status != "unknown":
                unknown_polls = 0
            if status != "welfare_idle":
                idle_polls = 0
            if status in {"completed", "already_completed"}:
                return WorkflowResult(
                    status,
                    {"actions": metrics.actions, "clicks": metrics.clicks},
                )

            action = actions.get(status)
            if action is not None:
                metrics.record(
                    action.execute(context.action_context(scene, decision, "福利签到"))
                )
                phase = {
                    "open_welfare": "welfare",
                    "open_scratch": "scratch",
                    "scratch_ready": "verify",
                    "confirm_reward": "verify",
                }[status]
                continue
            if status == "welfare_idle":
                idle_polls += 1
                if idle_polls >= 4:
                    raise RuntimeError("福利页面未显示今日签到入口或明确完成状态，已安全停止")
            else:
                unknown_polls += 1
                unknown_limit = 8 if phase in {"welfare", "scratch", "verify"} else 3
                if unknown_polls >= unknown_limit:
                    raise RuntimeError(
                        f"连续{unknown_limit}次无法识别福利签到页面，已停止以避免无证据点击"
                    )
            context.waiter.wait(0.8)

        raise RuntimeError("福利签到超过最大识别步骤，已安全停止")


__all__ = [
    "ScratchWelfareCardAction",
    "WELFARE_SIGN_IN_TEMPLATES",
    "WelfareSignInWorkflow",
]
