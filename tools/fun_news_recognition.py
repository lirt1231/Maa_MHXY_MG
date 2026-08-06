"""Scene classification for the 趣闻鉴赏 daily activity."""

from __future__ import annotations

import re

try:
    from .daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
        valid_box,
    )
    from .daily_schedule_macos_core import normalize_text
    from .daily_workflow_common import DAILY_TASK_TEMPLATES
except ImportError:  # Direct execution through a platform runner.
    from daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
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
            {"min_x": 0.32, "max_x": 0.50, "min_y": 0.10, "max_y": 0.94},
        )
    )
    return (
        len(channels) >= 5
        and all(label in top_text for label in ("关注", "图文", "视频", "论坛"))
        and has_open_space
        and has_follow
    )


def _fun_news_like_counter_box(texts, image_size):
    """Find the like count that shares a row with a post's comment count."""
    counters = []
    for item in texts or []:
        box = valid_box(item)
        value = normalize_text(item.get("text", ""))
        if (
            box is None
            or not re.fullmatch(r"\d+(?:\.\d+)?万?", value)
            or not in_region(
                item,
                image_size,
                min_x=0.14,
                max_x=0.34,
                min_y=0.15,
                max_y=0.94,
            )
        ):
            continue
        counters.append(box)
    for likes in counters:
        like_y = likes[1] + likes[3] / 2
        for comments in counters:
            comment_y = comments[1] + comments[3] / 2
            if (
                comments[0] > likes[0]
                and image_size[0] * 0.06 <= comments[0] - likes[0] <= image_size[0] * 0.16
                and abs(comment_y - like_y) <= image_size[1] * 0.04
            ):
                return likes
    return None


def _heart_box_from_like_counter(counter_box, image_size):
    """The heart is fixed just left of the verified like-count row."""
    width, height = image_size
    heart_width = max(24, round(width * 0.034))
    heart_height = max(22, round(height * 0.042))
    return [
        max(0, counter_box[0] - heart_width - round(width * 0.008)),
        max(0, round(counter_box[1] + counter_box[3] / 2 - heart_height / 2)),
        heart_width,
        heart_height,
    ]


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


def classify_fun_news_scene(
    texts, templates, image_size, *, daily_tab_confirmed=False
):
    """Classify only the daily-card navigation or verified feed controls."""
    if templates.get("fun_news_complete") or find_items(
        texts, r"(?:今日)?趣(?:闻|味)鉴赏(?:已)?完成|趣(?:闻|味)鉴赏全部完成"
    ):
        return {"status": "completed"}

    if is_fun_news_feed(texts, image_size):
        hearts = find_items(
            texts,
            r"心|❤|♥",
            image_size,
            {"min_x": 0.12, "max_x": 0.32, "min_y": 0.15, "max_y": 0.94},
        )
        if hearts:
            return {"status": "like", "box": valid_box(hearts[0]), "source": "ocr"}
        counter_box = _fun_news_like_counter_box(texts, image_size)
        if counter_box is not None:
            return {
                "status": "like",
                "box": _heart_box_from_like_counter(counter_box, image_size),
                "source": "like_counter_row",
            }
        return {"status": "refresh_feed"}

    like_box = templates.get("fun_news_like")
    if like_box:
        return {"status": "like", "box": list(like_box)}
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
    "is_fun_news_feed",
]
