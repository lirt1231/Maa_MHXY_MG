import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.mijingxiangyao_recognition import classify_mijingxiangyao_scene
from tools.run_mijingxiangyao_macos import run


def text(value, box=(10, 20, 80, 24)):
    return {"text": value, "box": list(box)}


def scene(texts=None, templates=None):
    return SimpleNamespace(
        image_size=(1020, 718),
        texts=texts or [],
        templates=templates or {},
    )


def daily_page(*items):
    return [
        text("活动", (450, 30, 80, 30)),
        text("日常活动", (80, 90, 100, 30)),
        *items,
    ]


class MijingxiangyaoSceneTests(unittest.TestCase):
    def test_unrelated_reincarnation_quests_do_not_match_dungeon_sidebar(self):
        decision = classify_mijingxiangyao_scene(
            [
                text("奇遇·两心相许", (830, 152, 127, 22)),
                text("上门的买卖", (833, 180, 80, 19)),
                text("星印・更上一重", (831, 231, 127, 19)),
                text("通过师父的考验", (830, 257, 115, 18)),
                text("引导-初入轮回境·二", (830, 307, 170, 22)),
                text("同袁天罡进入轮回境新", (830, 334, 164, 21)),
                text("手关卡", (831, 351, 50, 19)),
                text("活动", (260, 49, 40, 19)),
            ],
            {},
            (1020, 718),
        )
        self.assertEqual(decision["status"], "open_activity")

    def test_challenge_page_opens_daily_before_scanning_target(self):
        daily_tab = text("日常活动", (130, 161, 88, 27))
        decision = classify_mijingxiangyao_scene(
            [
                text("活动", (474, 100, 71, 23)),
                daily_tab,
                text("挑战活动", (127, 217, 90, 27)),
                text("秘境降妖", (669, 248, 84, 26)),
                text("参加", (838, 269, 47, 29)),
            ],
            {},
            (1020, 718),
            daily_tab_confirmed=False,
        )
        self.assertEqual(decision, {"status": "open_daily", "box": daily_tab["box"]})

    def test_activity_purchase_count_text_is_not_an_asset_prompt(self):
        daily_tab = text("日常活动", (130, 161, 88, 27))
        decision = classify_mijingxiangyao_scene(
            [
                text("活动", (474, 100, 71, 23)),
                daily_tab,
                text("挑战活动", (127, 219, 90, 25)),
                text("购买数量", (350, 330, 90, 24)),
            ],
            {},
            (1020, 718),
            daily_tab_confirmed=False,
        )
        self.assertEqual(decision, {"status": "open_daily", "box": daily_tab["box"]})

    def test_mode_selection_requires_manual_choice(self):
        decision = classify_mijingxiangyao_scene(
            [
                text("选择模式", (450, 90, 120, 30)),
                text("海底秘境", (280, 180, 120, 30)),
                text("日月之井", (720, 180, 120, 30)),
                text("进入", (280, 590, 100, 35)),
                text("进入", (720, 590, 100, 35)),
            ],
            {},
            (1020, 718),
            "await_lobby",
        )
        self.assertEqual(decision["status"], "mode_selection_required")
        self.assertIn("手动选择", decision["reason"])

    def test_current_lobby_split_continue_button_can_be_taken_over_from_any_phase(self):
        texts = [
            text("秘境降妖", (428, 75, 147, 38)),
            text("69级-日之井", (286, 152, 130, 28)),
            text("剩余挑战次数:2次", (442, 152, 180, 28)),
            text("继续", (803, 464, 49, 29)),
            text("挑战", (801, 487, 50, 29)),
            text("秘境挑战", (900, 180, 90, 28)),
        ]
        decision = classify_mijingxiangyao_scene(
            texts, {}, (1020, 718), "navigate"
        )
        self.assertEqual(
            decision,
            {"status": "start_challenge", "box": [801, 464, 51, 52]},
        )

    def test_split_continue_button_needs_full_lobby_evidence(self):
        decision = classify_mijingxiangyao_scene(
            [
                text("继续", (803, 464, 49, 29)),
                text("挑战", (801, 487, 50, 29)),
            ],
            {},
            (1020, 718),
            "navigate",
        )
        self.assertEqual(decision["status"], "unknown")

    def test_cloud_leyou_menu_option_is_actionable_with_npc_evidence(self):
        option = text("秘境降妖", (700, 220, 120, 30))
        decision = classify_mijingxiangyao_scene(
            [text("云乐游", (420, 180, 100, 30)), option],
            {},
            (1020, 718),
            "await_lobby",
        )
        self.assertEqual(decision, {"status": "enter_dungeon", "box": option["box"]})

    def test_asset_and_failure_evidence_stop_before_progress_actions(self):
        asset = classify_mijingxiangyao_scene(
            [
                text("是否花费100仙玉原地复活", (350, 260, 300, 30)),
                text("进入战斗", (700, 410, 140, 35)),
            ],
            {},
            (1020, 718),
            "stage_selected",
        )
        self.assertEqual(asset["status"], "unsupported_asset_branch")

        failure = classify_mijingxiangyao_scene(
            [text("本次挑战失败", (410, 240, 180, 30))],
            {},
            (1020, 718),
            "battle",
        )
        self.assertEqual(failure["status"], "challenge_failed")

        split_asset = classify_mijingxiangyao_scene(
            [text("购买", (420, 260, 80, 30)), text("助战", (520, 260, 80, 30))],
            {},
            (1020, 718),
            "battle",
        )
        self.assertEqual(split_asset["status"], "unsupported_asset_branch")

        medicine = classify_mijingxiangyao_scene(
            [text("使用药品", (420, 260, 120, 30))],
            {},
            (1020, 718),
            "battle",
        )
        self.assertEqual(medicine["status"], "unsupported_asset_branch")

        unrelated_messages = classify_mijingxiangyao_scene(
            [
                text("批量使用了个聚灵仙饮", (5, 648, 220, 24)),
                text("可前往助战培养界面", (350, 90, 220, 24)),
                text("取消", (961, 680, 44, 25)),
            ],
            {},
            (1020, 718),
            "battle",
        )
        self.assertEqual(
            unrelated_messages, {"status": "wait", "reason": "battle_ocr"}
        )

        nearby_unrelated_messages = classify_mijingxiangyao_scene(
            [
                text("批量使用了1个聚灵仙饮", (300, 180, 220, 24)),
                text("可前往助战培养界面", (350, 220, 220, 24)),
                text("取消", (961, 680, 44, 25)),
            ],
            {},
            (1020, 718),
            "battle",
        )
        self.assertEqual(
            nearby_unrelated_messages, {"status": "wait", "reason": "battle_ocr"}
        )

    def test_zero_remaining_attempts_never_clicks_continue(self):
        decision = classify_mijingxiangyao_scene(
            [
                text("秘境降妖", (428, 75, 147, 38)),
                text("69级-日之井", (286, 152, 130, 28)),
                text("剩余挑战次数:0次", (442, 152, 180, 28)),
                text("继续", (803, 464, 49, 29)),
                text("挑战", (801, 487, 50, 29)),
            ],
            {},
            (1020, 718),
        )
        self.assertEqual(
            decision,
            {"status": "challenge_failed", "reason": "今日秘境挑战次数已用完"},
        )

    def test_risk_overlay_has_priority_over_background_stage_completion(self):
        decision = classify_mijingxiangyao_scene(
            [
                text("全部通关", (790, 210, 100, 30)),
                text("是否花费100仙玉原地复活", (350, 260, 300, 30)),
            ],
            {"battle": (1, 2, 3, 4)},
            (1020, 718),
            "battle",
        )
        self.assertEqual(decision["status"], "unsupported_asset_branch")

    def test_battle_is_wait_only_even_if_action_text_remains_visible(self):
        decision = classify_mijingxiangyao_scene(
            [
                text("进入战斗", (700, 410, 140, 35)),
                text("商城", (40, 220, 60, 28)),
            ],
            {"battle": (1, 2, 3, 4)},
            (1020, 718),
            "stage_selected",
        )
        self.assertEqual(decision, {"status": "wait", "reason": "battle"})

    def test_stage_and_battle_buttons_require_expected_regions(self):
        stage = text("第3关", (780, 180, 90, 30))
        self.assertEqual(
            classify_mijingxiangyao_scene(
                [stage], {}, (1020, 718), "stage_select"
            )["status"],
            "select_stage",
        )
        chat_stage = text("第3关", (100, 650, 90, 30))
        self.assertEqual(
            classify_mijingxiangyao_scene(
                [chat_stage], {}, (1020, 718), "stage_select"
            )["status"],
            "unknown",
        )

    def test_multiple_stage_candidates_are_never_clicked_blindly(self):
        decision = classify_mijingxiangyao_scene(
            [
                text("第2关", (780, 180, 90, 30)),
                text("第3关", (780, 250, 90, 30)),
            ],
            {},
            (1020, 718),
            "stage_select",
        )
        self.assertEqual(
            decision,
            {"status": "unknown", "reason": "multiple_stage_candidates"},
        )

    def test_completion_text_must_be_explicit_or_in_stage_region(self):
        self.assertEqual(
            classify_mijingxiangyao_scene(
                [text("秘境降妖任务已完成")], {}, (1020, 718)
            )["status"],
            "completed",
        )
        self.assertEqual(
            classify_mijingxiangyao_scene(
                [text("全部通关", (790, 210, 100, 30))],
                {},
                (1020, 718),
                "battle",
            )["status"],
            "completed",
        )
        self.assertEqual(
            classify_mijingxiangyao_scene(
                [text("通关", (100, 650, 80, 30))], {}, (1020, 718), "battle"
            )["status"],
            "unknown",
        )
        self.assertEqual(
            classify_mijingxiangyao_scene(
                [text("通关", (790, 210, 80, 30))], {}, (1020, 718), "battle"
            ),
            {"status": "wait", "reason": "stage_completed"},
        )

    def test_explicit_today_completion_stays_distinct(self):
        self.assertEqual(
            classify_mijingxiangyao_scene(
                [text("今日秘境降妖已完成")], {}, (1020, 718)
            )["status"],
            "already_completed",
        )
        self.assertEqual(
            classify_mijingxiangyao_scene(
                [
                    text("秘境降妖", (250, 180, 100, 25)),
                    text("今日已完成", (500, 210, 90, 25)),
                ],
                {},
                (1020, 718),
            )["status"],
            "already_completed",
        )
        self.assertEqual(
            classify_mijingxiangyao_scene(
                [text("今日已完成")], {}, (1020, 718)
            )["status"],
            "unknown",
        )

    def test_adjacent_task_today_completion_does_not_complete_mijing(self):
        join = text("参加", (500, 210, 60, 25))
        decision = classify_mijingxiangyao_scene(
            daily_page(
                text("秘境降妖", (250, 180, 100, 25)),
                join,
                text("宝图任务", (250, 300, 100, 25)),
                text("今日已完成", (500, 330, 90, 25)),
            ),
            {},
            (1020, 718),
            daily_tab_confirmed=True,
        )
        self.assertEqual(decision, {"status": "join_task", "box": join["box"]})


class MijingxiangyaoRunnerTests(unittest.TestCase):
    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_bounded_flow_clicks_only_recognized_evidence(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene([text("活动", (260, 45, 50, 22))]),
            scene(daily_page(text("挑战活动", (120, 150, 100, 30)))),
            scene(
                daily_page(
                    text("秘境降妖", (250, 180, 100, 25)),
                    text("参加", (500, 210, 60, 25)),
                )
            ),
            scene(
                [
                    text("云乐游", (420, 180, 100, 30)),
                    text("秘境降妖", (700, 220, 120, 30)),
                ]
            ),
            scene(
                [
                    text("秘境降妖", (428, 75, 147, 38)),
                    text("69级-日之井", (286, 152, 130, 28)),
                    text("剩余挑战次数:2次", (442, 152, 180, 28)),
                    text("继续", (803, 464, 49, 29)),
                    text("挑战", (801, 487, 50, 29)),
                ]
            ),
            scene([], {"battle": (1, 2, 3, 4)}),
            scene([text("全部通关", (790, 210, 100, 30))]),
        ]
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=3, max_steps=15
        )
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(runtime.click_box.call_count, 5)
        clicked_boxes = [call.args[0] for call in runtime.click_box.call_args_list]
        self.assertEqual(clicked_boxes[1], [80, 90, 100, 30])
        self.assertEqual(clicked_boxes[2], [500, 210, 60, 25])
        result = next(
            json.loads(line[len("result_json=") :])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["battles"], 1)

    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_unknown_scene_stops_without_input(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = scene()
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=3, max_steps=30
        )

        with self.assertRaisesRegex(RuntimeError, "无证据点击"):
            run(args, Mock())
        runtime.click_box.assert_not_called()
        runtime.drag.assert_not_called()

    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_long_unknown_transition_can_be_taken_over_when_battle_appears(
        self, runtime_class
    ):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            *[scene() for _ in range(12)],
            scene([], {"battle": (1, 2, 3, 4)}),
            scene([text("秘境降妖任务已完成", (420, 220, 220, 30))]),
        ]
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=3, max_steps=20
        )
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        runtime.click_box.assert_not_called()
        result = next(
            json.loads(line[len("result_json=") :])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["battles"], 1)

    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_asset_branch_stops_without_clicking(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = scene(
            [text("是否花费100仙玉原地复活", (350, 260, 300, 30))]
        )
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=3, max_steps=4
        )

        with self.assertRaisesRegex(RuntimeError, "资产或购买分支"):
            run(args, Mock())
        runtime.click_box.assert_not_called()

    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_stale_current_lobby_stops_after_two_clicks(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = scene(
            [
                text("秘境降妖", (428, 75, 147, 38)),
                text("69级-日之井", (286, 152, 130, 28)),
                text("剩余挑战次数:2次", (442, 152, 180, 28)),
                text("继续", (803, 464, 49, 29)),
                text("挑战", (801, 487, 50, 29)),
            ]
        )
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=3, max_steps=5
        )

        with self.assertRaisesRegex(RuntimeError, "连续重复执行"):
            run(args, Mock())
        self.assertEqual(runtime.click_box.call_count, 2)

    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_sidebar_task_can_advance_through_repeated_navigation_steps(
        self, runtime_class
    ):
        runtime = runtime_class.return_value
        sidebar_task = scene([text("秘境-日之井", (830, 180, 90, 28))])
        runtime.read_scene.side_effect = [
            sidebar_task,
            scene(),
            sidebar_task,
            scene(),
            sidebar_task,
            scene([text("秘境降妖任务已完成", (420, 220, 220, 30))]),
        ]
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=3, max_steps=8
        )
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(runtime.click_box.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in runtime.click_box.call_args_list],
            [[830, 180, 90, 28]] * 3,
        )
        waits = [call.args[0] for call in runtime_class.call_args.args[0].wait.call_args_list]
        self.assertEqual(waits.count(8.0), 3)

    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_daily_list_searches_down_then_reverses_up_in_small_steps(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [scene() for _ in range(13)]
        decisions = [
            *[
                {"status": "scroll_daily", "reason": "target_not_visible"}
                for _ in range(12)
            ],
            {"status": "already_completed"},
        ]
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=3, max_steps=15
        )
        output = io.StringIO()

        with patch(
            "tools.run_mijingxiangyao_macos.classify_mijingxiangyao_scene",
            side_effect=decisions,
        ):
            with redirect_stdout(output):
                self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(runtime.drag.call_count, 12)
        downward = [call.args[0:2] for call in runtime.drag.call_args_list[:7]]
        upward = [call.args[0:2] for call in runtime.drag.call_args_list[7:]]
        self.assertTrue(all(start[1] > end[1] for start, end in downward))
        self.assertTrue(all(start[1] < end[1] for start, end in upward))
        self.assertTrue(
            all(abs(start[1] - end[1]) <= 80 for start, end in downward + upward)
        )

    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_stale_sidebar_task_still_has_a_hard_repeat_limit(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = scene(
            [text("秘境-日之井", (830, 180, 90, 28))]
        )
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=3, max_steps=12
        )

        with self.assertRaisesRegex(RuntimeError, "连续重复执行 continue_task"):
            run(args, Mock())

        self.assertEqual(runtime.click_box.call_count, 8)

    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_battle_safety_limit_stops_without_a_limit_reached_result(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene([], {"battle": (1, 2, 3, 4)}),
            scene(),
        ]
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=1, max_steps=6
        )

        with patch(
            "tools.run_mijingxiangyao_macos.classify_mijingxiangyao_scene",
            side_effect=[
                {"status": "wait", "reason": "battle"},
                {"status": "start_challenge", "box": [801, 464, 51, 52]},
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "未识别到明确完成状态"):
                run(args, Mock())
        runtime.click_box.assert_not_called()

    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_retried_enter_battle_prompt_counts_as_one_battle(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene([text("进入战斗", (700, 410, 140, 35))]),
            scene([text("进入战斗", (700, 410, 140, 35))]),
            scene([], {"battle": (1, 2, 3, 4)}),
            scene([text("秘境降妖任务已完成", (420, 220, 220, 30))]),
        ]
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=3, max_steps=6
        )
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(runtime.click_box.call_count, 2)
        result = next(
            json.loads(line[len("result_json=") :])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["battles"], 1)

    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_activity_card_already_completed_stays_distinct(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene(daily_page(text("挑战活动", (120, 150, 100, 30)))),
            scene(
                daily_page(
                    text("秘境降妖", (250, 180, 100, 25)),
                    text("已完成", (500, 210, 70, 25)),
                )
            ),
        ]
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=3, max_steps=4
        )
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)
        result = next(
            json.loads(line[len("result_json=") :])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "already_completed")

    @patch("tools.run_mijingxiangyao_macos.MacOSTaskRuntime")
    def test_long_battle_wait_is_not_mistaken_for_navigation_loop(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            *[scene([], {"battle": (1, 2, 3, 4)}) for _ in range(15)],
            scene([text("全部通关", (790, 210, 100, 30))]),
        ]
        args = SimpleNamespace(
            inspect_scene=False, recognize_only=False, max_battles=3, max_steps=20
        )
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)
        runtime.click_box.assert_not_called()


if __name__ == "__main__":
    unittest.main()
