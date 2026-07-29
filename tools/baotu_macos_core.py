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


def classify_baotu_scene(
    texts, templates, image_size, *, daily_tab_confirmed=False
):
    if find_items(texts, r"宝图任务完成|今日宝图任务已完成|宝图任务全部完成"):
        return {"status": "completed"}

    completion_dialogues = find_items(
        texts,
        r"任务完成",
        image_size,
        {"min_x": 0.3, "max_x": 0.7, "min_y": 0.1, "max_y": 0.45},
    )
    treasure_map_rewards = find_items(
        texts,
        r".*(?:搜出|获得).*(?:藏宝|宝图).*",
        image_size,
        {"min_x": 0.25, "max_x": 0.75, "min_y": 0.35, "max_y": 0.75},
    )
    if completion_dialogues and treasure_map_rewards:
        return {"status": "completed"}

    if not daily_tab_confirmed:
        navigation = classify_daily_navigation(
            texts,
            templates,
            image_size,
            target_pattern=r"宝图任务",
            daily_tab_confirmed=False,
        )
        if navigation["status"] == "open_daily":
            return navigation

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
    battle_cancel = find_items(
        texts,
        r"取消",
        image_size,
        {"min_x": 0.9, "min_y": 0.88},
    )
    if battle_cancel:
        return {"status": "wait", "reason": "battle_ocr"}
    if find_items(texts, r"自动寻路中?|寻路中"):
        return {"status": "wait", "reason": "auto_pathing_ocr"}

    return classify_daily_navigation(
        texts,
        templates,
        image_size,
        target_pattern=r"宝图任务",
        daily_tab_confirmed=daily_tab_confirmed,
    )


__all__ = ["classify_baotu_scene"]
