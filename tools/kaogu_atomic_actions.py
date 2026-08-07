"""Bounded archaeology asset actions shared by the archaeology workflow."""

from __future__ import annotations

from typing import Mapping

try:
    from .automation_actions import EvidenceClickAction, EvidenceDoubleClickAction, RelativeDragAction
except ImportError:
    from automation_actions import EvidenceClickAction, EvidenceDoubleClickAction, RelativeDragAction


def archaeology_actions(action_limits: Mapping[str, int], *, normalize_drags: int, scan_drags: int):
    actions = {
        status: EvidenceClickAction(name=f"kaogu_{status}", statuses=(status,), reason=("开始考古并前往挖掘地点" if status == "start_archaeology" else f"考古流程：{status}"), wait_seconds=(1.8 if status == "start_archaeology" else 2.0 if status == "close_sale_shop" else 1.0), repeat_limit=limit, repeat_error=f"考古步骤 {status} 重复达到上限，已安全停止", signature_factory=lambda context: context.decision_status)
        for status, limit in action_limits.items()
    }
    actions["normalize_inventory"] = RelativeDragAction(name="normalize_antique_inventory", statuses=("normalize_inventory",), start_ratio=(0.71, 0.31), end_ratio=(0.71, 0.62), reason="将包裹物品列表下拉到顶部", wait_seconds=0.6, repeat_limit=normalize_drags, repeat_error="包裹归顶动作达到上限，已安全停止")
    actions["scan_inventory"] = RelativeDragAction(name="scan_antique_inventory", statuses=("scan_inventory",), start_ratio=(0.71, 0.64), end_ratio=(0.71, 0.50), reason="继续浏览包裹中的非真品古董", wait_seconds=0.5, repeat_limit=scan_drags, repeat_error="已浏览完整包裹但未找到非真品古董，保留现场待确认")
    actions["activate_antique"] = EvidenceDoubleClickAction(name="activate_non_genuine_antique", statuses=("activate_antique",), reason="双击非真品古董并启动自动寻路", click_interval_seconds=0.12, wait_seconds=1.0, repeat_limit=1, repeat_error="古董已双击一次，未确认页面变化前不会重复双击")
    return actions


def purchase_shovel_action(max_purchases: int):
    return EvidenceClickAction(name="buy_archaeology_shovel", statuses=("buy_shovel",), reason="购买一个洛阳铲", wait_seconds=1.8, repeat_limit=max_purchases, repeat_error="洛阳铲购买已达到一次上限，已停止以避免重复购买", signature_factory=lambda context: context.decision_status)


def dig_archaeology_site_action(max_attempts: int, next_dig):
    return EvidenceClickAction(name="dig_archaeology_site", statuses=("dig",), reason=lambda _context: f"进行第 {next_dig()} 次挖掘", wait_seconds=0.0, repeat_limit=max_attempts, repeat_error="本次挖掘按钮连续点击仍未进入结算，已安全停止", signature_factory=lambda context: context.decision_status)


__all__ = ["archaeology_actions", "dig_archaeology_site_action", "purchase_shovel_action"]
