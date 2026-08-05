"""Platform-neutral atomic actions shared by quiz-style tasks."""

from __future__ import annotations

import math
from typing import Any, Sequence

try:
    from .automation_actions import (
        ActionBudget,
        ActionContext,
        ActionResult,
        AtomicAction,
    )
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_actions import ActionBudget, ActionContext, ActionResult, AtomicAction


def _valid_point(value: Any, image_size: Sequence[int]) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not isinstance(image_size, (list, tuple)) or len(image_size) != 2:
        return None
    x, y = value
    width, height = image_size
    values = (x, y, width, height)
    if not all(
        isinstance(item, (int, float))
        and not isinstance(item, bool)
        and math.isfinite(item)
        for item in values
    ):
        return None
    if width <= 0 or height <= 0 or not (0 <= x < width and 0 <= y < height):
        return None
    return x, y


class AnswerOptionClickAction(AtomicAction):
    """Click one recognized answer while rejecting duplicate answer evidence."""

    name = "answer_option_click"
    statuses = frozenset(("ready",))

    def __init__(
        self,
        task_label: str,
        *,
        max_clicks: int,
        wait_seconds: float,
    ):
        if wait_seconds < 0:
            raise ValueError("答题点击后的等待时间不能为负数")
        self.task_label = task_label
        self.wait_seconds = wait_seconds
        self.budget = ActionBudget(
            max_clicks,
            f"{task_label}答案点击达到上限，已停止以避免异常循环",
        )
        self._selected_answers: set[tuple[str, str]] = set()

    @property
    def clicks(self) -> int:
        return self.budget.used

    def execute(self, context: ActionContext) -> ActionResult:
        if not self.accepts(context):
            raise RuntimeError(
                f"动作 {self.name} 不接受状态 {context.decision_status or 'missing'}"
            )
        question_key = context.decision.get("question_key")
        answer = context.decision.get("answer")
        if not isinstance(question_key, str) or not question_key.strip():
            raise RuntimeError(f"{self.task_label}答案点击缺少稳定题目标识")
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError(f"{self.task_label}答案点击缺少答案标识")
        point = _valid_point(context.decision.get("click"), context.scene.image_size)
        if point is None:
            raise RuntimeError(f"{self.task_label}答案点击坐标无效或超出当前画面")

        signature = question_key, answer
        if signature in self._selected_answers:
            raise RuntimeError(
                f"检测到同一道题重复选择同一答案：{context.decision.get('question', question_key)}，已安全停止"
            )
        self.budget.consume()
        self._selected_answers.add(signature)
        reason = f"回答{self.task_label}题目"
        context.input.click(point, context.scene, reason)
        if self.wait_seconds:
            context.waiter.wait(self.wait_seconds)
        result = ActionResult(
            name=self.name,
            decision_status=context.decision_status,
            actions=1,
            clicks=1,
            wait_seconds=self.wait_seconds,
        )
        context.emit(
            "action_completed",
            action=self.name,
            task=context.task_label,
            decision=context.decision_status,
            questionKey=question_key,
            answer=answer,
            clicks=1,
            drags=0,
        )
        return result


__all__ = ["AnswerOptionClickAction"]
