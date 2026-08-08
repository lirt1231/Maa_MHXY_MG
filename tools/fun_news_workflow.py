"""Bounded, evidence-backed workflow for 趣闻鉴赏."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

try:
    from .automation_actions import ActionMetrics
    from .automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from .daily_activity_actions import DailyActivityActions
    from .fun_news_recognition import (
        FUN_NEWS_TEMPLATES,
        classify_fun_news_scene,
        fun_news_feed_signature,
    )
    from .game_atomic_actions import (
        LikeVisibleFeedItemAction,
        RefreshFeedAction,
        ReturnFromFunNewsFeedAction,
    )
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import ActionMetrics
    from automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from daily_activity_actions import DailyActivityActions
    from fun_news_recognition import (
        FUN_NEWS_TEMPLATES,
        classify_fun_news_scene,
        fun_news_feed_signature,
    )
    from game_atomic_actions import (
        LikeVisibleFeedItemAction,
        RefreshFeedAction,
        ReturnFromFunNewsFeedAction,
    )


TARGET_LIKES = 5


@dataclass(frozen=True)
class FunNewsWorkflow:
    max_steps: int = 80
    target_likes: int = TARGET_LIKES
    max_feed_scrolls: int = 8
    max_feed_loading_polls: int = 4
    max_completion_verification_polls: int = 8
    classifier: Callable[..., Mapping[str, Any]] = field(
        default=classify_fun_news_scene,
        repr=False,
        compare=False,
    )

    def __post_init__(self):
        if any(
            value <= 0
            for value in (
                self.max_steps,
                self.target_likes,
                self.max_feed_scrolls,
                self.max_feed_loading_polls,
                self.max_completion_verification_polls,
            )
        ):
            raise ValueError("趣闻鉴赏 Workflow 上限必须是正整数")

    def execute(self, context: WorkflowContext, *, inspect: bool = False) -> WorkflowOutcome:
        metrics = ActionMetrics()
        daily_actions = DailyActivityActions("趣闻鉴赏")
        like_action = LikeVisibleFeedItemAction()
        scroll_feed = RefreshFeedAction(repeat_limit=self.max_feed_scrolls)
        return_to_activity = ReturnFromFunNewsFeedAction()
        unknown_polls = 0
        likes = 0
        feed_scrolls = 0
        needs_scroll = False
        awaiting_new_entry = False
        stale_feed_polls = 0
        last_like_box = None
        last_feed_signature = None
        feed_loading_polls = 0
        completion_verification = False
        completion_return_sent = False
        completion_verification_polls = 0

        for step in range(1, self.max_steps + 1):
            scene = context.runtime.read_scene(FUN_NEWS_TEMPLATES)
            decision = self.classifier(
                scene.texts,
                scene.templates,
                scene.image_size,
                daily_tab_confirmed=daily_actions.daily_tab_confirmed,
                completion_verification=completion_verification,
            )
            status = str(decision.get("status", "unknown"))
            context.emit(
                "scene",
                step=step,
                status=status,
                likes=likes,
                feedScrolls=feed_scrolls,
            )
            if inspect:
                return WorkflowInspection.from_scene(
                    scene,
                    decision,
                    context.inspection_evidence(scene, decision),
                )

            if status in {"completed", "already_completed"}:
                return WorkflowResult(
                    "completed" if completion_verification else status,
                    {
                        "likes": likes,
                        "scrolls": feed_scrolls,
                        "actions": metrics.actions,
                        "clicks": metrics.clicks,
                    },
                )

            action_context = context.action_context(scene, decision, "趣闻鉴赏")
            if completion_verification:
                if status == "return_to_activity" and not completion_return_sent:
                    metrics.record(return_to_activity.execute(action_context))
                    completion_return_sent = True
                    completion_verification_polls = 0
                    continue
                completion_verification_polls += 1
                if completion_verification_polls >= self.max_completion_verification_polls:
                    raise RuntimeError(
                        "已完成 5 次点赞，但未能在活动页面确认趣闻鉴赏已完成"
                    )
                context.emit(
                    "completion_verification_wait",
                    task="趣闻鉴赏",
                    poll=completion_verification_polls,
                    returnSent=completion_return_sent,
                )
                context.waiter.wait(1.0)
                continue

            if needs_scroll:
                if status not in {"like", "refresh_feed"}:
                    unknown_polls += 1
                    if unknown_polls >= 3:
                        raise RuntimeError("点赞后未能确认趣闻动态页面，已停止以避免无证据滚动")
                    context.waiter.wait(1.0)
                    continue
                if feed_scrolls >= self.max_feed_scrolls:
                    raise RuntimeError("趣闻动态滚动达到上限，未收集到足够的新动态")
                metrics.record(scroll_feed.execute(action_context))
                feed_scrolls += 1
                needs_scroll = False
                awaiting_new_entry = True
                unknown_polls = 0
                like_action.reset_repetition()
                continue

            navigation_result = daily_actions.execute(action_context)
            if navigation_result is not None:
                metrics.record(navigation_result)
                unknown_polls = 0
                continue

            if status == "like":
                feed_loading_polls = 0
                like_box = tuple(decision["box"])
                feed_signature = fun_news_feed_signature(scene.texts, scene.image_size)
                if (
                    awaiting_new_entry
                    and like_box == last_like_box
                    and (feed_signature is None or feed_signature == last_feed_signature)
                ):
                    stale_feed_polls += 1
                    if stale_feed_polls >= 3:
                        raise RuntimeError(
                            "下滑后仍是同一条趣闻动态，已停止以避免重复点赞"
                        )
                    context.emit(
                        "feed_refresh_wait",
                        task="趣闻鉴赏",
                        poll=stale_feed_polls,
                    )
                    context.waiter.wait(1.0)
                    continue
                metrics.record(like_action.execute(action_context))
                likes += 1
                needs_scroll = likes < self.target_likes
                awaiting_new_entry = False
                stale_feed_polls = 0
                last_like_box = like_box
                last_feed_signature = feed_signature
                unknown_polls = 0
                context.emit(
                    "fun_news_liked",
                    task="趣闻鉴赏",
                    likes=likes,
                    targetLikes=self.target_likes,
                )
                if likes >= self.target_likes:
                    completion_verification = True
                    completion_verification_polls = 0
                continue

            if status == "refresh_feed":
                if feed_loading_polls < self.max_feed_loading_polls:
                    feed_loading_polls += 1
                    context.emit(
                        "feed_loading_wait",
                        task="趣闻鉴赏",
                        poll=feed_loading_polls,
                        limit=self.max_feed_loading_polls,
                    )
                    context.waiter.wait(1.0)
                    continue
                if feed_scrolls >= self.max_feed_scrolls:
                    raise RuntimeError("趣闻动态滚动达到上限，未识别到可点赞内容")
                metrics.record(scroll_feed.execute(action_context))
                feed_scrolls += 1
                feed_loading_polls = 0
                unknown_polls = 0
                continue

            if status == "wait":
                unknown_polls = 0
                context.waiter.wait(1.5)
                continue

            unknown_polls += 1
            if unknown_polls >= 3:
                raise RuntimeError("连续三次无法识别趣闻鉴赏页面，已停止以避免无证据输入")
            context.waiter.wait(1.0)

        raise RuntimeError("趣闻鉴赏超过最大识别步骤，已安全停止")


__all__ = ["FunNewsWorkflow", "TARGET_LIKES"]
