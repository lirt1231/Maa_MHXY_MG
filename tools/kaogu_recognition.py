"""Platform-neutral scene classification for the bounded archaeology workflow."""

from __future__ import annotations

try:
    from .daily_activity_navigation_core import find_items, in_region, valid_box
except ImportError:  # Direct execution through a platform runner.
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


def _union_boxes(items):
    boxes = [valid_box(item) for item in items]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return [left, top, right - left, bottom - top]


def _purchase_slot_box(box):
    """Target only the green plus in the first shop slot template."""
    x, y, width, height = box
    return [
        round(x + width * 0.16),
        round(y + height * 0.43),
        max(1, round(width * 0.22)),
        max(1, round(height * 0.63)),
    ]


def _inventory_close_box(tab_box):
    """Locate the close badge above and left of the right-side inventory tab."""
    x, y, width, height = tab_box
    return [
        round(x - width * 1.6),
        round(y - height * 2.6),
        max(1, round(width * 1.8)),
        max(1, round(height * 1.2)),
    ]


def _main_screen_evidence(texts, templates, image_size):
    if not templates.get("menu_plus"):
        return False
    anchors = ["menu_plus"]
    main_task = find_items(
        texts,
        r".*任务",
        image_size,
        {"min_x": 0.72, "min_y": 0.05, "max_y": 0.3},
    )
    main_team = find_items(
        texts,
        r"(?:队)?伍",
        image_size,
        {"min_x": 0.82, "min_y": 0.05, "max_y": 0.3},
    )
    if main_task and main_team:
        anchors.append("task_team_tabs")
    if find_items(
        texts,
        r"(?:福利|商城|节日活动)",
        image_size,
        {"max_x": 0.18, "min_y": 0.1, "max_y": 0.4},
    ):
        anchors.append("left_menu")
    if find_items(
        texts,
        r"活动",
        image_size,
        {"min_x": 0.18, "max_x": 0.46, "max_y": 0.18},
    ):
        anchors.append("activity_toolbar")
    if find_items(
        texts,
        r"(?:当前|个人|世界|帮派|队伍)",
        image_size,
        {"max_x": 0.18, "min_y": 0.75},
    ):
        anchors.append("chat_tab")
    return len(anchors) >= 3


def _classify_antique_sale(texts, templates, image_size, phase):
    auxiliary_titles = find_items(
        texts,
        r"^辅助技能$",
        image_size,
        {"min_x": 0.35, "max_x": 0.65, "max_y": 0.25},
    )
    if auxiliary_titles:
        return {
            "status": "close_skills_for_sale",
            "box": _relative_box(image_size, 0.74, 0.14, 0.05, 0.07),
        }

    archaeology_titles = find_items(
        texts,
        r"^工坊[·•.]?考古$",
        image_size,
        {"min_x": 0.35, "max_x": 0.65, "max_y": 0.25},
    )
    close_controls = find_items(
        texts,
        r"^(?:×|x|X)$",
        image_size,
        {"min_x": 0.75, "max_y": 0.3},
    )
    if archaeology_titles:
        return {
            "status": "close_archaeology_for_sale",
            "box": (
                valid_box(close_controls[-1])
                if close_controls
                else _relative_box(image_size, 0.875, 0.18, 0.05, 0.07)
            ),
        }

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

    sale_page = _template_box(
        templates,
        "antique_sale_page",
        image_size,
        max_x=0.75,
        max_y=0.5,
    )
    sale_titles = find_items(
        texts,
        r"^古董售卖[·.。]?$",
        image_size,
        {"min_x": 0.3, "max_x": 0.7, "max_y": 0.3},
    )
    sale_totals = find_items(
        texts,
        r"^(?:总价|拥有)$",
        image_size,
        {"min_x": 0.5, "max_x": 0.9, "min_y": 0.55, "max_y": 0.85},
    )
    sell_buttons = find_items(
        texts,
        r"^售卖$",
        image_size,
        {"min_x": 0.55, "max_x": 0.9, "min_y": 0.68, "max_y": 0.92},
    )
    no_saleable_goods = find_items(
        texts,
        r"^没有可以售卖的商品(?:哦)?$",
        image_size,
        {"min_x": 0.55, "max_x": 0.9, "min_y": 0.2, "max_y": 0.55},
    )
    sale_close_buttons = find_items(
        texts,
        r"^[xX×]$",
        image_size,
        {"min_x": 0.75, "max_x": 0.98, "min_y": 0.12, "max_y": 0.45},
    )
    sale_page = bool(sale_page or (sale_titles and len(sale_totals) >= 2))
    cancel_buttons = find_items(
        texts,
        r"^取消$",
        image_size,
        {"min_x": 0.3, "max_x": 0.65, "min_y": 0.45, "max_y": 0.75},
    )
    confirm_buttons = find_items(
        texts,
        r"^确定$",
        image_size,
        {"min_x": 0.5, "max_x": 0.75, "min_y": 0.45, "max_y": 0.75},
    )
    if sale_page and cancel_buttons and confirm_buttons:
        return {
            "status": "cancel_genuine_sale",
            "box": valid_box(cancel_buttons[-1]),
            "reason": "genuine_confirmation",
        }

    if templates.get("battle"):
        return {"status": "wait_for_battle", "reason": "battle"}
    if templates.get("auto_pathing") or find_items(texts, r"自动寻路中?|寻路中"):
        return {"status": "wait_for_sale_pathing", "reason": "auto_pathing"}

    inventory_page = bool(templates.get("inventory_page"))
    inventory_title = find_items(
        texts,
        r"物品",
        image_size,
        {"min_x": 0.35, "max_x": 0.75, "max_y": 0.35},
    )
    inventory_tabs = find_items(
        texts,
        r"包裹",
        image_size,
        {"min_x": 0.75, "min_y": 0.1, "max_y": 0.5},
    )
    warehouse_tabs = find_items(
        texts,
        r"仓库",
        image_size,
        {"min_x": 0.75, "min_y": 0.25, "max_y": 0.7},
    )
    inventory_page = inventory_page or bool(
        inventory_tabs and (inventory_title or warehouse_tabs)
    )
    antique = _template_box(
        templates,
        "non_genuine_antique",
        image_size,
        min_x=0.1,
        max_x=0.95,
        min_y=0.48,
        max_y=0.75,
    )
    use_buttons = find_items(
        texts,
        r"^使用$",
        image_size,
        {"min_x": 0.3, "max_x": 0.75, "min_y": 0.35, "max_y": 0.9},
    )
    organize_buttons = find_items(
        texts,
        r"^整理$",
        image_size,
        {"min_x": 0.55, "max_x": 0.9, "min_y": 0.72, "max_y": 0.95},
    )
    if phase in {"sale_wait_main", "sale_inventory_normalize"} and inventory_page:
        return {"status": "normalize_inventory"}
    if phase == "sale_inventory_sort" and inventory_page and organize_buttons:
        return {
            "status": "organize_inventory",
            "box": valid_box(organize_buttons[-1]),
        }
    if (
        phase in {"sale_inventory_scan", "sale_inventory_verify_empty"}
        and inventory_page
        and antique
    ):
        return {"status": "activate_antique", "box": antique}
    if phase == "sale_inventory_scan" and inventory_page:
        return {"status": "scan_inventory"}
    if (
        phase in {"sale_inventory_verify_empty", "sale_confirm_main"}
        and inventory_page
        and inventory_tabs
    ):
        return {
            "status": "close_inventory",
            "box": _inventory_close_box(valid_box(inventory_tabs[-1])),
        }
    if phase == "sale_pathing" and inventory_page and use_buttons:
        return {"status": "use_antique", "box": valid_box(use_buttons[-1])}

    sale_complete = _template_box(
        templates,
        "antique_sale_complete",
        image_size,
        min_x=0.45,
        max_x=0.95,
        max_y=0.65,
    )
    if phase in {
        "sale_wait_main",
        "sale_pathing",
        "sale_select",
        "sale_sell",
    } and (sale_complete or no_saleable_goods):
        return {"status": "antique_sale_finished", "reason": "no_saleable_antique"}
    if phase in {
        "sale_wait_main",
        "sale_pathing",
        "sale_select",
        "sale_sell",
    } and sale_page and sell_buttons:
        # The client automatically selects the next antique in its selling
        # list. Selecting item thumbnails here is both redundant and unsafe,
        # so the only allowed input on a confirmed sale page is its control.
        # `sale_wait_main` is also the recovery entry point after an
        # interrupted run, which may already be on this page.
        return {"status": "sell_antique", "box": valid_box(sell_buttons[-1])}
    if phase == "sale_select" and sale_page and not sell_buttons:
        return {"status": "sale_candidate_absent", "reason": "confirming_empty_sale"}
    if phase in {"sale_wait_main", "sale_closing", "sale_confirm_main"} and sale_page:
        close_box = _template_box(
            templates,
            "shop_close",
            image_size,
            min_x=0.75,
            max_y=0.4,
        )
        if close_box is None and sale_close_buttons:
            close_box = valid_box(sale_close_buttons[-1])
        if close_box and (not sell_buttons or no_saleable_goods):
            return {"status": "close_sale_shop", "box": close_box}

    if _main_screen_evidence(texts, templates, image_size):
        if phase in {"sale_wait_main", "sale_rescan_main", "sale_confirm_main"}:
            main_inventory = find_items(
                texts,
                r"包裹",
                image_size,
                {"min_x": 0.88, "min_y": 0.72, "max_y": 0.95},
            )
            if phase in {"sale_wait_main", "sale_rescan_main"}:
                return {
                    "status": "open_inventory_for_sale",
                    "box": (
                        valid_box(main_inventory[-1])
                        if main_inventory
                        else _relative_box(image_size, 0.925, 0.805, 0.07, 0.11)
                    ),
                }
            return {"status": "antique_sale_main_confirmed"}
    return {"status": "wait_for_antique_sale"}


def classify_kaogu_scene(
    texts,
    templates,
    image_size,
    *,
    phase="enter",
    tool_mode="luoyang_shovel",
):
    """Choose one action backed by current OCR/template evidence."""
    if not image_size or len(image_size) != 2:
        return {"status": "unknown"}

    if phase.startswith("sale_"):
        return _classify_antique_sale(texts, templates, image_size, phase)

    if phase == "digging" and (
        templates.get("auto_pathing")
        or find_items(texts, r"自动寻路中?|寻路中")
    ):
        return {"status": "wait_for_start_pathing", "reason": "auto_pathing"}

    battle_cancel = find_items(
        texts,
        r"^取消$",
        image_size,
        {"min_x": 0.85, "min_y": 0.85},
    )
    if templates.get("battle") or battle_cancel:
        return {"status": "wait_for_battle", "reason": "battle"}

    busy_tasks = find_items(
        texts,
        r".*(?:运镖|捉鬼|师门|宝图)任务.*",
        image_size,
        {"min_x": 0.2, "max_x": 0.75, "min_y": 0.15, "max_y": 0.75},
    )
    if busy_tasks:
        return {
            "status": "wait_for_busy_task",
            "reason": busy_tasks[0]["text"],
        }

    vitality = find_items(texts, r".*(?:活力不足|活力不够|活力值不足).*" )
    if vitality:
        return {"status": "blocked_resource", "reason": "活力不足"}

    archaeology_settlement = find_items(
        texts,
        r".*(?:考古成功.*(?:获得|发现)|本次考古消耗了?\d+点活力).*",
        image_size,
        {"min_x": 0.2, "max_x": 0.8, "min_y": 0.15, "max_y": 0.85},
    )
    if archaeology_settlement:
        return {"status": "dig_confirmed", "reason": "archaeology_settlement"}

    # On some client builds the excavation panel closes immediately after a
    # successful dig. The personal-channel receipt is then the only remaining
    # completion signal on the returned main screen. It is intentionally
    # accepted only in the active digging phase; the workflow additionally
    # requires a pending, evidence-backed dig click before it increments.
    archaeology_main_receipt = find_items(
        texts,
        r".*(?:个人|系统).*你获得了?\d*个.*",
        image_size,
        {"max_x": 0.32, "min_y": 0.82},
    )

    archaeology_transition = find_items(
        texts,
        r".*考古中[.。…]*.*",
        image_size,
        {"min_x": 0.2, "max_x": 0.8, "min_y": 0.15, "max_y": 0.85},
    )
    if archaeology_transition:
        return {"status": "wait_for_dig", "reason": "archaeology_in_progress"}

    archaeology_product = find_items(
        texts,
        r".*类型[:：]考古产品.*",
        image_size,
        {"min_x": 0.2, "max_x": 0.8, "min_y": 0.15, "max_y": 0.85},
    )
    if archaeology_product:
        return {"status": "wait_for_dig", "reason": "archaeology_product"}

    dig_buttons = find_items(
        texts,
        r"挖掘",
        image_size,
        {"min_x": 0.72, "min_y": 0.62},
    )
    right_shovel_labels = find_items(
        texts,
        r"^洛阳铲$",
        image_size,
        {"min_x": 0.72, "min_y": 0.6, "max_y": 0.9},
    )
    inventory_title = find_items(
        texts,
        r"物品",
        image_size,
        {"min_x": 0.45, "max_x": 0.7, "min_y": 0.12, "max_y": 0.35},
    )
    inventory_tabs = find_items(
        texts,
        r"包裹",
        image_size,
        {"min_x": 0.8, "min_y": 0.15, "max_y": 0.45},
    )
    warehouse_tabs = find_items(
        texts,
        r"仓库",
        image_size,
        {"min_x": 0.8, "min_y": 0.3, "max_y": 0.65},
    )
    if inventory_title and inventory_tabs and warehouse_tabs:
        return {
            "status": "close_inventory",
            "box": _inventory_close_box(valid_box(inventory_tabs[0])),
        }

    temporary_bag = find_items(
        texts,
        r"临时(?:道具|物品)(?:栏|包裹)",
        image_size,
        {"min_x": 0.3, "max_x": 0.7, "max_y": 0.4},
    )
    if temporary_bag and not dig_buttons:
        return {
            "status": "close_temporary_bag",
            "box": _relative_box(image_size, 0.655, 0.25, 0.045, 0.07),
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
    if tool_mode == "selected" and phase == "digging" and dig_buttons:
        return {
            "status": "dig",
            "box": valid_box(dig_buttons[0]),
            "reason": "manually_selected_tool",
        }
    if (shovel or right_shovel_labels) and dig_buttons:
        return {"status": "dig", "box": valid_box(dig_buttons[0])}
    if shovel and in_region(
        {"box": shovel}, image_size, min_x=0.72, min_y=0.45
    ):
        # The existing Maa pipeline uses this button only while the shovel
        # animation is positively matched in the right-side archaeology panel.
        return {
            "status": "dig",
            "box": _relative_box(image_size, 0.815, 0.758, 0.09, 0.085),
            "reason": "shovel_template",
        }
    settled_menu_plus = _template_box(
        templates,
        "menu_plus",
        image_size,
        min_x=0.85,
        min_y=0.75,
    )
    settled_main_task = find_items(
        texts,
        r".*任务",
        image_size,
        {"min_x": 0.72, "min_y": 0.05, "max_y": 0.3},
    )
    settled_main_team = find_items(
        texts,
        r"队伍",
        image_size,
        {"min_x": 0.82, "min_y": 0.05, "max_y": 0.3},
    )
    if (
        phase == "digging"
        and settled_menu_plus
        and settled_main_task
        and settled_main_team
    ):
        if archaeology_main_receipt:
            return {
                "status": "dig_confirmed_on_main",
                "reason": "archaeology_main_receipt",
            }
        # Starting archaeology normally replaces this page with the right-side
        # excavation panel. A confirmed return to the main screen means that
        # transition did not become observable, so leave recovery policy to
        # the workflow instead of consuming a regular menu-click budget.
        return {"status": "start_transition_main", "box": settled_menu_plus}
    if (
        phase == "tool_select"
        and settled_menu_plus
        and settled_main_task
        and settled_main_team
    ):
        return {"status": "expand_menu", "box": settled_menu_plus}
    if phase == "digging":
        return {"status": "wait_for_dig"}

    shop_close = _template_box(
        templates, "shop_close", image_size, min_x=0.75, min_y=0.05, max_y=0.4
    )
    shop_titles = find_items(
        texts,
        r"^洛阳杂货铺$",
        image_size,
        {"min_x": 0.3, "max_x": 0.7, "max_y": 0.25},
    )
    if not shop_close and shop_titles:
        shop_close_labels = find_items(
            texts,
            r"^[Xx×]$",
            image_size,
            {"min_x": 0.85, "min_y": 0.15, "max_y": 0.45},
        )
        shop_close = (
            valid_box(shop_close_labels[-1]) if shop_close_labels else None
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
    split_buy_button = find_items(
        texts,
        r"^[购买]$",
        image_size,
        {"min_x": 0.65, "max_x": 0.85, "min_y": 0.7, "max_y": 0.9},
    )
    if len(split_buy_button) >= 2:
        buy_buttons = [{"box": _union_boxes(split_buy_button)}]
    if phase == "shop_closing" and shop_close:
        return {"status": "close_shop", "box": shop_close}
    if phase == "enter" and shovel_products and shop_close:
        # An interrupted run may be restarted while the previously confirmed
        # shop is still visible. This is deliberately not a purchase state:
        # the workflow can only close it when its durable purchase guard
        # proves that this run already owns a purchase attempt.
        return {
            "status": "shop_resume",
            "close_box": shop_close,
        }
    if phase == "shop_purchase_verify" and shop_titles and shovel_products:
        # The current client keeps the shop open after a successful purchase.
        # Preserve the recognized close box so the workflow can leave this
        # stable shop only under its one-purchase reservation, then verify the
        # shovel from the tool-selection dialog.
        return {
            "status": "wait_for_purchase",
            "reason": "awaiting_tool_delivery",
            "close_box": shop_close,
        }
    # A visible shop-like overlay alone is never purchase authority. The
    # workflow must first observe the empty tool dialog and explicitly enter
    # this shop in the current run; otherwise a stale/transitioning shop page
    # could consume the bounded purchase reservation.
    if phase == "shop" and shovel_products and buy_buttons and shop_close:
        return {
            "status": "buy_shovel",
            "box": valid_box(buy_buttons[-1]),
            "close_box": shop_close,
        }

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
    shovel_names = find_items(texts, r".*洛阳铲.*")
    if choose_tool and use_buttons and shovel_names:
        return {"status": "use_shovel", "box": valid_box(use_buttons[-1])}
    if choose_tool and shovel:
        return {"status": "select_shovel", "box": shovel}
    empty_shovel_notice = find_items(
        texts,
        r".*包裹中暂时没有洛阳.*",
        image_size,
        {"min_x": 0.45, "max_x": 0.8, "min_y": 0.3, "max_y": 0.65},
    )
    purchase_hints = find_items(
        texts,
        r"购买",
        image_size,
        {"min_x": 0.45, "max_x": 0.8, "min_y": 0.35, "max_y": 0.7},
    )
    if choose_tool and empty_shovel_notice and purchase_hints:
        return {
            "status": "open_shovel_shop",
            "box": _relative_box(image_size, 0.3, 0.34, 0.075, 0.1),
        }
    first_slot_counts = find_items(
        texts,
        r"^\d+$",
        image_size,
        {"min_x": 0.25, "max_x": 0.4, "min_y": 0.25, "max_y": 0.5},
    )
    if choose_tool and use_buttons and first_slot_counts:
        return {
            "status": "select_shovel",
            "box": _relative_box(image_size, 0.3, 0.34, 0.075, 0.1),
        }
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
    if choose_tool and use_buttons and not empty_shovel_notice:
        # In the current fixed tool dialog, an occupied first slot can be
        # icon-only. The dialog, visible Use button, and absence of the
        # explicit empty-inventory notice jointly identify it as selectable.
        return {
            "status": "select_shovel",
            "box": _relative_box(image_size, 0.3, 0.34, 0.075, 0.1),
            "reason": "occupied_first_tool_slot",
        }
    if choose_tool:
        return {"status": "tool_selection_idle"}

    archaeology_header = _template_box(
        templates, "archaeology_header", image_size, min_x=0.25, max_x=0.8, max_y=0.3
    )
    if not archaeology_header:
        archaeology_titles = find_items(
            texts,
            r"^工坊[·•.]?考古$",
            image_size,
            {"min_x": 0.35, "max_x": 0.65, "max_y": 0.25},
        )
        archaeology_header = (
            valid_box(archaeology_titles[0]) if archaeology_titles else None
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
    if tool_mode == "selected" and archaeology_header and archaeology_buttons:
        return {
            "status": "start_archaeology",
            "box": valid_box(archaeology_buttons[-1]),
            "reason": "manually_selected_tool",
        }
    if archaeology_header and selected_shovel:
        return {
            "status": "start_archaeology",
            "box": (
                valid_box(archaeology_buttons[-1])
                if archaeology_buttons
                else _relative_box(image_size, 0.515, 0.835, 0.14, 0.075)
            ),
        }
    selected_tool_counts = find_items(
        texts,
        r"^\d+(?:/\d+)?$",
        image_size,
        {"min_x": 0.5, "max_x": 0.65, "min_y": 0.35, "max_y": 0.65},
    )
    if archaeology_header and archaeology_buttons and selected_tool_counts:
        return {
            "status": "start_archaeology",
            "box": valid_box(archaeology_buttons[-1]),
        }
    vitality_cost = find_items(
        texts,
        r"消耗活力[:：].*",
        image_size,
        {"min_x": 0.35, "max_x": 0.75, "min_y": 0.6, "max_y": 0.9},
    )
    if (
        archaeology_header
        and phase == "tool_ready"
        and archaeology_buttons
        and vitality_cost
    ):
        return {
            "status": "start_archaeology",
            "box": valid_box(archaeology_buttons[-1]),
            "reason": "confirmed_tool_ready",
        }

    production_tab = _template_box(
        templates,
        "production_tab",
        image_size,
        min_x=0.75,
        min_y=0.2,
        max_y=0.75,
    )
    if not production_tab:
        production_tabs = find_items(
            texts,
            r"^技能生产$",
            image_size,
            {"min_x": 0.75, "min_y": 0.2, "max_y": 0.75},
        )
        production_tab = (
            valid_box(production_tabs[-1]) if production_tabs else None
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
    if not higher_tier:
        higher_tiers = find_items(
            texts,
            r"^[三四五]阶$",
            image_size,
            {"max_x": 0.4, "min_y": 0.1, "max_y": 0.65},
        )
        higher_tier = valid_box(higher_tiers[0]) if higher_tiers else None
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
    if not current_low_tier:
        low_tiers = find_items(
            texts,
            r"^[一二]阶$",
            image_size,
            {"max_x": 0.4, "min_y": 0.1, "max_y": 0.65},
        )
        current_low_tier = valid_box(low_tiers[0]) if low_tiers else None
    tool_plus = _template_box(
        templates,
        "tool_plus",
        image_size,
        min_x=0.45,
        max_x=0.75,
        min_y=0.25,
        max_y=0.7,
    )
    if archaeology_header and current_low_tier and phase == "tool_ready":
        return {
            "status": "start_archaeology",
            "box": _relative_box(image_size, 0.515, 0.795, 0.14, 0.075),
            "reason": "confirmed_tool_ready",
        }
    if archaeology_header and current_low_tier and tool_plus:
        return {"status": "open_tool_selection", "box": tool_plus}
    if archaeology_header and current_low_tier and phase == "production":
        return {
            "status": "open_tool_selection",
            "box": _relative_box(image_size, 0.555, 0.42, 0.065, 0.1),
            "reason": "low_tier_tool_slot",
        }
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

    auxiliary_skill_titles = find_items(
        texts,
        r"^辅助技能$",
        image_size,
        {"min_x": 0.35, "max_x": 0.65, "max_y": 0.25},
    )
    archaeology_labels = find_items(
        texts,
        r"^考古$",
        image_size,
        {"min_x": 0.65, "max_x": 0.9, "min_y": 0.3, "max_y": 0.6},
    )
    if auxiliary_skill_titles and archaeology_labels:
        return {
            "status": "open_archaeology",
            "box": valid_box(archaeology_labels[-1]),
        }

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

    skill_page_titles = find_items(
        texts,
        r"(?:人物|帮派|修炼|辅助)技能.*",
        image_size,
        {"min_x": 0.35, "max_x": 0.65, "max_y": 0.25},
    )
    auxiliary_labels = find_items(
        texts,
        r"^辅(?:助)?$",
        image_size,
        {"min_x": 0.82, "min_y": 0.55, "max_y": 0.9},
    )
    if skill_page_titles and auxiliary_labels:
        return {
            "status": "open_auxiliary",
            "box": valid_box(auxiliary_labels[-1]),
        }

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

    menu_close = _template_box(
        templates,
        "menu_close",
        image_size,
        min_x=0.85,
        min_y=0.75,
    )
    if menu_close:
        return {"status": "menu_open"}

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
        r".*任务",
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
        r".+",
        image_size,
        {"min_x": 0.92, "min_y": 0.9},
    )
    if main_task and main_team and corner_symbols:
        return {"status": "expand_menu", "box": valid_box(corner_symbols[-1])}
    main_inventory = find_items(
        texts,
        r"包裹",
        image_size,
        {"min_x": 0.92, "min_y": 0.8, "max_y": 0.93},
    )
    if main_task and main_team and main_inventory:
        return {
            "status": "expand_menu",
            "box": _relative_box(image_size, 0.94, 0.9, 0.055, 0.08),
        }
    return {"status": "unknown"}


__all__ = ["classify_kaogu_scene"]
