import io
import json
import tempfile
import unittest
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

    def test_dig_keeps_workflow_in_digging_phase(self):
        self.assertEqual(next_kaogu_phase("dig", "enter"), "digging")

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
            {"status": "buy_shovel", "box": [733, 554, 67, 30]},
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

        expected = {
            "status": "wait_for_dig",
            "reason": "archaeology_transition",
        }
        self.assertEqual(animation, expected)
        self.assertEqual(reward, expected)

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
        type(self).instances.append(self)

    def read_scene(self, _templates):
        return next(self._scenes)

    def click_box(self, box, _scene, reason):
        self.clicks.append((list(box), reason))


class FailingClickRuntime(FakeRuntime):
    def click_box(self, box, scene, reason):
        super().click_box(box, scene, reason)
        raise RuntimeError("injected click failure")


class FakePurchaseGuard:
    def __init__(self, reserved=False):
        self.reserved = reserved
        self.reservations = 0
        self.releases = 0

    def is_reserved(self):
        return self.reserved

    def reserve(self):
        if self.reserved:
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
            max_steps=max_steps,
            target_digs=target_digs,
            tool_mode=tool_mode,
        )

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_selected_tool_mode_counts_start_and_nine_map_digs(self):
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
                text("任务", (1100, 80, 45, 25)),
                text("队伍", (1190, 95, 45, 25)),
            ],
            {"menu_plus": (1206, 637, 44, 42)},
        )
        FakeRuntime.scenes = [
            start,
            *[dig for _ in range(TARGET_DIGS - 1)],
            main,
            main,
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
        self.assertEqual(click_reasons[0], "考古流程：start_archaeology")
        self.assertEqual(sum("次挖掘" in reason for reason in click_reasons), 9)
        self.assertIn("进行第 2 次挖掘", click_reasons)
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
    def test_final_dig_waits_for_main_screen_without_clicking_a_new_dig(self):
        start = scene(
            [
                text("工坊·考古", (436, 98, 148, 33)),
                text("考古", (564, 560, 59, 29)),
            ]
        )
        lingering_dig = scene([text("挖掘", (1044, 550, 101, 51))])
        transition = scene([text("考古中...", (450, 430, 100, 30))])
        inventory = scene(
            [
                text("物品", (600, 120, 70, 30)),
                text("包裹", (1070, 180, 55, 28)),
                text("仓库", (1070, 330, 55, 28)),
            ]
        )
        main = scene(
            [
                text("任务", (1100, 80, 45, 25)),
                text("队伍", (1190, 95, 45, 25)),
            ],
            {"menu_plus": (1206, 637, 44, 42)},
        )
        FakeRuntime.scenes = [
            start,
            lingering_dig,
            transition,
            inventory,
            main,
            main,
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
        self.assertEqual(
            click_reasons,
            [
                "考古流程：start_archaeology",
                "考古流程：close_inventory",
            ],
        )

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
                text("任务", (1100, 80, 45, 25)),
                text("队伍", (1190, 95, 45, 25)),
            ],
            {"menu_plus": (1206, 637, 44, 42)},
        )
        battle = scene([text("取消", (1215, 680, 50, 26))])
        FakeRuntime.scenes = [start, main, battle, main, main]
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
        self.assertEqual(len(FakeRuntime.instances[0].clicks), 1)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_purchase_once_and_complete_exactly_ten_digs(self):
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
            purchase,
            close_shop,
            select_shovel,
            use_shovel,
            start,
            *[dig for _ in range(TARGET_DIGS)],
            scene(
                [
                    text("任务", (1100, 80, 45, 25)),
                    text("队伍", (1190, 95, 45, 25)),
                ],
                {"menu_plus": (1206, 637, 44, 42)},
            ),
            scene(
                [
                    text("任务", (1100, 80, 45, 25)),
                    text("队伍", (1190, 95, 45, 25)),
                ],
                {"menu_plus": (1206, 637, 44, 42)},
            ),
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
        click_reasons = [reason for _, reason in FakeRuntime.instances[0].clicks]
        self.assertEqual(click_reasons.count("购买一个洛阳铲"), 1)
        self.assertEqual(sum("次挖掘" in reason for reason in click_reasons), 10)
        self.assertEqual(purchase_guard.reservations, 1)
        self.assertEqual(purchase_guard.releases, 1)
        self.assertFalse(purchase_guard.is_reserved())

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FakeRuntime)
    def test_interrupted_purchase_is_not_repeated_after_restart(self):
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

        FakeRuntime.scenes = [purchase, scene(), scene(), scene()]
        with self.assertRaisesRegex(RuntimeError, "连续三次无法识别"):
            run(self._args(), guard, purchase_guard)

        self.assertTrue(purchase_guard.is_reserved())
        self.assertEqual(purchase_guard.reservations, 1)
        first_clicks = FakeRuntime.instances[-1].clicks
        self.assertEqual(
            [reason for _, reason in first_clicks].count("购买一个洛阳铲"),
            1,
        )

        FakeRuntime.scenes = [purchase]
        with self.assertRaisesRegex(RuntimeError, "不再自动购买"):
            run(self._args(), guard, purchase_guard)

        self.assertEqual(FakeRuntime.instances[-1].clicks, [])
        self.assertEqual(purchase_guard.reservations, 1)

    @patch("tools.run_kaogu_macos.MacOSTaskRuntime", FailingClickRuntime)
    def test_purchase_is_reserved_before_click_backend_is_called(self):
        FailingClickRuntime.scenes = [
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


if __name__ == "__main__":
    unittest.main()
