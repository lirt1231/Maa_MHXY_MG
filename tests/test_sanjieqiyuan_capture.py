import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from tools.run_sanjieqiyuan_macos import (
    CancellationRequested,
    WindowCapture,
    advance_question_count,
    main,
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
