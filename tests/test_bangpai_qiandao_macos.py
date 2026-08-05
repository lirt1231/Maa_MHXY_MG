import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.bangpai_qiandao_recognition import (
    clan_entry_box,
    classify_clan_sign_in_scene,
    expand_menu_box,
    expanded_menu_visible,
    welfare_close_box,
)
import tools.run_bangpai_qiandao_macos as clan_runner
from tools.run_bangpai_qiandao_macos import run


def text(value, box=(10, 20, 80, 30)):
    return {"text": value, "box": list(box)}


def scene(texts=None, templates=None):
    return SimpleNamespace(
        image_size=(1020, 718),
        texts=texts or [],
        templates=templates or {},
    )


def welfare_texts(*items):
    return [
        text("·帮派·", (445, 85, 103, 34)),
        text("帮派签到", (721, 382, 84, 29)),
        text("帮派分红", (720, 504, 85, 25)),
        *items,
    ]


def protocol_payloads(output, prefix):
    return [
        json.loads(line[len(prefix):])
        for line in output.splitlines()
        if line.startswith(prefix)
    ]


class ClanSignInSceneTests(unittest.TestCase):
    def test_closes_only_a_fully_recognized_leftover_welfare_page(self):
        texts = [
            text("梦幻刮刮乐", (460, 90, 130, 28)),
            text("今日奖品", (605, 160, 127, 39)),
            text("当前累计：3/7次", (450, 590, 141, 22)),
        ]

        decision = classify_clan_sign_in_scene(texts, {}, "enter", (1020, 718))
        incomplete = classify_clan_sign_in_scene(texts[:-1], {}, "enter", (1020, 718))

        self.assertEqual(decision, {
            "status": "close_known_welfare_overlay",
            "box": welfare_close_box((1020, 718)),
        })
        self.assertEqual(incomplete["status"], "unknown")

    def test_welfare_template_is_only_closed_from_entry_phase(self):
        templates = {"welfare_header": (420, 20, 300, 60)}
        self.assertEqual(
            classify_clan_sign_in_scene([], templates, "enter", (1020, 718))["status"],
            "close_known_welfare_overlay",
        )
        self.assertEqual(
            classify_clan_sign_in_scene([], templates, "clan", (1020, 718))["status"],
            "unknown",
        )

    def test_expands_menu_only_from_entry_phase(self):
        decision = classify_clan_sign_in_scene(
            [], {"expand_menu": (940, 630, 50, 50)}, "enter"
        )
        self.assertEqual(decision["status"], "expand_menu")
        self.assertEqual(
            classify_clan_sign_in_scene(
                [], {"expand_menu": (940, 630, 50, 50)}, "verify"
            )["status"],
            "unknown",
        )

    def test_expands_live_main_menu_from_strict_ocr_evidence(self):
        texts = [
            text("任务", (869, 107, 42, 25)),
            text("队伍", (970, 106, 45, 25)),
            text("包裹", (971, 620, 33, 16)),
            text("+", (966, 658, 43, 46)),
            text("福利", (10, 134, 36, 18)),
            text("当前", (8, 675, 37, 18)),
        ]

        self.assertEqual(
            classify_clan_sign_in_scene(texts, {}, "enter", (1020, 718)),
            {"status": "expand_menu", "box": expand_menu_box((1020, 718))},
        )

    def test_current_live_plus_template_requires_three_main_screen_regions(self):
        live_plus = {"expand_menu_live": (870, 653, 50, 62)}
        current_main = [
            text("任务", (787, 116, 38, 22)),
            text("队伍", (881, 117, 37, 19)),
            text("福利", (11, 134, 35, 18)),
            text("活动", (242, 66, 26, 14)),
        ]
        self.assertEqual(
            classify_clan_sign_in_scene(
                current_main, live_plus, "enter", (923, 720)
            ),
            {"status": "expand_menu", "box": expand_menu_box((923, 720))},
        )
        for texts in (current_main[:2], current_main[2:]):
            with self.subTest(texts=texts):
                self.assertEqual(
                    classify_clan_sign_in_scene(
                        texts, live_plus, "enter", (923, 720)
                    )["status"],
                    "unknown",
                )

    def test_fixed_clan_slot_requires_menu_phase_and_main_screen_regions(self):
        current_main = [
            text("任务", (787, 116, 38, 22)),
            text("队伍", (881, 117, 37, 19)),
            text("福利", (11, 134, 35, 18)),
            text("活动", (242, 66, 26, 14)),
        ]
        self.assertEqual(
            classify_clan_sign_in_scene(
                current_main, {}, "menu", (923, 720)
            ),
            {"status": "open_clan", "box": clan_entry_box((923, 720))},
        )
        for texts, phase in (
            (current_main[:2], "menu"),
            (current_main[2:], "menu"),
            (current_main, "enter"),
        ):
            with self.subTest(texts=texts, phase=phase):
                self.assertEqual(
                    classify_clan_sign_in_scene(
                        texts, {}, phase, (923, 720)
                    )["status"],
                    "unknown",
                )

    def test_menu_phase_retries_expand_while_plus_is_still_visible(self):
        current_main = [
            text("任务", (787, 116, 38, 22)),
            text("队伍", (881, 117, 37, 19)),
            text("福利", (11, 134, 35, 18)),
            text("活动", (242, 66, 26, 14)),
        ]
        decision = classify_clan_sign_in_scene(
            current_main,
            {"expand_menu_live": (885, 666, 28, 35)},
            "menu",
            (923, 720),
        )

        self.assertEqual(
            decision,
            {"status": "expand_menu", "box": expand_menu_box((923, 720))},
        )

    def test_ocr_plus_requires_main_screen_anchors_and_entry_phase(self):
        plus = text("+", (966, 658, 43, 46))
        anchors = [
            text("任务", (869, 107, 42, 25)),
            text("队伍", (970, 106, 45, 25)),
        ]
        cases = [
            ([plus], "enter"),
            ([*anchors, text("+", (100, 100, 43, 46))], "enter"),
            ([*anchors, plus], "welfare"),
        ]

        for texts, phase in cases:
            with self.subTest(texts=texts, phase=phase):
                self.assertEqual(
                    classify_clan_sign_in_scene(
                        texts, {}, phase, (1020, 718)
                    )["status"],
                    "unknown",
                )

    def test_opens_recognized_clan_entry(self):
        decision = classify_clan_sign_in_scene(
            [text(" 帮 派 ", (800, 620, 70, 60))], {}, "menu"
        )
        self.assertEqual(
            decision,
            {"status": "open_clan", "box": clan_entry_box(None)},
        )

    def test_opens_fixed_clan_slot_from_strict_expanded_menu_evidence(self):
        texts = [
            text("强化", (538, 687, 40, 20)),
            text("家园", (785, 690, 32, 15)),
            text("技能", (847, 689, 31, 16)),
            text("X", (975, 670, 18, 22)),
        ]

        self.assertTrue(expanded_menu_visible(texts, (1020, 718)))
        self.assertTrue(expanded_menu_visible(
            [*texts[:-1], text("``", (975, 670, 18, 21))],
            (1020, 718),
        ))
        for phase in ("enter", "menu"):
            with self.subTest(phase=phase):
                self.assertEqual(
                    classify_clan_sign_in_scene(texts, {}, phase, (1020, 718)),
                    {"status": "open_clan", "box": clan_entry_box((1020, 718))},
                )

    def test_fixed_clan_slot_requires_close_button_and_multiple_menu_labels(self):
        labels = [
            text("家园", (785, 690, 32, 15)),
            text("技能", (847, 689, 31, 16)),
        ]
        close_button = text("X", (975, 670, 18, 22))

        for texts, phase in (
            (labels, "menu"),
            ([labels[0], close_button], "menu"),
            ([*labels, text("X", (100, 100, 18, 22))], "menu"),
            ([*labels, close_button], "clan"),
        ):
            with self.subTest(texts=texts, phase=phase):
                self.assertNotEqual(
                    classify_clan_sign_in_scene(
                        texts, {}, phase, (1020, 718)
                    )["status"],
                    "open_clan",
                )

    def test_clan_header_and_welfare_template_are_both_required(self):
        self.assertEqual(
            classify_clan_sign_in_scene(
                [], {"clan_welfare": (880, 350, 60, 90)}, "clan"
            )["status"],
            "unknown",
        )
        decision = classify_clan_sign_in_scene(
            [],
            {
                "clan_header": (350, 0, 320, 70),
                "clan_welfare": (880, 350, 60, 90),
            },
            "clan",
        )
        self.assertEqual(decision["status"], "open_welfare")

    def test_opens_live_clan_welfare_from_strict_ocr_navigation(self):
        texts = [
            text("·帮派·", (445, 86, 103, 34)),
            text("信息", (903, 204, 23, 44)),
            text("成员", (901, 307, 26, 45)),
            text("福利", (901, 412, 25, 44)),
            text("活动", (899, 516, 28, 44)),
        ]

        self.assertEqual(
            classify_clan_sign_in_scene(texts, {}, "clan", (1020, 718)),
            {"status": "open_welfare", "box": [901, 412, 25, 44]},
        )

    def test_ocr_welfare_requires_title_and_multiple_navigation_anchors(self):
        title = text("·帮派·", (445, 86, 103, 34))
        welfare = text("福利", (901, 412, 25, 44))
        member = text("成员", (901, 307, 26, 45))
        cases = [
            [welfare, member],
            [title, welfare],
            [title, member, text("福利", (100, 100, 25, 44))],
        ]

        for texts in cases:
            with self.subTest(texts=texts):
                self.assertNotEqual(
                    classify_clan_sign_in_scene(
                        texts, {}, "clan", (1020, 718)
                    )["status"],
                    "open_welfare",
                )

    def test_scaled_welfare_page_uses_strict_ocr_header_context(self):
        texts = [
            text("·帮派·", (445, 85, 103, 34)),
            text("帮派签到", (723, 385, 81, 24)),
            text("签到", (750, 454, 59, 30)),
        ]

        decision = classify_clan_sign_in_scene(
            texts,
            {},
            "enter",
            (1020, 718),
        )

        self.assertEqual(decision, {"status": "sign_in", "box": [750, 454, 59, 30]})

    def test_ocr_header_context_requires_title_section_and_expected_regions(self):
        title = text("·帮派·", (445, 85, 103, 34))
        section = text("帮派签到", (723, 385, 81, 24))
        sign_in = text("签到", (750, 454, 59, 30))
        cases = [
            [section, sign_in],
            [title, sign_in],
            [title, text("帮派签到", (100, 100, 81, 24)), sign_in],
        ]
        for texts in cases:
            with self.subTest(texts=texts):
                self.assertEqual(
                    classify_clan_sign_in_scene(
                        texts,
                        {},
                        "enter",
                        (1020, 718),
                    )["status"],
                    "unknown",
                )

    def test_sign_in_and_dividend_are_phase_and_context_gated(self):
        self.assertEqual(
            classify_clan_sign_in_scene([text("签到")], {}, "clan")["status"],
            "unknown",
        )
        self.assertEqual(
            classify_clan_sign_in_scene(
                [text("签到")], {"clan_header": (350, 0, 320, 70)}, "welfare"
            )["status"],
            "sign_in",
        )
        self.assertEqual(
            classify_clan_sign_in_scene([text("领取分红")], {}, "enter")["status"],
            "unknown",
        )

    def test_claims_dividend_only_with_full_welfare_context(self):
        claim = text("领取分红", (738, 576, 82, 24))
        image_size = (1020, 718)

        self.assertEqual(
            classify_clan_sign_in_scene(
                welfare_texts(text("已签到", (749, 458, 61, 23)), claim),
                {},
                "enter",
                image_size,
            ),
            {"status": "claim_dividend", "box": [738, 576, 82, 24]},
        )

        invalid_scenes = [
            [text("帮派签到", (721, 382, 84, 29)), text("帮派分红", (720, 504, 85, 25)), claim],
            [text("·帮派·", (445, 85, 103, 34)), text("帮派分红", (720, 504, 85, 25)), claim],
            welfare_texts(text("领取分红", (20, 30, 82, 24))),
            [*welfare_texts(claim)[:2], claim],
        ]
        for texts in invalid_scenes:
            with self.subTest(texts=texts):
                self.assertNotEqual(
                    classify_clan_sign_in_scene(
                        texts, {}, "enter", image_size
                    )["status"],
                    "claim_dividend",
                )

    def test_zero_dividend_finishes_without_clicking_claim_button(self):
        zero_dividend = welfare_texts(
            text("已签到", (749, 455, 62, 26)),
            text("0", (783, 540, 12, 16)),
            text("领取分红", (739, 578, 82, 22)),
        )

        self.assertEqual(
            classify_clan_sign_in_scene(
                zero_dividend, {}, "enter", (1020, 718)
            )["status"],
            "already_completed",
        )
        for phase in ("verify_sign_in", "check_dividend", "verify_dividend"):
            with self.subTest(phase=phase):
                self.assertEqual(
                    classify_clan_sign_in_scene(
                        zero_dividend, {}, phase, (1020, 718)
                    )["status"],
                    "completed",
                )

    def test_zero_outside_dividend_card_does_not_hide_claim(self):
        decision = classify_clan_sign_in_scene(
            welfare_texts(
                text("已签到", (749, 455, 62, 26)),
                text("0", (448, 193, 19, 18)),
                text("领取分红", (739, 578, 82, 22)),
            ),
            {},
            "enter",
            (1020, 718),
        )

        self.assertEqual(decision["status"], "claim_dividend")

    def test_dividend_confirmation_requires_expected_phase_and_region(self):
        confirmation = text("领取", (475, 550, 70, 40))
        self.assertEqual(
            classify_clan_sign_in_scene(
                [confirmation], {}, "dividend_confirm", (1020, 718)
            ),
            {"status": "confirm_dividend", "box": [475, 550, 70, 40]},
        )
        for phase, box in (("enter", (475, 550, 70, 40)), ("dividend_confirm", (20, 30, 70, 40))):
            with self.subTest(phase=phase, box=box):
                self.assertEqual(
                    classify_clan_sign_in_scene(
                        [text("领取", box)], {}, phase, (1020, 718)
                    )["status"],
                    "unknown",
                )

    def test_completion_requires_welfare_context(self):
        self.assertEqual(
            classify_clan_sign_in_scene([text("今日已签到")], {}, "enter")["status"],
            "unknown",
        )
        self.assertEqual(
            classify_clan_sign_in_scene(
                [text("今日已签到")],
                {"clan_header": (350, 0, 320, 70)},
                "welfare",
            )["status"],
            "already_completed",
        )
        self.assertEqual(
            classify_clan_sign_in_scene(
                [text("签到成功")],
                {"clan_header": (350, 0, 320, 70)},
                "verify_sign_in",
            )["status"],
            "sign_in_confirmed",
        )

    def test_sign_in_completion_waits_for_dividend_terminal_evidence(self):
        transient = welfare_texts(text("签到成功", (749, 458, 61, 23)))
        unavailable = welfare_texts(
            text("已签到", (749, 458, 61, 23)),
            text("下周揭晓", (745, 538, 88, 22)),
        )

        self.assertEqual(
            classify_clan_sign_in_scene(
                transient, {}, "verify_sign_in", (1020, 718)
            )["status"],
            "sign_in_confirmed",
        )
        self.assertEqual(
            classify_clan_sign_in_scene(
                transient, {}, "check_dividend", (1020, 718)
            )["status"],
            "sign_in_confirmed",
        )
        self.assertEqual(
            classify_clan_sign_in_scene(
                unavailable, {}, "check_dividend", (1020, 718)
            )["status"],
            "completed",
        )

    def test_out_of_place_templates_are_not_actionable(self):
        self.assertEqual(
            classify_clan_sign_in_scene(
                [], {"expand_menu": (10, 10, 50, 50)}, "enter", (1020, 718)
            )["status"],
            "unknown",
        )
        self.assertEqual(
            classify_clan_sign_in_scene(
                [text("签到", (10, 20, 80, 30))],
                {"clan_header": (350, 0, 320, 70)},
                "welfare",
                (1020, 718),
            )["status"],
            "clan_idle",
        )

    def test_not_joined_clan_is_explicit_terminal_state(self):
        self.assertEqual(
            classify_clan_sign_in_scene([text("当前未加入帮派")], {}, "enter")["status"],
            "no_clan",
        )


class ClanSignInRunnerTests(unittest.TestCase):
    @patch("tools.run_bangpai_qiandao_macos.MacOSTaskRuntime")
    def test_recovers_from_welfare_page_left_by_previous_task(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene(templates={"welfare_header": (420, 20, 300, 60)}),
            scene(templates={"expand_menu": (940, 630, 50, 50)}),
            scene(templates={"clan_entry": (800, 620, 70, 60)}),
            scene(templates={
                "clan_header": (350, 0, 320, 70),
                "clan_welfare": (880, 350, 60, 90),
            }),
            scene(
                [text("今日已签到")],
                {"clan_header": (350, 0, 320, 70)},
            ),
        ]
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=6)
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(
            [call.args[0] for call in runtime.click_box.call_args_list],
            [
                welfare_close_box((1020, 718)),
                expand_menu_box((1020, 718)),
                clan_entry_box((1020, 718)),
                [880, 350, 60, 90],
            ],
        )
        self.assertEqual(
            protocol_payloads(output.getvalue(), "result_json=")[0]["status"],
            "already_completed",
        )

    @patch("tools.run_bangpai_qiandao_macos.MacOSTaskRuntime")
    def test_leftover_welfare_close_is_clicked_at_most_once(self, runtime_class):
        runtime = runtime_class.return_value
        welfare = scene(templates={"welfare_header": (420, 20, 300, 60)})
        runtime.read_scene.side_effect = [welfare, welfare]
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=3)

        with self.assertRaisesRegex(RuntimeError, "关闭后仍未变化"):
            run(args, Mock())
        self.assertEqual(runtime.click_box.call_count, 1)

    @patch("tools.run_bangpai_qiandao_macos.MacOSTaskRuntime")
    def test_full_flow_clicks_only_recognized_boxes(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene(templates={"expand_menu": (940, 630, 50, 50)}),
            scene(templates={"clan_entry": (800, 620, 70, 60)}),
            scene(templates={
                "clan_header": (350, 0, 320, 70),
                "clan_welfare": (880, 350, 60, 90),
            }),
            scene(
                [text("签到", (700, 460, 150, 50))],
                {"clan_header": (350, 0, 320, 70)},
            ),
            scene(
                [text("签到成功")],
                {"clan_header": (350, 0, 320, 70)},
            ),
            scene(welfare_texts(
                text("已签到", (749, 458, 61, 23)),
                text("下周揭晓", (745, 538, 88, 22)),
            )),
        ]
        guard = Mock()
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=8)
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, guard), 0)

        self.assertEqual(
            [call.args[0] for call in runtime.click_box.call_args_list],
            [
                expand_menu_box((1020, 718)),
                clan_entry_box((1020, 718)),
                [880, 350, 60, 90],
                [700, 460, 150, 50],
            ],
        )
        self.assertEqual(
            protocol_payloads(output.getvalue(), "result_json="),
            [{
                "task_id": "bangpai_qiandao",
                "status": "completed",
                "actions": 4,
                "clicks": 4,
            }],
        )

    @patch("tools.run_bangpai_qiandao_macos.MacOSTaskRuntime")
    def test_already_completed_emits_one_terminal_result(self, runtime_class):
        runtime_class.return_value.read_scene.return_value = scene(
            [text("今日已签到")],
            {"clan_header": (350, 0, 320, 70)},
        )
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=2)
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(
            protocol_payloads(output.getvalue(), "result_json="),
            [{
                "task_id": "bangpai_qiandao",
                "status": "already_completed",
                "actions": 0,
                "clicks": 0,
            }],
        )

    @patch("tools.run_bangpai_qiandao_macos.MacOSTaskRuntime")
    def test_sign_in_button_is_clicked_at_most_once(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene(templates={
                "clan_header": (350, 0, 320, 70),
                "clan_welfare": (880, 350, 60, 90),
            }),
            scene(
                [text("签到", (700, 460, 150, 50))],
                {"clan_header": (350, 0, 320, 70)},
            ),
            scene(
                [text("签到", (700, 460, 150, 50))],
                {"clan_header": (350, 0, 320, 70)},
            ),
        ]
        guard = Mock()
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=4)

        with self.assertRaisesRegex(RuntimeError, "点击后仍未变化"):
            run(args, guard)
        self.assertEqual(runtime.click_box.call_count, 2)

    @patch("tools.run_bangpai_qiandao_macos.MacOSTaskRuntime")
    def test_dividend_after_sign_in_is_claimed_and_confirmed(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene(
                welfare_texts(text("签到", (750, 454, 59, 30))),
            ),
            scene(
                welfare_texts(
                    text("已签到", (749, 458, 61, 23)),
                    text("领取分红", (738, 576, 82, 24)),
                ),
            ),
            scene([text("领取", (475, 550, 70, 40))]),
            scene(welfare_texts(
                text("已签到", (749, 458, 61, 23)),
                text("已领取", (750, 576, 70, 24)),
            )),
        ]
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=5)
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(
            [call.args[0] for call in runtime.click_box.call_args_list],
            [
                [750, 454, 59, 30],
                [738, 576, 82, 24],
                [475, 550, 70, 40],
            ],
        )
        self.assertEqual(
            protocol_payloads(output.getvalue(), "result_json="),
            [{
                "task_id": "bangpai_qiandao",
                "status": "completed",
                "actions": 3,
                "clicks": 3,
            }],
        )

    @patch("tools.run_bangpai_qiandao_macos.MacOSTaskRuntime")
    def test_dividend_and_confirmation_are_each_clicked_at_most_once(self, runtime_class):
        claim_scene = scene(welfare_texts(
            text("已签到", (749, 458, 61, 23)),
            text("领取分红", (738, 576, 82, 24)),
        ))
        runtime = runtime_class.return_value
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=4)

        runtime.read_scene.side_effect = [claim_scene, claim_scene]
        with self.assertRaisesRegex(RuntimeError, "分红按钮点击后仍未变化"):
            run(args, Mock())
        self.assertEqual(runtime.click_box.call_count, 1)

        runtime.reset_mock()
        runtime.read_scene.side_effect = [
            claim_scene,
            scene([text("领取", (475, 550, 70, 40))]),
            scene([text("领取", (475, 550, 70, 40))]),
        ]
        with self.assertRaisesRegex(RuntimeError, "分红确认后页面仍未变化"):
            run(args, Mock())
        self.assertEqual(runtime.click_box.call_count, 2)

    @patch("tools.run_bangpai_qiandao_macos.MacOSTaskRuntime")
    def test_read_only_modes_emit_only_preflight_protocol(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = scene(
            templates={"clan_entry": (800, 620, 70, 60)}
        )
        runtime.inspection_evidence.return_value = {}
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
            patch.object(clan_runner, "parse_args", return_value=args),
            patch.object(clan_runner, "CancellationGuard", return_value=Mock()),
            patch.object(clan_runner, "run", side_effect=RuntimeError("未加入帮派")),
            redirect_stdout(output),
        ):
            self.assertEqual(clan_runner.main(), 1)

        self.assertEqual(
            protocol_payloads(output.getvalue(), "result_json="),
            [{
                "task_id": "bangpai_qiandao",
                "status": "safe_stopped",
                "reason": "未加入帮派",
            }],
        )

    def test_main_emits_one_cancelled_result(self):
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=1)
        guard = Mock()
        output = io.StringIO()
        with (
            patch.object(clan_runner, "parse_args", return_value=args),
            patch.object(clan_runner, "CancellationGuard", return_value=guard),
            patch.object(
                clan_runner,
                "run",
                side_effect=clan_runner.CancellationRequested("收到停止请求"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(clan_runner.main(), 0)

        self.assertEqual(
            protocol_payloads(output.getvalue(), "result_json="),
            [{
                "task_id": "bangpai_qiandao",
                "status": "cancelled",
                "reason": "收到停止请求",
            }],
        )
        guard.wait_for_tasker_stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
