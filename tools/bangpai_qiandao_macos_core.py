"""Pure scene classification for evidence-backed 帮派签到和分红领取."""

from __future__ import annotations

import re


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def find_text(texts: list[dict], pattern: str):
    regex = re.compile(pattern)
    return next(
        (item for item in texts if regex.search(normalized_text(item["text"]))),
        None,
    )


def box_in_region(box, image_size, *, min_x=0.0, max_x=1.0, min_y=0.0, max_y=1.0):
    if not box:
        return False
    if image_size is None:
        return True
    x, y, width, height = box
    image_width, image_height = image_size
    if width <= 0 or height <= 0 or image_width <= 0 or image_height <= 0:
        return False
    center_x = (x + width / 2) / image_width
    center_y = (y + height / 2) / image_height
    return min_x <= center_x <= max_x and min_y <= center_y <= max_y


def classify_clan_sign_in_scene(
    texts: list[dict], templates: dict, phase: str, image_size=None
) -> dict:
    no_clan = find_text(texts, r"未加入帮派|申请加入帮派|创建帮派")
    if no_clan:
        return {"status": "no_clan"}

    clan_header = templates.get("clan_header")
    clan_title = find_text(texts, r"^[·•]?帮派[·•]?$")
    clan_title_valid = clan_title and box_in_region(
        clan_title["box"],
        image_size,
        min_x=0.35,
        max_x=0.65,
        max_y=0.25,
    )
    sign_in_section = find_text(texts, r"^帮派签到$")
    ocr_header_valid = (
        clan_title_valid
        and sign_in_section
        and box_in_region(
            sign_in_section["box"],
            image_size,
            min_x=0.6,
            max_x=0.9,
            min_y=0.45,
            max_y=0.7,
        )
    )
    header_valid = box_in_region(
        clan_header, image_size, max_y=0.3
    ) or bool(ocr_header_valid)
    completion = find_text(texts, r"今日已签到|签到成功|帮派签到成功|^已签到$")
    dividend_section = find_text(texts, r"^帮派分红$")
    dividend = find_text(texts, r"^领取分红$")
    dividend_context_valid = (
        bool(ocr_header_valid)
        and dividend_section
        and box_in_region(
            dividend_section["box"],
            image_size,
            min_x=0.6,
            max_x=0.9,
            min_y=0.6,
            max_y=0.85,
        )
    )
    dividend_valid = dividend_context_valid and dividend and box_in_region(
        dividend["box"],
        image_size,
        min_x=0.6,
        max_x=0.9,
        min_y=0.72,
        max_y=0.95,
    )
    dividend_claimed = find_text(texts, r"^(今日)?已领取$")
    dividend_claimed_valid = dividend_context_valid and dividend_claimed and box_in_region(
        dividend_claimed["box"],
        image_size,
        min_x=0.6,
        max_x=0.9,
        min_y=0.72,
        max_y=0.95,
    )
    dividend_unavailable = find_text(texts, r"^下周揭晓$|^暂无分红$|^暂不可领$")
    dividend_unavailable_valid = (
        dividend_context_valid
        and dividend_unavailable
        and box_in_region(
            dividend_unavailable["box"],
            image_size,
            min_x=0.6,
            max_x=0.9,
            min_y=0.65,
            max_y=0.9,
        )
    )

    confirmation = find_text(texts, r"^领取$")
    if (
        confirmation
        and phase in {"dividend_confirm", "verify_dividend"}
        and box_in_region(
            confirmation["box"],
            image_size,
            min_x=0.3,
            max_x=0.7,
            min_y=0.65,
            max_y=0.95,
        )
    ):
        return {"status": "confirm_dividend", "box": confirmation["box"]}

    sign_in = find_text(texts, r"^签到$")
    sign_in_valid = sign_in and box_in_region(
        sign_in["box"], image_size, min_x=0.5, min_y=0.4, max_y=0.85
    )
    if sign_in_valid and header_valid and phase in {"enter", "welfare", "verify_sign_in"}:
        return {"status": "sign_in", "box": sign_in["box"]}

    if dividend_valid and phase in {
        "enter",
        "welfare",
        "verify_sign_in",
        "check_dividend",
        "dividend_confirm",
        "verify_dividend",
    }:
        return {"status": "claim_dividend", "box": dividend["box"]}

    if dividend_claimed_valid and phase == "verify_dividend":
        return {"status": "completed"}

    if completion and header_valid:
        if phase == "verify_dividend" and dividend_context_valid and not dividend:
            return {"status": "completed"}
        if phase == "verify_sign_in":
            return {"status": "sign_in_confirmed"}
        if phase == "check_dividend":
            if dividend_claimed_valid or dividend_unavailable_valid:
                return {"status": "completed"}
            return {"status": "sign_in_confirmed"}
        if phase in {"enter", "welfare"}:
            return {"status": "already_completed"}

    clan_welfare = templates.get("clan_welfare")
    welfare_valid = box_in_region(
        clan_welfare, image_size, min_x=0.7, min_y=0.35, max_y=0.85
    )
    welfare_text = find_text(texts, r"^福利$")
    clan_nav_anchors = [
        find_text(texts, r"^信息$"),
        find_text(texts, r"^成员$"),
        welfare_text,
        find_text(texts, r"^活动$"),
    ]
    clan_nav_regions = (
        {"min_x": 0.85, "min_y": 0.2, "max_y": 0.5},
        {"min_x": 0.85, "min_y": 0.3, "max_y": 0.6},
        {"min_x": 0.85, "min_y": 0.45, "max_y": 0.75},
        {"min_x": 0.85, "min_y": 0.6, "max_y": 0.85},
    )
    visible_clan_nav = sum(
        bool(anchor)
        and box_in_region(anchor["box"], image_size, **region)
        for anchor, region in zip(clan_nav_anchors, clan_nav_regions)
    )
    ocr_welfare = (
        welfare_text
        if clan_title_valid
        and welfare_text
        and visible_clan_nav >= 2
        and box_in_region(
            welfare_text["box"], image_size, min_x=0.85, min_y=0.45, max_y=0.75
        )
        else None
    )
    welfare_box = (
        clan_welfare
        if header_valid and welfare_valid
        else (ocr_welfare["box"] if ocr_welfare else None)
    )
    if welfare_box and phase in {"enter", "clan"}:
        return {"status": "open_welfare", "box": welfare_box}
    if header_valid:
        return {"status": "clan_idle"}

    clan_entry = templates.get("clan_entry")
    if (
        clan_entry
        and phase in {"enter", "menu"}
        and box_in_region(clan_entry, image_size, min_x=0.55, min_y=0.72)
    ):
        return {"status": "open_clan", "box": clan_entry}
    clan_text = find_text(texts, r"^帮派$")
    if (
        clan_text
        and phase == "menu"
        and box_in_region(clan_text["box"], image_size, min_x=0.55, min_y=0.72)
    ):
        return {"status": "open_clan", "box": clan_text["box"]}

    expand_menu = templates.get("expand_menu")
    expand_menu_text = find_text(texts, r"^\+$")
    main_screen_anchors = [
        find_text(texts, r"^任务$"),
        find_text(texts, r"^队伍$"),
        find_text(texts, r"^包裹$"),
    ]
    anchor_regions = (
        {"min_x": 0.75, "max_y": 0.3},
        {"min_x": 0.9, "max_y": 0.3},
        {"min_x": 0.9, "min_y": 0.75},
    )
    visible_main_anchors = sum(
        bool(anchor)
        and box_in_region(anchor["box"], image_size, **region)
        for anchor, region in zip(main_screen_anchors, anchor_regions)
    )
    ocr_expand_menu = (
        expand_menu_text
        if expand_menu_text
        and visible_main_anchors >= 2
        and box_in_region(
            expand_menu_text["box"], image_size, min_x=0.9, min_y=0.85
        )
        else None
    )
    expand_menu = expand_menu or (
        ocr_expand_menu["box"] if ocr_expand_menu else None
    )
    if (
        expand_menu
        and phase == "enter"
        and box_in_region(expand_menu, image_size, min_x=0.88, min_y=0.75)
    ):
        return {"status": "expand_menu", "box": expand_menu}
    return {"status": "unknown"}
