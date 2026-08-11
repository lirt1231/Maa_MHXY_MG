"""Stable game-canvas controls shared by snapshot and recovery workflows."""

from __future__ import annotations

from dataclasses import dataclass


REFERENCE_SIZE = (1020, 718)


@dataclass(frozen=True)
class FixedGameControl:
    action_id: str
    point: tuple[int, int]
    required_scene: str
    reason: str


OPEN_BACKPACK = FixedGameControl(
    "open_backpack", (985, 630), "main_screen", "打开包裹"
)
OPEN_CHARACTER_PROFILE = FixedGameControl(
    "open_character_profile", (978, 55), "main_screen", "打开人物属性"
)
CLOSE_STANDARD_PANEL = FixedGameControl(
    "close_standard_panel", (890, 112), "standard_panel", "关闭已确认的标准面板"
)
CLOSE_HUISHI_SETTLEMENT = FixedGameControl(
    "close_huishi_settlement", (909, 204), "huishi_settlement", "关闭已确认的会试结算页"
)
CLOSE_TEAM_QUICK_PANEL = FixedGameControl(
    "close_team_quick_panel", (798, 113), "team_quick_panel", "收起已确认的快捷组队面板"
)
CLOSE_TEMPORARY_BAG = FixedGameControl(
    "close_temporary_bag", (691, 172), "temporary_bag", "关闭已确认的临时包裹"
)


__all__ = [
    "CLOSE_STANDARD_PANEL",
    "CLOSE_HUISHI_SETTLEMENT",
    "CLOSE_TEMPORARY_BAG",
    "CLOSE_TEAM_QUICK_PANEL",
    "FixedGameControl",
    "OPEN_BACKPACK",
    "OPEN_CHARACTER_PROFILE",
    "REFERENCE_SIZE",
]
