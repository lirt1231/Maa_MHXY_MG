"""Platform-neutral scene classification for the low-risk 师门 workflow."""

from __future__ import annotations

try:
    from .daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
        valid_box,
    )
except ImportError:  # Direct execution through a platform runner.
    from daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
        valid_box,
    )


ASSET_BRANCH_PATTERN = (
    r".*(?:购买|上交|提交|交付|"
    r"寻找.*(?:装备|药品|烹饪|宠物|召唤灵)|"
    r"需要.*(?:装备|药品|烹饪|宠物|召唤灵)|"
    r"选择.*(?:装备|药品|烹饪|宠物|召唤灵)).*"
)


def classify_shimen_scene(
    texts, templates, image_size, *, daily_tab_confirmed=False
):
    if find_items(
        texts, r"师门任务完成|今日师门任务已完成|师门任务全部完成"
    ):
        return {"status": "completed"}

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

    if find_items(texts, r"\d+秒后自动(?:购买|上交)"):
        return {"status": "wait", "reason": "game_auto_asset_action"}
    if find_items(texts, r"\d+秒后继续"):
        return {"status": "wait", "reason": "game_auto_continue"}

    if not daily_tab_confirmed:
        navigation = classify_daily_navigation(
            texts,
            templates,
            image_size,
            target_pattern=r"师门任务",
            daily_tab_confirmed=False,
        )
        if navigation["status"] == "open_daily":
            return navigation

    progress_buttons = find_items(
        texts,
        r"去完成|继续任务",
        image_size,
        {"min_x": 0.3, "max_x": 0.88, "min_y": 0.5, "max_y": 0.72},
    )
    if progress_buttons:
        return {"status": "progress_task", "box": valid_box(progress_buttons[0])}

    task_dialogue_options = [
        item
        for item in find_items(texts, r"师门.+[-－].+")
        if in_region(
            item,
            image_size,
            min_x=0.68,
            min_y=0.55,
            max_y=0.85,
        )
    ]
    if task_dialogue_options:
        return {
            "status": "accept_task_dialogue",
            "box": valid_box(task_dialogue_options[0]),
        }

    sidebar_instructions = [
        item
        for item in find_items(
            texts,
            r"(?:向师父领取.{0,12}任务|领取今天师门任务)",
        )
        if in_region(item, image_size, min_x=0.78, min_y=0.1, max_y=0.48)
    ]
    if sidebar_instructions:
        return {"status": "start_task", "box": valid_box(sidebar_instructions[0])}

    sidebar_continue_instructions = [
        item
        for item in find_items(texts, r"继续师门.{0,12}任务")
        if in_region(item, image_size, min_x=0.78, min_y=0.1, max_y=0.48)
    ]
    if sidebar_continue_instructions:
        return {
            "status": "continue_task",
            "box": valid_box(sidebar_continue_instructions[0]),
        }

    final_assessment_progress = [
        item
        for item in find_items(texts, r"师门\s*[-－].*\(10/10\)")
        if in_region(item, image_size, min_x=0.78, min_y=0.1, max_y=0.48)
    ]
    final_assessment_instructions = [
        item
        for item in find_items(texts, r".*(?:师父|师傅).*最后.*考.*")
        if in_region(item, image_size, min_x=0.78, min_y=0.1, max_y=0.48)
    ]
    if final_assessment_progress and final_assessment_instructions:
        return {
            "status": "final_assessment",
            "box": valid_box(final_assessment_instructions[0]),
        }

    sidebar_details = [
        item
        for item in find_items(texts, r"师门\s*[-－].+")
        if in_region(item, image_size, min_x=0.78, min_y=0.1, max_y=0.48)
    ]
    if sidebar_details:
        return {"status": "continue_task", "box": valid_box(sidebar_details[0])}

    asset_branch = find_items(texts, ASSET_BRANCH_PATTERN)
    if asset_branch:
        return {
            "status": "unsupported_asset_branch",
            "reason": asset_branch[0]["text"],
        }

    return classify_daily_navigation(
        texts,
        templates,
        image_size,
        target_pattern=r"师门任务",
        daily_tab_confirmed=daily_tab_confirmed,
    )


__all__ = ["ASSET_BRANCH_PATTERN", "classify_shimen_scene"]
