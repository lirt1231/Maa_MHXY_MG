"""Platform-neutral scene classification for the 秘境降妖 workflow."""

from __future__ import annotations

try:
    from .daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
        same_activity_card,
        valid_box,
    )
except ImportError:  # Direct execution through a platform runner.
    from daily_activity_navigation_core import (
        classify_daily_navigation,
        find_items,
        in_region,
        same_activity_card,
        valid_box,
    )


ASSET_BRANCH_PATTERN = (
    r".*(?:购买.*(?:助战|药品|道具|材料)|花费|消耗.*(?:金币|银币|仙玉)|"
    r"原地复活|复活需要|使用.*(?:秘境材料|道具|药品)|招募.*助战|"
    r"购买.*助战|更换.*助战).*"
)
FAILURE_PATTERN = r".*(?:挑战失败|战斗失败|本次挑战失败|今日挑战次数已用完).*"


def _union_box(first, second):
    first_box = valid_box(first)
    second_box = valid_box(second)
    left = min(first_box[0], second_box[0])
    top = min(first_box[1], second_box[1])
    right = max(first_box[0] + first_box[2], second_box[0] + second_box[2])
    bottom = max(first_box[1] + first_box[3], second_box[1] + second_box[3])
    return [left, top, right - left, bottom - top]


def _split_continue_challenge(texts, image_size):
    continue_items = find_items(
        texts,
        r"继续",
        image_size,
        {"min_x": 0.72, "min_y": 0.4, "max_y": 0.9},
    )
    challenge_items = find_items(
        texts,
        r"挑战",
        image_size,
        {"min_x": 0.72, "min_y": 0.4, "max_y": 0.94},
    )
    image_width, image_height = image_size
    for continue_item in continue_items:
        continue_box = valid_box(continue_item)
        continue_x = continue_box[0] + continue_box[2] / 2
        continue_y = continue_box[1] + continue_box[3] / 2
        for challenge_item in challenge_items:
            challenge_box = valid_box(challenge_item)
            challenge_x = challenge_box[0] + challenge_box[2] / 2
            challenge_y = challenge_box[1] + challenge_box[3] / 2
            if (
                abs(continue_x - challenge_x) <= image_width * 0.08
                and 0 <= challenge_y - continue_y <= image_height * 0.1
            ):
                return _union_box(continue_item, challenge_item)
    return None


def _asset_branch_reason(texts, image_size):
    overlay_region = {
        "min_x": 0.18,
        "max_x": 0.88,
        "min_y": 0.18,
        "max_y": 0.82,
    }
    direct = find_items(texts, ASSET_BRANCH_PATTERN, image_size, overlay_region)
    if direct:
        return direct[0]["text"]
    action_object_pairs = (
        (r".*(?:购买|更换|招募).*", r".*助战.*"),
        (r".*(?:购买|使用).*", r".*药品.*"),
    )
    image_width, image_height = image_size
    for action_pattern, object_pattern in action_object_pairs:
        actions = find_items(texts, action_pattern, image_size, overlay_region)
        objects = find_items(texts, object_pattern, image_size, overlay_region)
        for action in actions:
            action_box = valid_box(action)
            action_center = (
                action_box[0] + action_box[2] / 2,
                action_box[1] + action_box[3] / 2,
            )
            for object_item in objects:
                object_box = valid_box(object_item)
                object_center = (
                    object_box[0] + object_box[2] / 2,
                    object_box[1] + object_box[3] / 2,
                )
                if (
                    abs(action_center[0] - object_center[0])
                    <= image_width * 0.35
                    and abs(action_center[1] - object_center[1])
                    <= image_height * 0.12
                ):
                    return f"{action['text']}/{object_item['text']}"
    return None


def classify_mijingxiangyao_scene(
    texts,
    templates,
    image_size,
    phase="navigate",
    *,
    daily_tab_confirmed=False,
):
    """Return one action only when the current frame supplies matching evidence."""
    explicit_already_completed = find_items(
        texts,
        r"今日秘境降妖(?:任务)?(?:已)?完成|秘境降妖今日已完成",
    )
    generic_today_completed = find_items(texts, r"今日已完成")
    activity_anchors = find_items(texts, r"秘境降妖")
    generic_completion_on_target = any(
        same_activity_card(anchor, completion, image_size)
        for anchor in activity_anchors
        for completion in generic_today_completed
    )
    if explicit_already_completed or generic_completion_on_target:
        return {"status": "already_completed"}

    explicit_completion = find_items(
        texts,
        r"秘境降妖(?:任务)?(?:已)?完成|秘境降妖全部完成",
    )
    if explicit_completion:
        return {"status": "completed"}

    failure = find_items(texts, FAILURE_PATTERN)
    if templates.get("failure") or failure:
        reason = failure[0]["text"] if failure else "failure_template"
        return {"status": "challenge_failed", "reason": reason}

    asset_branch_reason = _asset_branch_reason(texts, image_size)
    if asset_branch_reason:
        return {
            "status": "unsupported_asset_branch",
            "reason": asset_branch_reason,
        }

    no_attempts = find_items(texts, r"剩余挑战次数[:：]?0次")
    if no_attempts:
        return {
            "status": "challenge_failed",
            "reason": "今日秘境挑战次数已用完",
        }

    stage_completion = find_items(
        texts,
        r"全部通关|秘境通关|所有关卡已通关",
        image_size,
        {"min_x": 0.68, "min_y": 0.12, "max_y": 0.62},
    )
    if stage_completion:
        return {"status": "completed"}

    stage_transition = find_items(
        texts,
        r"通关|本关通关",
        image_size,
        {"min_x": 0.68, "min_y": 0.12, "max_y": 0.62},
    )
    if stage_transition:
        return {"status": "wait", "reason": "stage_completed"}

    if templates.get("battle"):
        return {"status": "wait", "reason": "battle"}
    battle_cancel = find_items(
        texts,
        r"取消",
        image_size,
        {"min_x": 0.9, "min_y": 0.88},
    )
    if battle_cancel:
        return {"status": "wait", "reason": "battle_ocr"}

    if templates.get("auto_pathing") or find_items(texts, r"自动寻路中?|寻路中"):
        return {"status": "wait", "reason": "auto_pathing"}

    if not daily_tab_confirmed:
        navigation = classify_daily_navigation(
            texts,
            templates,
            image_size,
            target_pattern=r"秘境降妖",
            daily_tab_confirmed=False,
        )
        if navigation["status"] == "open_daily":
            return navigation

    mode_page = templates.get("mode_page") or find_items(texts, r"选择模式")
    if mode_page:
        return {
            "status": "mode_selection_required",
            "reason": "检测到秘境模式选择页，请手动选择后重新启动任务",
        }

    current_lobby = bool(
        find_items(texts, r"秘境降妖")
        and find_items(texts, r"\d+级[-－].*(?:日之井|月之井|秘境)")
        and find_items(texts, r"剩余挑战次数[:：]?\d+次")
    )

    challenge_buttons = find_items(
        texts,
        r"挑战|继续挑战",
        image_size,
        {"min_x": 0.72, "min_y": 0.4, "max_y": 0.94},
    )
    split_challenge_box = _split_continue_challenge(texts, image_size)
    if current_lobby and split_challenge_box is not None:
        return {"status": "start_challenge", "box": split_challenge_box}
    if current_lobby and challenge_buttons:
        return {"status": "start_challenge", "box": valid_box(challenge_buttons[0])}

    battle_buttons = find_items(
        texts,
        r"进入战斗",
        image_size,
        {"min_x": 0.55, "min_y": 0.35, "max_y": 0.78},
    )
    if battle_buttons:
        return {"status": "enter_battle", "box": valid_box(battle_buttons[0])}

    stages = find_items(
        texts,
        r"第\d+关",
        image_size,
        {"min_x": 0.7, "min_y": 0.12, "max_y": 0.62},
    )
    if len(stages) == 1:
        return {
            "status": "select_stage",
            "box": valid_box(stages[0]),
            "stage": stages[0]["text"],
        }
    if len(stages) > 1:
        return {"status": "unknown", "reason": "multiple_stage_candidates"}

    menu_evidence = find_items(texts, r".*云乐游.*")
    menu_options = find_items(
        texts,
        r"秘境降妖",
        image_size,
        {"min_x": 0.42, "min_y": 0.08, "max_y": 0.86},
    )
    if menu_evidence and menu_options:
        return {"status": "enter_dungeon", "box": valid_box(menu_options[0])}

    sidebar_tasks = [
        item
        for item in find_items(texts, r"秘境降妖|秘境[-－].*|继续秘境降妖")
        if in_region(item, image_size, min_x=0.78, min_y=0.1, max_y=0.78)
    ]
    if sidebar_tasks:
        return {"status": "continue_task", "box": valid_box(sidebar_tasks[0])}

    return classify_daily_navigation(
        texts,
        templates,
        image_size,
        target_pattern=r"秘境降妖",
        daily_tab_confirmed=daily_tab_confirmed,
    )


__all__ = [
    "ASSET_BRANCH_PATTERN",
    "FAILURE_PATTERN",
    "classify_mijingxiangyao_scene",
]
