"""Fixed, evidence-gated controls for resource capture panels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

try:
    from .fixed_game_controls import (
        CLOSE_STANDARD_PANEL,
        OPEN_BACKPACK,
        OPEN_CHARACTER_PROFILE,
        FixedGameControl,
        REFERENCE_SIZE,
    )
except ImportError:  # Direct execution through tools/run_resource_snapshot_macos.py.
    from fixed_game_controls import (
        CLOSE_STANDARD_PANEL,
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


__all__ = [
    "CloseStandardPanelAction",
    "FixedPanelControlAction",
    "OpenBackpackAction",
    "OpenCharacterProfileAction",
]
