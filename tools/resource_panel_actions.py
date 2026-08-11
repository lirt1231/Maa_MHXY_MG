"""Fixed, evidence-gated controls for resource capture panels."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol, Sequence

try:
    from .fixed_game_controls import (
        CLOSE_STANDARD_PANEL,
        CLOSE_HUISHI_SETTLEMENT,
        CLOSE_TEAM_QUICK_PANEL,
        OPEN_BACKPACK,
        OPEN_CHARACTER_PROFILE,
        FixedGameControl,
        REFERENCE_SIZE,
    )
except ImportError:  # Direct execution through tools/run_resource_snapshot_macos.py.
    from fixed_game_controls import (
        CLOSE_STANDARD_PANEL,
        CLOSE_HUISHI_SETTLEMENT,
        CLOSE_TEAM_QUICK_PANEL,
        OPEN_BACKPACK,
        OPEN_CHARACTER_PROFILE,
        FixedGameControl,
        REFERENCE_SIZE,
    )


class FixedControlInput(Protocol):
    def click(self, x: int, y: int, reference_size: Sequence[int]) -> None: ...


@dataclass(frozen=True)
class FixedPanelControlAction:
    """One stable canvas click, authorized by an already-confirmed page identity."""

    control: FixedGameControl

    def execute(self, native_input: FixedControlInput, *, confirmed_scene: str) -> None:
        if confirmed_scene != self.control.required_scene:
            raise RuntimeError(
                f"动作 {self.control.action_id} 需要页面 {self.control.required_scene}，"
                f"当前确认页为 {confirmed_scene or 'unknown'}"
            )
        native_input.click(*self.control.point, REFERENCE_SIZE)


class OpenBackpackAction(FixedPanelControlAction):
    def __init__(self):
        super().__init__(OPEN_BACKPACK)


class OpenCharacterProfileAction(FixedPanelControlAction):
    def __init__(self):
        super().__init__(OPEN_CHARACTER_PROFILE)


class CloseStandardPanelAction(FixedPanelControlAction):
    def __init__(self):
        super().__init__(CLOSE_STANDARD_PANEL)


class CloseHuishiSettlementAction(FixedPanelControlAction):
    def __init__(self):
        super().__init__(CLOSE_HUISHI_SETTLEMENT)


class CloseTeamQuickPanelAction(FixedPanelControlAction):
    def __init__(self):
        super().__init__(CLOSE_TEAM_QUICK_PANEL)


def parse_number(value: str) -> int | None:
    normalized = str(value or "").translate(
        str.maketrans("０１２３４５６７８９", "0123456789")
    )
    match = re.search(r"-?\d[\d,，.]*", normalized)
    if not match:
        return None
    digits = re.sub(r"[,，.]", "", match.group(0))
    try:
        return int(digits)
    except ValueError:
        return None


def parse_backpack_resources(results: list[dict]) -> dict:
    """Read the two fixed currency slots in the normalized bag footer."""
    candidates: list[tuple[float, dict, int]] = []
    for item in results:
        x, y, width, _ = item["box"]
        text = re.sub(r"\s+", "", item["text"])
        center_x = x + width / 2
        if not 565 <= y <= 640 or not re.fullmatch(r"\d[\d,，.]*", text):
            continue
        value = parse_number(text)
        if value is not None:
            candidates.append((center_x, item, value))

    def in_slot(left: float, right: float, target: float):
        matches = [item for item in candidates if left <= item[0] < right]
        return min(matches, key=lambda item: abs(item[0] - target)) if matches else None

    gold = in_slot(120, 300, 210)
    silver = in_slot(300, 520, 395)
    return {
        "gold": None if gold is None else gold[2],
        "silver": None if silver is None else silver[2],
        "goldItem": None if gold is None else gold[1],
        "silverItem": None if silver is None else silver[1],
    }


def parse_profile_resources(results: list[dict]) -> dict:
    level = None
    experience = None
    required = None
    level_item = None
    experience_item = None
    for item in results:
        text = re.sub(r"\s+", "", item["text"])
        if "级" in text:
            match = re.search(r"(\d+)级", text)
            if match:
                digits = match.group(1)
                for width in (3, 2, 1):
                    if len(digits) >= width:
                        candidate = int(digits[-width:])
                        if 20 <= candidate <= 200:
                            level = candidate
                            level_item = item
                            break
        x, y, _, _ = item["box"]
        fraction = re.fullmatch(r"(\d[\d,，.]*)/(\d[\d,，.]*)", text)
        if fraction and y >= 500 and x >= 450:
            numerator = parse_number(fraction.group(1))
            denominator = parse_number(fraction.group(2))
            if numerator is not None and denominator is not None and numerator >= 10000:
                experience, required = numerator, denominator
                experience_item = item
    return {
        "level": level,
        "experience": experience,
        "experienceRequired": required,
        "levelItem": level_item,
        "experienceItem": experience_item,
    }


@dataclass(frozen=True)
class CaptureWallet:
    """Read the stable, fixed wallet slots from a confirmed backpack frame."""

    def capture(self, results: list[dict]) -> dict:
        return parse_backpack_resources(results)


@dataclass(frozen=True)
class CaptureExperience:
    """Read level and experience from a confirmed character profile frame."""

    def capture(self, results: list[dict]) -> dict:
        return parse_profile_resources(results)


__all__ = [
    "CloseStandardPanelAction",
    "CloseHuishiSettlementAction",
    "CloseTeamQuickPanelAction",
    "CaptureExperience",
    "CaptureWallet",
    "FixedPanelControlAction",
    "OpenBackpackAction",
    "OpenCharacterProfileAction",
    "parse_backpack_resources",
    "parse_number",
    "parse_profile_resources",
]
