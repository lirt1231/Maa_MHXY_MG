"""Semantic, platform-neutral game actions built on the shared action kernel."""

from __future__ import annotations

from typing import Any, Callable

try:
    from .automation_actions import ActionBudget, EvidenceClickAction, RelativeDragAction
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import ActionBudget, EvidenceClickAction, RelativeDragAction


class OpenActivityCenterAction(EvidenceClickAction):
    def __init__(self, *, budget: ActionBudget | None = None):
        super().__init__(
            name="open_activity_center",
            statuses=("open_activity",),
            reason="打开活动面板",
            wait_seconds=1.0,
            budget=budget,
        )


class ExpandMainShortcutMenuAction(EvidenceClickAction):
    def __init__(self, *, repeat_limit: int, repeat_error: str | None = None):
        super().__init__(
            name="expand_main_shortcut_menu",
            statuses=("expand_menu",),
            reason="展开主界面菜单",
            wait_seconds=0.8,
            repeat_limit=repeat_limit,
            repeat_error=repeat_error,
            signature_factory=lambda context: context.decision_status,
        )


class CloseKnownWelfareOverlayAction(EvidenceClickAction):
    def __init__(self):
        super().__init__(
            name="close_known_welfare_overlay",
            statuses=("close_known_welfare_overlay",),
            reason="关闭上一任务遗留的福利页面",
            wait_seconds=1.5,
            repeat_limit=1,
            repeat_error="福利页面关闭后仍未变化，已安全停止",
            signature_factory=lambda context: context.decision_status,
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


class StartShimenTaskAction(EvidenceClickAction):
    def __init__(self, *, budget: ActionBudget):
        super().__init__(
            name="start_shimen_task",
            statuses=("start_task",),
            reason="前往师父领取师门任务",
            wait_seconds=3.0,
            budget=budget,
            repeat_limit=2,
            repeat_error="师门领取入口点击后页面仍未变化，已安全停止",
        )


class StartShimenFinalAssessmentAction(EvidenceClickAction):
    def __init__(self, *, budget: ActionBudget):
        super().__init__(
            name="start_shimen_final_assessment",
            statuses=("final_assessment",),
            reason="前往师父接受师门最终考核",
            wait_seconds=3.0,
            budget=budget,
            repeat_limit=1,
            repeat_error="师门最终考核入口点击后页面仍未变化，已安全停止",
        )


class ProgressShimenTaskAction(EvidenceClickAction):
    def __init__(self, *, budget: ActionBudget):
        super().__init__(
            name="progress_shimen_task",
            statuses=("accept_task_dialogue", "progress_task"),
            reason=lambda context: {
                "accept_task_dialogue": "接取师门 NPC 对话任务",
                "progress_task": "执行师门去完成按钮",
            }[context.decision_status],
            wait_seconds=2.0,
            budget=budget,
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


class OpenWelfareAction(EvidenceClickAction):
    def __init__(self):
        super().__init__(
            name="open_welfare",
            statuses=("open_welfare",),
            reason="打开福利",
            wait_seconds=1.2,
        )


class OpenScratchCardAction(EvidenceClickAction):
    def __init__(self):
        super().__init__(
            name="open_welfare_scratch_card",
            statuses=("open_scratch",),
            reason="打开刮刮乐",
            wait_seconds=2.5,
        )


class ConfirmFreeRewardAction(EvidenceClickAction):
    def __init__(self):
        super().__init__(
            name="confirm_welfare_reward",
            statuses=("confirm_reward",),
            reason="确认签到奖励",
            wait_seconds=0.8,
            repeat_limit=1,
        )


class ClanWelfareAction(EvidenceClickAction):
    _CONFIG = {
        "open_clan": ("open_clan", "打开帮派", 0.8),
        "open_welfare": ("open_clan_welfare", "打开帮派福利", 0.8),
        "sign_in": ("clan_sign_in", "执行帮派签到", 0.8),
        "claim_dividend": ("claim_clan_dividend", "领取帮派分红", 0.8),
        "confirm_dividend": ("confirm_clan_dividend", "确认领取帮派分红", 0.8),
    }

    def __init__(self, status: str, *, repeat_limit: int, repeat_error: str | None = None):
        if status not in self._CONFIG:
            raise ValueError("帮派福利操作状态无效")
        name, reason, wait_seconds = self._CONFIG[status]
        super().__init__(
            name=name,
            statuses=(status,),
            reason=reason,
            wait_seconds=wait_seconds,
            repeat_limit=repeat_limit,
            repeat_error=repeat_error,
            signature_factory=lambda context: context.decision_status,
        )


class LikeVisibleFeedItemAction(EvidenceClickAction):
    def __init__(self):
        super().__init__(
            name="like_visible_feed_item",
            statuses=("like",),
            reason="点赞当前趣闻动态",
            wait_seconds=1.2,
            repeat_limit=1,
            repeat_error="同一条趣闻动态的点赞控件重复出现，已停止以避免重复点赞",
        )


class ReturnFromFunNewsFeedAction(EvidenceClickAction):
    def __init__(self):
        super().__init__(
            name="return_from_fun_news_feed",
            statuses=("return_to_activity",),
            reason="返回活动页面确认趣闻鉴赏完成",
            wait_seconds=1.2,
            repeat_limit=1,
            repeat_error="趣闻鉴赏返回活动页控件重复出现，已停止以避免重复点击",
        )


class RefreshFeedAction(RelativeDragAction):
    def __init__(self, *, repeat_limit: int):
        super().__init__(
            name="refresh_fun_news_feed",
            statuses=("like", "refresh_feed"),
            # Finger moves upward so the feed advances to older unseen posts.
            # The post cards occupy the feed center. Drag along the fixed
            # right gutter so macOS never begins the gesture on a tappable
            # post body, which opens its detail page.
            start_ratio=(0.445, 0.78),
            end_ratio=(0.445, 0.28),
            reason="下滑趣闻动态以加载下一条",
            wait_seconds=1.5,
            repeat_limit=repeat_limit,
            repeat_error="趣闻动态连续滚动未出现新的可点赞内容，已安全停止",
        )


__all__ = [
    "AcceptIdentifiedDialogueAction",
    "ConfirmEscortAction",
    "ContinueTrackedTaskAction",
    "EnterDungeonStageAction",
    "ExpandMainShortcutMenuAction",
    "ClanWelfareAction",
    "CloseKnownWelfareOverlayAction",
    "ConfirmFreeRewardAction",
    "LikeVisibleFeedItemAction",
    "ReturnFromFunNewsFeedAction",
    "JoinIdentifiedActivityAction",
    "OpenActivityCenterAction",
    "OpenScratchCardAction",
    "OpenWelfareAction",
    "ProgressShimenTaskAction",
    "RefreshFeedAction",
    "SelectActivityTabAction",
    "SelectEscortModeAction",
    "StartDungeonChallengeAction",
    "StartShimenFinalAssessmentAction",
    "StartShimenTaskAction",
]
