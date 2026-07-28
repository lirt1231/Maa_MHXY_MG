import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.run_yunbiao_macos import run
from tools.yunbiao_macos_core import classify_yunbiao_scene


def text(value, box=(10, 20, 80, 24)):
    return {"text": value, "box": list(box)}


def scene(texts=None, templates=None):
    return SimpleNamespace(
        image_size=(1020, 718),
        texts=texts or [],
        templates=templates or {},
    )


class YunbiaoSceneTests(unittest.TestCase):
    def test_main_toolbar_uses_neighbor_anchors_when_activity_ocr_is_missing(self):
        decision = classify_yunbiao_scene(
            [
                text("指引", (199, 48, 34, 20)),
                text("排行", (323, 49, 37, 19)),
                text("泊活动", (83, 127, 54, 19)),
            ],
            {},
            (1020, 718),
            "enter",
        )
        self.assertEqual(decision["status"], "open_activity")
        self.assertEqual(decision["reason"], "guide_ranking_anchors")
        self.assertEqual(decision["box"], [254, 42, 51, 32])

    def test_challenge_page_opens_daily_before_other_actions(self):
        daily_tab = text("日常活动", (130, 161, 88, 27))
        decision = classify_yunbiao_scene(
            [
                text("活动", (474, 100, 71, 23)),
                daily_tab,
                text("挑战活动", (127, 217, 90, 27)),
            ],
            {},
            (1020, 718),
            "enter",
            daily_tab_confirmed=False,
        )
        self.assertEqual(decision, {"status": "open_daily", "box": daily_tab["box"]})

    def test_only_exact_normal_delivery_is_actionable(self):
        normal = classify_yunbiao_scene(
            [text("押送普通镖银", (760, 300, 160, 28))], {}, (1020, 718), "enter"
        )
        self.assertEqual(normal["status"], "select_normal")

        other = classify_yunbiao_scene(
            [text("押送高级镖银", (760, 300, 160, 28))], {}, (1020, 718), "enter"
        )
        self.assertEqual(other["status"], "unsupported_delivery")

    def test_confirmation_requires_normal_delivery_phase_and_region(self):
        confirmation = [text("确定", (480, 390, 80, 30))]
        self.assertEqual(
            classify_yunbiao_scene(
                confirmation, {}, (1020, 718), "confirm_normal"
            )["status"],
            "confirm_normal",
        )
        self.assertEqual(
            classify_yunbiao_scene(confirmation, {}, (1020, 718), "enter")["status"],
            "unknown",
        )

    def test_delivery_and_resource_evidence_are_wait_or_stop(self):
        active = classify_yunbiao_scene(
            [text("距离运镖完成：42秒", (120, 20, 180, 25))],
            {},
            (1020, 718),
            "await_delivery",
        )
        self.assertEqual(active["status"], "delivery_active")
        blocked = classify_yunbiao_scene(
            [text("活力不足")], {}, (1020, 718), "enter"
        )
        self.assertEqual(blocked["status"], "blocked_resource")

    def test_current_battle_cancel_is_wait_only(self):
        decision = classify_yunbiao_scene(
            [text("取消", (961, 680, 44, 25))],
            {},
            (1020, 718),
            "await_delivery",
        )
        self.assertEqual(decision, {"status": "wait", "reason": "battle_ocr"})


class YunbiaoRunnerTests(unittest.TestCase):
    @patch("tools.run_yunbiao_macos.MacOSTaskRuntime")
    def test_three_rounds_require_delivery_evidence_before_completion(self, runtime_class):
        runtime = runtime_class.return_value
        normal = scene([text("押送普通镖银", (760, 300, 160, 28))])
        confirm = scene([text("确定", (480, 390, 80, 30))])
        active = scene([text("距离运镖完成：42秒", (120, 20, 180, 25))])
        runtime.read_scene.side_effect = [
            normal, confirm, active,
            normal, confirm, active,
            normal, confirm, active,
            normal,
        ]
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, rounds=3, max_steps=15
        )
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(runtime.click_box.call_count, 6)
        result = next(
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["rounds"], 3)

    @patch("tools.run_yunbiao_macos.MacOSTaskRuntime")
    def test_repeated_normal_option_stops_before_second_click(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = scene(
            [text("押送普通镖银", (760, 300, 160, 28))]
        )
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, rounds=3, max_steps=4
        )

        with self.assertRaisesRegex(RuntimeError, "页面未变化"):
            run(args, Mock())
        self.assertEqual(runtime.click_box.call_count, 1)


if __name__ == "__main__":
    unittest.main()
