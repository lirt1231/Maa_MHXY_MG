"""Scene classification for the 趣闻鉴赏 daily activity."""

from __future__ import annotations

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
            {"min_x": 0.32, "max_x": 0.50, "min_y": 0.10, "max_y": 0.94},
        )
    )
    return (
        len(channels) >= 5
        and all(label in top_text for label in ("关注", "图文", "视频", "论坛"))
        and has_open_space
        and has_follow
    )


def _heart_box_from_template(template_box):
    """Restrict a composite template hit to its left-hand heart icon.

    The recorded templates include the heart and its adjacent count/control.
    Clicking the match center therefore targets the wrong control.  The heart
    occupies the left portion for both supported template variants.
    """
    x, y, width, height = template_box
    heart_width = max(1, round(width * 0.48))
    return [x, y, heart_width, height]


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


def classify_fun_news_scene(
    texts, templates, image_size, *, daily_tab_confirmed=False,
    completion_verification=False,
):
    """Classify only the daily-card navigation or verified feed controls."""
    if templates.get("fun_news_complete") or find_items(
        texts, r"(?:今日)?趣(?:闻|味)鉴赏(?:已)?完成|趣(?:闻|味)鉴赏全部完成"
    ):
        return {"status": "completed"}

    if is_fun_news_feed(texts, image_size):
        if completion_verification:
            close_box = templates.get("fun_news_feed_close")
            if close_box:
                return {
                    "status": "return_to_activity",
                    "box": list(close_box),
                    "source": "template",
                }
            return {"status": "wait", "reason": "feed_return_control_missing"}
        like = _template_like_decision(templates)
        if like is not None:
            return like
        return {"status": "refresh_feed"}

    like = _template_like_decision(templates)
    if like is not None:
        return like
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
