"""Bounded entry workflow for 科举会试 from the game main screen."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

try:
    from .automation_actions import ActionMetrics
    from .automation_workflows import WorkflowContext, WorkflowInspection, WorkflowOutcome, WorkflowResult
    from .daily_activity_actions import DailyActivityActions
    from .daily_activity_navigation_core import classify_daily_navigation
    from .daily_workflow_common import DAILY_TASK_TEMPLATES
except ImportError:
    from automation_actions import ActionMetrics
    from automation_workflows import WorkflowContext, WorkflowInspection, WorkflowOutcome, WorkflowResult
    from daily_activity_actions import DailyActivityActions
    from daily_activity_navigation_core import classify_daily_navigation
    from daily_workflow_common import DAILY_TASK_TEMPLATES


MAX_ENTRY_TRANSITION_POLLS = 12


def _title_texts(texts, image_size) -> str:
    width, height = image_size
    values = []
    for item in texts:
        box = item.get("box") if isinstance(item, Mapping) else None
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        center_x = box[0] + box[2] / 2
        center_y = box[1] + box[3] / 2
        if width * 0.25 <= center_x <= width * 0.75 and height * 0.08 <= center_y <= height * 0.24:
            values.append(str(item.get("text", "")).replace(" ", ""))
    return "".join(values)


def classify_huishi_entry_scene(texts, templates, image_size, *, daily_tab_confirmed=False):
    """Require an independently visible title before beginning answer input."""
    title = _title_texts(texts, image_size)
    if "科举会试" in title:
        return {"status": "exam_ready"}
    if "科举乡试" in title:
        return {"status": "wrong_exam_type", "reason": "当前显示科举乡试，不能开始科举会试"}
    navigation = classify_daily_navigation(
        texts,
        templates,
        image_size,
        target_pattern=r"(?:科举)?会试",
        daily_tab_confirmed=daily_tab_confirmed,
    )
    # A visible 会试 row without a same-card action is ambiguous.  Scrolling
    # here could move a valid row away and later pair a different card's button.
    if navigation.get("status") == "scroll_daily" and any(
        re.fullmatch(r"(?:科举)?会试", str(item.get("text", "")).replace(" ", ""))
        for item in texts
        if isinstance(item, Mapping)
    ):
        return {"status": "unknown", "reason": "会试任务行缺少同卡参加按钮"}
    return navigation


@dataclass(frozen=True)
class HuishiEntryWorkflow:
    max_steps: int = 48
    classifier: Callable[..., Mapping[str, Any]] = classify_huishi_entry_scene

    def __post_init__(self):
        if self.max_steps <= 0:
            raise ValueError("会试进入识别步骤必须是正整数")

    def execute(self, context: WorkflowContext, *, inspect: bool = False) -> WorkflowOutcome:
        metrics = ActionMetrics()
        daily_actions = DailyActivityActions("科举会试")
        awaiting_exam = False
        transition_polls = 0
        unknown_polls = 0
        for step in range(1, self.max_steps + 1):
            scene = context.runtime.read_scene(DAILY_TASK_TEMPLATES)
            decision = self.classifier(
                scene.texts,
                scene.templates,
                scene.image_size,
                daily_tab_confirmed=daily_actions.daily_tab_confirmed,
            )
            status = decision["status"]
            context.emit("entry_status", step=step, status=status, reason=decision.get("reason"))
            if inspect:
                return WorkflowInspection.from_scene(
                    scene, decision, context.inspection_evidence(scene, decision)
                )
            if status == "exam_ready":
                return WorkflowResult("completed", {"actions": metrics.actions, "clicks": metrics.clicks})
            if status == "wrong_exam_type":
                raise RuntimeError(str(decision.get("reason") or "当前不是科举会试页面"))
            if status == "already_completed":
                return WorkflowResult("already_completed", {"actions": metrics.actions, "clicks": metrics.clicks})
            if awaiting_exam:
                transition_polls += 1
                if transition_polls >= MAX_ENTRY_TRANSITION_POLLS:
                    raise RuntimeError("点击会试参加后未进入科举会试页面，已停止以避免重复点击")
                context.emit("entry_wait", poll=transition_polls, status=status)
                context.waiter.wait(1.5)
                continue
            result = daily_actions.execute(context.action_context(scene, decision, "科举会试"))
            if result is not None:
                metrics.record(result)
                unknown_polls = 0
                if status == "join_task":
                    awaiting_exam = True
                    transition_polls = 0
                continue
            if status == "wait":
                unknown_polls = 0
                context.waiter.wait(1.0)
                continue
            unknown_polls += 1
            if unknown_polls >= 3:
                raise RuntimeError("连续三帧无法确认会试入口，已停止以避免无证据点击")
            context.waiter.wait(1.0)
        raise RuntimeError("进入科举会试超过识别步骤上限，已安全停止")


__all__ = ["HuishiEntryWorkflow", "classify_huishi_entry_scene"]
