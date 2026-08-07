"""Evidence-backed atomic actions used by the 捉鬼 team configuration flow."""

from __future__ import annotations

import math

try:
    from .automation_actions import ActionContext, ActionResult, AtomicAction
    from .automation_geometry import relative_point
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import ActionContext, ActionResult, AtomicAction
    from automation_geometry import relative_point


def level_drag_end(current_level: int, target_level: int) -> float:
    difference = current_level - target_level
    if difference == 0:
        raise ValueError("current level already matches target")
    if difference > 0:
        return 0.75 if difference > 15 else 0.45
    return 0.10 if difference < -15 else 0.34


def evidence_point(value, image_size):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x, y = value
    width, height = image_size
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) for item in (x, y, width, height)):
        return None
    if width <= 0 or height <= 0 or not (0 <= x < width and 0 <= y < height):
        return None
    return x, y


class ZhuoguiEvidenceClickAction(AtomicAction):
    name = "zhuogui_evidence_click"
    statuses = frozenset(("click",))

    def execute(self, context: ActionContext) -> ActionResult:
        point = evidence_point(context.decision.get("point"), context.scene.image_size)
        if point is None:
            raise RuntimeError(f"{context.decision.get('reason', '捉鬼动作')} 缺少经过识别的点击位置")
        context.input.click(point, context.scene, str(context.decision["reason"]))
        result = ActionResult(self.name, "click", 1, clicks=1)
        context.emit("action_completed", action=str(context.decision.get("semantic", self.name)), task=context.task_label, decision="click", clicks=1, drags=0)
        return result


class AdjustTargetLevelAction(AtomicAction):
    name = "adjust_zhuogui_minimum_level"
    statuses = frozenset(("adjust_level",))

    def execute(self, context: ActionContext) -> ActionResult:
        current, target = context.decision.get("current_level"), context.decision.get("target_level")
        if not isinstance(current, int) or not isinstance(target, int):
            raise RuntimeError("调整捉鬼最低等级缺少可靠等级证据")
        start = relative_point((0.623, 0.38), context.scene.image_size)
        end = relative_point((0.623, level_drag_end(current, target)), context.scene.image_size)
        context.input.drag(start, end, context.scene, f"调整最低等级至{target}")
        result = ActionResult(self.name, "adjust_level", 1, drags=1)
        context.emit("action_completed", action=self.name, task=context.task_label, decision="adjust_level", currentLevel=current, targetLevel=target, clicks=0, drags=1)
        return result


__all__ = ["AdjustTargetLevelAction", "ZhuoguiEvidenceClickAction", "level_drag_end"]
