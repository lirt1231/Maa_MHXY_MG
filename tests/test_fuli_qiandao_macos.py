import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.fuli_qiandao_macos_core import (
    classify_welfare_scene,
    completed_scratch_page,
    scratch_box_from_ocr,
    scratch_gestures,
    welfare_entry_box,
)
import tools.run_fuli_qiandao_macos as welfare_runner
from tools.run_fuli_qiandao_macos import run
from tools.macos_task_runtime import MacOSScene, MacOSTaskRuntime


def text(value, box=(10, 20, 80, 30)):
    return {"text": value, "box": list(box)}


def scene(texts=None, templates=None):
    return SimpleNamespace(
        image_size=(1020, 718),
        texts=texts or [],
        templates=templates or {},
    )


def protocol_payloads(output, prefix):
    return [
        json.loads(line[len(prefix):])
        for line in output.splitlines()
        if line.startswith(prefix)
    ]


class WelfareSceneClassificationTests(unittest.TestCase):
    def test_main_screen_opens_only_recognized_welfare_entry(self):
        decision = classify_welfare_scene([text(" 福 利 ")], {}, "enter")
        self.assertEqual(decision, {"status": "open_welfare", "box": [10, 0, 80, 60]})

    def test_welfare_entry_targets_icon_above_ocr_label(self):
        label = [12, 128, 39, 21]
        icon = welfare_entry_box(label, (1020, 718))

        self.assertEqual(icon, [12, 86, 39, 42])
        self.assertLess(icon[1] + icon[3] // 2, label[1])

    def test_welfare_page_opens_recognized_scratch_card(self):
        decision = classify_welfare_scene(
            [text("打开刮刮乐", (820, 590, 160, 50))], {}, "welfare"
        )
        self.assertEqual(decision["status"], "open_scratch")
        self.assertEqual(decision["box"], [820, 590, 160, 50])

    def test_template_is_preferred_as_scratch_boundary(self):
        scratch_box = (300, 210, 428, 157)
        decision = classify_welfare_scene(
            [text("滑动刮奖", (430, 250, 100, 30))],
            {"scratch": scratch_box},
            "scratch",
        )
        self.assertEqual(decision, {"status": "scratch_ready", "box": scratch_box})

    def test_complete_scratch_page_context_derives_safe_boundary(self):
        texts = [
            text("梦幻刮刮乐", (464, 97, 123, 25)),
            text("今日奖品", (605, 160, 127, 39)),
            text("滑动刮奖", (578, 398, 189, 33)),
            text("当前累计：2/7次", (450, 590, 141, 22)),
        ]

        decision = classify_welfare_scene(texts, {}, "scratch", (1020, 718))

        self.assertEqual(
            decision,
            {"status": "scratch_ready", "box": [518, 348, 309, 118]},
        )
        for start, end in scratch_gestures(decision["box"]):
            for x, y in (start, end):
                self.assertGreaterEqual(x, 518)
                self.assertLessEqual(x, 827)
                self.assertGreaterEqual(y, 348)
                self.assertLessEqual(y, 466)

    def test_ocr_boundary_requires_all_scratch_page_evidence(self):
        required = [
            text("梦幻刮刮乐", (464, 97, 123, 25)),
            text("今日奖品", (605, 160, 127, 39)),
            text("滑动刮奖", (578, 398, 189, 33)),
            text("当前累计：2/7次", (450, 590, 141, 22)),
        ]
        for missing_index in range(len(required)):
            with self.subTest(missing=required[missing_index]["text"]):
                texts = required[:missing_index] + required[missing_index + 1 :]
                self.assertIsNone(scratch_box_from_ocr(texts, (1020, 718)))

    def test_ocr_boundary_rejects_out_of_region_prompt(self):
        texts = [
            text("梦幻刮刮乐", (464, 97, 123, 25)),
            text("今日奖品", (605, 160, 127, 39)),
            text("滑动刮奖", (50, 50, 189, 33)),
            text("当前累计：2/7次", (450, 590, 141, 22)),
        ]

        self.assertIsNone(scratch_box_from_ocr(texts, (1020, 718)))

    def test_post_scratch_page_is_a_terminal_state(self):
        texts = [
            text("梦幻刮刮乐", (463, 94, 158, 29)),
            text("今日奖品", (605, 160, 127, 39)),
            text("刮奖满一定次数可", (450, 531, 145, 18)),
            text("获得累计奖励", (449, 547, 112, 22)),
            text("当前累计：3/7次", (450, 590, 141, 22)),
        ]

        self.assertTrue(completed_scratch_page(texts, (1020, 718)))
        self.assertEqual(
            classify_welfare_scene(texts, {}, "verify", (1020, 718))["status"],
            "completed",
        )
        self.assertEqual(
            classify_welfare_scene(texts, {}, "enter", (1020, 718))["status"],
            "already_completed",
        )

    def test_post_scratch_state_requires_full_context_and_absent_prompt(self):
        texts = [
            text("梦幻刮刮乐", (463, 94, 158, 29)),
            text("今日奖品", (605, 160, 127, 39)),
            text("刮奖满一定次数可", (450, 531, 145, 18)),
            text("获得累计奖励", (449, 547, 112, 22)),
            text("当前累计：3/7次", (450, 590, 141, 22)),
        ]
        for missing_index in range(len(texts)):
            with self.subTest(missing=texts[missing_index]["text"]):
                incomplete = texts[:missing_index] + texts[missing_index + 1 :]
                self.assertFalse(completed_scratch_page(incomplete, (1020, 718)))
        self.assertFalse(
            completed_scratch_page(
                texts + [text("滑动刮奖", (578, 398, 189, 33))],
                (1020, 718),
            )
        )

    def test_confirmation_is_limited_to_scratch_or_verify_phase(self):
        self.assertEqual(
            classify_welfare_scene([text("签到奖励"), text("确定")], {}, "enter")["status"],
            "welfare_idle",
        )
        self.assertEqual(
            classify_welfare_scene([text("确定")], {}, "verify")["status"],
            "unknown",
        )
        self.assertEqual(
            classify_welfare_scene([text("获得奖励"), text("确定")], {}, "verify")["status"],
            "confirm_reward",
        )

    def test_completed_and_unknown_scenes_are_distinguished(self):
        self.assertEqual(
            classify_welfare_scene([text("签到成功")], {}, "verify")["status"],
            "completed",
        )
        self.assertEqual(
            classify_welfare_scene([text("今日已签到")], {}, "welfare")["status"],
            "already_completed",
        )
        self.assertEqual(classify_welfare_scene([], {}, "enter")["status"], "unknown")

    def test_direct_completed_page_requires_welfare_context(self):
        self.assertEqual(
            classify_welfare_scene([text("今日已签到")], {}, "enter")["status"],
            "unknown",
        )
        self.assertEqual(
            classify_welfare_scene(
                [text("今日已签到")],
                {"welfare_header": (300, 10, 437, 53)},
                "enter",
                (1020, 718),
            )["status"],
            "already_completed",
        )

    def test_unrelated_completion_text_does_not_skip_welfare_entry(self):
        decision = classify_welfare_scene(
            [text("福利", (5, 110, 60, 50)), text("师门任务已完成")], {}, "enter"
        )
        self.assertEqual(decision["status"], "open_welfare")

    def test_ocr_scratch_prompt_is_not_used_as_drag_boundary(self):
        decision = classify_welfare_scene([text("滑动刮奖")], {}, "scratch")
        self.assertEqual(decision["status"], "unknown")

    def test_actionable_evidence_must_be_in_expected_regions(self):
        image_size = (1020, 718)
        cases = [
            ([text("福利", (800, 100, 80, 40))], {}, "enter"),
            ([text("打开刮刮乐", (20, 30, 160, 50))], {}, "welfare"),
            ([], {"scratch": (850, 10, 160, 80)}, "scratch"),
            ([text("获得奖励"), text("确定", (20, 30, 80, 40))], {}, "verify"),
        ]
        for texts, templates, phase in cases:
            with self.subTest(phase=phase, texts=texts, templates=templates):
                self.assertEqual(
                    classify_welfare_scene(texts, templates, phase, image_size)["status"],
                    "unknown",
                )

    def test_scratch_entry_is_not_reopened_after_scratching(self):
        self.assertEqual(
            classify_welfare_scene(
                [text("打开刮刮乐", (820, 590, 160, 50))],
                {},
                "verify",
                (1020, 718),
            )["status"],
            "unknown",
        )

    def test_scratch_gestures_alternate_and_stay_inside_evidence_box(self):
        box = (100, 200, 400, 160)
        gestures = scratch_gestures(box)
        self.assertEqual(len(gestures), 5)
        for start, end in gestures:
            for x, y in (start, end):
                self.assertGreaterEqual(x, box[0])
                self.assertLessEqual(x, box[0] + box[2])
                self.assertGreaterEqual(y, box[1])
                self.assertLessEqual(y, box[1] + box[3])
        self.assertLess(gestures[0][0][0], gestures[0][1][0])
        self.assertGreater(gestures[1][0][0], gestures[1][1][0])


class WelfareRunnerTests(unittest.TestCase):
    @patch("tools.run_fuli_qiandao_macos.MacOSTaskRuntime")
    def test_full_flow_uses_only_recognized_boxes(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene([text("福利", (5, 110, 60, 50))]),
            scene([text("打开刮刮乐", (820, 590, 160, 50))]),
            scene(templates={"scratch": (300, 210, 428, 157)}),
            scene([text("获得奖励"), text("确定", (470, 520, 80, 40))]),
            scene([text("签到成功")]),
        ]
        guard = Mock()
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=8)
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, guard), 0)

        self.assertEqual(runtime.click_box.call_count, 3)
        self.assertEqual(runtime.click_box.call_args_list[0].args[0], [5, 10, 60, 100])
        self.assertEqual(runtime.click_box.call_args_list[1].args[0], [820, 590, 160, 50])
        self.assertEqual(runtime.click_box.call_args_list[2].args[0], [470, 520, 80, 40])
        self.assertEqual(runtime.drag.call_count, 5)
        for call in runtime.drag.call_args_list:
            start, end = call.args[:2]
            for x, y in (start, end):
                self.assertGreaterEqual(x, 300)
                self.assertLessEqual(x, 728)
                self.assertGreaterEqual(y, 210)
                self.assertLessEqual(y, 367)
        self.assertEqual(
            protocol_payloads(output.getvalue(), "result_json="),
            [{
                "task_id": "fuli_qiandao",
                "status": "completed",
                "actions": 8,
                "clicks": 3,
            }],
        )

    @patch("tools.run_fuli_qiandao_macos.MacOSTaskRuntime")
    def test_already_completed_emits_one_terminal_result(self, runtime_class):
        runtime_class.return_value.read_scene.side_effect = [
            scene([text("福利", (5, 110, 60, 50))]),
            scene([text("今日已签到")]),
        ]
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=2)
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(
            protocol_payloads(output.getvalue(), "result_json="),
            [{
                "task_id": "fuli_qiandao",
                "status": "already_completed",
                "actions": 1,
                "clicks": 1,
            }],
        )

    @patch("tools.run_fuli_qiandao_macos.MacOSTaskRuntime")
    def test_unknown_limit_requires_consecutive_unknown_scenes(self, runtime_class):
        runtime_class.return_value.read_scene.side_effect = [
            scene(),
            scene([text("福利", (5, 110, 60, 50))]),
            scene(),
            scene(),
            scene(),
        ]
        guard = Mock()
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=6)

        with self.assertRaisesRegex(RuntimeError, "连续三次"):
            run(args, guard)

        self.assertEqual(runtime_class.return_value.read_scene.call_count, 5)

    @patch("tools.run_fuli_qiandao_macos.MacOSTaskRuntime")
    def test_repeated_scratch_region_has_bounded_drag_attempts(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene(templates={"scratch": (300, 210, 428, 157)}),
            scene(templates={"scratch": (300, 210, 428, 157)}),
            scene(templates={"scratch": (300, 210, 428, 157)}),
        ]
        guard = Mock()
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=5)

        with self.assertRaisesRegex(RuntimeError, "连续两次"):
            run(args, guard)

        self.assertEqual(runtime.drag.call_count, 10)

    @patch("tools.run_fuli_qiandao_macos.MacOSTaskRuntime")
    def test_read_only_modes_emit_only_preflight_protocol(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = scene([text("福利")])
        guard = Mock()

        for inspect_scene, recognize_only in ((True, False), (False, True)):
            with self.subTest(inspect_scene=inspect_scene, recognize_only=recognize_only):
                output = io.StringIO()
                args = SimpleNamespace(
                    inspect_scene=inspect_scene,
                    recognize_only=recognize_only,
                    max_steps=1,
                )
                with redirect_stdout(output):
                    self.assertEqual(run(args, guard), 0)
                self.assertEqual(
                    len(protocol_payloads(output.getvalue(), "preflight_json=")), 1
                )
                self.assertEqual(
                    protocol_payloads(output.getvalue(), "result_json="), []
                )
        runtime.click_box.assert_not_called()
        runtime.drag.assert_not_called()

    def test_main_emits_one_safe_stopped_result_with_reason(self):
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=1)
        output = io.StringIO()
        with (
            patch.object(welfare_runner, "parse_args", return_value=args),
            patch.object(welfare_runner, "CancellationGuard", return_value=Mock()),
            patch.object(welfare_runner, "run", side_effect=RuntimeError("无可靠签到画面")),
            redirect_stdout(output),
        ):
            self.assertEqual(welfare_runner.main(), 1)

        self.assertEqual(
            protocol_payloads(output.getvalue(), "result_json="),
            [{
                "task_id": "fuli_qiandao",
                "status": "safe_stopped",
                "reason": "无可靠签到画面",
            }],
        )

    def test_main_emits_one_cancelled_result(self):
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=1)
        guard = Mock()
        output = io.StringIO()
        with (
            patch.object(welfare_runner, "parse_args", return_value=args),
            patch.object(welfare_runner, "CancellationGuard", return_value=guard),
            patch.object(
                welfare_runner,
                "run",
                side_effect=welfare_runner.CancellationRequested("收到停止请求"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(welfare_runner.main(), 0)

        self.assertEqual(
            protocol_payloads(output.getvalue(), "result_json="),
            [{
                "task_id": "fuli_qiandao",
                "status": "cancelled",
                "reason": "收到停止请求",
            }],
        )
        guard.wait_for_tasker_stop.assert_called_once_with()


class MacOSTaskRuntimeInputTests(unittest.TestCase):
    def setUp(self):
        self.runtime = MacOSTaskRuntime.__new__(MacOSTaskRuntime)
        self.runtime.guard = Mock()
        self.runtime.native_input = Mock()
        self.scene = MacOSScene((1020, 718), [], {})

    def test_click_rejects_out_of_bounds_evidence(self):
        with self.assertRaisesRegex(RuntimeError, "超出当前画面"):
            self.runtime.click((-1, 20), self.scene, "测试点击")
        self.runtime.guard.perform_input.assert_not_called()

    def test_click_box_rejects_invalid_dimensions(self):
        with self.assertRaisesRegex(RuntimeError, "识别区域无效"):
            self.runtime.click_box((10, 10, 0, 30), self.scene, "测试点击")
        self.runtime.guard.perform_input.assert_not_called()

    def test_drag_rejects_non_finite_or_out_of_bounds_points(self):
        with self.assertRaisesRegex(RuntimeError, "输入坐标无效"):
            self.runtime.drag((10, 20), (float("nan"), 30), self.scene, "测试拖动")
        with self.assertRaisesRegex(RuntimeError, "超出当前画面"):
            self.runtime.drag((10, 20), (1020, 30), self.scene, "测试拖动")
        self.runtime.guard.perform_input.assert_not_called()


if __name__ == "__main__":
    unittest.main()
