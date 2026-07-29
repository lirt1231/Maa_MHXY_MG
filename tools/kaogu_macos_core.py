"""Pure scene classification for the bounded macOS archaeology workflow."""

from __future__ import annotations

try:
    from .daily_activity_navigation_core import find_items, in_region, valid_box
except ImportError:  # Direct execution through tools/run_kaogu_macos.py.
    from daily_activity_navigation_core import find_items, in_region, valid_box


def _template_box(templates, name, image_size, **region):
    box = templates.get(name)
    item = {"box": box}
    if valid_box(item) is None:
        return None
    if region and not in_region(item, image_size, **region):
        return None
    return list(box)


def _relative_box(image_size, left, top, width, height):
    image_width, image_height = image_size
    return [
        round(image_width * left),
        round(image_height * top),
        max(1, round(image_width * width)),
        max(1, round(image_height * height)),
    ]


def _purchase_slot_box(box):
    """Target only the green plus in the first shop slot template."""
    x, y, width, height = box
    return [
        round(x + width * 0.16),
        round(y + height * 0.43),
        max(1, round(width * 0.22)),
        max(1, round(height * 0.63)),
    ]


def classify_kaogu_scene(texts, templates, image_size, *, phase="enter"):
    """Choose one action backed by current OCR/template evidence."""
    if not image_size or len(image_size) != 2:
        return {"status": "unknown"}

    vitality = find_items(texts, r".*(?:活力不足|活力不够|活力值不足).*" )
    if vitality:
        return {"status": "blocked_resource", "reason": "活力不足"}

    temporary_bag = find_items(
        texts,
        r"临时(?:道具|物品)(?:栏|包裹)",
        image_size,
        {"min_x": 0.3, "max_x": 0.7, "max_y": 0.4},
    )
    if temporary_bag:
        return {
            "status": "close_temporary_bag",
            "box": _relative_box(image_size, 0.655, 0.205, 0.045, 0.07),
        }

    shovel = _template_box(
        templates,
        "shovel",
        image_size,
        min_x=0.2,
        max_x=0.95,
        min_y=0.15,
        max_y=0.8,
    )
    dig_buttons = find_items(
        texts,
        r"挖掘",
        image_size,
        {"min_x": 0.72, "min_y": 0.62},
    )
    if phase == "digging" and shovel and dig_buttons:
        return {"status": "dig", "box": valid_box(dig_buttons[0])}
    if phase == "digging" and shovel and in_region(
        {"box": shovel}, image_size, min_x=0.72, min_y=0.45
    ):
        # The existing Maa pipeline uses this button only while the shovel
        # animation is positively matched in the right-side archaeology panel.
        return {
            "status": "dig",
            "box": _relative_box(image_size, 0.815, 0.758, 0.09, 0.085),
            "reason": "shovel_template",
        }
    if phase == "digging":
        return {"status": "wait_for_dig"}

    shop_close = _template_box(
        templates, "shop_close", image_size, min_x=0.75, min_y=0.05, max_y=0.4
    )
    shovel_products = find_items(
        texts,
        r"洛阳铲",
        image_size,
        {"min_x": 0.55, "min_y": 0.05, "max_y": 0.45},
    )
    buy_buttons = find_items(
        texts,
        r"购买",
        image_size,
        {"min_x": 0.5, "min_y": 0.65},
    )
    if phase == "shop_closing" and shop_close:
        return {"status": "close_shop", "box": shop_close}
    if shovel_products and buy_buttons and shop_close:
        return {"status": "buy_shovel", "box": valid_box(buy_buttons[-1])}

    choose_tool = find_items(
        texts,
        r"选择工具",
        image_size,
        {"min_x": 0.3, "max_x": 0.7, "max_y": 0.4},
    )
    use_buttons = find_items(
        texts,
        r"使用",
        image_size,
        {"min_x": 0.45, "max_x": 0.8, "min_y": 0.55},
    )
    shovel_names = find_items(texts, r"洛阳铲")
    if choose_tool and shovel and use_buttons and shovel_names:
        return {"status": "use_shovel", "box": valid_box(use_buttons[-1])}
    if choose_tool and shovel:
        return {"status": "select_shovel", "box": shovel}
    purchase_slot = _template_box(
        templates,
        "purchase_slot",
        image_size,
        min_x=0.15,
        max_x=0.65,
        min_y=0.12,
        max_y=0.7,
    )
    if choose_tool and purchase_slot:
        return {
            "status": "open_shovel_shop",
            "box": _purchase_slot_box(purchase_slot),
        }
    if choose_tool:
        return {"status": "tool_selection_idle"}

    archaeology_header = _template_box(
        templates, "archaeology_header", image_size, min_x=0.25, max_x=0.8, max_y=0.3
    )
    selected_shovel = _template_box(
        templates,
        "selected_shovel",
        image_size,
        min_x=0.45,
        max_x=0.75,
        min_y=0.25,
        max_y=0.7,
    )
    archaeology_buttons = find_items(
        texts,
        r"考古",
        image_size,
        {"min_x": 0.4, "max_x": 0.75, "min_y": 0.7},
    )
    if archaeology_header and selected_shovel:
        return {
            "status": "start_archaeology",
            "box": (
                valid_box(archaeology_buttons[-1])
                if archaeology_buttons
                else _relative_box(image_size, 0.515, 0.835, 0.14, 0.075)
            ),
        }

    production_tab = _template_box(
        templates,
        "production_tab",
        image_size,
        min_x=0.75,
        min_y=0.2,
        max_y=0.75,
    )
    if archaeology_header and phase in {"archaeology", "enter"} and production_tab:
        return {"status": "open_production", "box": production_tab}

    higher_tier = next(
        (
            _template_box(
                templates,
                name,
                image_size,
                max_x=0.45,
                min_y=0.05,
                max_y=0.75,
            )
            for name in ("tier_three", "tier_four", "tier_five")
            if templates.get(name)
        ),
        None,
    )
    left_arrow = _template_box(
        templates,
        "tier_left_arrow",
        image_size,
        max_x=0.4,
        max_y=0.35,
    )
    if archaeology_header and higher_tier and left_arrow:
        return {"status": "switch_tier", "box": left_arrow}

    current_low_tier = _template_box(
        templates, "tier_two", image_size, max_x=0.45, max_y=0.75
    ) or _template_box(
        templates, "tier_one", image_size, max_x=0.45, max_y=0.75
    )
    tool_plus = _template_box(
        templates,
        "tool_plus",
        image_size,
        min_x=0.45,
        max_x=0.75,
        min_y=0.25,
        max_y=0.7,
    )
    if archaeology_header and current_low_tier and tool_plus:
        return {"status": "open_tool_selection", "box": tool_plus}
    if archaeology_header:
        return {"status": "production_idle"}

    archaeology_entry = _template_box(
        templates,
        "archaeology_entry",
        image_size,
        min_x=0.55,
        min_y=0.1,
        max_y=0.75,
    )
    if archaeology_entry:
        return {"status": "open_archaeology", "box": archaeology_entry}

    auxiliary_tab = _template_box(
        templates,
        "auxiliary_tab",
        image_size,
        min_x=0.75,
        min_y=0.35,
        max_y=0.95,
    )
    skill_page = any(
        templates.get(name)
        for name in ("character_skills", "clan_skills", "training_skills", "auxiliary_skills")
    )
    if skill_page and auxiliary_tab:
        return {"status": "open_auxiliary", "box": auxiliary_tab}

    skills_icon = _template_box(
        templates,
        "skills_icon",
        image_size,
        min_x=0.7,
        min_y=0.65,
    )
    if skills_icon:
        return {"status": "open_skills", "box": skills_icon}

    skills_labels = find_items(
        texts,
        r"技能",
        image_size,
        {"min_x": 0.62, "min_y": 0.62},
    )
    if skills_labels:
        return {"status": "open_skills", "box": valid_box(skills_labels[-1])}

    menu_plus = _template_box(
        templates,
        "menu_plus",
        image_size,
        min_x=0.85,
        min_y=0.75,
    )
    if menu_plus:
        return {"status": "expand_menu", "box": menu_plus}

    # In narrow macOS windows the plus keeps its size but its surrounding
    # artwork changes enough for the original template to miss. Require both
    # main-screen tabs and the isolated bottom-right symbol before using OCR.
    main_task = find_items(
        texts,
        r"任务",
        image_size,
        {"min_x": 0.72, "min_y": 0.05, "max_y": 0.3},
    )
    main_team = find_items(
        texts,
        r"队伍",
        image_size,
        {"min_x": 0.82, "min_y": 0.05, "max_y": 0.3},
    )
    corner_symbols = find_items(
        texts,
        r"(?:X|x|×|十|\+|＋)",
        image_size,
        {"min_x": 0.9, "min_y": 0.85},
    )
    if main_task and main_team and corner_symbols:
        return {"status": "expand_menu", "box": valid_box(corner_symbols[-1])}
    return {"status": "unknown"}


__all__ = ["classify_kaogu_scene"]
