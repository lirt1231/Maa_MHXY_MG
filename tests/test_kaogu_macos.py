import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.kaogu_recognition import classify_kaogu_scene
from tools.kaogu_purchase_guard import FilePurchaseGuard
from tools.macos_cancellation import CancellationGuard
from tools.kaogu_workflow import KAOGU_TEMPLATES, next_kaogu_phase
from tools.run_kaogu_macos import TARGET_DIGS, run


IMAGE_SIZE = (1280, 720)


def text(value, box):
    return {"text": value, "box": list(box)}


def scene(texts=None, templates=None):
    return SimpleNamespace(
        image_size=IMAGE_SIZE,
        texts=texts or [],
        templates=templates or {},
    )


def dig_settlement():
    return scene([text("本次考古消耗了54点活力", (430, 430, 220, 30))])


def sale_flow_scenes(*, include_main=True):
    main = scene(
        [
            text("福利", (15, 129, 33, 16)),
            text("活动", (268, 53, 28, 12)),
            text("任务", (1100, 80, 45, 25)),
            text("队伍", (1190, 95, 45, 25)),
            text("包裹", (1190, 620, 45, 25)),
        ],
        {"menu_plus": (1206, 637, 44, 42)},
    )
    inventory = scene(
        [
            text("物品", (600, 120, 70, 30)),
            text("包裹", (1070, 180, 55, 28)),
            text("整理", (900, 620, 100, 45)),
        ],
        {"inventory_page": (335, 5, 567, 58)},
    )
    antique_inventory = scene(
        inventory.texts,
        {
            "inventory_page": (335, 5, 567, 58),
            "non_genuine_antique": (700, 420, 64, 42),
        },
    )
    pathing = scene(templates={"auto_pathing": (1050, 620, 120, 30)})
    sale_page = scene(
        [text("售卖", (890, 592, 150, 58))],
        {"antique_sale_page": (480, 60, 280, 79)},
    )
    genuine_prompt = scene(
        [
            text("取消", (464, 420, 142, 51)),
            text("确定", (672, 408, 157, 69)),
        ],
        {"antique_sale_page": (480, 60, 280, 79)},
    )
    closing = scene(
        templates={
            "antique_sale_page": (480, 60, 280, 79),
            "shop_close": (1125, 135, 48, 43),
        }
    )
    return ([main] if include_main else []) + [
        inventory,
        inventory,
        inventory,
        inventory,
        antique_inventory,
        pathing,
        sale_page,
        sale_page,
        genuine_prompt,
        closing,
        main,
        inventory,
        inventory,
        inventory,
        inventory,
        *[inventory for _ in range(20)],
        inventory,
        main,
        main,
    ]


class KaoguSceneTests(unittest.TestCase):
    def test_shovel_templates_include_current_923_canvas(self):
        self.assertIn(
            "jineng/luoyangchan_live_923.png",
            KAOGU_TEMPLATES["shovel"],
        )
        self.assertIn(
            "jineng/yixuan_luoyangchan_live_923.png",
            KAOGU_TEMPLATES["selected_shovel"],
        )

    def test_sale_entry_templates_require_explicit_non_genuine_badges(self):
        antique = KAOGU_TEMPLATES["non_genuine_antique"]

        self.assertEqual(
            antique["templates"],
            [
                "jineng/gd/gaofang.png",
                "jineng/gd/yanpin.png",
                "jineng/gd/gaofang_live_923.png",
                "jineng/gd/yanpin_live_923.png",
            ],
        )
        self.assertTrue(antique["green_mask"])
        self.assertEqual(antique["threshold"], 0.82)
        self.assertEqual(antique["roi_ratio"], [0.1, 0.48, 0.79, 0.27])

        self.assertNotIn("sale_non_genuine_antique", KAOGU_TEMPLATES)

    def test_shop_close_templates_include_current_narrow_client(self):
        close = KAOGU_TEMPLATES["shop_close"]

        self.assertIn("jineng/zahuopu_guanbi_live_923.png", close["templates"])
        self.assertEqual(close["threshold"], 0.8)

    def test_shop_purchase_evidence_includes_a_confirmed_close_path(self):
        decision = classify_kaogu_scene(
            [
                text("洛阳杂货铺", (415, 104, 166, 38)),
                text("洛阳铲", (712, 190, 67, 26)),
                text("购买", (735, 540, 66, 29)),
                text("×", (906, 199, 33, 28)),
            ],
            {},
            (1020, 718),
            phase="shop",
        )

        self.assertEqual(decision["status"], "buy_shovel")
        self.assertEqual(decision["close_box"], [906, 199, 33, 28])

    def test_shop_after_purchase_waits_for_tool_delivery_evidence(self):
        decision = classify_kaogu_scene(
            [
                text("洛阳杂货铺", (415, 104, 166, 38)),
                text("洛阳铲", (712, 190, 67, 26)),
                text("购买", (735, 540, 66, 29)),
                text("×", (906, 199, 33, 28)),
            ],
            {},
            (1020, 718),
            phase="shop_purchase_verify",
        )

        self.assertEqual(
            decision,
            {
                "status": "wait_for_purchase",
                "reason": "awaiting_tool_delivery",
                "close_box": [906, 199, 33, 28],
            },
        )

    def test_sale_completion_uses_explicit_no_goods_evidence(self):
        completion = KAOGU_TEMPLATES["antique_sale_complete"]

        self.assertEqual(
            completion["templates"],
            ["jineng/shoumai_wancheng2.png"],
        )

        decision = classify_kaogu_scene(
            [
                text("古董售卖·", (426, 84, 142, 37)),
                text("总价", (636, 461, 42, 25)),
                text("拥有", (635, 500, 42, 23)),
                text("没有可以售卖的商品哦", (665, 225, 187, 21)),
                text("售卖", (731, 552, 72, 35)),
            ],
            {},
            (1020, 718),
            phase="sale_wait_main",
        )

        self.assertEqual(
            decision,
            {"status": "antique_sale_finished", "reason": "no_saleable_antique"},
        )

    def test_dig_keeps_workflow_in_digging_phase(self):
        self.assertEqual(next_kaogu_phase("dig", "enter"), "digging")

    def test_main_screen_after_shop_replays_navigation_to_tool_selection(self):
        main = scene(
            [
                text("福利", (15, 129, 33, 16)),
                text("任务", (1100, 80, 45, 25)),
                text("队伍", (1190, 95, 45, 25)),
            ],
            {"menu_plus": (1206, 637, 44, 42)},
        )

        decision = classify_kaogu_scene(
            main.texts, main.templates, IMAGE_SIZE, phase="tool_select"
        )

        self.assertEqual(decision["status"], "expand_menu")
        self.assertEqual(decision["box"], [1206, 637, 44, 42])

    def test_main_screen_after_start_is_a_non_input_recovery_signal(self):
        main = scene(
            [
                text("福利", (15, 129, 33, 16)),
                text("任务", (1100, 80, 45, 25)),
                text("队伍", (1190, 95, 45, 25)),
            ],
            {"menu_plus": (1206, 637, 44, 42)},
        )

        decision = classify_kaogu_scene(
            main.texts, main.templates, IMAGE_SIZE, phase="digging"
        )

        self.assertEqual(
            decision,
            {"status": "start_transition_main", "box": [1206, 637, 44, 42]},
        )

    def test_main_screen_personal_receipt_confirms_pending_dig(self):
        main = scene(
            [
                text("福利", (15, 129, 33, 16)),
                text("任务", (1100, 80, 45, 25)),
                text("队伍", (1190, 95, 45, 25)),
                text("个人你获得了1个战国山字镜", (8, 676, 235, 20)),
            ],
            {"menu_plus": (1206, 637, 44, 42)},
        )

        decision = classify_kaogu_scene(
            main.texts, main.templates, IMAGE_SIZE, phase="digging"
        )

        self.assertEqual(
            decision,
            {
                "status": "dig_confirmed_on_main",
                "reason": "archaeology_main_receipt",
            },
        )

    def test_sale_flow_opens_inventory_and_activates_only_known_antique(self):
        main, inventory = sale_flow_scenes()[:2]

        open_inventory = classify_kaogu_scene(
            main.texts, main.templates, IMAGE_SIZE, phase="sale_wait_main"
        )
        normalize = classify_kaogu_scene(
            inventory.texts,
            inventory.templates,
            IMAGE_SIZE,
            phase="sale_inventory_normalize",
        )
        organize = classify_kaogu_scene(
            inventory.texts,
            inventory.templates,
            IMAGE_SIZE,
            phase="sale_inventory_sort",
        )
        antique_inventory = sale_flow_scenes()[5]
        activate = classify_kaogu_scene(
            antique_inventory.texts,
            antique_inventory.templates,
            IMAGE_SIZE,
            phase="sale_inventory_scan",
        )

        self.assertEqual(open_inventory["status"], "open_inventory_for_sale")
        self.assertEqual(normalize, {"status": "normalize_inventory"})
        self.assertEqual(organize["status"], "organize_inventory")
        self.assertEqual(activate["status"], "activate_antique")
        self.assertEqual(activate["box"], [700, 420, 64, 42])

    def test_upper_inventory_event_badge_never_activates_as_an_antique(self):
        inventory = sale_flow_scenes()[1]
        decision = classify_kaogu_scene(
            inventory.texts,
            {
                "inventory_page": (335, 5, 567, 58),
                # The live client places unrelated “赛事” ribbons in this
                # upper slot; their corner shape must not authorize a click.
                "non_genuine_antique": (511, 294, 50, 42),
            },
            IMAGE_SIZE,
            phase="sale_inventory_scan",
        )

        self.assertEqual(decision, {"status": "scan_inventory"})

    def test_genuine_confirmation_can_only_cancel(self):
        prompt = sale_flow_scenes()[9]

        decision = classify_kaogu_scene(
            prompt.texts, prompt.templates, IMAGE_SIZE, phase="sale_sell"
        )

        self.assertEqual(decision["status"], "cancel_genuine_sale")
        self.assertEqual(decision["box"], [464, 420, 142, 51])

    def test_live_sale_page_uses_ocr_identity_and_recognized_safe_controls(self):
        texts = [
            text("古董售卖", (426, 84, 142, 37)),
            text("总价", (636, 461, 42, 25)),
            text("拥有", (635, 500, 42, 23)),
            text("售卖", (731, 553, 70, 32)),
        ]
        sell = classify_kaogu_scene(
            texts, {}, (1020, 718), phase="sale_pathing"
        )
        resumed = classify_kaogu_scene(
            texts, {}, (1020, 718), phase="sale_wait_main"
        )
        finished = classify_kaogu_scene(
            texts, {}, (1020, 718), phase="sale_select"
        )

        self.assertEqual(sell, {"status": "sell_antique", "box": [731, 553, 70, 32]})
        self.assertEqual(
            resumed,
            {"status": "sell_antique", "box": [731, 553, 70, 32]},
        )
        self.assertEqual(
            finished,
            {"status": "sell_antique", "box": [731, 553, 70, 32]},
        )

    def test_sale_without_control_waits_for_explicit_empty_evidence(self):
        texts = [
            text("古董售卖", (426, 84, 142, 37)),
            text("总价", (636, 461, 42, 25)),
            text("拥有", (635, 500, 42, 23)),
        ]

        decision = classify_kaogu_scene(
            texts,
            {},
            (1020, 718),
            phase="sale_select",
        )

        self.assertEqual(
            decision,
            {"status": "sale_candidate_absent", "reason": "confirming_empty_sale"},
        )

    def test_explicit_no_goods_page_finishes_sale(self):
        texts = [
            text("古董售卖", (426, 84, 142, 37)),
            text("总价", (636, 461, 42, 25)),
            text("拥有", (635, 500, 42, 23)),
            text("售卖", (731, 553, 70, 32)),
        ]

        decision = classify_kaogu_scene(
            texts,
            {"antique_sale_complete": (766, 66, 368, 382)},
            (1020, 718),
            phase="sale_select",
        )

        self.assertEqual(
            decision,
            {"status": "antique_sale_finished", "reason": "no_saleable_antique"},
        )

    def test_pathing_arrival_sells_the_previously_verified_antique(self):
        texts = [
            text("古董售卖", (414, 107, 131, 37)),
            text("总价", (615, 472, 41, 24)),
            text("拥有", (614, 508, 41, 23)),
            text("售卖", (707, 561, 68, 31)),
        ]

        decision = classify_kaogu_scene(
            texts,
            {},
            (986, 720),
            phase="sale_pathing",
        )

        self.assertEqual(
            decision,
            {"status": "sell_antique", "box": [707, 561, 68, 31]},
        )

    def test_sale_resume_closes_empty_shop_only_with_close_template(self):
        texts = [
            text("古董售卖", (426, 84, 142, 37)),
            text("总价", (636, 461, 42, 25)),
            text("拥有", (635, 500, 42, 23)),
        ]

        decision = classify_kaogu_scene(
            texts,
            {"shop_close": (900, 180, 40, 40)},
            (1020, 718),
            phase="sale_wait_main",
        )

        self.assertEqual(
            decision,
            {"status": "close_sale_shop", "box": [900, 180, 40, 40]},
        )

    def test_live_empty_sale_shop_closes_with_page_identity_and_ocr_control(self):
        texts = [
            text("古董售卖·", (414, 107, 131, 37)),
            text("总价", (615, 472, 41, 24)),
            text("拥有", (614, 508, 41, 23)),
            text("没有可以售卖的商品哦", (665, 225, 187, 21)),
            text("售卖", (729, 551, 73, 35)),
            text("×", (873, 204, 36, 43)),
        ]

        decision = classify_kaogu_scene(
            texts,
            {},
            (986, 720),
            phase="sale_closing",
        )

        self.assertEqual(
            decision,
            {"status": "close_sale_shop", "box": [873, 204, 36, 43]},
        )

    def test_unknown_sale_page_never_proposes_input(self):
        decision = classify_kaogu_scene(
            [text("确定", (672, 408, 157, 69))],
            {},
            IMAGE_SIZE,
            phase="sale_sell",
        )

        self.assertEqual(decision, {"status": "wait_for_antique_sale"})

    def test_sorted_inventory_without_visible_antique_scans_next_rows(self):
        inventory = sale_flow_scenes()[1]

        decision = classify_kaogu_scene(
            inventory.texts,
            inventory.templates,
            IMAGE_SIZE,
            phase="sale_inventory_scan",
        )

        self.assertEqual(decision, {"status": "scan_inventory"})

    def test_split_inventory_title_keeps_page_identity_during_scan(self):
        texts = [
            text("物", (544, 174, 34, 27)),
            text("品", (574, 178, 25, 22)),
            text("包裹", (871, 223, 25, 43)),
            text("仓库", (869, 322, 29, 44)),
        ]

        decision = classify_kaogu_scene(
            texts,
            {},
            (986, 720),
            phase="sale_inventory_scan",
        )

        self.assertEqual(decision, {"status": "scan_inventory"})

    def test_post_sale_empty_inventory_proposes_evidence_backed_close(self):
        inventory = sale_flow_scenes()[1]

        decision = classify_kaogu_scene(
            inventory.texts,
            inventory.templates,
            IMAGE_SIZE,
            phase="sale_inventory_verify_empty",
        )

        self.assertEqual(
            decision,
            {"status": "close_inventory", "box": [982, 107, 99, 34]},
        )

        final_confirmation = classify_kaogu_scene(
            inventory.texts,
            inventory.templates,
            IMAGE_SIZE,
            phase="sale_confirm_main",
        )

        self.assertEqual(
            final_confirmation,
            {"status": "close_inventory", "box": [982, 107, 99, 34]},
        )

    def test_post_sale_rescan_main_reopens_inventory(self):
        main = sale_flow_scenes()[0]

        decision = classify_kaogu_scene(
            main.texts,
            main.templates,
            IMAGE_SIZE,
            phase="sale_rescan_main",
        )

        self.assertEqual(decision["status"], "open_inventory_for_sale")

    def test_sale_resume_accepts_an_inventory_already_left_open(self):
        inventory = sale_flow_scenes()[1]

        decision = classify_kaogu_scene(
            inventory.texts,
            inventory.templates,
            IMAGE_SIZE,
            phase="sale_wait_main",
        )

        self.assertEqual(decision, {"status": "normalize_inventory"})

    def test_sale_main_tolerates_partial_team_ocr_with_cross_region_anchors(self):
        decision = classify_kaogu_scene(
            [
                text("福利", (15, 129, 33, 16)),
                text("活动", (268, 53, 28, 12)),
                text("任务", (871, 108, 38, 22)),
                text("伍", (990, 110, 22, 17)),
            ],
            {"menu_plus": (968, 658, 44, 42)},
            (1020, 718),
            phase="sale_wait_main",
        )

        self.assertEqual(decision["status"], "open_inventory_for_sale")

    def test_sale_main_rejects_partial_team_without_cross_region_anchors(self):
        decision = classify_kaogu_scene(
            [
                text("任务", (871, 108, 38, 22)),
                text("伍", (990, 110, 22, 17)),
            ],
            {"menu_plus": (968, 658, 44, 42)},
            (1020, 718),
            phase="sale_wait_main",
        )

        self.assertEqual(decision, {"status": "wait_for_antique_sale"})

    def test_main_menu_navigation_requires_visible_templates(self):
        plus = classify_kaogu_scene(
            [], {"menu_plus": (1206, 637, 44, 42)}, IMAGE_SIZE
        )
        skills = classify_kaogu_scene(
            [], {"skills_icon": (1048, 638, 44, 39)}, IMAGE_SIZE
        )

        self.assertEqual(plus["status"], "expand_menu")
        self.assertEqual(skills["status"], "open_skills")

    def test_battle_cancel_is_wait_only_even_if_plus_template_false_matches(self):
        decision = classify_kaogu_scene(
            [text("取消", (961, 680, 44, 25))],
            {"menu_plus": (967, 658, 44, 42)},
            (1020, 718),
        )

        self.assertEqual(
            decision,
            {"status": "wait_for_battle", "reason": "battle"},
        )

    def test_existing_task_is_wait_only_between_battles(self):
        decision = classify_kaogu_scene(
            [
                text("运镖任务", (372, 256, 90, 27)),
                text("+", (966, 657, 45, 46)),
            ],
            {"menu_plus": (966, 657, 45, 46)},
            (1020, 718),
        )

        self.assertEqual(
            decision,
            {"status": "wait_for_busy_task", "reason": "运镖任务"},
        )

    def test_right_side_task_offer_does_not_block_archaeology_navigation(self):
        decision = classify_kaogu_scene(
            [
                text("师门任务", (827, 137, 78, 25)),
                text("领取今天师门任务", (828, 164, 134, 19)),
                text("任务", (869, 98, 42, 23)),
                text("队伍", (966, 97, 49, 23)),
            ],
            {"menu_plus": (979, 663, 28, 35)},
            (1020, 718),
        )

        self.assertEqual(
            decision,
            {"status": "expand_menu", "box": [979, 663, 28, 35]},
        )

    def test_expanded_menu_stops_before_reclicking_false_plus_match(self):
        decision = classify_kaogu_scene(
            [
                text("任务", (864, 104, 46, 24)),
                text("队伍", (967, 104, 47, 21)),
                text("X", (963, 653, 47, 47)),
            ],
            {
                "menu_plus": (967, 658, 44, 42),
                "menu_close": (963, 653, 47, 47),
            },
            (1020, 718),
        )

        self.assertEqual(decision, {"status": "menu_open"})

    def test_narrow_window_uses_strict_main_screen_ocr_for_plus(self):
        texts = [
            text("任务", (787, 118, 37, 19)),
            text("队伍", (874, 117, 44, 18)),
            text("X", (873, 665, 41, 40)),
        ]

        decision = classify_kaogu_scene(texts, {}, (923, 720))
        without_team = classify_kaogu_scene(texts[:1] + texts[2:], {}, (923, 720))

        self.assertEqual(
            decision, {"status": "expand_menu", "box": [873, 665, 41, 40]}
        )
        self.assertEqual(without_team["status"], "unknown")

    def test_main_screen_ocr_tolerates_task_label_prefix_noise(self):
        decision = classify_kaogu_scene(
            [
                text("四任务", (834, 101, 78, 27)),
                text("队伍", (962, 102, 52, 24)),
                text("X", (962, 653, 49, 50)),
            ],
            {},
            (1020, 718),
        )

        self.assertEqual(
            decision, {"status": "expand_menu", "box": [962, 653, 49, 50]}
        )

    def test_main_screen_uses_corner_box_when_plus_ocr_is_unstable(self):
        decision = classify_kaogu_scene(
            [
                text("四任务", (834, 102, 82, 25)),
                text("队伍", (906, 101, 109, 26)),
                text("包裹", (970, 619, 34, 16)),
                text("🎉", (971, 663, 32, 32)),
            ],
            {},
            (1020, 718),
        )

        self.assertEqual(
            decision, {"status": "expand_menu", "box": [971, 663, 32, 32]}
        )

    def test_main_screen_uses_bounded_corner_when_plus_is_not_ocr_visible(self):
        decision = classify_kaogu_scene(
            [
                text("任务", (863, 103, 48, 25)),
                text("队伍", (970, 102, 45, 25)),
                text("包裹", (969, 619, 34, 16)),
            ],
            {},
            (1020, 718),
        )

        self.assertEqual(
            decision,
            {"status": "expand_menu", "box": [959, 646, 56, 57]},
        )

    def test_expanded_narrow_menu_prefers_ocr_skills_label(self):
        decision = classify_kaogu_scene(
            [
                text("任务", (787, 118, 37, 19)),
                text("队伍", (874, 117, 44, 18)),
                text("技能", (720, 650, 44, 24)),
                text("X", (873, 665, 41, 40)),
            ],
            {},
            (923, 720),
        )

        self.assertEqual(decision["status"], "open_skills")
        self.assertEqual(decision["box"], [720, 650, 44, 24])

    def test_character_skills_page_uses_strict_auxiliary_ocr_label(self):
        decision = classify_kaogu_scene(
            [
                text("人物技能", (452, 87, 96, 26)),
                text("帮派", (897, 301, 30, 48)),
                text("修炼", (898, 407, 29, 47)),
                text("辅助", (898, 512, 29, 46)),
            ],
            {},
            (1020, 718),
            phase="skills",
        )

        self.assertEqual(
            decision,
            {"status": "open_auxiliary", "box": [898, 512, 29, 46]},
        )

    def test_character_skills_page_tolerates_title_suffix_noise(self):
        decision = classify_kaogu_scene(
            [
                text("人物技能修", (436, 115, 222, 22)),
                text("帮派", (867, 320, 29, 46)),
                text("修炼", (866, 420, 32, 48)),
                text("辅助", (868, 523, 28, 43)),
            ],
            {},
            (986, 720),
            phase="skills",
        )

        self.assertEqual(
            decision,
            {"status": "open_auxiliary", "box": [868, 523, 28, 43]},
        )

    def test_current_skills_page_accepts_vertical_auxiliary_label_as_single_character(self):
        decision = classify_kaogu_scene(
            [
                text("人物技能意修", (446, 104, 241, 32)),
                text("辅", (898, 501, 29, 43)),
            ],
            {},
            (1020, 718),
            phase="skills",
        )

        self.assertEqual(
            decision,
            {"status": "open_auxiliary", "box": [898, 501, 29, 43]},
        )

    def test_auxiliary_skills_page_prefers_archaeology_ocr_entry(self):
        decision = classify_kaogu_scene(
            [
                text("辅助技能", (450, 87, 96, 27)),
                text("工坊职业", (158, 173, 88, 25)),
                text("考古", (757, 304, 62, 32)),
                text("辅助", (901, 513, 25, 44)),
            ],
            {},
            (1020, 718),
            phase="skills",
        )

        self.assertEqual(
            decision,
            {"status": "open_archaeology", "box": [757, 304, 62, 32]},
        )

    def test_higher_tier_uses_recognized_left_arrow(self):
        decision = classify_kaogu_scene(
            [],
            {
                "archaeology_header": (462, 10, 312, 68),
                "tier_three": (115, 83, 236, 328),
                "tier_left_arrow": (134, 100, 40, 34),
            },
            IMAGE_SIZE,
            phase="production",
        )

        self.assertEqual(decision, {"status": "switch_tier", "box": [134, 100, 40, 34]})

    def test_current_skin_archaeology_page_uses_ocr_title_and_tier(self):
        decision = classify_kaogu_scene(
            [
                text("五阶", (181, 165, 45, 26)),
                text("工坊·考古", (436, 98, 147, 33)),
                text("考古", (564, 560, 59, 29)),
            ],
            {
                "tier_left_arrow": (118, 162, 40, 34),
                "tool_plus": (567, 313, 60, 60),
            },
            (1020, 718),
            phase="archaeology",
        )

        self.assertEqual(
            decision,
            {"status": "switch_tier", "box": [118, 162, 40, 34]},
        )

    def test_current_skin_uses_ocr_production_tab_when_template_misses(self):
        decision = classify_kaogu_scene(
            [
                text("五阶", (215, 174, 32, 79)),
                text("工坊·考古", (435, 97, 148, 35)),
                text("技能生产", (903, 302, 28, 76)),
            ],
            {},
            (1020, 718),
            phase="archaeology",
        )

        self.assertEqual(
            decision,
            {"status": "open_production", "box": [903, 302, 28, 76]},
        )

    def test_inventory_overlay_is_closed_before_navigation(self):
        decision = classify_kaogu_scene(
            [
                text("物品", (561, 148, 63, 31)),
                text("包裹", (901, 200, 28, 47)),
                text("仓库", (902, 304, 25, 44)),
            ],
            {},
            (1020, 718),
            phase="enter",
        )

        self.assertEqual(
            decision,
            {"status": "close_inventory", "box": [856, 78, 50, 56]},
        )

    def test_current_skin_low_tier_opens_tool_selection(self):
        decision = classify_kaogu_scene(
            [
                text("二阶", (181, 165, 45, 26)),
                text("工坊·考古", (436, 98, 147, 33)),
            ],
            {"tool_plus": (567, 313, 60, 60)},
            (1020, 718),
            phase="production",
        )

        self.assertEqual(
            decision,
            {"status": "open_tool_selection", "box": [567, 313, 60, 60]},
        )

    def test_low_tier_recovers_tool_slot_when_template_misses(self):
        decision = classify_kaogu_scene(
            [
                text("二阶", (197, 166, 28, 24)),
                text("工坊·考古", (436, 98, 148, 33)),
            ],
            {},
            (1020, 718),
            phase="production",
        )

        self.assertEqual(
            decision,
            {
                "status": "open_tool_selection",
                "box": [566, 302, 66, 72],
                "reason": "low_tier_tool_slot",
            },
        )

    def test_confirmed_tool_ready_starts_when_stylized_button_ocr_misses(self):
        decision = classify_kaogu_scene(
            [
                text("二阶", (197, 166, 28, 24)),
                text("工坊·考古", (436, 98, 148, 33)),
            ],
            {},
            (1020, 718),
            phase="tool_ready",
        )

        self.assertEqual(
            decision,
            {
                "status": "start_archaeology",
                "box": [525, 571, 143, 54],
                "reason": "confirmed_tool_ready",
            },
        )

    def test_current_923_tool_ready_does_not_require_tier_number_ocr(self):
        decision = classify_kaogu_scene(
            [
                text("阶", (183, 200, 19, 19)),
                text("工坊·考古", (393, 137, 135, 31)),
                text("消耗活力：1758/54", (455, 519, 151, 17)),
                text("考古", (510, 553, 53, 27)),
            ],
            {},
            (923, 720),
            phase="tool_ready",
        )

        self.assertEqual(
            decision,
            {
                "status": "start_archaeology",
                "box": [510, 553, 53, 27],
                "reason": "confirmed_tool_ready",
            },
        )

    def test_current_skin_selected_tool_starts_archaeology(self):
        decision = classify_kaogu_scene(
            [
                text("二阶", (192, 165, 34, 26)),
                text("工坊·考古", (436, 98, 147, 33)),
                text("1", (583, 330, 15, 17)),
                text("考古", (564, 560, 59, 29)),
            ],
            {},
            (1020, 718),
            phase="tool_ready",
        )

        self.assertEqual(
            decision,
            {"status": "start_archaeology", "box": [564, 560, 59, 29]},
        )

    def test_manual_mode_accepts_current_page_without_tool_name_ocr(self):
        decision = classify_kaogu_scene(
            [
                text("二阶", (185, 177, 43, 30)),
                text("工坊·考古", (436, 120, 146, 30)),
                text("考古", (563, 547, 61, 26)),
            ],
            {},
            (1020, 718),
            phase="enter",
            tool_mode="selected",
        )

        self.assertEqual(
            decision,
            {
                "status": "start_archaeology",
                "box": [563, 547, 61, 26],
                "reason": "manually_selected_tool",
            },
        )

    def test_purchase_requires_shovel_buy_button_and_shop_context(self):
        complete = classify_kaogu_scene(
            [
                text("洛阳铲", (887, 112, 106, 64)),
                text("购买", (890, 592, 153, 57)),
            ],
            {"shop_close": (1125, 135, 48, 43)},
            IMAGE_SIZE,
            phase="shop",
        )
        missing_product = classify_kaogu_scene(
            [text("购买", (890, 592, 153, 57))],
            {"shop_close": (1125, 135, 48, 43)},
            IMAGE_SIZE,
            phase="shop",
        )
        missing_button = classify_kaogu_scene(
            [text("洛阳铲", (887, 112, 106, 64))],
            {"shop_close": (1125, 135, 48, 43)},
            IMAGE_SIZE,
            phase="shop",
        )

        self.assertEqual(complete["status"], "buy_shovel")
        self.assertEqual(missing_product["status"], "unknown")
        self.assertEqual(missing_button["status"], "unknown")

    def test_current_skin_shop_combines_split_buy_button(self):
        decision = classify_kaogu_scene(
            [
                text("洛阳杂货铺", (416, 83, 164, 37)),
                text("洛阳铲", (712, 175, 66, 28)),
                text("购买数量", (634, 415, 76, 23)),
                text("购", (733, 554, 46, 30)),
                text("买", (768, 555, 32, 28)),
                text("X", (898, 177, 47, 96)),
            ],
            {},
            (1020, 718),
            phase="shop",
        )

        self.assertEqual(
            decision,
            {
                "status": "buy_shovel",
                "box": [733, 554, 67, 30],
                "close_box": [898, 177, 47, 96],
            },
        )

    def test_existing_shovel_is_selected_then_used(self):
        choose = text("选择工具", (569, 136, 150, 48))
        shovel_name = text("洛阳铲", (661, 203, 100, 51))
        shovel_template = {"shovel": (351, 179, 76, 78)}

        selected = classify_kaogu_scene(
            [choose], shovel_template, IMAGE_SIZE, phase="tool_select"
        )
        used = classify_kaogu_scene(
            [choose, shovel_name, text("使用", (702, 510, 190, 46))],
            shovel_template,
            IMAGE_SIZE,
            phase="tool_select",
        )

        self.assertEqual(selected["status"], "select_shovel")
        self.assertEqual(used["status"], "use_shovel")

    def test_purchase_toast_and_use_button_recover_when_shovel_template_misses(self):
        decision = classify_kaogu_scene(
            [
                text("选择工具", (467, 184, 89, 26)),
                text("成功购买了1件洛阳铲", (449, 318, 173, 21)),
                text("使用", (608, 488, 51, 24)),
            ],
            {},
            (1020, 718),
            phase="enter",
        )

        self.assertEqual(
            decision,
            {"status": "use_shovel", "box": [608, 488, 51, 24]},
        )

    def test_empty_tool_slot_targets_only_its_green_plus(self):
        decision = classify_kaogu_scene(
            [text("选择工具", (569, 136, 150, 48))],
            {"purchase_slot": (337, 173, 272, 98)},
            IMAGE_SIZE,
            phase="tool_select",
        )

        self.assertEqual(decision["status"], "open_shovel_shop")
        x, y, width, height = decision["box"]
        self.assertGreaterEqual(x, 375)
        self.assertLessEqual(x + width, 450)
        self.assertGreaterEqual(y, 210)
        self.assertLessEqual(y + height, 280)

    def test_current_skin_empty_tool_slot_uses_dialog_context(self):
        decision = classify_kaogu_scene(
            [
                text("选择工具", (467, 184, 89, 26)),
                text("包裹中暂时没有洛阳", (538, 288, 163, 20)),
                text("购买", (536, 352, 43, 24)),
                text("使用", (608, 488, 51, 24)),
            ],
            {},
            (1020, 718),
            phase="tool_select",
        )

        self.assertEqual(decision["status"], "open_shovel_shop")
        self.assertEqual(decision["box"], [306, 244, 76, 72])

    def test_current_skin_selects_purchased_shovel_from_first_slot(self):
        decision = classify_kaogu_scene(
            [
                text("选择工具", (467, 184, 89, 27)),
                text("2", (320, 266, 9, 9)),
                text("使用", (608, 488, 51, 24)),
            ],
            {},
            (1020, 718),
            phase="tool_select",
        )

        self.assertEqual(
            decision,
            {"status": "select_shovel", "box": [306, 244, 76, 72]},
        )

    def test_dig_requires_right_side_shovel_context(self):
        decision = classify_kaogu_scene(
            [text("挖掘", (1044, 550, 101, 51))],
            {"shovel": (1030, 398, 76, 78)},
            IMAGE_SIZE,
            phase="digging",
        )
        no_shovel = classify_kaogu_scene(
            [text("挖掘", (1044, 550, 101, 51))],
            {},
            IMAGE_SIZE,
            phase="digging",
        )

        self.assertEqual(decision["status"], "dig")
        self.assertEqual(no_shovel["status"], "wait_for_dig")

    def test_dig_ignores_the_normal_temporary_bag_panel(self):
        decision = classify_kaogu_scene(
            [
                text("临时道具栏", (520, 150, 150, 36)),
                text("洛阳铲", (1040, 510, 58, 23)),
                text("挖掘", (1044, 550, 101, 51)),
            ],
            {},
            IMAGE_SIZE,
            phase="digging",
        )

        self.assertEqual(decision, {"status": "dig", "box": [1044, 550, 101, 51]})

    def test_temporary_bag_closes_only_when_it_hides_the_dig_panel(self):
        decision = classify_kaogu_scene(
            [text("临时道具栏", (520, 150, 150, 36))],
            {},
            IMAGE_SIZE,
            phase="digging",
        )

        self.assertEqual(
            decision,
            {"status": "close_temporary_bag", "box": [838, 180, 58, 50]},
        )

    def test_sale_cleanup_closes_the_archaeology_window_before_opening_inventory(self):
        decision = classify_kaogu_scene(
            [
                text("工坊·考古", (548, 98, 185, 33)),
                text("×", (1110, 102, 35, 35)),
            ],
            {},
            IMAGE_SIZE,
            phase="sale_wait_main",
        )

        self.assertEqual(
            decision,
            {"status": "close_archaeology_for_sale", "box": [1110, 102, 35, 35]},
        )

    def test_sale_cleanup_closes_auxiliary_skills_before_opening_inventory(self):
        decision = classify_kaogu_scene(
            [text("辅助技能", (540, 100, 150, 35))],
            {},
            IMAGE_SIZE,
            phase="sale_wait_main",
        )

        self.assertEqual(
            decision,
            {"status": "close_skills_for_sale", "box": [947, 101, 64, 50]},
        )

    def test_sale_cleanup_uses_bounded_corner_when_close_ocr_is_missing(self):
        decision = classify_kaogu_scene(
            [text("工坊·考古", (435, 102, 148, 33))],
            {},
            (1020, 718),
            phase="sale_wait_main",
        )

        self.assertEqual(
            decision,
            {"status": "close_archaeology_for_sale", "box": [892, 129, 51, 50]},
        )

    def test_manual_mode_digs_with_any_selected_tool(self):
        decision = classify_kaogu_scene(
            [text("挖掘", (1044, 550, 101, 51))],
            {},
            IMAGE_SIZE,
            phase="digging",
            tool_mode="selected",
        )

        self.assertEqual(
            decision,
            {
                "status": "dig",
                "box": [1044, 550, 101, 51],
                "reason": "manually_selected_tool",
            },
        )

    def test_active_dig_panel_recovers_after_runner_restart(self):
        decision = classify_kaogu_scene(
            [text("挖掘", (844, 594, 80, 35))],
            {"shovel": (830, 470, 70, 76)},
            (1020, 718),
            phase="enter",
        )

        self.assertEqual(
            decision,
            {"status": "dig", "box": [844, 594, 80, 35]},
        )

    def test_current_skin_dig_panel_uses_right_side_ocr_context(self):
        decision = classify_kaogu_scene(
            [
                text("洛阳铲", (840, 545, 59, 29)),
                text("挖掘", (846, 589, 49, 28)),
            ],
            {},
            (1020, 718),
            phase="enter",
        )

        self.assertEqual(
            decision,
            {"status": "dig", "box": [846, 589, 49, 28]},
        )

    def test_animation_and_reward_detail_are_wait_only(self):
        animation = classify_kaogu_scene(
            [text("考古中...", (450, 430, 100, 30))],
            {},
            (1020, 718),
            phase="enter",
        )
        reward = classify_kaogu_scene(
            [text("类型：考古产品", (273, 259, 127, 26))],
            {"menu_plus": (776, 438, 44, 42)},
            (1020, 718),
            phase="enter",
        )

        self.assertEqual(
            animation,
            {
                "status": "wait_for_dig",
                "reason": "archaeology_in_progress",
            },
        )
        self.assertEqual(
            reward,
            {
                "status": "wait_for_dig",
                "reason": "archaeology_product",
            },
        )

    def test_start_pathing_is_wait_only_before_excavation_panel_appears(self):
        decision = classify_kaogu_scene(
            [],
            {"auto_pathing": (780, 590, 120, 30)},
            (1020, 718),
            phase="digging",
        )

        self.assertEqual(
            decision,
            {"status": "wait_for_start_pathing", "reason": "auto_pathing"},
        )

    def test_excavation_settlement_is_explicit_completion_evidence(self):
        decision = classify_kaogu_scene(
            [
                text("考古成功，你获得了1个汉朝陶罐", (365, 390, 300, 30)),
                text("本次考古消耗了54点活力", (405, 430, 230, 30)),
            ],
            {},
            (1020, 718),
            phase="digging",
        )

        self.assertEqual(
            decision,
            {
                "status": "dig_confirmed",
                "reason": "archaeology_settlement",
            },
        )

    def test_world_chat_archaeology_message_is_not_a_dig_transition(self):
        decision = classify_kaogu_scene(
            [text("系统玩家在挖掘考古中级藏宝图时获得奖励", (5, 625, 310, 24))],
            {},
            (1020, 718),
            phase="enter",
        )

        self.assertEqual(decision, {"status": "unknown"})

    def test_vitality_shortage_blocks_all_clicks(self):
        decision = classify_kaogu_scene(
            [text("当前活力不足", (760, 559, 90, 28))],
            {"menu_plus": (1206, 637, 44, 42)},
            IMAGE_SIZE,
        )
        self.assertEqual(decision, {"status": "blocked_resource", "reason": "活力不足"})


class FakeRuntime:
    scenes = []
    instances = []

    def __init__(self, _guard):
        self._scenes = iter(type(self).scenes)
        self.clicks = []
        self.drags = []
        type(self).instances.append(self)

    def read_scene(self, _templates):
        return next(self._scenes)

    def click_box(self, box, _scene, reason):
        self.clicks.append((list(box), reason))

    def click(self, point, _scene, reason):
        self.clicks.append((list(point), reason))

    def drag(self, start, end, _scene, reason):
        self.drags.append((tuple(start), tuple(end), reason))


class FailingClickRuntime(FakeRuntime):
    def click_box(self, box, scene, reason):
        super().click_box(box, scene, reason)
        if reason == "购买一个洛阳铲":
            raise RuntimeError("injected click failure")


class FakePurchaseGuard:
    def __init__(self, reserved=False, purchases=0):
        self.reserved = reserved
        self.reservations = purchases or int(reserved)
        self.releases = 0

    def is_reserved(self):
        return self.reserved

    def purchase_count(self):
        return self.reservations

    def can_reserve(self, max_purchases):
        return self.reservations < max_purchases

    def reserve(self, max_purchases=1):
        if not self.can_reserve(max_purchases):
            raise RuntimeError("重复购买保护已生效")
        self.reserved = True
        self.reservations += 1

    def release(self):
        self.reserved = False
        self.releases += 1


class KaoguRunnerTests(unittest.TestCase):
    def setUp(self):
        FakeRuntime.instances = []

    @staticmethod
    def _args(
        max_steps=30,
        tool_mode="luoyang_shovel",
        target_digs=TARGET_DIGS,
    ):
        return SimpleNamespace(
            inspect_scene=False,
            recognize_only=False,
            cleanup_only=False,
            max_steps=max_steps,
            target_digs=target_digs,
            tool_mode=tool_mode,
        )

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_selected_tool_mode_counts_only_ten_confirmed_map_digs(self):
        start = scene(
            [
                text("二阶", (246, 166, 35, 24)),
                text("工坊·考古", (548, 98, 185, 33)),
                text("10/1", (730, 330, 39, 17)),
                text("考古", (705, 560, 74, 29)),
            ]
        )
        dig = scene(
            [text("挖掘", (1044, 550, 101, 51))],
            {},
        )
        main = scene(
            [
                text("福利", (15, 129, 33, 16)),
                text("活动", (268, 53, 28, 12)),
                text("任务", (1100, 80, 45, 25)),
                text("队伍", (1190, 95, 45, 25)),
            ],
            {"menu_plus": (1206, 637, 44, 42)},
        )
        FakeRuntime.scenes = [
            start,
            *[
                item
                for _ in range(TARGET_DIGS)
                for item in (dig, dig_settlement())
            ],
            *sale_flow_scenes(),
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run(
                self._args(tool_mode="selected"),
                guard,
                FakePurchaseGuard(),
            )

        self.assertEqual(return_code, 0)
        result = next(
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["digs"], 10)
        self.assertEqual(result["purchases"], 0)
        click_reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertEqual(click_reasons[0], "开始考古并前往挖掘地点")
        self.assertEqual(sum("次挖掘" in reason for reason in click_reasons), 10)
        self.assertIn("进行第 1 次挖掘", click_reasons)
        self.assertIn("进行第 10 次挖掘", click_reasons)
        self.assertNotIn("购买一个洛阳铲", click_reasons)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_selected_tool_mode_never_navigates_or_opens_tool_selection(self):
        FakeRuntime.scenes = [
            scene(templates={"menu_plus": (1206, 637, 44, 42)})
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        with self.assertRaisesRegex(RuntimeError, "先手动选好工具"):
            run(
                self._args(tool_mode="selected"),
                guard,
                FakePurchaseGuard(),
            )

        self.assertEqual(FakeRuntime.instances[0].clicks, [])

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_cleanup_only_skips_purchase_and_dig_inputs(self):
        FakeRuntime.scenes = sale_flow_scenes()
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None
        args = self._args()
        args.cleanup_only = True

        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run(args, guard, FakePurchaseGuard())

        self.assertEqual(return_code, 0)
        result = next(
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["digs"], TARGET_DIGS)
        reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertNotIn("购买一个洛阳铲", reasons)
        self.assertFalse(any("次挖掘" in reason for reason in reasons))

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_reserved_purchase_closes_shop_before_verifying_existing_tool(self):
        shop = scene(
            [
                text("洛阳杂货铺", (415, 104, 166, 38)),
                text("洛阳铲", (712, 190, 67, 26)),
                text("购买", (735, 540, 66, 29)),
                text("×", (1106, 199, 33, 28)),
            ]
        )
        tool_selection = scene(
            [
                text("选择工具", (467, 184, 89, 27)),
                text("1", (320, 266, 9, 9)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        selected_tool = scene(
            [
                text("选择工具", (467, 184, 89, 27)),
                text("洛阳铲", (320, 266, 52, 21)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        start = scene(
            [
                text("工坊·考古", (436, 98, 148, 33)),
                text("考古", (564, 560, 59, 29)),
                text("消耗活力：54", (500, 490, 96, 23)),
            ]
        )
        FakeRuntime.scenes = [
            shop,
            tool_selection,
            selected_tool,
            start,
            scene(
                [
                    text("洛阳铲", (1040, 510, 58, 23)),
                    text("挖掘", (1044, 550, 52, 29)),
                ]
            ),
            dig_settlement(),
            *sale_flow_scenes(),
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None
        purchase_guard = FakePurchaseGuard(reserved=True)

        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run(
                self._args(target_digs=1), guard, purchase_guard
            )

        self.assertEqual(return_code, 0)
        reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertNotIn("购买一个洛阳铲", reasons)
        self.assertIn("考古流程：close_shop", reasons)
        self.assertEqual(purchase_guard.releases, 1)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_successful_purchase_closes_static_shop_then_verifies_tool(self):
        empty_tool = scene(
            [
                text("选择工具", (569, 184, 89, 27)),
                text("包裹中暂时没有洛阳", (600, 288, 163, 20)),
                text("购买", (600, 352, 43, 24)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        shop = scene(
            [
                text("洛阳杂货铺", (415, 104, 166, 38)),
                text("洛阳铲", (712, 190, 67, 26)),
                text("购买", (735, 540, 66, 29)),
                text("×", (1106, 199, 33, 28)),
            ]
        )
        tool_selection = scene(
            [
                text("选择工具", (467, 184, 89, 27)),
                text("1", (320, 266, 9, 9)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        selected_tool = scene(
            [
                text("选择工具", (467, 184, 89, 27)),
                text("洛阳铲", (320, 266, 52, 21)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        start = scene(
            [
                text("工坊·考古", (436, 98, 148, 33)),
                text("考古", (564, 560, 59, 29)),
                text("消耗活力：54", (500, 490, 96, 23)),
            ]
        )
        FakeRuntime.scenes = [
            empty_tool,
            shop,
            shop,
            shop,
            tool_selection,
            selected_tool,
            start,
            scene(
                [
                    text("洛阳铲", (1040, 510, 58, 23)),
                    text("挖掘", (1044, 550, 52, 29)),
                ]
            ),
            dig_settlement(),
            *sale_flow_scenes(),
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None
        purchase_guard = FakePurchaseGuard()

        with redirect_stdout(io.StringIO()):
            return_code = run(self._args(target_digs=1), guard, purchase_guard)

        self.assertEqual(return_code, 0)
        reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertEqual(reasons.count("购买一个洛阳铲"), 1)
        self.assertEqual(reasons.count("考古流程：close_shop"), 1)
        self.assertEqual(purchase_guard.reservations, 1)
        self.assertEqual(purchase_guard.releases, 1)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_unreserved_shop_at_start_stops_without_purchase(self):
        shop = scene(
            [
                text("洛阳杂货铺", (415, 104, 166, 38)),
                text("洛阳铲", (712, 190, 67, 26)),
                text("购买", (735, 540, 66, 29)),
                text("×", (1106, 199, 33, 28)),
            ]
        )
        FakeRuntime.scenes = [shop]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        with self.assertRaisesRegex(RuntimeError, "未确认从空工具页进入"):
            run(self._args(), guard, FakePurchaseGuard())
        self.assertEqual(FakeRuntime.instances[0].clicks, [])

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_empty_tool_after_purchase_can_reopen_shop_for_bounded_replenishment(self):
        empty_tool = scene(
            [
                text("选择工具", (569, 184, 89, 27)),
                text("包裹中暂时没有洛阳", (600, 288, 163, 20)),
                text("购买", (600, 352, 43, 24)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        shop = scene(
            [
                text("洛阳杂货铺", (415, 104, 166, 38)),
                text("洛阳铲", (712, 190, 67, 26)),
                text("购买", (735, 540, 66, 29)),
                text("×", (1106, 199, 33, 28)),
            ]
        )
        tool_selection = scene(
            [
                text("选择工具", (467, 184, 89, 27)),
                text("1", (320, 266, 9, 9)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        selected_tool = scene(
            [
                text("选择工具", (467, 184, 89, 27)),
                text("洛阳铲", (320, 266, 52, 21)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        start = scene(
            [
                text("工坊·考古", (436, 98, 148, 33)),
                text("考古", (564, 560, 59, 29)),
                text("消耗活力：54", (500, 490, 96, 23)),
            ]
        )
        dig = scene(
            [
                text("洛阳铲", (1040, 510, 58, 23)),
                text("挖掘", (1044, 550, 52, 29)),
            ]
        )
        # Each static shop is observed twice after its purchase, then closed
        # before returning to the positively identified tool dialog.
        FakeRuntime.scenes = [
            empty_tool,
            shop,
            shop,
            shop,
            empty_tool,
            shop,
            shop,
            shop,
            tool_selection,
            selected_tool,
            start,
            dig,
            dig_settlement(),
            *sale_flow_scenes(),
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None
        purchase_guard = FakePurchaseGuard()

        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run(
                self._args(target_digs=1), guard, purchase_guard
            )

        self.assertEqual(return_code, 0)
        result = next(
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["purchases"], 2)
        reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertEqual(reasons.count("考古流程：open_shovel_shop"), 2)
        self.assertEqual(reasons.count("购买一个洛阳铲"), 2)
        self.assertEqual(reasons.count("考古流程：close_shop"), 2)
        self.assertEqual(purchase_guard.releases, 1)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_recent_purchase_selects_icon_only_first_tool_slot(self):
        icon_only_tool_dialog = scene(
            [
                text("选择工具", (467, 184, 89, 27)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        selected_tool = scene(
            [
                text("选择工具", (467, 184, 89, 27)),
                text("洛阳铲", (320, 266, 52, 21)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        start = scene(
            [
                text("工坊·考古", (436, 98, 148, 33)),
                text("考古", (564, 560, 59, 29)),
                text("消耗活力：54", (500, 490, 96, 23)),
            ]
        )
        FakeRuntime.scenes = [
            icon_only_tool_dialog,
            selected_tool,
            start,
            scene(
                [
                    text("洛阳铲", (1040, 510, 58, 23)),
                    text("挖掘", (1044, 550, 52, 29)),
                ]
            ),
            dig_settlement(),
            *sale_flow_scenes(),
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None
        purchase_guard = FakePurchaseGuard(reserved=True)

        with redirect_stdout(io.StringIO()):
            return_code = run(
                self._args(target_digs=1), guard, purchase_guard
            )

        self.assertEqual(return_code, 0)
        reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertIn("考古流程：select_shovel", reasons)
        self.assertNotIn("购买一个洛阳铲", reasons)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_start_transition_main_replays_navigation_once_without_reusing_menu_budget(self):
        start = scene(
            [
                text("二阶", (246, 166, 35, 24)),
                text("工坊·考古", (548, 98, 185, 33)),
                text("10/1", (730, 330, 39, 17)),
                text("考古", (705, 560, 74, 29)),
            ]
        )
        main = scene(
            [
                text("福利", (15, 129, 33, 16)),
                text("任务", (1100, 80, 45, 25)),
                text("队伍", (1190, 95, 45, 25)),
            ],
            {"menu_plus": (1206, 637, 44, 42)},
        )
        reopened_start = scene(
            [
                text("二阶", (246, 166, 35, 24)),
                text("工坊·考古", (548, 98, 185, 33)),
                text("10/1", (730, 330, 39, 17)),
                text("考古", (705, 560, 74, 29)),
            ]
        )
        skills = scene(
            [
                text("人物技能", (500, 87, 96, 26)),
                text("辅助", (1100, 512, 29, 46)),
            ]
        )
        auxiliary = scene(
            [
                text("辅助技能", (500, 87, 96, 27)),
                text("考古", (950, 304, 62, 32)),
            ]
        )
        archaeology = scene(
            [
                text("工坊·考古", (548, 98, 148, 33)),
                text("技能生产", (1100, 302, 28, 76)),
            ]
        )
        FakeRuntime.scenes = [
            start,
            main,
            main,
            main,
            skills,
            auxiliary,
            archaeology,
            reopened_start,
            scene(
                [text("挖掘", (1044, 550, 101, 51))],
                {"shovel": (1030, 398, 76, 78)},
            ),
            dig_settlement(),
            *sale_flow_scenes(),
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run(
                self._args(tool_mode="luoyang_shovel", target_digs=1),
                guard,
                FakePurchaseGuard(),
            )

        self.assertEqual(return_code, 0)
        reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertEqual(reasons.count("开始考古并前往挖掘地点"), 2)
        self.assertEqual(reasons.count("展开主界面快捷菜单"), 1)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_second_start_transition_main_stops_without_further_menu_clicks(self):
        start = scene(
            [
                text("二阶", (246, 166, 35, 24)),
                text("工坊·考古", (548, 98, 185, 33)),
                text("10/1", (730, 330, 39, 17)),
                text("考古", (705, 560, 74, 29)),
            ]
        )
        main = scene(
            [
                text("福利", (15, 129, 33, 16)),
                text("任务", (1100, 80, 45, 25)),
                text("队伍", (1190, 95, 45, 25)),
            ],
            {"menu_plus": (1206, 637, 44, 42)},
        )
        skills = scene(
            [
                text("人物技能", (500, 87, 96, 26)),
                text("辅助", (1100, 512, 29, 46)),
            ]
        )
        auxiliary = scene(
            [
                text("辅助技能", (500, 87, 96, 27)),
                text("考古", (950, 304, 62, 32)),
            ]
        )
        archaeology = scene(
            [
                text("工坊·考古", (548, 98, 148, 33)),
                text("技能生产", (1100, 302, 28, 76)),
            ]
        )
        FakeRuntime.scenes = [
            start,
            main,
            main,
            main,
            skills,
            auxiliary,
            archaeology,
            start,
            main,
            main,
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        with self.assertRaisesRegex(RuntimeError, "未出现挖掘面板"):
            run(
                self._args(tool_mode="luoyang_shovel", target_digs=1),
                guard,
                FakePurchaseGuard(),
            )

        reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertEqual(reasons.count("开始考古并前往挖掘地点"), 2)
        self.assertEqual(reasons.count("展开主界面快捷菜单"), 1)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_final_dig_waits_for_main_screen_without_clicking_a_new_dig(self):
        start = scene(
            [
                text("工坊·考古", (436, 98, 148, 33)),
                text("考古", (564, 560, 59, 29)),
            ]
        )
        lingering_dig = scene([text("挖掘", (1044, 550, 101, 51))])
        transition = scene([text("考古中...", (450, 430, 100, 30))])
        FakeRuntime.scenes = [
            start,
            lingering_dig,
            transition,
            dig_settlement(),
            *sale_flow_scenes(),
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run(
                self._args(tool_mode="selected", target_digs=1),
                guard,
                FakePurchaseGuard(),
            )

        self.assertEqual(return_code, 0)
        result = next(
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["digs"], 1)
        click_reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertEqual(click_reasons[0], "开始考古并前往挖掘地点")
        self.assertEqual(click_reasons[1], "进行第 1 次挖掘")
        self.assertEqual(click_reasons.count("双击非真品古董并启动自动寻路"), 2)
        self.assertIn("考古流程：cancel_genuine_sale", click_reasons)
        self.assertIn("考古流程：close_inventory", click_reasons)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_transitional_main_before_final_battle_does_not_finish_early(self):
        start = scene(
            [
                text("工坊·考古", (436, 98, 148, 33)),
                text("考古", (564, 560, 59, 29)),
            ]
        )
        main = scene(
            [
                text("福利", (15, 129, 33, 16)),
                text("活动", (268, 53, 28, 12)),
                text("任务", (1100, 80, 45, 25)),
                text("队伍", (1190, 95, 45, 25)),
            ],
            {"menu_plus": (1206, 637, 44, 42)},
        )
        battle = scene([text("取消", (1215, 680, 50, 26))])
        FakeRuntime.scenes = [
            start,
            scene([text("挖掘", (1044, 550, 101, 51))]),
            dig_settlement(),
            main,
            battle,
            *sale_flow_scenes(include_main=False),
        ]
        guard = CancellationGuard()
        waits = []
        guard.wait = waits.append

        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run(
                self._args(tool_mode="selected", target_digs=1),
                guard,
                FakePurchaseGuard(),
            )

        self.assertEqual(return_code, 0)
        result = next(
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["digs"], 1)
        self.assertIn(5.0, waits)
        self.assertEqual(len(FakeRuntime.instances[0].clicks), 13)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_unchanged_dig_button_retries_without_counting_until_settlement(self):
        start = scene(
            [
                text("工坊·考古", (436, 98, 148, 33)),
                text("考古", (564, 560, 59, 29)),
            ]
        )
        dig = scene([text("挖掘", (1044, 550, 101, 51))])
        FakeRuntime.scenes = [
            start,
            dig,
            dig,
            dig_settlement(),
            *sale_flow_scenes(),
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run(
                self._args(tool_mode="selected", target_digs=1),
                guard,
                FakePurchaseGuard(),
            )

        self.assertEqual(return_code, 0)
        result = next(
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["digs"], 1)
        reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertEqual(reasons.count("进行第 1 次挖掘"), 2)
        self.assertNotIn("进行第 2 次挖掘", reasons)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_purchase_once_and_complete_exactly_ten_digs(self):
        empty_tool = scene(
            [
                text("选择工具", (569, 184, 89, 27)),
                text("包裹中暂时没有洛阳", (600, 288, 163, 20)),
                text("购买", (600, 352, 43, 24)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        purchase = scene(
            [
                text("洛阳铲", (887, 112, 106, 64)),
                text("购买", (890, 592, 153, 57)),
            ],
            {"shop_close": (1125, 135, 48, 43)},
        )
        close_shop = scene(templates={"shop_close": (1125, 135, 48, 43)})
        select_shovel = scene(
            [text("选择工具", (569, 136, 150, 48))],
            {"shovel": (351, 179, 76, 78)},
        )
        use_shovel = scene(
            [
                text("选择工具", (569, 136, 150, 48)),
                text("洛阳铲", (661, 203, 100, 51)),
                text("使用", (702, 510, 190, 46)),
            ],
            {"shovel": (351, 179, 76, 78)},
        )
        start = scene(
            templates={
                "archaeology_header": (462, 10, 312, 68),
                "selected_shovel": (684, 270, 76, 70),
            }
        )
        dig = scene(
            [text("挖掘", (1044, 550, 101, 51))],
            {"shovel": (1030, 398, 76, 78)},
        )
        FakeRuntime.scenes = [
            empty_tool,
            purchase,
            close_shop,
            select_shovel,
            use_shovel,
            start,
            *[
                item
                for _ in range(TARGET_DIGS)
                for item in (dig, dig_settlement())
            ],
            *sale_flow_scenes(),
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        output = io.StringIO()
        purchase_guard = FakePurchaseGuard()
        with redirect_stdout(output):
            return_code = run(self._args(), guard, purchase_guard)

        self.assertEqual(return_code, 0)
        result = next(
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["digs"], 10)
        self.assertEqual(result["target_digs"], 10)
        self.assertEqual(result["purchases"], 1)
        self.assertEqual(result["sale_clicks"], 2)
        self.assertEqual(
            result["sale_outcome"], "all_non_genuine_sold_genuine_retained"
        )
        self.assertTrue(result["genuine_protected"])
        click_reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertEqual(click_reasons.count("购买一个洛阳铲"), 1)
        self.assertEqual(sum("次挖掘" in reason for reason in click_reasons), 10)
        self.assertIn("进行第 1 次挖掘", click_reasons)
        self.assertEqual(purchase_guard.reservations, 1)
        self.assertEqual(purchase_guard.releases, 1)
        self.assertFalse(purchase_guard.is_reserved())

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_interrupted_purchase_is_not_repeated_after_restart(self):
        empty_tool = scene(
            [
                text("选择工具", (569, 184, 89, 27)),
                text("包裹中暂时没有洛阳", (600, 288, 163, 20)),
                text("购买", (600, 352, 43, 24)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        purchase = scene(
            [
                text("洛阳铲", (887, 112, 106, 64)),
                text("购买", (890, 592, 153, 57)),
            ],
            {"shop_close": (1125, 135, 48, 43)},
        )
        purchase_guard = FakePurchaseGuard()
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        FakeRuntime.scenes = [empty_tool, purchase, scene(), scene(), scene()]
        with self.assertRaisesRegex(RuntimeError, "连续三次无法识别"):
            run(self._args(), guard, purchase_guard)

        self.assertTrue(purchase_guard.is_reserved())
        self.assertEqual(purchase_guard.reservations, 1)
        first_clicks = FakeRuntime.instances[-1].clicks
        self.assertEqual(
            [reason for _, reason in first_clicks].count("购买一个洛阳铲"),
            1,
        )

        FakeRuntime.scenes = [purchase, scene(), scene(), scene()]
        with self.assertRaisesRegex(RuntimeError, "连续三次无法识别"):
            run(self._args(), guard, purchase_guard)

        restart_reasons = [reason for _, reason in FakeRuntime.instances[-1].clicks]
        self.assertEqual(restart_reasons, ["考古流程：close_shop"])
        self.assertEqual(purchase_guard.reservations, 1)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FailingClickRuntime)
    def test_purchase_is_reserved_before_click_backend_is_called(self):
        empty_tool = scene(
            [
                text("选择工具", (569, 184, 89, 27)),
                text("包裹中暂时没有洛阳", (600, 288, 163, 20)),
                text("购买", (600, 352, 43, 24)),
                text("使用", (608, 488, 51, 24)),
            ]
        )
        FailingClickRuntime.scenes = [
            empty_tool,
            scene(
                [
                    text("洛阳铲", (887, 112, 106, 64)),
                    text("购买", (890, 592, 153, 57)),
                ],
                {"shop_close": (1125, 135, 48, 43)},
            )
        ]
        purchase_guard = FakePurchaseGuard()
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        with self.assertRaisesRegex(RuntimeError, "injected click failure"):
            run(self._args(), guard, purchase_guard)

        self.assertTrue(purchase_guard.is_reserved())
        self.assertEqual(purchase_guard.reservations, 1)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_missing_purchase_evidence_stops_before_click(self):
        FakeRuntime.scenes = [
            scene(
                [text("购买", (890, 592, 153, 57))],
                {"shop_close": (1125, 135, 48, 43)},
            )
            for _ in range(3)
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        with self.assertRaisesRegex(RuntimeError, "连续三次无法识别"):
            run(self._args(), guard, FakePurchaseGuard())
        self.assertEqual(FakeRuntime.instances[0].clicks, [])

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_duplicate_main_receipt_after_settlement_does_not_stop_or_double_count(self):
        start = scene([
            text("工坊·考古", (436, 98, 148, 33)),
            text("考古", (564, 560, 59, 29)),
        ])
        dig = scene([
            text("洛阳铲", (1040, 510, 58, 23)),
            text("挖掘", (1044, 550, 52, 29)),
        ])
        duplicate_receipt = scene(
            [
                text("福利", (15, 129, 33, 16)),
                text("任务", (1100, 80, 45, 25)),
                text("队伍", (1190, 95, 45, 25)),
                text("个人你获得了1个战国山字镜", (8, 676, 235, 20)),
            ],
            {"menu_plus": (1206, 637, 44, 42)},
        )
        FakeRuntime.scenes = [
            start, dig, dig_settlement(), duplicate_receipt, dig,
            dig_settlement(), *sale_flow_scenes(),
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run(
                self._args(tool_mode="selected", target_digs=2),
                guard,
                FakePurchaseGuard(),
            )

        self.assertEqual(return_code, 0)
        result = next(
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["digs"], 2)
        self.assertIn('"event": "dig_receipt_ignored"', output.getvalue())


class FilePurchaseGuardTests(unittest.TestCase):
    def test_reservation_is_visible_across_instances_until_released(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = FilePurchaseGuard(Path(directory) / "purchase.json")
            restarted_process = FilePurchaseGuard(marker.path)

            marker.reserve()

            self.assertTrue(restarted_process.is_reserved())
            with self.assertRaisesRegex(RuntimeError, "不再自动购买"):
                restarted_process.reserve()

            restarted_process.release()
            self.assertFalse(marker.is_reserved())

    def test_exited_owner_reservation_is_recovered_for_a_new_run(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = FilePurchaseGuard(Path(directory) / "purchase.json")
            marker.path.write_text(
                json.dumps(
                    {
                        "task_id": "kaogu",
                        "pid": 999_999_999,
                        "reserved_at": (
                            datetime.now(timezone.utc) - timedelta(minutes=31)
                        ).isoformat(),
                    }
                ),
                encoding="utf-8",
            )

            self.assertTrue(marker.reset_if_owner_exited())
            self.assertFalse(marker.is_reserved())

    def test_live_owner_reservation_remains_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = FilePurchaseGuard(Path(directory) / "purchase.json")
            marker.path.write_text(
                json.dumps(
                    {
                        "task_id": "kaogu",
                        "pid": os.getpid(),
                        "reserved_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                encoding="utf-8",
            )

            self.assertFalse(marker.reset_if_owner_exited())
            self.assertTrue(marker.is_reserved())

    def test_exited_owner_can_make_only_one_bounded_replenishment_purchase(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = FilePurchaseGuard(Path(directory) / "purchase.json")
            marker.path.write_text(
                json.dumps({
                    "task_id": "kaogu", "pid": 999_999_999,
                    "reserved_at": datetime.now(timezone.utc).isoformat(),
                    "purchases": 1,
                }),
                encoding="utf-8",
            )

            self.assertTrue(marker.can_reserve(2))
            marker.reserve(2)
            self.assertEqual(marker.purchase_count(), 2)
            self.assertFalse(marker.can_reserve(2))

    def test_recent_exited_owner_reservation_remains_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = FilePurchaseGuard(Path(directory) / "purchase.json")
            marker.path.write_text(
                json.dumps(
                    {
                        "task_id": "kaogu",
                        "pid": 999_999_999,
                        "reserved_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                encoding="utf-8",
            )

            self.assertFalse(marker.reset_if_owner_exited())
            self.assertTrue(marker.is_reserved())


if __name__ == "__main__":
    unittest.main()
