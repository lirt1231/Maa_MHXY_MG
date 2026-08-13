import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from tools.run_keju_macos import (
    CancellationRequested,
    bind_keju_window,
    classify_keju_inspection,
    detect_exam_type,
    main,
    parse_args,
    parse_question_round,
    model_profile_for_exam,
    recognize_keju_frame,
    should_end_loop,
    stable_question_key,
    task_result,
)


class KejuArgumentTests(unittest.TestCase):
    def test_exam_profile_overrides_only_the_selected_exam_fields(self):
        profile = model_profile_for_exam({
            "url": "https://example.test/v1/chat/completions",
            "model": "shared", "timeout": 6,
            "exam_profiles": {
                "xiangshi": {"model": "gemini-3.5-flash", "timeout": 12},
                "huishi": {"model": "gemini-3-flash-preview-nothinking"},
            },
        }, "xiangshi")

        self.assertEqual(profile["model"], "gemini-3.5-flash")
        self.assertEqual(profile["timeout"], 12)
        self.assertEqual(profile["url"], "https://example.test/v1/chat/completions")
        self.assertNotIn("exam_profiles", profile)
    def test_xiangshi_defaults_to_ten_answers(self):
        with patch(
            "tools.run_keju_macos.sys.argv",
            ["run_keju_macos.py", "--exam-type", "xiangshi"],
        ):
            self.assertEqual(parse_args().max_steps, 10)

    def test_xiangshi_rejects_more_than_ten_answers(self):
        with patch(
            "tools.run_keju_macos.sys.argv",
            [
                "run_keju_macos.py",
                "--exam-type",
                "xiangshi",
                "--max-steps",
                "11",
            ],
        ), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_args()

    def test_huishi_keeps_its_twenty_answer_default(self):
        with patch(
            "tools.run_keju_macos.sys.argv",
            ["run_keju_macos.py", "--exam-type", "huishi"],
        ):
            self.assertEqual(parse_args().max_steps, 20)

    def test_wait_for_entry_is_opt_in_and_parseable(self):
        with patch(
            "tools.run_keju_macos.sys.argv",
            ["run_keju_macos.py", "--wait-for-entry"],
        ):
            self.assertTrue(parse_args().wait_for_entry)


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

    def test_waiting_status_survives_empty_custom_recognition_result(self):
        self.task.succeeded = False

        def waiting_recognition(*_args):
            self.recognizer.last_status = "waiting"
            return self.task

        self.tasker.post_recognition.side_effect = waiting_recognition

        self.assertIsNone(
            recognize_keju_frame(
                self.tasker,
                self.capture,
                self.recognizer,
                self.guard,
            )
        )
        self.assertEqual(self.recognizer.last_status, "waiting")

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
    def test_navigation_reuses_bound_window_without_second_toolkit_lookup(self):
        toolkit = Mock()
        controller_factory = Mock()
        capture_factory = Mock()
        input_factory = Mock()
        runtime = SimpleNamespace(
            window_id=157261,
            controller=object(),
            capture=object(),
            native_input=object(),
        )

        components = bind_keju_window(
            Mock(),
            navigation_runtime=runtime,
            toolkit=toolkit,
            controller_factory=controller_factory,
            capture_factory=capture_factory,
            input_factory=input_factory,
            screencap_method=object(),
            input_method=object(),
        )

        self.assertEqual(
            components,
            (runtime.window_id, runtime.controller, runtime.capture, runtime.native_input),
        )
        toolkit.init_option.assert_not_called()
        toolkit.find_desktop_windows.assert_not_called()
        controller_factory.assert_not_called()
        capture_factory.assert_not_called()
        input_factory.assert_not_called()

    def test_direct_answering_falls_back_to_native_window_lookup(self):
        toolkit = Mock()
        toolkit.find_desktop_windows.return_value = []
        native_input = Mock()
        native_input.find_window_id.return_value = 157261
        input_factory = Mock(return_value=native_input)
        connection = Mock(succeeded=True)
        controller = Mock()
        controller.post_connection.return_value.wait.return_value = connection
        controller_factory = Mock(return_value=controller)
        capture = object()
        capture_factory = Mock(return_value=capture)
        guard = Mock()

        components = bind_keju_window(
            guard,
            navigation_runtime=None,
            toolkit=toolkit,
            controller_factory=controller_factory,
            capture_factory=capture_factory,
            input_factory=input_factory,
            screencap_method="screen_capture_kit",
            input_method="global_event",
        )

        self.assertEqual(components, (157261, controller, capture, native_input))
        native_input.find_window_id.assert_called_once_with()
        native_input.bind_window.assert_called_once_with(157261)
        controller_factory.assert_called_once_with(
            157261, "screen_capture_kit", "global_event"
        )
        guard.checkpoint.assert_called_once_with()

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
            classify_keju_inspection(
                [{"text": "科举乡试 第1题 课堂复习 以下哪一年是国际天文年"}]
            )["status"],
            "formal_question_ready",
        )
        self.assertEqual(
            classify_keju_inspection([{"text": "长安城 世界地图"}])["status"],
            "unknown",
        )

    def test_exam_type_uses_only_the_title_band(self):
        image_size = (1000, 720)
        texts = [
            {"text": "科举乡试", "box": [430, 120, 120, 30]},
            {"text": "第3/20题 科举会试在哪里参加", "box": [330, 250, 360, 40]},
        ]

        self.assertEqual(detect_exam_type(texts, image_size), "xiangshi")
        decision = classify_keju_inspection(texts, image_size)
        self.assertEqual(decision, {
            "status": "formal_question_ready",
            "examType": "xiangshi",
        })

    def test_exam_type_rejects_missing_or_ambiguous_title(self):
        image_size = (1000, 720)
        question_only = [
            {"text": "第3/15题 科举会试在哪里参加", "box": [330, 250, 360, 40]}
        ]
        both_titles = [
            {"text": "科举乡试 科举会试", "box": [400, 120, 220, 30]},
        ]

        self.assertIsNone(detect_exam_type(question_only, image_size))
        self.assertIsNone(detect_exam_type(both_titles, image_size))
        self.assertIsNone(
            classify_keju_inspection(question_only, image_size)["examType"]
        )

    def test_waiting_page_is_not_parsed_as_first_question(self):
        self.assertIsNone(parse_question_round("课堂复习 剩余时间 12"))
        self.assertEqual(parse_question_round("第 1 / 15 题剩余时间12题目"), (1, 15))
        self.assertEqual(
            parse_question_round("科举乡试 第 2 / 20 题剩余时间12题目"),
            (2, 20),
        )

    def test_same_round_has_a_stable_key_despite_timer_changes(self):
        first = "第1/15题剩余时间30我国古代信奉的火神是谁"
        later = "第 1 / 15 题 剩余时间 18 我国古代信奉的火神是谁"
        self.assertEqual(stable_question_key(first), "round:1")
        self.assertEqual(stable_question_key(first), stable_question_key(later))

    def test_final_question_ends_even_before_max_answers(self):
        self.assertTrue(should_end_loop(3, 20, (15, 15)))

    def test_fixed_final_question_ends_without_total_in_ocr(self):
        self.assertTrue(
            should_end_loop(
                4,
                10,
                question_number=10,
                final_question_number=10,
            )
        )

    def test_fixed_final_question_does_not_end_early_or_for_huishi(self):
        self.assertFalse(
            should_end_loop(
                4,
                10,
                question_number=9,
                final_question_number=10,
            )
        )
        self.assertFalse(
            should_end_loop(
                10,
                20,
                question_number=10,
                final_question_number=20,
            )
        )

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

    def test_huishi_uses_an_independent_task_id(self):
        result = task_result(
            "completed", exam_type="huishi", answers=15, clicks=15, limit=15
        )

        self.assertEqual(result["task_id"], "keju_huishi")


if __name__ == "__main__":
    unittest.main()
