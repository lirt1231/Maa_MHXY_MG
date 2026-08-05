"""Platform-neutral navigation decisions for daily-activity workflows."""

from __future__ import annotations

import re

try:
    from .automation_geometry import center, in_region, valid_box
    from .daily_schedule_macos_core import daily_schedule_page_evidence, normalize_text
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_geometry import center, in_region, valid_box
    from daily_schedule_macos_core import daily_schedule_page_evidence, normalize_text


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


ACTIVITY_UNAVAILABLE_PATTERN = (
    r"(?:\d{1,2}[:：]\d{2}开启|未开启|尚未开启|暂未开放|"
    r"明日开启|等级不足|不可参与|已锁定|活动已结束)"
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
    visible_card_actions = find_items(
        texts,
        r"参加|前往|去完成|领取任务|完成|已完成|全部完成",
        image_size,
        {"min_x": 0.42, "max_x": 0.95, "min_y": 0.15, "max_y": 0.82},
    )
    loaded_card_list = len(visible_card_actions) >= 2
    confirmed_daily_page = bool(daily_page) or bool(
        daily_tab_confirmed and daily_tabs
    )
    if confirmed_daily_page and targets:
        target = targets[0]
        completed = [
            item
            for item in find_items(texts, r"完成|已完成|全部完成")
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
        unavailable = [
            item
            for item in find_items(texts, ACTIVITY_UNAVAILABLE_PATTERN)
            if same_activity_card(target, item, image_size)
        ]
        if unavailable:
            return {
                "status": "unavailable",
                "reason": normalize_text(unavailable[0].get("text", "")),
            }
        return {"status": "scroll_daily", "reason": "target_action_not_visible"}

    if daily_page or (daily_tab_confirmed and daily_tabs and loaded_card_list):
        return {"status": "scroll_daily", "reason": "target_not_visible"}

    if daily_tab_confirmed and daily_tabs:
        return {"status": "wait", "reason": "daily_page_loading"}

    if daily_tabs:
        return {"status": "unknown", "reason": "daily_tab_without_page"}

    activity_buttons = find_items(
        texts,
        r"活动",
        image_size,
        {"min_x": 0.18, "max_x": 0.46, "max_y": 0.18},
    )
    if activity_buttons:
        return {"status": "open_activity", "box": valid_box(activity_buttons[0])}

    guide_buttons = find_items(
        texts,
        r"指引",
        image_size,
        {"min_x": 0.14, "max_x": 0.3, "max_y": 0.16},
    )
    ranking_buttons = find_items(
        texts,
        r"排行(?:榜)?",
        image_size,
        {"min_x": 0.25, "max_x": 0.43, "max_y": 0.16},
    )
    if guide_buttons and ranking_buttons:
        guide_center = center(guide_buttons[0])
        ranking_center = center(ranking_buttons[0])
        image_width, image_height = image_size
        if (
            guide_center[0] < ranking_center[0]
            and ranking_center[0] - guide_center[0] <= image_width * 0.18
            and abs(ranking_center[1] - guide_center[1]) <= image_height * 0.04
        ):
            activity_center = [
                round((guide_center[0] + ranking_center[0]) / 2),
                round((guide_center[1] + ranking_center[1]) / 2),
            ]
            width = max(32, round(image_width * 0.05))
            height = max(24, round(image_height * 0.045))
            return {
                "status": "open_activity",
                "box": [
                    activity_center[0] - width // 2,
                    activity_center[1] - height // 2,
                    width,
                    height,
                ],
                "reason": "guide_ranking_anchors",
            }

    activity_template = templates.get("activity_toolbar")
    if activity_template:
        item = {"box": activity_template}
        if in_region(item, image_size, min_x=0.18, max_x=0.46, max_y=0.2):
            return {"status": "open_activity", "box": list(activity_template)}
    return {"status": "unknown"}


__all__ = [
    "ACTIVITY_UNAVAILABLE_PATTERN",
    "center",
    "classify_daily_navigation",
    "find_items",
    "in_region",
    "same_activity_card",
    "valid_box",
]
