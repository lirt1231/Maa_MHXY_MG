"""Platform-neutral counters and time budgets for the 抓鬼 workflow."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


def round_completion_action(completed_rounds: int, target_rounds: int) -> str:
    if target_rounds <= 0:
        raise ValueError("target_rounds must be positive")
    return "finish" if completed_rounds + 1 >= target_rounds else "continue"


@dataclass
class RuntimeBudget:
    max_shouts: int = 3
    shout_interval: float = 45.0
    recruitment_timeout: float = 180.0
    max_rebuilds: int = 3
    battle_timeout: float = 180.0
    round_timeout: float = 1200.0
    started_at: float = field(default_factory=time.monotonic)
    recruitment_started_at: float | None = None
    round_started_at: float | None = None
    battle_started_at: float | None = None
    last_shout_at: float | None = None
    shouts: int = 0
    rebuilds: int = 0

    def can_shout(self, now: float) -> bool:
        if self.shouts >= self.max_shouts:
            return False
        return self.last_shout_at is None or now - self.last_shout_at >= self.shout_interval

    def record_shout(self, now: float) -> None:
        self.shouts += 1
        self.last_shout_at = now

    def recruitment_expired(self, now: float) -> bool:
        return self.recruitment_started_at is not None and now - self.recruitment_started_at > self.recruitment_timeout

    def can_rebuild(self) -> bool:
        return self.rebuilds < self.max_rebuilds

    def record_rebuild(self) -> None:
        if not self.can_rebuild():
            raise RuntimeError("team rebuild budget exhausted")
        self.rebuilds += 1

    def battle_expired(self, now: float) -> bool:
        return self.battle_started_at is not None and now - self.battle_started_at > self.battle_timeout

    def round_expired(self, now: float) -> bool:
        return self.round_started_at is not None and now - self.round_started_at > self.round_timeout


__all__ = ["RuntimeBudget", "round_completion_action"]
