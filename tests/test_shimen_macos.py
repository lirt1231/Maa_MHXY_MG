import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.run_shimen_macos import MAX_DAILY_LOADING_POLLS, run
from tools.shimen_recognition import classify_shimen_scene


def text(value, box=(10, 20, 80, 24)):
    return {"text": value, "box": list(box)}


def scene(texts=None, templates=None):
    return SimpleNamespace(
        image_size=(1020, 718),
        texts=texts or [],
        templates=templates or {},
    )


class ShimenSceneTests(unittest.TestCase):
    def test_challenge_page_opens_daily_before_unrelated_progress_button(self):
        daily_tab = text("日常活动", (130, 161, 88, 27))
        decision = classify_shimen_scene(
            [
                text("活动", (474, 100, 71, 23)),
                daily_tab,
                text("挑战活动", (127, 217, 90, 27)),
                text("去完成", (500, 410, 100, 30)),
            ],
            {},
            (1020, 718),
            daily_tab_confirmed=False,
        )
        self.assertEqual(decision, {"status": "open_daily", "box": daily_tab["box"]})

    def test_explicit_purchase_screen_without_countdown_is_blocked(self):
        decision = classify_shimen_scene(
            [
                text("我要购买", (173, 161, 84, 22)),
                text("购买数量", (743, 538, 120, 21)),
                text("购买", (762, 566, 82, 31)),
            ],
            {},
            (1020, 718),
        )
        self.assertEqual(decision["status"], "unsupported_asset_branch")
        self.assertIn("购买", decision["reason"])

    def test_owned_item_instruction_navigates_before_asset_guard(self):
        decision = classify_shimen_scene(
            [
                text("师门-玄妙法术（7/10）", (830, 142, 171, 18)),
                text("臭豆腐麻烦周怀生修复", (829, 181, 165, 18)),
                text("一下（拥有1/1)，上交不", (831, 196, 164, 18)),
            ],
            {},
            (1020, 718),
        )

        self.assertEqual(decision["status"], "continue_task")
        self.assertEqual(decision["box"], [830, 142, 171, 18])

    def test_map_merchant_name_does_not_block_active_task(self):
        decision = classify_shimen_scene(
            [
                text("坊商会总管", (0, 433, 91, 20)),
                text("师门-玄妙法术（1/10）", (830, 142, 171, 18)),
                text("听说周怀生有竹简材料", (828, 166, 166, 18)),
            ],
            {},
            (1020, 718),
        )

        self.assertEqual(decision["status"], "continue_task")
        self.assertEqual(decision["box"], [830, 142, 171, 18])

    def test_game_managed_purchase_countdown_waits_without_clicking(self):
        decision = classify_shimen_scene(
            [
                text("我要购买", (173, 161, 84, 22)),
                text("2秒后自动购买", (743, 538, 120, 21)),
                text("购买", (762, 566, 82, 31)),
            ],
            {},
            (1020, 718),
        )

        self.assertEqual(
            decision, {"status": "wait", "reason": "game_auto_asset_action"}
        )

    def test_game_managed_submit_countdown_waits_without_clicking(self):
        decision = classify_shimen_scene(
            [
                text("任务物品选择", (779, 160, 123, 24)),
                text("0秒后自动上交", (780, 491, 118, 21)),
                text("上交", (822, 522, 45, 25)),
            ],
            {},
            (1020, 718),
        )

        self.assertEqual(
            decision, {"status": "wait", "reason": "game_auto_asset_action"}
        )

    def test_game_managed_dialogue_countdown_waits_without_clicking(self):
        decision = classify_shimen_scene(
            [
                text("点击任意地方继续", (796, 41, 162, 22)),
                text("0秒后继续", (902, 688, 86, 18)),
            ],
            {},
            (1020, 718),
        )

        self.assertEqual(
            decision, {"status": "wait", "reason": "game_auto_continue"}
        )

    def test_progress_and_sidebar_require_expected_regions(self):
        progress = classify_shimen_scene(
            [text("去完成", (500, 410, 100, 30))], {}, (1020, 718)
        )
        self.assertEqual(progress["status"], "progress_task")
        sidebar = classify_shimen_scene(
            [text("师门-巡逻", (830, 180, 130, 28))], {}, (1020, 718)
        )
        self.assertEqual(sidebar["status"], "continue_task")
        chat = classify_shimen_scene(
            [text("师门-巡逻", (100, 650, 130, 28))], {}, (1020, 718)
        )
        self.assertEqual(chat["status"], "unknown")

    def test_current_sect_training_sidebar_is_actionable(self):
        decision = classify_shimen_scene(
            [
                text("师门历练", (831, 153, 73, 20)),
                text("向师父领取历练任务", (830, 180, 147, 18)),
            ],
            {},
            (1020, 718),
        )
        self.assertEqual(decision["status"], "start_task")
        self.assertEqual(decision["box"], [830, 180, 147, 18])

    def test_current_claim_today_sidebar_is_preaccept_not_completion(self):
        instruction = text("领取今天师门任务", (828, 175, 133, 21))
        decision = classify_shimen_scene(
            [
                text("师门任务", (829, 150, 74, 19)),
                instruction,
            ],
            {},
            (1020, 718),
        )

        self.assertEqual(
            decision,
            {"status": "start_task", "box": instruction["box"]},
        )

    def test_ten_of_ten_final_assessment_is_a_distinct_bounded_action(self):
        instruction = text("去找师父接受最后的考核", (830, 180, 160, 22))
        decision = classify_shimen_scene(
            [
                text("师门-三界游历（10/10）", (830, 150, 175, 22)),
                instruction,
            ],
            {},
            (1020, 718),
        )
        self.assertEqual(
            decision,
            {"status": "final_assessment", "box": instruction["box"]},
        )

    def test_bare_sidebar_header_is_not_treated_as_active_task(self):
        decision = classify_shimen_scene(
            [text("师门任务", (829, 150, 74, 19))],
            {},
            (1020, 718),
        )

        self.assertNotEqual(decision["status"], "continue_task")

    def test_current_item_collection_dialogue_precedes_sidebar_start(self):
        decision = classify_shimen_scene(
            [
                text("师门集物", (831, 142, 74, 18)),
                text("向师父领取集物任务", (831, 167, 145, 15)),
                text("请选择要做的事：", (773, 475, 145, 18)),
                text("师门集物-玄妙法术", (773, 519, 184, 22)),
            ],
            {},
            (1020, 718),
        )
        self.assertEqual(decision["status"], "accept_task_dialogue")
        self.assertEqual(decision["box"], [773, 519, 184, 22])

    def test_current_item_collection_sidebar_starts_after_dialogue(self):
        decision = classify_shimen_scene(
            [
                text("师门集物", (831, 142, 74, 18)),
                text("向师父领取集物任务", (831, 167, 145, 15)),
            ],
            {},
            (1020, 718),
        )
        self.assertEqual(decision["status"], "start_task")
        self.assertEqual(decision["box"], [831, 167, 145, 15])

    def test_battle_waits_even_if_standard_item_label_is_visible(self):
        decision = classify_shimen_scene(
            [text("药品", (900, 600, 70, 25))],
            {"battle": (1, 2, 3, 4)},
            (1020, 718),
        )
        self.assertEqual(decision["status"], "wait")

    def test_current_battle_cancel_is_wait_only(self):
        decision = classify_shimen_scene(
            [text("取消", (961, 680, 44, 25))], {}, (1020, 718)
        )
        self.assertEqual(decision, {"status": "wait", "reason": "battle_ocr"})

    def test_completion_has_priority_over_background_text(self):
        decision = classify_shimen_scene(
            [text("师门任务完成"), text("购买药品")], {}, (1020, 718)
        )
        self.assertEqual(decision["status"], "completed")

    def test_legacy_completion_template_cannot_finish_subtask_page(self):
        decision = classify_shimen_scene(
            [
                text("已完成", (615, 413, 52, 20)),
                text("去完成", (750, 414, 66, 27)),
            ],
            {"completion": (413, 509, 500, 196)},
            (1020, 718),
        )
        self.assertEqual(decision["status"], "progress_task")


class ShimenRunnerTests(unittest.TestCase):
    @patch("tools.run_shimen_macos.MacOSTaskRuntime")
    def test_accepts_dialogue_then_clicks_sidebar_to_start(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene(
                [
                    text("师门集物", (831, 142, 74, 18)),
                    text("向师父领取集物任务", (831, 167, 145, 15)),
                    text("师门集物-玄妙法术", (773, 519, 184, 22)),
                ]
            ),
            scene(
                [
                    text("师门-玄妙法术（1/10）", (831, 142, 171, 18)),
                ]
            ),
            scene([text("今日师门任务已完成")]),
        ]
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=6)

        self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(runtime.click_box.call_count, 2)
        self.assertEqual(
            runtime.click_box.call_args_list[0].args[0], [773, 519, 184, 22]
        )
        self.assertEqual(
            runtime.click_box.call_args_list[1].args[0], [831, 142, 171, 18]
        )
        self.assertEqual(runtime.read_scene.call_count, 3)

    @patch("tools.run_shimen_macos.MacOSTaskRuntime")
    def test_preaccept_sidebar_does_not_finish_before_dialogue(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene([
                text("师门任务", (829, 150, 74, 19)),
                text("领取今天师门任务", (828, 175, 133, 21)),
            ]),
            scene(templates={"auto_pathing": (450, 620, 120, 30)}),
            scene([text("师门巡逻-巡查长安", (773, 519, 184, 22))]),
            scene([text("师门-巡逻（1/10）", (830, 180, 150, 28))]),
            scene([text("今日师门任务已完成")]),
        ]
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=8)
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(runtime.click_box.call_count, 3)
        self.assertEqual(runtime.read_scene.call_count, 5)
        result = next(
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("reason", result)

    @patch("tools.run_shimen_macos.MacOSTaskRuntime")
    def test_active_sidebar_click_continues_observing_until_completion(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene([text("师门-巡逻", (830, 180, 130, 28))]),
            scene(templates={"auto_pathing": (450, 620, 120, 30)}),
            scene([text("师门任务全部完成")]),
        ]
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=5)

        self.assertEqual(run(args, Mock()), 0)
        runtime.click_box.assert_called_once()
        self.assertEqual(runtime.read_scene.call_count, 3)

    @patch("tools.run_shimen_macos.MacOSTaskRuntime")
    def test_handoff_makes_following_sidebar_and_progress_read_only(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = scene()
        decisions = [
            {"status": "continue_task", "box": [830, 180, 130, 28]},
            {"status": "continue_task", "box": [830, 180, 130, 28]},
            {"status": "progress_task", "box": [500, 410, 100, 30]},
            {"status": "completed"},
        ]
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=6)

        with patch("tools.run_shimen_macos.classify_shimen_scene", side_effect=decisions):
            self.assertEqual(run(args, Mock()), 0)

        runtime.click_box.assert_called_once()
        self.assertEqual(runtime.click_box.call_args.args[0], [830, 180, 130, 28])

    @patch("tools.run_shimen_macos.MacOSTaskRuntime")
    def test_active_task_disappearance_on_stable_main_completes(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = scene()
        decisions = [
            {"status": "continue_task", "box": [830, 180, 130, 28]},
            {"status": "wait", "reason": "auto_pathing"},
            *[
                {"status": "open_activity", "box": [260, 45, 50, 22]}
                for _ in range(5)
            ],
        ]
        args = SimpleNamespace(
            inspect_scene=False,
            recognize_only=False,
            max_steps=len(decisions),
        )
        output = io.StringIO()

        with patch(
            "tools.run_shimen_macos.classify_shimen_scene",
            side_effect=decisions,
        ), redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        runtime.click_box.assert_called_once()
        result = next(
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["completionEvidence"],
            "stable_main_after_task_disappeared",
        )

    @patch("tools.run_shimen_macos.MacOSTaskRuntime")
    def test_handoff_allows_one_explicit_final_assessment_click(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = scene()
        decisions = [
            {"status": "continue_task", "box": [830, 180, 130, 28]},
            {"status": "final_assessment", "box": [830, 180, 160, 22]},
            {"status": "final_assessment", "box": [830, 180, 160, 22]},
            {"status": "completed"},
        ]
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=6)

        with patch("tools.run_shimen_macos.classify_shimen_scene", side_effect=decisions):
            self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(runtime.click_box.call_count, 2)
        self.assertEqual(runtime.click_box.call_args.args[0], [830, 180, 160, 22])

    @patch("tools.run_shimen_macos.MacOSTaskRuntime")
    def test_repeated_five_step_navigation_cycle_stops_early(self, runtime_class):
        runtime = runtime_class.return_value
        decisions = [
            {"status": "progress_task", "box": [500, 410, 100, 30]},
            {"status": "open_activity", "box": [260, 45, 50, 22]},
            {"status": "open_daily", "box": [80, 90, 100, 30]},
            {"status": "join_task", "box": [500, 210, 60, 25]},
            {"status": "scroll_daily"},
        ] * 2
        runtime.read_scene.return_value = scene()
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=12)

        with patch(
            "tools.run_shimen_macos.classify_shimen_scene", side_effect=decisions
        ):
            with self.assertRaisesRegex(RuntimeError, "五步页面序列"):
                run(args, Mock())
        self.assertEqual(runtime.click_box.call_count, 8)

    @patch("tools.run_shimen_macos.MacOSTaskRuntime")
    def test_reselects_daily_tab_after_bounded_loading_wait(self, runtime_class):
        runtime = runtime_class.return_value
        daily_tab = text("日常活动", (130, 174, 88, 25))
        full_page = [
            text("活动", (471, 114, 198, 32)),
            daily_tab,
            text("师门任务", (670, 173, 83, 22)),
            text("参加", (839, 193, 46, 24)),
        ]
        runtime.read_scene.side_effect = [
            scene([daily_tab]),
            *[scene([daily_tab]) for _ in range(MAX_DAILY_LOADING_POLLS)],
            scene(full_page),
            scene(full_page),
            scene([text("师门-巡逻", (830, 180, 130, 28))]),
            scene([text("师门任务全部完成")]),
        ]
        args = SimpleNamespace(
            inspect_scene=False,
            recognize_only=False,
            max_steps=MAX_DAILY_LOADING_POLLS + 6,
        )

        self.assertEqual(run(args, Mock()), 0)
        self.assertEqual(runtime.click_box.call_count, 4)


if __name__ == "__main__":
    unittest.main()
