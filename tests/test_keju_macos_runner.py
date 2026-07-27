import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from tools.run_keju_macos import (
    CancellationRequested,
    classify_keju_inspection,
    main,
    parse_question_round,
    recognize_keju_frame,
    should_end_loop,
    stable_question_key,
    task_result,
)


class KejuExplicitFrameTests(unittest.TestCase):
    def setUp(self):
        self.image = np.ones((718, 1020, 3), dtype=np.uint8)
        self.capture = Mock()
        self.capture.capture.return_value = self.image
        self.recognizer = SimpleNamespace(last_status="idle")
        self.guard = Mock()
        self.task = Mock(succeeded=True)
        self.task.wait.return_value = self.task
        self.recognition = object()
        self.task.get.return_value = SimpleNamespace(
            nodes=[SimpleNamespace(recognition=self.recognition)]
        )
        self.tasker = Mock()

        def post_recognition(*_args):
            self.recognizer.last_status = "ready"
            return self.task

        self.tasker.post_recognition.side_effect = post_recognition

    def test_passes_bound_window_capture_to_custom_recognition(self):
        result = recognize_keju_frame(
            self.tasker,
            self.capture,
            self.recognizer,
            self.guard,
        )

        self.assertIs(result, self.recognition)
        self.capture.capture.assert_called_once_with(self.guard)
        recognition_type, params, image = self.tasker.post_recognition.call_args.args
        self.assertEqual(recognition_type, "Custom")
        self.assertEqual(params.custom_recognition, "KejuHybridAnswer")
        self.assertEqual(params.roi, (0, 0, 1020, 718))
        self.assertIs(image, self.image)

    def test_capture_failure_sets_terminal_status(self):
        self.capture.capture.side_effect = RuntimeError("no frame")

        with self.assertRaisesRegex(RuntimeError, "科举窗口截图失败"):
            recognize_keju_frame(
                self.tasker,
                self.capture,
                self.recognizer,
                self.guard,
            )

        self.assertEqual(self.recognizer.last_status, "capture_failed")
        self.tasker.post_recognition.assert_not_called()

    def test_recognition_failure_sets_terminal_status(self):
        self.tasker.post_recognition.side_effect = RuntimeError("recognizer crashed")

        with self.assertRaisesRegex(RuntimeError, "科举识别失败"):
            recognize_keju_frame(
                self.tasker,
                self.capture,
                self.recognizer,
                self.guard,
            )

        self.assertEqual(self.recognizer.last_status, "recognition_failed")

    def test_running_status_cannot_escape_as_success(self):
        def incomplete_recognition(*_args):
            self.recognizer.last_status = "running"
            return self.task

        self.tasker.post_recognition.side_effect = incomplete_recognition

        with self.assertRaisesRegex(RuntimeError, "未返回明确状态"):
            recognize_keju_frame(
                self.tasker,
                self.capture,
                self.recognizer,
                self.guard,
            )

        self.assertEqual(self.recognizer.last_status, "recognition_failed")


class KejuMacOSRunnerStateTests(unittest.TestCase):
    def test_single_scene_inspection_classifies_without_answering(self):
        self.assertEqual(
            classify_keju_inspection([{"text": "第 3 / 15 题 剩余时间 20"}])["status"],
            "formal_question_ready",
        )
        self.assertEqual(
            classify_keju_inspection([{"text": "课堂复习 即将开始"}])["status"],
            "waiting_for_question",
        )
        self.assertEqual(
            classify_keju_inspection([{"text": "长安城 世界地图"}])["status"],
            "unknown",
        )

    def test_waiting_page_is_not_parsed_as_first_question(self):
        self.assertIsNone(parse_question_round("课堂复习 剩余时间 12"))
        self.assertEqual(parse_question_round("第 1 / 15 题剩余时间12题目"), (1, 15))

    def test_same_round_has_a_stable_key_despite_timer_changes(self):
        first = "第1/15题剩余时间30我国古代信奉的火神是谁"
        later = "第 1 / 15 题 剩余时间 18 我国古代信奉的火神是谁"
        self.assertEqual(stable_question_key(first), "round:1")
        self.assertEqual(stable_question_key(first), stable_question_key(later))

    def test_final_question_ends_even_before_max_answers(self):
        self.assertTrue(should_end_loop(3, 20, (15, 15)))

    def test_max_steps_counts_answers_instead_of_polling_attempts(self):
        for _poll_attempt in range(10):
            self.assertFalse(should_end_loop(answered=0, max_steps=1))
        self.assertTrue(should_end_loop(answered=1, max_steps=1))

    def test_non_final_question_does_not_end_below_max_answers(self):
        self.assertFalse(should_end_loop(4, 20, (5, 15)))


class KejuResultContractTests(unittest.TestCase):
    @staticmethod
    def args(*, inspect_scene=False, recognize_only=False):
        return SimpleNamespace(
            inspect_scene=inspect_scene,
            recognize_only=recognize_only,
            max_steps=20,
        )

    @staticmethod
    def protocol_results(output: str) -> list[dict]:
        prefix = "result_json="
        return [
            json.loads(line[len(prefix):])
            for line in output.splitlines()
            if line.startswith(prefix)
        ]

    @patch("tools.run_keju_macos.CancellationGuard")
    @patch("tools.run_keju_macos.parse_args")
    @patch("tools.run_keju_macos.run")
    def test_completed_run_emits_exactly_one_result(self, run, parse_args, guard):
        parse_args.return_value = self.args()
        run.return_value = task_result(
            "completed", answers=15, clicks=15, limit=20
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            self.protocol_results(output.getvalue()),
            [{
                "task_id": "keju",
                "status": "completed",
                "answers": 15,
                "clicks": 15,
                "limit": 20,
            }],
        )
        guard.return_value.install_signal_handlers.assert_called_once_with()

    @patch("tools.run_keju_macos.CancellationGuard")
    @patch("tools.run_keju_macos.parse_args")
    @patch("tools.run_keju_macos.run", side_effect=RuntimeError("选项识别不足"))
    def test_exception_emits_one_safe_stop(self, run, parse_args, guard):
        parse_args.return_value = self.args()
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            self.protocol_results(output.getvalue()),
            [{
                "task_id": "keju",
                "status": "safe_stopped",
                "reason": "选项识别不足",
                "answers": 0,
                "clicks": 0,
                "limit": 20,
            }],
        )

    @patch("tools.run_keju_macos.CancellationGuard")
    @patch("tools.run_keju_macos.parse_args")
    @patch("tools.run_keju_macos.run", side_effect=CancellationRequested())
    def test_cancellation_emits_one_cancelled_result(self, run, parse_args, guard):
        parse_args.return_value = self.args()
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            self.protocol_results(output.getvalue()),
            [{
                "task_id": "keju",
                "status": "cancelled",
                "reason": "收到停止请求",
                "answers": 0,
                "clicks": 0,
                "limit": 20,
            }],
        )
        guard.return_value.wait_for_tasker_stop.assert_called_once_with()

    @patch("tools.run_keju_macos.CancellationGuard")
    @patch("tools.run_keju_macos.parse_args")
    @patch("tools.run_keju_macos.run")
    def test_inspection_never_emits_result_protocol(self, run, parse_args, guard):
        parse_args.return_value = self.args(inspect_scene=True)

        def preflight(*_args):
            print('preflight_json={"decision":{"status":"unknown"}}')
            return None

        run.side_effect = preflight
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(self.protocol_results(output.getvalue()), [])
        self.assertIn("preflight_json=", output.getvalue())

    @patch("tools.run_keju_macos.CancellationGuard")
    @patch("tools.run_keju_macos.parse_args")
    @patch("tools.run_keju_macos.run", side_effect=RuntimeError("截图失败"))
    def test_inspection_error_still_never_emits_result(self, run, parse_args, guard):
        parse_args.return_value = self.args(inspect_scene=True)
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(self.protocol_results(output.getvalue()), [])
        self.assertIn("read_only_error=截图失败", output.getvalue())

    @patch("tools.run_keju_macos.CancellationGuard")
    @patch("tools.run_keju_macos.parse_args")
    @patch("tools.run_keju_macos.run")
    def test_recognize_only_never_emits_result_protocol(self, run, parse_args, guard):
        parse_args.return_value = self.args(recognize_only=True)
        run.return_value = task_result(
            "limit_reached", answers=0, clicks=0, limit=20
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(self.protocol_results(output.getvalue()), [])

    @patch("tools.run_keju_macos.CancellationGuard")
    @patch("tools.run_keju_macos.parse_args")
    @patch("tools.run_keju_macos.run", side_effect=RuntimeError(""))
    def test_read_only_empty_error_never_emits_result(self, run, parse_args, guard):
        parse_args.return_value = self.args(recognize_only=True)
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(self.protocol_results(output.getvalue()), [])

    @patch("tools.run_keju_macos.CancellationGuard")
    @patch("tools.run_keju_macos.parse_args")
    @patch("tools.run_keju_macos.run", return_value=None)
    def test_formal_run_without_outcome_emits_one_safe_stop(self, run, parse_args, guard):
        parse_args.return_value = self.args()
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main()

        self.assertEqual(exit_code, 1)
        results = self.protocol_results(output.getvalue())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "safe_stopped")
        self.assertEqual(results[0]["answers"], 0)
        self.assertEqual(results[0]["clicks"], 0)
        self.assertEqual(results[0]["limit"], 20)

    def test_all_declared_success_results_use_non_sensitive_counts(self):
        for status in ("completed", "already_completed", "limit_reached"):
            with self.subTest(status=status):
                result = task_result(
                    status, answers=3, clicks=3, limit=20
                )
                self.assertEqual(result["task_id"], "keju")
                self.assertEqual(result["status"], status)
                self.assertEqual(set(result) - {"task_id", "status"}, {
                    "answers", "clicks", "limit"
                })


if __name__ == "__main__":
    unittest.main()
