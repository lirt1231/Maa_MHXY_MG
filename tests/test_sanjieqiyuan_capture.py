import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from tools.run_sanjieqiyuan_macos import (
    CancellationRequested,
    NativeMacOSInput,
    WindowCapture,
    advance_question_count,
    classify_scene,
    main,
    navigate_to_exam,
    verify_answering_completion,
)
from custom.recognition.reco_sjqy import sjqy_rois
from custom.recognition.searchAnswer import get_question_bank, search_answer


class FakeGuard:
    def __init__(self, fail_at=None):
        self.calls = 0
        self.fail_at = fail_at

    def checkpoint(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise CancellationRequested()


class WindowCaptureTests(unittest.TestCase):
    def controller_with_image(self, image):
        job = Mock()
        job.wait.return_value = job
        job.get.return_value = image
        controller = Mock()
        controller.post_screencap.return_value = job
        return controller

    def test_uses_maa_image_when_available(self):
        image = np.ones((2, 3, 3), dtype=np.uint8)
        fallback = Mock()
        capture = WindowCapture(self.controller_with_image(image), 42, fallback)

        self.assertIs(capture.capture(FakeGuard()), image)
        fallback.assert_not_called()

    def test_switches_to_system_capture_after_maa_exception(self):
        controller = Mock()
        controller.post_screencap.side_effect = RuntimeError("no frame")
        fallback_image = np.ones((4, 5, 3), dtype=np.uint8)
        fallback = Mock(return_value=fallback_image)
        capture = WindowCapture(controller, 42, fallback)

        self.assertIs(capture.capture(FakeGuard()), fallback_image)
        self.assertIs(capture.capture(FakeGuard()), fallback_image)
        self.assertEqual(controller.post_screencap.call_count, 1)
        self.assertEqual(fallback.call_count, 2)

    def test_empty_maa_image_uses_fallback(self):
        fallback_image = np.ones((4, 5, 3), dtype=np.uint8)
        fallback = Mock(return_value=fallback_image)
        capture = WindowCapture(
            self.controller_with_image(np.empty((0, 0, 3), dtype=np.uint8)),
            42,
            fallback,
        )

        self.assertIs(capture.capture(FakeGuard()), fallback_image)

    def test_checks_cancellation_before_capture(self):
        controller = self.controller_with_image(np.ones((2, 3, 3), dtype=np.uint8))
        capture = WindowCapture(controller, 42, Mock())

        with self.assertRaises(CancellationRequested):
            capture.capture(FakeGuard(fail_at=1))
        controller.post_screencap.assert_not_called()

    def test_does_not_treat_cancellation_after_maa_wait_as_capture_failure(self):
        fallback = Mock()
        controller = self.controller_with_image(np.ones((2, 3, 3), dtype=np.uint8))
        capture = WindowCapture(controller, 42, fallback)

        with self.assertRaises(CancellationRequested):
            capture.capture(FakeGuard(fail_at=2))
        fallback.assert_not_called()
        self.assertTrue(capture.maa_available)


class NativeMacOSInputBindingTests(unittest.TestCase):
    @staticmethod
    def input_stub(window_id=0):
        native_input = NativeMacOSInput.__new__(NativeMacOSInput)
        native_input.window_id = window_id
        native_input.find_window_function = Mock(return_value=0)
        native_input.click_function = Mock(return_value=0)
        native_input.drag_function = Mock(return_value=0)
        return native_input

    def test_click_uses_explicitly_bound_window(self):
        native_input = self.input_stub()
        native_input.bind_window(314)

        native_input.click(100, 200, (1020, 718))

        self.assertEqual(native_input.click_function.call_args.args[0], 314)
        native_input.find_window_function.assert_not_called()

    def test_drag_uses_same_explicitly_bound_window(self):
        native_input = self.input_stub(271)

        native_input.drag((10, 20), (30, 40), (1020, 718))

        self.assertEqual(native_input.drag_function.call_args.args[0], 271)

    def test_legacy_unbound_input_resolves_window_only_once(self):
        native_input = self.input_stub()
        native_input.find_window_function.return_value = 42

        native_input.click(10, 20, (1020, 718))
        native_input.click(30, 40, (1020, 718))

        self.assertEqual(native_input.window_id, 42)
        native_input.find_window_function.assert_called_once_with(
            b"com.netease.my", "梦幻西游".encode()
        )
        self.assertEqual(
            [call.args[0] for call in native_input.click_function.call_args_list],
            [42, 42],
        )

    def test_rejects_invalid_window_binding(self):
        native_input = self.input_stub()

        with self.assertRaisesRegex(ValueError, "有效的窗口编号"):
            native_input.bind_window(0)


class SanjieRoiTests(unittest.TestCase):
    def test_macos_question_roi_covers_both_wrapped_lines(self):
        image = np.ones((718, 1020, 3), dtype=np.uint8)

        question_roi, answer_roi = sjqy_rois(image)

        self.assertLessEqual(question_roi[1], 133)
        self.assertGreaterEqual(question_roi[1] + question_roi[3], 175)
        self.assertGreater(answer_roi[1], question_roi[1])

    def test_multi_select_choices_count_as_one_question(self):
        current, completed = advance_question_count(None, "第一题", 0)
        current, completed = advance_question_count(current, "第一题", completed)
        current, completed = advance_question_count(current, "第一题", completed)
        self.assertEqual((current, completed), ("第一题", 0))

        current, completed = advance_question_count(current, "第二题", completed)
        self.assertEqual((current, completed), ("第二题", 1))

    def test_pansi_teacher_question_has_current_screen_answer(self):
        answer, confidence, _ = search_answer(
            get_question_bank(force_reload=True),
            "以下哪个角色不是盘丝洞的门派师父？",
        )

        self.assertEqual(answer, '["白姑娘"]')
        self.assertEqual(confidence, 100)

    def test_lion_monster_question_uses_the_real_option_name(self):
        answer, confidence, _ = search_answer(
            get_question_bank(force_reload=True),
            "以下谁是狮子怪？",
        )

        self.assertEqual(answer, '["九灵元圣"]')
        self.assertEqual(confidence, 100)


class SanjieNavigationTests(unittest.TestCase):
    image_size = [1020, 718]

    @staticmethod
    def item(text, x, y, width=90, height=24):
        return {"text": text, "box": [x, y, width, height]}

    def activity_scene(self, *items):
        return {
            "image_size": self.image_size,
            "battle": False,
            "battle_box": None,
            "activity_box": None,
            "texts": [
                self.item("活动", 480, 35, 60),
                self.item("日常活动", 90, 95, 100),
                *items,
            ],
        }

    def test_does_not_pair_sanjie_with_other_column_join_button(self):
        scene = self.activity_scene(
            self.item("参加", 500, 315, 70),
            self.item("三界奇缘", 650, 260, 100),
        )

        decision = classify_scene(scene)

        self.assertEqual(decision["status"], "scroll_daily")
        self.assertEqual(decision["reason"], "target_action_not_visible")

    def test_clicks_join_only_when_it_is_in_the_sanjie_card(self):
        scene = self.activity_scene(
            self.item("三界奇缘", 650, 260, 100),
            self.item("参加", 805, 315, 70),
        )

        decision = classify_scene(scene)

        self.assertEqual(decision["status"], "click_join")
        self.assertEqual(decision["click"], [840, 327])

    def test_confirmed_daily_tab_recovers_when_stylized_title_is_missed(self):
        scene = {
            "image_size": self.image_size,
            "battle": False,
            "battle_box": None,
            "activity_box": None,
            "texts": [
                self.item("日常活动", 120, 174, 106),
                self.item("三界奇缘", 650, 560, 100),
                self.item("参加", 805, 615, 70),
            ],
        }

        self.assertEqual(classify_scene(scene)["status"], "click_daily")
        self.assertEqual(
            classify_scene(scene, daily_tab_confirmed=True),
            {"status": "click_join", "click": [840, 627]},
        )

    def test_rejects_festival_activity_text_as_toolbar_entry(self):
        scene = {
            "image_size": self.image_size,
            "battle": False,
            "battle_box": None,
            "activity_box": None,
            "texts": [self.item("活动", 80, 120, 70)],
        }

        self.assertEqual(classify_scene(scene)["status"], "unknown")

    def test_accepts_activity_text_in_top_toolbar(self):
        scene = {
            "image_size": self.image_size,
            "battle": False,
            "battle_box": None,
            "activity_box": None,
            "texts": [self.item("活动", 245, 45, 70)],
        }

        decision = classify_scene(scene)

        self.assertEqual(decision["status"], "click_activity")
        self.assertEqual(decision["click"], [280, 57])

    @patch("tools.run_sanjieqiyuan_macos.perform_click")
    @patch("tools.run_sanjieqiyuan_macos.read_scene")
    def test_scrolls_after_selecting_daily_tab(self, read_scene, perform_click):
        read_scene.side_effect = [
            {
                "image_size": self.image_size,
                "decision": {"status": "click_daily", "click": [140, 105]},
            },
            {
                "image_size": self.image_size,
                "decision": {
                    "status": "scroll_daily",
                    "reason": "target_action_not_visible",
                },
            },
            {"image_size": self.image_size, "decision": {"status": "answering"}},
        ]
        perform_click.return_value = (140, 105)
        native_input = Mock()
        guard = Mock()
        guard.perform_input.side_effect = lambda function, *args: function(*args)

        status = navigate_to_exam(
            Mock(), Mock(), Mock(), native_input, timeout=30, guard=guard
        )

        self.assertEqual(status, "answering")
        native_input.drag.assert_called_once_with(
            (632, 445), (632, 373), self.image_size
        )

    @patch("tools.run_sanjieqiyuan_macos.perform_click")
    @patch("tools.run_sanjieqiyuan_macos.read_scene")
    def test_direct_scroll_keeps_daily_list_selected(self, read_scene, perform_click):
        read_scene.side_effect = [
            {
                "image_size": self.image_size,
                "decision": {
                    "status": "scroll_daily",
                    "reason": "target_action_not_visible",
                },
            },
            {
                "image_size": self.image_size,
                "decision": {"status": "click_daily", "click": [140, 105]},
            },
            {"image_size": self.image_size, "decision": {"status": "answering"}},
        ]
        native_input = Mock()
        guard = Mock()
        guard.perform_input.side_effect = lambda function, *args: function(*args)

        status = navigate_to_exam(
            Mock(), Mock(), Mock(), native_input, timeout=30, guard=guard
        )

        self.assertEqual(status, "answering")
        self.assertEqual(native_input.drag.call_count, 2)
        perform_click.assert_not_called()

    @patch("tools.run_sanjieqiyuan_macos.perform_click")
    @patch("tools.run_sanjieqiyuan_macos.read_scene")
    def test_stops_repeating_navigation_cycle(self, read_scene, perform_click):
        activity = {
            "image_size": self.image_size,
            "decision": {"status": "click_activity", "click": [280, 58]},
        }
        wrong_join = {
            "image_size": self.image_size,
            "decision": {"status": "click_join", "click": [534, 374]},
        }
        read_scene.side_effect = [
            activity, wrong_join, activity, wrong_join, activity
        ]
        perform_click.return_value = (0, 0)
        guard = Mock()

        with self.assertRaisesRegex(RuntimeError, "重复导航动作.*死循环"):
            navigate_to_exam(
                Mock(), Mock(), Mock(), Mock(), timeout=30, guard=guard
            )

        self.assertEqual(perform_click.call_count, 4)


class SanjieCompletionTests(unittest.TestCase):
    @patch("tools.run_sanjieqiyuan_macos.read_scene")
    def test_completion_requires_explicit_completion_scene(self, read_scene):
        read_scene.side_effect = [
            {"decision": {"status": "unknown"}},
            {"decision": {"status": "completed"}},
        ]
        guard = Mock()

        result = verify_answering_completion(Mock(), Mock(), guard, attempts=3)

        self.assertEqual(result, "completed")
        guard.wait.assert_called_once_with(0.5)

    @patch("tools.run_sanjieqiyuan_macos.read_scene")
    def test_missing_completion_evidence_returns_none(self, read_scene):
        read_scene.return_value = {"decision": {"status": "unknown"}}
        guard = Mock()

        result = verify_answering_completion(Mock(), Mock(), guard, attempts=2)

        self.assertIsNone(result)
        guard.wait.assert_called_once_with(0.5)


class SanjieResultContractTests(unittest.TestCase):
    @staticmethod
    def args(*, inspect_scene=False, recognize_only=False):
        return SimpleNamespace(
            inspect_scene=inspect_scene,
            recognize_only=recognize_only,
            input_method="global",
        )

    @staticmethod
    def protocol_results(output: str) -> list[dict]:
        prefix = "result_json="
        return [
            json.loads(line[len(prefix):])
            for line in output.splitlines()
            if line.startswith(prefix)
        ]

    @patch("tools.run_sanjieqiyuan_macos.CancellationGuard")
    @patch("tools.run_sanjieqiyuan_macos.parse_args")
    @patch("tools.run_sanjieqiyuan_macos.run", side_effect=RuntimeError("截图失败"))
    def test_inspection_error_never_emits_formal_result(self, run, parse_args, guard):
        parse_args.return_value = self.args(inspect_scene=True)
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(self.protocol_results(output.getvalue()), [])

    @patch("tools.run_sanjieqiyuan_macos.CancellationGuard")
    @patch("tools.run_sanjieqiyuan_macos.parse_args")
    @patch("tools.run_sanjieqiyuan_macos.run", side_effect=CancellationRequested())
    def test_read_only_cancellation_never_emits_formal_result(self, run, parse_args, guard):
        parse_args.return_value = self.args(recognize_only=True)
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(self.protocol_results(output.getvalue()), [])
        guard.return_value.wait_for_tasker_stop.assert_called_once_with()

    @patch("tools.run_sanjieqiyuan_macos.CancellationGuard")
    @patch("tools.run_sanjieqiyuan_macos.parse_args")
    @patch("tools.run_sanjieqiyuan_macos.run", side_effect=RuntimeError(""))
    def test_formal_empty_error_has_non_empty_safe_stop_reason(self, run, parse_args, guard):
        parse_args.return_value = self.args()
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main()

        self.assertEqual(exit_code, 1)
        results = self.protocol_results(output.getvalue())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "safe_stopped")
        self.assertEqual(results[0]["reason"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
