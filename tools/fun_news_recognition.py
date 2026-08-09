"""Scene classification for the 趣闻鉴赏 daily activity."""

from __future__ import annotations

try:
    from .daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
        same_activity_card,
        valid_box,
    )
    from .daily_schedule_macos_core import normalize_text
    from .daily_workflow_common import DAILY_TASK_TEMPLATES
except ImportError:  # Direct execution through a platform runner.
    from daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
        same_activity_card,
        valid_box,
    )
    from daily_schedule_macos_core import normalize_text
    from daily_workflow_common import DAILY_TASK_TEMPLATES


FUN_NEWS_TEMPLATES = {
    **DAILY_TASK_TEMPLATES,
    "fun_news_complete": {
        "templates": [
            "qiandao/quwenjianshang_yiwancheng1.png",
            "qiandao/quwenjianshang_yiwancheng2.png",
        ],
        "threshold": 0.90,
        "roi_ratio": (0.30, 0.10, 0.62, 0.62),
    },
    "fun_news_like": {
        "templates": [
            "qiandao/kongjian_jianshang.png",
            "qiandao/kongjian_jianshang3.png",
        ],
        "threshold": 0.90,
        "green_mask": True,
        "roi_ratio": (0.10, 0.13, 0.50, 0.83),
    },
    "fun_news_heart": {
        "templates": ["qiandao/kongjian_like_live.png"],
        "threshold": 0.88,
        "roi_ratio": (0.10, 0.13, 0.50, 0.83),
    },
    "fun_news_feed_close": {
        "templates": ["qiandao/liaotian_guanbi.png"],
        "threshold": 0.90,
        "roi_ratio": (0.55, 0.35, 0.12, 0.22),
    },
}


def is_fun_news_feed(texts, image_size) -> bool:
    """Require independent navigation, tab, and post anchors for 空间动态页."""
    channels = {
        str(item.get("text", ""))
        for item in texts or []
        if valid_box(item)
        and in_region(item, image_size, max_x=0.11, min_y=0.10, max_y=0.68)
        and str(item.get("text", ""))
        in {"系统", "世界", "当前", "门派", "帮派", "队伍", "空间", "同城", "组合"}
    }
    top_text = " ".join(
        normalize_text(item.get("text", ""))
        for item in texts or []
        if valid_box(item)
        and in_region(item, image_size, min_x=0.08, max_x=0.48, min_y=0.05, max_y=0.18)
    )
    has_open_space = bool(
        find_items(
            texts,
            r"打开空间",
            image_size,
            {"max_x": 0.14, "min_y": 0.05, "max_y": 0.20},
        )
    )
    has_follow = bool(
        find_items(
            texts,
            r"\+?关注|十关注",
            image_size,
            {"min_x": 0.32, "max_x": 0.96, "min_y": 0.10, "max_y": 0.94},
        )
    )
    return (
        len(channels) >= 5
        and all(label in top_text for label in ("图文", "视频", "论坛"))
        and has_open_space
        and has_follow
    )


def is_fun_news_detail(texts, image_size) -> bool:
    """Recognize an opened post by its comment composer, never as a feed.

    Both the list and a post detail view expose heart-shaped controls. The
    detail view additionally shows a comment placeholder and submit command;
    treating it as a feed would make a valid heart template affect the wrong
    interaction.
    """
    composer = find_items(
        texts,
        r".*(?:友善评论|文明发言|发表评论|写评论).*",
        image_size,
        {"min_x": 0.40, "max_x": 0.88, "min_y": 0.62, "max_y": 0.94},
    )
    submit = find_items(
        texts,
        r".*(?:发送|发表).*",
        image_size,
        {"min_x": 0.72, "max_x": 0.98, "min_y": 0.62, "max_y": 0.94},
    )
    return bool(composer and submit)


def _heart_box_from_template(template_box):
    """Restrict a composite template hit to its left-hand heart icon.

    The recorded templates include the heart and its adjacent count/control.
    Clicking the match center therefore targets the wrong control.  The heart
    occupies the left portion for both supported template variants.
    """
    x, y, width, height = template_box
    heart_width = max(1, round(width * 0.48))
    return [x, y, heart_width, height]


def _feed_return_box(image_size):
    """Stable return-arrow control within a fully identified feed canvas."""
    width, height = image_size
    return [
        round(width * 0.445),
        round(height * 0.41),
        max(1, round(width * 0.04)),
        max(1, round(height * 0.14)),
    ]


def _template_like_decision(templates):
    """Prefer the dedicated heart template over the older composite samples."""
    heart_box = templates.get("fun_news_heart")
    if heart_box:
        return {"status": "like", "box": list(heart_box), "source": "heart_template"}
    composite_box = templates.get("fun_news_like")
    if composite_box:
        return {
            "status": "like",
            "box": _heart_box_from_template(composite_box),
            "source": "template",
        }
    return None


def fun_news_feed_signature(texts, image_size):
    """Return visible feed text used to prove a scroll reached a new entry."""
    entries = []
    for item in texts or []:
        box = valid_box(item)
        value = normalize_text(item.get("text", ""))
        if (
            box is None
            or len(value) < 2
            or value in {"点赞", "评论", "转发", "更多"}
            or not in_region(
                item,
                image_size,
                min_x=0.10,
                max_x=0.66,
                min_y=0.13,
                max_y=0.95,
            )
        ):
            continue
        entries.append((value, round(box[1] / image_size[1], 2)))
    return tuple(entries) or None


def _completed_activity_card(texts, image_size) -> bool:
    """Recognize the completed 趣闻鉴赏 card without relying on one OCR line.

    The live activity list renders the task name, progress and action in
    separate OCR items.  Require all of them to align to one card before
    treating a newly opened activity page as already complete.
    """
    targets = find_items(texts, r"趣(?:闻|味)鉴赏")
    completions = find_items(texts, r"完成|已完成|全部完成")
    progress = find_items(texts, r"次数\s*5\s*/\s*5")
    activity = find_items(texts, r"活跃\s*5\s*/\s*5")
    image_width, image_height = image_size

    for target in targets:
        target_box = valid_box(target)
        if target_box is None or not in_region(
            target,
            image_size,
            min_x=0.30,
            max_x=0.80,
            min_y=0.14,
            max_y=0.82,
        ):
            continue
        target_x = target_box[0] + target_box[2] / 2
        target_y = target_box[1] + target_box[3] / 2

        def belongs_to_card(item):
            box = valid_box(item)
            if box is None:
                return False
            candidate_x = box[0] + box[2] / 2
            candidate_y = box[1] + box[3] / 2
            return (
                target_x - image_width * 0.06 <= candidate_x <= target_x + image_width * 0.31
                and target_y - image_height * 0.04 <= candidate_y <= target_y + image_height * 0.15
            )

        if (
            any(belongs_to_card(item) for item in progress)
            and any(belongs_to_card(item) for item in activity)
            and any(same_activity_card(target, item, image_size) for item in completions)
        ):
            return True
    return False


def classify_fun_news_scene(
    texts, templates, image_size, *, daily_tab_confirmed=False,
    completion_verification=False,
):
    """Classify only the daily-card navigation or verified feed controls."""
    if templates.get("fun_news_complete") or find_items(
        texts, r"(?:今日)?趣(?:闻|味)鉴赏(?:已)?完成|趣(?:闻|味)鉴赏全部完成"
    ) or _completed_activity_card(texts, image_size):
        return {"status": "completed"}

    if is_fun_news_detail(texts, image_size):
        return {"status": "post_detail"}

    if is_fun_news_feed(texts, image_size):
        if completion_verification:
            return {
                "status": "return_to_activity",
                "box": _feed_return_box(image_size),
                "source": "feed_identity",
            }
        like = _template_like_decision(templates)
        if like is not None:
            return like
        return {"status": "refresh_feed"}

    if templates.get("fun_news_feed_close"):
        return {"status": "refresh_feed"}

    return classify_daily_navigation(
        texts,
        templates,
        image_size,
        target_pattern=r"趣(?:闻|味)鉴赏",
        daily_tab_confirmed=daily_tab_confirmed,
    )


__all__ = [
    "FUN_NEWS_TEMPLATES",
    "classify_fun_news_scene",
    "fun_news_feed_signature",
    "is_fun_news_detail",
    "is_fun_news_feed",
]
