"""Semantic, platform-neutral game actions built on the shared action kernel."""

from __future__ import annotations

from typing import Any, Callable

try:
    from .automation_actions import ActionBudget, EvidenceClickAction
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import ActionBudget, EvidenceClickAction


class OpenActivityCenterAction(EvidenceClickAction):
    def __init__(self, *, budget: ActionBudget | None = None):
        super().__init__(
            name="open_activity_center",
            statuses=("open_activity",),
            reason="打开活动面板",
            wait_seconds=1.0,
            budget=budget,
        )


class SelectActivityTabAction(EvidenceClickAction):
    def __init__(self, *, budget: ActionBudget | None = None):
        super().__init__(
            name="select_daily_activity_tab",
            statuses=("open_daily",),
            reason="打开日常活动页签",
            wait_seconds=2.0,
            budget=budget,
        )


class JoinIdentifiedActivityAction(EvidenceClickAction):
    def __init__(self, *, budget: ActionBudget | None = None):
        super().__init__(
            name="join_identified_activity",
            statuses=("join_task",),
            reason=lambda context: f"参加{context.task_label}",
            wait_seconds=2.0,
            budget=budget,
        )


class ContinueTrackedTaskAction(EvidenceClickAction):
    def __init__(
        self,
        task_label: str,
        *,
        wait_seconds: float,
        budget: ActionBudget | None = None,
        repeat_limit: int = 12,
        signature_factory: Callable[[Any], Any] | None = None,
    ):
        super().__init__(
            name="continue_tracked_task",
            statuses=("continue_task",),
            reason=f"继续{task_label}",
            wait_seconds=wait_seconds,
            budget=budget,
            repeat_limit=repeat_limit,
            signature_factory=signature_factory,
        )


class AcceptIdentifiedDialogueAction(EvidenceClickAction):
    def __init__(
        self,
        task_label: str,
        *,
        wait_seconds: float,
        budget: ActionBudget | None = None,
        repeat_limit: int = 2,
    ):
        super().__init__(
            name="accept_identified_dialogue",
            statuses=("accept_dialogue",),
            reason=f"接受{task_label}对话",
            wait_seconds=wait_seconds,
            budget=budget,
            repeat_limit=repeat_limit,
        )


class SelectEscortModeAction(EvidenceClickAction):
    def __init__(self, *, signature_factory: Callable[[Any], Any] | None = None):
        super().__init__(
            name="select_ordinary_escort",
            statuses=("select_normal",),
            reason="选择押送普通镖银",
            wait_seconds=1.0,
            repeat_limit=1,
            repeat_error="普通镖选项点击后页面未变化，已安全停止",
            signature_factory=signature_factory,
        )


class ConfirmEscortAction(EvidenceClickAction):
    def __init__(self):
        super().__init__(
            name="confirm_ordinary_escort",
            statuses=("confirm_normal",),
            reason="确认押送普通镖银",
            wait_seconds=2.0,
            repeat_limit=3,
        )


class EnterDungeonStageAction(EvidenceClickAction):
    def __init__(
        self,
        *,
        status: str,
        reason: str,
        wait_seconds: float,
        budget: ActionBudget | None = None,
        signature_factory: Callable[[Any], Any] | None = None,
    ):
        if status not in {"enter_dungeon", "select_stage", "enter_battle"}:
            raise ValueError("秘境进入操作状态无效")
        super().__init__(
            name={
                "enter_dungeon": "enter_mijingxiangyao_dungeon",
                "select_stage": "select_mijingxiangyao_stage",
                "enter_battle": "enter_mijingxiangyao_battle",
            }[status],
            statuses=(status,),
            reason=reason,
            wait_seconds=wait_seconds,
            budget=budget,
            signature_factory=signature_factory,
        )


class StartDungeonChallengeAction(EvidenceClickAction):
    def __init__(
        self,
        *,
        signature_factory: Callable[[Any], Any] | None = None,
    ):
        super().__init__(
            name="start_mijingxiangyao_challenge",
            statuses=("start_challenge",),
            reason="开始秘境挑战",
            wait_seconds=1.0,
            signature_factory=signature_factory,
        )


__all__ = [
    "AcceptIdentifiedDialogueAction",
    "ConfirmEscortAction",
    "ContinueTrackedTaskAction",
    "EnterDungeonStageAction",
    "JoinIdentifiedActivityAction",
    "OpenActivityCenterAction",
    "SelectActivityTabAction",
    "SelectEscortModeAction",
    "StartDungeonChallengeAction",
]
