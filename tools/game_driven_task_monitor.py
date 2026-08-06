"""Read-only observation state for tasks completed by the game client."""

from __future__ import annotations

from dataclasses import dataclass

try:
    from .automation_workflows import WorkflowContext
except ImportError:  # Direct execution through a platform runner.
    from automation_workflows import WorkflowContext


@dataclass
class GameDrivenTaskMonitor:
    task_label: str
    max_observations: int
    max_unknown_polls: int
    wait_seconds: float = 2.0
    active: bool = False
    observations: int = 0
    unknown_polls: int = 0

    def __post_init__(self) -> None:
        if self.max_observations <= 0 or self.max_unknown_polls <= 0:
            raise ValueError("游戏自动任务监控上限必须是正整数")
        if self.wait_seconds < 0:
            raise ValueError("游戏自动任务监控等待时间不能为负数")

    def activate(self, context: WorkflowContext, reason: str) -> None:
        if self.active:
            return
        self.active = True
        context.emit("game_auto_monitor_started", task=self.task_label, reason=reason)

    def observe(
        self,
        context: WorkflowContext,
        status: str,
        reason: str | None = None,
    ) -> bool:
        """Observe one non-terminal scene without sending any game input."""
        if not self.active:
            return False

        self.observations += 1
        self.unknown_polls = self.unknown_polls + 1 if status == "unknown" else 0
        context.emit(
            "game_auto_monitor",
            task=self.task_label,
            status=status,
            reason=reason,
            observation=self.observations,
            unknownPolls=self.unknown_polls,
        )
        if self.unknown_polls >= self.max_unknown_polls:
            raise RuntimeError(
                f"{self.task_label}自动执行期间连续无法识别页面，已停止并保留现场"
            )
        if self.observations >= self.max_observations:
            raise RuntimeError(
                f"{self.task_label}自动执行监控达到上限，尚无明确完成证据，已停止并保留现场"
            )
        context.waiter.wait(self.wait_seconds)
        return True


@dataclass
class StableMainCompletionTracker:
    """Confirm an auto-run task ended only after observed gameplay settles at main."""

    required_polls: int = 5
    activity_observed: bool = False
    main_screen_polls: int = 0

    def __post_init__(self) -> None:
        if self.required_polls <= 1:
            raise ValueError("稳定主界面完成确认至少需要两次观察")

    def observe(self, status: str, reason: str | None = None) -> bool:
        active_evidence = status in {"continue_task", "unsupported_asset_branch"} or (
            status == "wait"
            and reason
            in {
                "battle",
                "battle_ocr",
                "auto_pathing",
                "auto_pathing_ocr",
                "game_auto_asset_action",
                "game_auto_continue",
            }
        )
        if active_evidence:
            self.activity_observed = True
            self.main_screen_polls = 0
            return False
        if status == "open_activity" and self.activity_observed:
            self.main_screen_polls += 1
            return self.main_screen_polls >= self.required_polls
        self.main_screen_polls = 0
        return False


__all__ = ["GameDrivenTaskMonitor", "StableMainCompletionTracker"]
