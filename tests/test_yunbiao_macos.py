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
