import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from tools.kaogu_macos_core import classify_kaogu_scene
from tools.macos_cancellation import CancellationGuard
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
    def test_main_menu_navigation_requires_visible_templates(self):
        plus = classify_kaogu_scene(
            [], {"menu_plus": (1206, 637, 44, 42)}, IMAGE_SIZE
        )
        skills = classify_kaogu_scene(
            [], {"skills_icon": (1048, 638, 44, 39)}, IMAGE_SIZE
        )

        self.assertEqual(plus["status"], "expand_menu")
        self.assertEqual(skills["status"], "open_skills")

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


class KaoguRunnerTests(unittest.TestCase):
    def setUp(self):
        FakeRuntime.instances = []

    @staticmethod
    def _args(max_steps=30):
        return SimpleNamespace(
            inspect_scene=False,
            recognize_only=False,
            max_steps=max_steps,
        )

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
        ]
        guard = CancellationGuard()
        guard.wait = lambda _seconds: None

        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run(self._args(), guard)

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
            run(self._args(), guard)
        self.assertEqual(FakeRuntime.instances[0].clicks, [])


if __name__ == "__main__":
    unittest.main()
