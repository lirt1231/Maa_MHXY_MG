"""Scene classification for the 趣闻鉴赏 daily activity."""

from __future__ import annotations

try:
    from .daily_activity_navigation_core import classify_daily_navigation, find_items
    from .daily_workflow_common import DAILY_TASK_TEMPLATES
except ImportError:  # Direct execution through a platform runner.
    from daily_activity_navigation_core import classify_daily_navigation, find_items
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


def classify_fun_news_scene(
    texts, templates, image_size, *, daily_tab_confirmed=False
):
    """Classify only the daily-card navigation or verified feed controls."""
    if templates.get("fun_news_complete") or find_items(
        texts, r"(?:今日)?趣(?:闻|味)鉴赏(?:已)?完成|趣(?:闻|味)鉴赏全部完成"
    ):
        return {"status": "completed"}

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


__all__ = ["FUN_NEWS_TEMPLATES", "classify_fun_news_scene"]
