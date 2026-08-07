"""Named navigation actions unique to the 三界奇缘 entry flow."""

from __future__ import annotations

try:
    from .automation_actions import ActionBudget, EvidenceClickAction
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_actions import ActionBudget, EvidenceClickAction


class SanjieNavigationAction(EvidenceClickAction):
    _CONFIG = {
        "open_battle_menu": "打开战斗快捷菜单",
        "click_activity_side_menu": "从战斗快捷菜单打开活动",
        "dismiss_known_popup": "关闭已识别弹窗",
        "close_bag_overlay": "关闭包裹遮挡",
    }

    def __init__(
        self,
        status: str,
        *,
        budget: ActionBudget,
        repeat_limit: int,
    ):
        if status not in self._CONFIG:
            raise ValueError("三界奇缘导航操作状态无效")
        super().__init__(
            name=f"sanjie_{status}",
            statuses=(status,),
            reason=self._CONFIG[status],
            wait_seconds=1.2,
            budget=budget,
            repeat_limit=repeat_limit,
            repeat_error=f"检测到重复导航动作 {status}，已停止以避免死循环",
        )


__all__ = ["SanjieNavigationAction"]
