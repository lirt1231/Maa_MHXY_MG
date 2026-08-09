import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
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
    recognize_answer,
    stable_answer_click,
    system_window_capture,
    system_window_capture_frame,
    verify_answering_completion,
)
from custom.recognition.reco_sjqy import (
    parse_answer_candidates,
    remaining_answer_candidates,
    sjqy_rois,
    stable_question_key,
)
from custom.recognition.searchAnswer import (
    get_question_answer_groups,
    get_question_bank,
    search_answer,
)
from tools.sanjieqiyuan_recognition import find_unique_answer_match


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
        self.assertEqual(capture.last_signature.source, "maa")
        self.assertEqual(capture.last_signature.image_size, (3, 2))

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
        self.assertEqual(capture.last_signature.source, "system")
        self.assertEqual(capture.last_signature.source_size, (5, 4))

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

    @patch("tools.macos_window_adapter.subprocess.run")
    def test_system_capture_writes_to_a_new_path(self, run_command):
        def create_capture(command, **_kwargs):
            output = Path(command[-1])
            self.assertFalse(output.exists())
            from PIL import Image

            Image.new("RGB", (1700, 1400), "white").save(output)
            return subprocess.CompletedProcess(command, 0)

        run_command.side_effect = create_capture

        image = system_window_capture(42)

        self.assertEqual(image.shape, (718, 1020, 3))
        self.assertEqual(run_command.call_args.args[0][2:4], ["-l", "42"])

    @patch("tools.macos_window_adapter.subprocess.run")
    def test_system_capture_retains_raw_geometry_signature(self, run_command):
        def create_capture(command, **_kwargs):
            from PIL import Image

            Image.new("RGB", (1700, 1400), "white").save(Path(command[-1]))
            return subprocess.CompletedProcess(command, 0)

        run_command.side_effect = create_capture

        frame = system_window_capture_frame(42)

        self.assertEqual(frame.image.shape, (718, 1020, 3))
        self.assertEqual(frame.signature.source_size, (1700, 1400))
        self.assertEqual(frame.signature.content_rect, (0, 48, 1700, 1352))
        self.assertEqual(frame.signature.pixel_scale, 2.0)

    @patch("tools.macos_window_adapter.subprocess.run")
    def test_system_capture_uses_alpha_for_dark_window_bounds(self, run_command):
        def create_capture(command, **_kwargs):
            from PIL import Image

            image = Image.new("RGBA", (1200, 800), (0, 0, 0, 0))
            opaque_black = Image.new("RGBA", (1000, 700), (0, 0, 0, 255))
            image.paste(opaque_black, (100, 50))
            image.save(Path(command[-1]))
            return subprocess.CompletedProcess(command, 0)

        run_command.side_effect = create_capture

        frame = system_window_capture_frame(42)

        self.assertEqual(frame.signature.content_rect, (100, 74, 1000, 676))


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
    def test_portrait_selection_questions_click_the_recognized_label(self):
        self.assertEqual(
            stable_answer_click(
                (451, 372),
                (1020, 718),
                "找出3只瑶池珍兽",
            ),
            (451, 372),
        )

    def test_regular_answers_keep_the_existing_card_body_click(self):
        self.assertEqual(
            stable_answer_click(
                (451, 372),
                (1020, 718),
                "以下谁是玉皇大帝的外甥？",
            ),
            (451, 214),
        )

    def test_splits_combined_multi_select_answers(self):
        self.assertEqual(
            parse_answer_candidates('["吸血鬼，孤魂野鬼，千年僵尸"]'),
            ["吸血鬼", "孤魂野鬼", "千年僵尸"],
        )

    def test_excludes_already_selected_multi_select_answers(self):
        candidates = ["吸血鬼", "孤魂野鬼", "千年僵尸"]

        self.assertEqual(
            remaining_answer_candidates(candidates, {"吸血鬼", "孤魂野鬼"}),
            ["千年僵尸"],
        )

    def test_multi_select_counter_does_not_change_question_identity(self):
        first = stable_question_key("第9题：找出3只地府恶鬼（1/3）")
        second = stable_question_key("第9题：找出3只地府恶鬼（2/3）")

        self.assertEqual(first, second)
        self.assertEqual(first, "第9题：找出3只地府恶鬼")

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

    def test_jinghe_dragon_king_bet_uses_yuan_shoucheng(self):
        answer, confidence, _ = search_answer(
            get_question_bank(force_reload=True),
            "泾河龙王打赌输给了谁？",
        )

        self.assertEqual(answer, '["袁守诚"]')
        self.assertEqual(confidence, 100)

    def test_duplicate_yaochi_questions_expose_all_dynamic_option_answers(self):
        answer, confidence, _ = search_answer(
            get_question_bank(force_reload=True),
            "找出3只瑶池珍兽",
        )

        self.assertEqual(
            parse_answer_candidates(answer),
            [
                "凤凰仙子",
                "蛟龙",
                "啸天虎",
                "芙蓉仙子",
                "雾中仙",
                "猴小仙",
                "蟠桃童子",
                "天兵",
            ],
        )
        self.assertEqual(confidence, 100)

    def test_duplicate_yaochi_questions_preserve_each_answer_group(self):
        self.assertEqual(
            get_question_answer_groups(force_reload=True)["找出3只瑶池珍兽"],
            [
                ("凤凰仙子", "蛟龙", "啸天虎"),
                ("芙蓉仙子", "雾中仙", "猴小仙"),
                ("蟠桃童子", "天兵", "凤凰仙子"),
            ],
        )


class SanjieAnswerRecognitionTests(unittest.TestCase):
    @staticmethod
    def ocr_result(text, box):
        return SimpleNamespace(text=text, box=box)

    @patch(
        "custom.recognition.searchAnswer.SearchQuestions",
        return_value=(' ["吸血鬼，孤魂野鬼，千年僵尸"] ', 100, "精确匹配"),
    )
    @patch("tools.run_sanjieqiyuan_macos.recognition_detail")
    def test_multi_select_skips_answer_already_clicked(self, recognition_detail, _search):
        question_text = "第9题：找出3只地府恶鬼（2/3）"
        question_key = "第9题"
        question_result = self.ocr_result(question_text, (330, 130, 300, 40))
        question_detail = SimpleNamespace(
            hit=True,
            filtered_results=[question_result],
        )
        answer_result = self.ocr_result("孤魂野鬼", (430, 270, 80, 25))
        answer_detail = SimpleNamespace(
            hit=True,
            box=answer_result.box,
            best_result=answer_result,
        )

        def recognize(_tasker, _type, params, _image, _guard):
            if params.roi[1] < 200:
                return question_detail
            self.assertEqual(params.expected, ["孤魂野鬼", "千年僵尸"])
            self.assertEqual(params.threshold, 0.7)
            self.assertEqual(
                params.replace,
                [
                    ["屍", "尸"],
                    ["殭", "僵"],
                    ["戶", "户"],
                    ["僵户", "僵尸"],
                ],
            )
            return answer_detail

        recognition_detail.side_effect = recognize
        result = recognize_answer(
            Mock(),
            np.ones((718, 1020, 3), dtype=np.uint8),
            Mock(),
            question_key,
            {"吸血鬼"},
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["answer"], "孤魂野鬼")
        self.assertEqual(result["question_key"], "第9题")
        self.assertEqual(result["question_number"], 9)

    def test_matches_traditional_and_misrecognized_corpse_variants(self):
        candidates = ["千年僵尸", "孤魂野鬼", "吸血鬼"]
        roi = (365, 215, 530, 210)

        traditional = find_unique_answer_match(
            [self.ocr_result("千年僵屍", (416, 352, 78, 25))],
            candidates,
            roi,
        )
        misrecognized = find_unique_answer_match(
            [self.ocr_result("千年僵戶", (417, 354, 73, 21))],
            candidates,
            roi,
        )

        self.assertEqual(traditional.candidate, "千年僵尸")
        self.assertEqual(traditional.edit_distance, 0)
        self.assertEqual(misrecognized.candidate, "千年僵尸")
        self.assertEqual(misrecognized.edit_distance, 1)

    def test_recovers_unique_four_character_answer_truncated_by_ocr(self):
        match = find_unique_answer_match(
            [self.ocr_result("万剑归", (753, 359, 68, 24))],
            ["万剑归一"],
            (365, 215, 530, 210),
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.candidate, "万剑归一")
        self.assertEqual(match.recognized_text, "万剑归")
        self.assertEqual(match.edit_distance, 1)

    def test_rejects_unrelated_and_ambiguous_ocr_results(self):
        roi = (365, 215, 530, 210)
        unrelated = [
            self.ocr_result("鲛将军", (589, 352, 58, 22)),
            self.ocr_result("蟹将", (757, 352, 42, 23)),
        ]
        ambiguous = [self.ocr_result("甲乙丙丁", (417, 354, 73, 21))]

        self.assertIsNone(
            find_unique_answer_match(
                unrelated,
                ["千年僵尸", "孤魂野鬼", "吸血鬼"],
                roi,
            )
        )
        self.assertIsNone(
            find_unique_answer_match(
                ambiguous,
                ["甲乙丙戊", "甲乙丙己"],
                roi,
            )
        )
        self.assertIsNone(
            find_unique_answer_match(
                [self.ocr_result("甲乙丙", (417, 354, 73, 21))],
                ["甲乙丙丁", "甲乙丙戊"],
                roi,
            )
        )

    @patch(
        "custom.recognition.searchAnswer.SearchQuestions",
        return_value=(' ["千年僵尸，孤魂野鬼，吸血鬼"] ', 100, "精确匹配"),
    )
    @patch("tools.run_sanjieqiyuan_macos.recognition_detail")
    def test_uses_unique_raw_ocr_fallback_box(self, recognition_detail, _search):
        question_detail = SimpleNamespace(
            hit=True,
            filtered_results=[
                self.ocr_result(
                    "第3题：找出3个生活在地府的角色（1/3)",
                    (380, 162, 306, 21),
                )
            ],
        )
        raw_answer = self.ocr_result("千年僵戶", (417, 354, 73, 21))
        answer_detail = SimpleNamespace(
            hit=False,
            all_results=[
                raw_answer,
                self.ocr_result("鲛将军", (589, 352, 58, 22)),
                self.ocr_result("蟹将", (757, 352, 42, 23)),
            ],
        )
        recognition_detail.side_effect = [question_detail, answer_detail, answer_detail]

        result = recognize_answer(
            Mock(),
            np.ones((718, 1020, 3), dtype=np.uint8),
            Mock(),
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["answer"], "千年僵尸")
        self.assertEqual(result["click"], (453, 364))

    @patch(
        "custom.recognition.searchAnswer.SearchQuestions",
        return_value=(
            '["凤凰仙子","蛟龙","啸天虎","芙蓉仙子","雾中仙","猴小仙","蟠桃童子","天兵"]',
            100,
            "精确匹配",
        ),
    )
    @patch("tools.run_sanjieqiyuan_macos.recognition_detail")
    def test_falls_back_to_raw_yaochi_option_after_unusable_best_hit(
        self, recognition_detail, _search
    ):
        question_detail = SimpleNamespace(
            hit=True,
            filtered_results=[
                self.ocr_result("第4题：找出3只瑶池珍兽（1/3）", (380, 162, 306, 21))
            ],
        )
        exact_answer = self.ocr_result("蛟龙", (417, 354, 73, 21))
        answer_detail = SimpleNamespace(
            hit=True,
            best_result=self.ocr_result("答案区域", (417, 354, 73, 21)),
            all_results=[
                self.ocr_result("答案区域", (417, 354, 73, 21)),
                exact_answer,
            ],
        )
        recognition_detail.side_effect = [question_detail, answer_detail, answer_detail]

        result = recognize_answer(
            Mock(), np.ones((718, 1020, 3), dtype=np.uint8), Mock()
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["answer"], "蛟龙")
        self.assertEqual(result["click"], (453, 364))

    @patch(
        "custom.recognition.searchAnswer.SearchQuestions",
        return_value=(
            '["凤凰仙子","蛟龙","啸天虎","芙蓉仙子","雾中仙","猴小仙","蟠桃童子","天兵"]',
            100,
            "精确匹配",
        ),
    )
    @patch("tools.run_sanjieqiyuan_macos.recognition_detail")
    def test_yaochi_uses_the_only_complete_visible_answer_group(
        self, recognition_detail, _search
    ):
        question_detail = SimpleNamespace(
            hit=True,
            filtered_results=[
                self.ocr_result("第4题：找出3只瑶池珍兽（1/3）", (380, 162, 306, 21))
            ],
        )
        visible = [
            self.ocr_result("芙蓉仙子", (417, 254, 80, 25)),
            self.ocr_result("雾中仙", (589, 254, 58, 25)),
            self.ocr_result("猴小仙", (757, 254, 58, 25)),
            self.ocr_result("天兵", (417, 354, 42, 25)),
        ]
        visible_detail = SimpleNamespace(hit=True, all_results=visible)
        selected = self.ocr_result("雾中仙", (589, 254, 58, 25))
        answer_detail = SimpleNamespace(hit=True, best_result=selected)
        recognition_detail.side_effect = [
            question_detail,
            visible_detail,
            answer_detail,
        ]

        result = recognize_answer(
            Mock(), np.ones((718, 1020, 3), dtype=np.uint8), Mock()
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["candidates"], ["芙蓉仙子", "雾中仙", "猴小仙"])
        self.assertEqual(result["answer"], "雾中仙")

    @patch(
        "custom.recognition.searchAnswer.SearchQuestions",
        return_value=(' ["千年僵尸，孤魂野鬼，吸血鬼"] ', 100, "精确匹配"),
    )
    @patch("tools.run_sanjieqiyuan_macos.recognition_detail")
    def test_exact_ocr_result_takes_priority(self, recognition_detail, _search):
        question_detail = SimpleNamespace(
            hit=True,
            filtered_results=[
                self.ocr_result(
                    "第3题：找出3个生活在地府的角色（1/3)",
                    (380, 162, 306, 21),
                )
            ],
        )
        exact_answer = self.ocr_result("孤魂野鬼", (417, 354, 73, 21))
        answer_detail = SimpleNamespace(
            hit=True,
            box=exact_answer.box,
            best_result=exact_answer,
            all_results=[self.ocr_result("千年僵戶", (589, 352, 58, 22))],
        )
        recognition_detail.side_effect = [question_detail, answer_detail]

        result = recognize_answer(
            Mock(),
            np.ones((718, 1020, 3), dtype=np.uint8),
            Mock(),
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["answer"], "孤魂野鬼")
        self.assertEqual(result["click"], (453, 364))

    @patch(
        "custom.recognition.searchAnswer.SearchQuestions",
        return_value=(' ["吸血鬼，孤魂野鬼，千年僵尸"] ', 100, "精确匹配"),
    )
    @patch("tools.run_sanjieqiyuan_macos.recognition_detail")
    def test_multi_select_waits_after_all_answers_clicked(self, recognition_detail, _search):
        question_text = "第9题：找出3只地府恶鬼（3/3）"
        question_key = "第9题"
        recognition_detail.return_value = SimpleNamespace(
            hit=True,
            filtered_results=[
                self.ocr_result(question_text, (330, 130, 300, 40))
            ],
        )

        result = recognize_answer(
            Mock(),
            np.ones((718, 1020, 3), dtype=np.uint8),
            Mock(),
            question_key,
            {"吸血鬼", "孤魂野鬼", "千年僵尸"},
        )

        self.assertEqual(result["status"], "awaiting_transition")
        recognition_detail.assert_called_once()

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

    def test_live_card_opening_time_stops_without_scrolling(self):
        scene = self.activity_scene(
            self.item("三界奇缘", 348, 432, 79, 23),
            self.item("11：00开启", 480, 429, 83, 21),
            self.item("12：10开启", 809, 431, 81, 18),
        )

        self.assertEqual(
            classify_scene(scene, daily_tab_confirmed=True),
            {"status": "unavailable", "reason": "11：00开启"},
        )

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
            (632, 431), (632, 352), self.image_size
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
