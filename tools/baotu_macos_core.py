"""Pure scene classification for the macOS 宝图获得 workflow."""

from __future__ import annotations

try:
    from .daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
        valid_box,
    )
except ImportError:  # Direct execution through tools/run_baotu_macos.py.
    from daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
        valid_box,
    )


def classify_baotu_scene(texts, templates, image_size):
    if find_items(texts, r"宝图任务完成|今日宝图任务已完成|宝图任务全部完成"):
        return {"status": "completed"}

    dialogue = find_items(
        texts,
        r"听听无妨",
        image_size,
        {"min_x": 0.55, "min_y": 0.12, "max_y": 0.82},
    )
    if dialogue:
        return {"status": "accept_dialogue", "box": valid_box(dialogue[0])}

    task_items = find_items(texts, r"宝图任务|宝图-.*")
    sidebar_tasks = [
        item
        for item in task_items
        if in_region(item, image_size, min_x=0.78, min_y=0.1, max_y=0.78)
    ]
    if sidebar_tasks:
        return {"status": "continue_task", "box": valid_box(sidebar_tasks[0])}

    if templates.get("battle"):
        return {"status": "wait", "reason": "battle"}
    if templates.get("auto_pathing"):
        return {"status": "wait", "reason": "auto_pathing"}

    return classify_daily_navigation(
        texts,
        templates,
        image_size,
        target_pattern=r"宝图任务",
    )


__all__ = ["classify_baotu_scene"]
