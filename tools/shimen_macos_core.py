"""Pure scene classification for the low-risk macOS 师门 workflow."""

from __future__ import annotations

try:
    from .daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
        valid_box,
    )
except ImportError:  # Direct execution through tools/run_shimen_macos.py.
    from daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
        valid_box,
    )


ASSET_BRANCH_PATTERN = (
    r".*(?:购买|上交|提交|交付|商会|摆摊|购买召唤灵|购买宠物|"
    r"寻找.*(?:装备|药品|烹饪|宠物|召唤灵)|"
    r"需要.*(?:装备|药品|烹饪|宠物|召唤灵)|"
    r"选择.*(?:装备|药品|烹饪|宠物|召唤灵)).*"
)


def classify_shimen_scene(texts, templates, image_size):
    if templates.get("completion") or find_items(
        texts, r"师门任务完成|今日师门任务已完成|师门任务全部完成"
    ):
        return {"status": "completed"}

    if templates.get("battle"):
        return {"status": "wait", "reason": "battle"}
    if templates.get("auto_pathing"):
        return {"status": "wait", "reason": "auto_pathing"}

    asset_branch = find_items(texts, ASSET_BRANCH_PATTERN)
    if asset_branch:
        return {
            "status": "unsupported_asset_branch",
            "reason": asset_branch[0]["text"],
        }

    progress_buttons = find_items(
        texts,
        r"去完成|继续任务",
        image_size,
        {"min_x": 0.3, "max_x": 0.88, "min_y": 0.5, "max_y": 0.72},
    )
    if progress_buttons:
        return {"status": "progress_task", "box": valid_box(progress_buttons[0])}

    sidebar_tasks = [
        item
        for item in find_items(texts, r"师门任务|师门-.*")
        if in_region(item, image_size, min_x=0.78, min_y=0.1, max_y=0.78)
    ]
    if sidebar_tasks:
        return {"status": "continue_task", "box": valid_box(sidebar_tasks[0])}

    return classify_daily_navigation(
        texts,
        templates,
        image_size,
        target_pattern=r"师门任务",
    )


__all__ = ["ASSET_BRANCH_PATTERN", "classify_shimen_scene"]
