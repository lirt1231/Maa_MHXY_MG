"""Platform-neutral actions for workflows entered through the daily activity list."""

from __future__ import annotations

try:
    from .automation_actions import (
        ActionBudget,
        ActionContext,
        ActionResult,
        AtomicAction,
        EvidenceClickAction,
    )
    from .daily_activity_scroll import DailyActivityScrollAction
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_actions import (
        ActionBudget,
        ActionContext,
        ActionResult,
        AtomicAction,
        EvidenceClickAction,
    )
    from daily_activity_scroll import DailyActivityScrollAction


class ScrollDailyActivitiesAction(AtomicAction):
    name = "scroll_daily_activities"
    statuses = frozenset({"scroll_daily"})

    def __init__(
        self,
        scroller: DailyActivityScrollAction | None = None,
        budget: ActionBudget | None = None,
    ):
        self.scroller = scroller or DailyActivityScrollAction()
        self.budget = budget

    def reset(self) -> None:
        self.scroller.reset()

    def execute(self, context: ActionContext) -> ActionResult:
        if not self.accepts(context):
            raise RuntimeError(
                f"动作 {self.name} 不接受状态 {context.decision_status or 'missing'}"
            )
        if self.budget is not None:
            self.budget.consume()
        step = self.scroller.perform(context.input, context.scene, "浏览日常活动列表")
        context.waiter.wait(1.2)
        result = ActionResult(
            name=self.name,
            decision_status=context.decision_status,
            actions=1,
            drags=1,
            wait_seconds=1.2,
        )
        context.emit(
            "action_completed",
            action=self.name,
            task=context.task_label,
            decision=context.decision_status,
            direction=step.direction,
            index=step.index,
            clicks=result.clicks,
            drags=result.drags,
        )
        return result


class DailyActivityActions:
    """Reusable action set for main screen -> daily tab -> task card."""

    def __init__(
        self,
        task_label: str,
        *,
        max_inputs: int = 14,
        budget: ActionBudget | None = None,
    ):
        self.task_label = task_label
        self.daily_tab_confirmed = False
        self.budget = budget or ActionBudget(
            max_inputs,
            f"{task_label}活动导航达到输入上限，已安全停止",
        )
        self.scroll = ScrollDailyActivitiesAction(budget=self.budget)
        self.actions: dict[str, AtomicAction] = {
            "open_activity": EvidenceClickAction(
                name="open_activity",
                statuses=("open_activity",),
                reason="打开活动面板",
                wait_seconds=1.0,
                budget=self.budget,
            ),
            "open_daily": EvidenceClickAction(
                name="open_daily_activity_tab",
                statuses=("open_daily",),
                reason="打开日常活动页签",
                wait_seconds=2.0,
                budget=self.budget,
            ),
            "join_task": EvidenceClickAction(
                name="join_daily_activity",
                statuses=("join_task",),
                reason=lambda context: f"参加{context.task_label}",
                wait_seconds=2.0,
                budget=self.budget,
            ),
            "scroll_daily": self.scroll,
        }

    def reset_page_state(self) -> None:
        self.daily_tab_confirmed = False

    def execute(self, context: ActionContext) -> ActionResult | None:
        if context.decision_status == "unavailable":
            reason = str(context.decision.get("reason") or "当前时段不可参与")
            raise RuntimeError(f"{self.task_label}当前不可参与：{reason}")
        action = self.actions.get(context.decision_status)
        if action is None:
            return None
        for candidate in self.actions.values():
            if candidate is not action and isinstance(candidate, EvidenceClickAction):
                candidate.reset_repetition()
        result = action.execute(context)
        if context.decision_status == "open_daily":
            self.scroll.reset()
            self.daily_tab_confirmed = True
        elif context.decision_status in {"open_activity", "join_task"}:
            self.daily_tab_confirmed = False
        return result


__all__ = ["DailyActivityActions", "ScrollDailyActivitiesAction"]
