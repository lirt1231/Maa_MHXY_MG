"""Platform-neutral scene classification for the bounded 普通运镖 workflow."""

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


def classify_yunbiao_scene(
    texts, templates, image_size, phase, *, daily_tab_confirmed=False
):
    if not daily_tab_confirmed:
        navigation = classify_daily_navigation(
            texts,
            templates,
            image_size,
            target_pattern=r"运镖任务|运镖",
            daily_tab_confirmed=False,
        )
        if navigation["status"] == "open_daily":
            return navigation

    blocked = find_items(
        texts,
        r"活力不足|活力不够|需要.*活力|押金不足|银币不足|无法押镖",
    )
    if blocked:
        return {"status": "blocked_resource", "reason": blocked[0]["text"]}

    completed = find_items(
        texts,
        (
            r"运镖任务完成|今日运镖已完成|本日运镖次数已用完|"
            r"本日剩余(?:运普通镖|普通运镖|普通镖)次数[:：]?0次[。.]?"
        ),
    )
    if completed:
        return {"status": "completed"}

    active = find_items(
        texts,
        r"距离运镖完成[:：]?.*",
        image_size,
        {"max_x": 0.42, "max_y": 0.24},
    )
    if active:
        return {"status": "delivery_active"}

    confirmations = find_items(
        texts,
        r"确定",
        image_size,
        {"min_x": 0.38, "max_x": 0.7, "min_y": 0.45, "max_y": 0.72},
    )
    if confirmations and phase == "confirm_normal":
        return {"status": "confirm_normal", "box": valid_box(confirmations[0])}

    normal = find_items(
        texts,
        r"押送普通镖银",
        image_size,
        {"min_x": 0.68, "min_y": 0.25, "max_y": 0.78},
    )
    if normal:
        return {"status": "select_normal", "box": valid_box(normal[0])}

    other_delivery = find_items(
        texts,
        r"押送(高级|珍贵|特殊|五级|四级).*镖|押送.*高级镖银",
        image_size,
        {"min_x": 0.68, "min_y": 0.2, "max_y": 0.82},
    )
    if other_delivery:
        return {"status": "unsupported_delivery", "reason": other_delivery[0]["text"]}

    sidebar = [
        item
        for item in find_items(texts, r"运镖任务|运镖-.*")
        if in_region(item, image_size, min_x=0.78, min_y=0.1, max_y=0.78)
    ]
    if sidebar:
        return {"status": "continue_task", "box": valid_box(sidebar[0])}

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
        target_pattern=r"运镖任务|运镖",
        daily_tab_confirmed=daily_tab_confirmed,
    )


__all__ = ["classify_yunbiao_scene"]
