"""Pure navigation decisions for macOS daily-activity task runners."""

from __future__ import annotations

import re

try:
    from .daily_schedule_macos_core import daily_schedule_page_evidence, normalize_text
except ImportError:  # Direct execution through tools/run_*.py.
    from daily_schedule_macos_core import daily_schedule_page_evidence, normalize_text


def valid_box(item):
    box = item.get("box") if isinstance(item, dict) else None
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if not all(isinstance(value, (int, float)) for value in box):
        return None
    if box[2] <= 0 or box[3] <= 0:
        return None
    return list(box)


def center(item):
    x, y, width, height = valid_box(item)
    return [round(x + width / 2), round(y + height / 2)]


def in_region(item, image_size, *, min_x=0, max_x=1, min_y=0, max_y=1):
    box = valid_box(item)
    if box is None or not image_size or len(image_size) != 2:
        return False
    image_width, image_height = image_size
    if image_width <= 0 or image_height <= 0:
        return False
    x, y = center(item)
    return (
        min_x <= x / image_width <= max_x
        and min_y <= y / image_height <= max_y
    )


def find_items(texts, pattern, image_size=None, region=None):
    regex = re.compile(pattern)
    matches = []
    for item in texts:
        if valid_box(item) is None:
            continue
        if not regex.fullmatch(normalize_text(item.get("text", ""))):
            continue
        if region and not in_region(item, image_size, **region):
            continue
        matches.append(item)
    return matches


def same_activity_card(anchor, candidate, image_size):
    image_width, image_height = image_size
    anchor_x, anchor_y = center(anchor)
    candidate_x, candidate_y = center(candidate)
    return (
        anchor_x + image_width * 0.04 <= candidate_x <= anchor_x + image_width * 0.31
        and anchor_y - image_height * 0.04
        <= candidate_y
        <= anchor_y + image_height * 0.14
    )


def classify_daily_navigation(
    texts,
    templates,
    image_size,
    *,
    target_pattern,
    daily_tab_confirmed=False,
):
    """Return one bounded navigation action backed by fresh OCR/template evidence."""
    daily_page = daily_schedule_page_evidence(texts, image_size, templates)
    daily_tabs = find_items(
        texts,
        r"日常活动",
        image_size,
        {"min_x": 0.03, "max_x": 0.36, "min_y": 0.07, "max_y": 0.34},
    )
    if daily_tabs and not daily_tab_confirmed:
        return {"status": "open_daily", "box": valid_box(daily_tabs[0])}

    targets = find_items(texts, target_pattern)
    if daily_page and targets:
        target = targets[0]
        completed = [
            item
            for item in find_items(texts, r"已完成|全部完成")
            if same_activity_card(target, item, image_size)
        ]
        if completed:
            return {"status": "already_completed"}
        actions = [
            item
            for item in find_items(texts, r"参加|前往|去完成|领取任务")
            if same_activity_card(target, item, image_size)
        ]
        if actions:
            return {"status": "join_task", "box": valid_box(actions[0])}
        return {"status": "scroll_daily", "reason": "target_action_not_visible"}

    if daily_page:
        return {"status": "scroll_daily", "reason": "target_not_visible"}

    if daily_tabs:
        return {"status": "unknown", "reason": "daily_tab_confirmed_without_page"}

    activity_buttons = find_items(
        texts,
        r"活动",
        image_size,
        {"min_x": 0.18, "max_x": 0.46, "max_y": 0.18},
    )
    if activity_buttons:
        return {"status": "open_activity", "box": valid_box(activity_buttons[0])}

    activity_template = templates.get("activity_toolbar")
    if activity_template:
        item = {"box": activity_template}
        if in_region(item, image_size, min_x=0.18, max_x=0.46, max_y=0.2):
            return {"status": "open_activity", "box": list(activity_template)}
    return {"status": "unknown"}


__all__ = [
    "center",
    "classify_daily_navigation",
    "find_items",
    "in_region",
    "same_activity_card",
    "valid_box",
]
