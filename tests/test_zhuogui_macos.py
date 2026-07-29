import json
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np


RECOGNITION_DIR = Path(__file__).parents[1] / "agent" / "custom" / "recognition"
sys.path.insert(0, str(RECOGNITION_DIR))

from zhuogui_macos_core import (  # noqa: E402
    RuntimeBudget,
    classify_scene,
    fifth_member_present,
    relative_point,
    round_completion_action,
    proposed_action,
)
from tools import run_zhuogui_macos as zhuogui_runner  # noqa: E402


def item(text, x=10, y=10, width=80, height=24):
    return {"text": text, "box": [x, y, width, height]}


class SceneClassificationTests(unittest.TestCase):
    image_size = (986, 720)

    def test_main_screen_uses_visible_team_button(self):
        decision = classify_scene([item("队伍", 900, 100)], self.image_size)
        self.assertEqual(decision["status"], "main_screen")
        self.assertEqual(decision["team"], (940, 112))

    def test_main_screen_accepts_position_bounded_team_ocr_error(self):
        decision = classify_scene([item("顾伍", 930, 90)], self.image_size)

        self.assertEqual(decision["status"], "main_screen")
        self.assertEqual(decision["team"], (970, 102))

        unrelated = classify_scene([item("顾伍", 300, 400)], self.image_size)
        self.assertEqual(unrelated, {"status": "unknown"})

    def test_changan_main_screen_keeps_team_action_and_location(self):
        decision = classify_scene(
            [item("长安城", 80, 40), item("队伍", 900, 100)], self.image_size
        )
        self.assertEqual(decision["status"], "main_screen")
        self.assertEqual(decision["location"], "长安城")

    def test_team_quick_panel_has_safe_create_action(self):
        decision = classify_scene(
            [item("请组建或加入队"), item("创建", 820, 380), item("找队", 910, 380)],
            self.image_size,
        )
        self.assertEqual(decision["status"], "team_quick_panel")
        self.assertEqual(decision["create"], (860, 392))

    def test_created_team_panel_enters_full_team_page(self):
        decision = classify_scene(
            [
                item("暂离队伍"),
                item("离开队伍"),
                item("邀请入队"),
                item("队伍", 930, 120),
            ],
            self.image_size,
        )
        self.assertEqual(decision["status"], "team_created_panel")
        self.assertEqual(decision["team"], (970, 132))

    def test_double_prompt_requires_full_wording(self):
        decision = classify_scene(
            [item("双倍点数不足，是否领取100点双倍？"), item("确定", 700, 400)],
            self.image_size,
        )
        self.assertEqual(decision["status"], "double_points_prompt")
        self.assertEqual(decision["confirm"], (740, 412))
        incomplete = classify_scene([item("双倍点数不足")], self.image_size)
        self.assertEqual(incomplete["status"], "unknown")

    def test_invite_overlay_requires_title_and_close_template(self):
        decision = classify_scene(
            [item("邀请成员")],
            self.image_size,
            {"generic_close": (780, 100, 40, 40)},
        )
        self.assertEqual(decision["status"], "invite_members_overlay")
        self.assertEqual(decision["close"], (780, 100, 40, 40))

    def test_invite_overlay_has_normalized_close_fallback(self):
        decision = classify_scene([item("邀请成员")], self.image_size)
        self.assertEqual(decision["status"], "invite_members_overlay")
        self.assertEqual(decision["close"], (789, 122, 40, 40))

    def test_known_welfare_overlay_has_bounded_close_action(self):
        decision = classify_scene([], self.image_size, {"welfare": (400, 20, 80, 30)})
        self.assertEqual(decision["status"], "welfare_overlay")
        self.assertEqual(decision["close"], (878, 58))

    def test_round_complete_has_separate_buttons(self):
        decision = classify_scene(
            [
                item("少侠已经捉完1轮鬼，是否继续捉鬼？"),
                item("取消", 420, 400),
                item("确定", 700, 400),
            ],
            self.image_size,
        )
        self.assertEqual(decision["status"], "round_complete")
        self.assertIsNotNone(decision["confirm"])
        self.assertIsNotNone(decision["cancel"])

    def test_daily_limit_is_an_explicit_already_completed_state(self):
        for message in ("今日捉鬼任务已完成", "捉鬼次数已达上限"):
            with self.subTest(message=message):
                decision = classify_scene([item(message)], self.image_size)
                self.assertEqual(decision, {"status": "already_completed"})

    def test_completion_words_from_other_rows_do_not_complete_ghost_task(self):
        decision = classify_scene(
            [item("今日活动"), item("捉鬼任务"), item("师门已完成")],
            self.image_size,
        )

        self.assertNotEqual(decision["status"], "already_completed")

    def test_team_page_detects_offline_and_full_fifth_slot(self):
        items = [
            item("退出队伍"),
            item("自动匹配", 800, 50),
            item("离线", 500, 500),
            *[item("69级", 100 + index * 120, 520) for index in range(5)],
        ]
        decision = classify_scene(items, self.image_size, {"team_header": (0, 0, 20, 20)})
        self.assertEqual(decision["status"], "team_page")
        self.assertTrue(decision["full"])
        self.assertIsNotNone(decision["offline"])
        self.assertTrue(fifth_member_present(items, self.image_size))

    def test_assist_slots_do_not_count_as_full_members(self):
        items = [
            *[item("69级", 100 + index * 120, 520) for index in range(5)],
            *[item("助战", 200 + index * 120, 200) for index in range(4)],
        ]
        self.assertFalse(fifth_member_present(items, self.image_size))

    def test_battle_and_ghost_task(self):
        battle = classify_scene([], self.image_size, {"battle": (1, 1, 20, 20)})
        self.assertEqual(battle["status"], "battle")
        battle_by_cancel = classify_scene(
            [item("取消", 920, 675, 50, 25)], self.image_size
        )
        self.assertEqual(battle_by_cancel["status"], "battle")
        pathing = classify_scene([], self.image_size, {"auto_pathing": (1, 1, 20, 20)})
        self.assertEqual(pathing["status"], "auto_pathing")
        task = classify_scene([item("捉鬼（3/10）", 800, 200)], self.image_size)
        self.assertEqual(task["status"], "ghost_task")
        self.assertEqual(task["progress"], 3)

    def test_target_dialog_reports_endpoints_preset_and_confirm(self):
        decision = classify_scene(
            [
                item("调整目标"),
                item("捉鬼任务", 250, 210),
                item("捉鬼任务", 250, 260),
                item("20", 602, 274, 25, 22),
                item("115级", 689, 274, 65, 22),
                item("90-115", 710, 330),
                item("确定", 450, 580),
            ],
            self.image_size,
        )
        self.assertEqual(decision["status"], "target_dialog")
        self.assertTrue(decision["target_is_ghost"])
        self.assertEqual(decision["ghost"], (290, 272))
        self.assertIsNotNone(decision["level_20"])
        self.assertIsNotNone(decision["level_115"])
        self.assertEqual(decision["current_min_level"], 20)
        self.assertEqual(decision["current_max_level"], 115)
        self.assertIsNotNone(decision["range_90_115"])
        self.assertIsNotNone(decision["confirm"])

    def test_unknown_state_has_no_click(self):
        decision = classify_scene([item("普通民宅")], self.image_size)
        self.assertEqual(decision, {"status": "unknown"})

    def test_location_text_without_main_or_map_evidence_is_unknown(self):
        decision = classify_scene([item("长安城", 80, 40)], self.image_size)
        self.assertEqual(decision, {"status": "unknown"})

    def test_matching_toast_does_not_impersonate_team_page(self):
        decision = classify_scene(
            [item("退出自动匹配"), item("队伍", 900, 100)],
            self.image_size,
        )
        self.assertEqual(decision["status"], "main_screen")

    def test_world_chat_current_label_is_not_a_channel_picker(self):
        decision = classify_scene(
            [item("当前", 0, 650), item("顾伍", 930, 90)],
            self.image_size,
        )

        self.assertEqual(decision["status"], "main_screen")

    def test_channel_picker_requires_full_label_in_recruitment_region(self):
        decision = classify_scene(
            [item("当前频道", 800, 400)],
            self.image_size,
        )

        self.assertEqual(decision["status"], "channel_picker")
        self.assertEqual(decision["click"], (840, 412))


class SceneCaptureTests(unittest.TestCase):
    @patch.object(zhuogui_runner, "template_box", return_value=None)
    @patch.object(zhuogui_runner, "recognition_detail", return_value=None)
    def test_read_scene_uses_window_capture_fallback(
        self, _recognition_detail, _template_box
    ):
        image = np.ones((718, 1020, 3), dtype=np.uint8)
        capture = Mock()
        capture.capture.return_value = image

        scene = zhuogui_runner.read_scene(Mock(), capture, Mock())

        capture.capture.assert_called_once()
        self.assertEqual(scene["image_size"], [1020, 718])
        self.assertEqual(scene["decision"], {"status": "unknown"})


class SafetyAndCountingTests(unittest.TestCase):
    def test_level_picker_drag_supports_both_directions(self):
        self.assertLess(zhuogui_runner.level_drag_end(21, 90), 0.395)
        self.assertLess(zhuogui_runner.level_drag_end(89, 90), 0.395)
        self.assertGreater(zhuogui_runner.level_drag_end(90, 20), 0.395)
        self.assertGreater(zhuogui_runner.level_drag_end(21, 20), 0.395)
        with self.assertRaises(ValueError):
            zhuogui_runner.level_drag_end(90, 90)

    def test_level_adjustment_budget_covers_90_to_20_picker(self):
        self.assertGreaterEqual(zhuogui_runner.MAX_TARGET_LEVEL_SWIPES, 21)

    def test_recognize_only_action_is_non_mutating_description(self):
        self.assertEqual(proposed_action({"status": "battle"}), "wait")
        self.assertEqual(proposed_action({"status": "unknown"}), "stop")
        self.assertEqual(
            proposed_action({"status": "target_dialog"}),
            "configure_ghost_target",
        )

    def test_round_counts_are_exact(self):
        for target in (1, 2, 10):
            for completed in range(target):
                expected = "finish" if completed + 1 == target else "continue"
                self.assertEqual(round_completion_action(completed, target), expected)
        with self.assertRaises(ValueError):
            round_completion_action(0, 0)

    def test_coordinate_scaling_uses_live_image(self):
        self.assertEqual(relative_point((0.625, 0.4), (986, 720)), (616, 288))

    def test_shout_and_recruitment_budgets(self):
        budget = RuntimeBudget(max_shouts=3, shout_interval=45, recruitment_timeout=180)
        budget.recruitment_started_at = 100
        self.assertTrue(budget.can_shout(100))
        budget.record_shout(100)
        self.assertFalse(budget.can_shout(120))
        self.assertTrue(budget.can_shout(145))
        budget.record_shout(145)
        budget.record_shout(190)
        self.assertFalse(budget.can_shout(300))
        self.assertFalse(budget.recruitment_expired(280))
        self.assertTrue(budget.recruitment_expired(281))

    def test_rebuild_battle_and_round_budgets_stop_before_overrun(self):
        budget = RuntimeBudget(
            max_rebuilds=3,
            battle_timeout=180,
            round_timeout=1200,
        )
        for _ in range(3):
            self.assertTrue(budget.can_rebuild())
            budget.record_rebuild()
        self.assertFalse(budget.can_rebuild())
        with self.assertRaises(RuntimeError):
            budget.record_rebuild()

        budget.battle_started_at = 100
        self.assertFalse(budget.battle_expired(280))
        self.assertTrue(budget.battle_expired(281))
        budget.round_started_at = 100
        self.assertFalse(budget.round_expired(1300))
        self.assertTrue(budget.round_expired(1301))


class StructuredResultTests(unittest.TestCase):
    @staticmethod
    def args(**changes):
        values = {
            "rounds": 2,
            "inspect_scene": False,
            "recognize_only": False,
            "max_steps": 600,
        }
        values.update(changes)
        return SimpleNamespace(**values)

    @staticmethod
    def protocol_results(output: str) -> list[dict]:
        return [
            json.loads(line[len(zhuogui_runner.RESULT_PREFIX):])
            for line in output.splitlines()
            if line.startswith(zhuogui_runner.RESULT_PREFIX)
        ]

    def run_main(self, args, workflow):
        guard = Mock()
        output = StringIO()
        with (
            patch.object(zhuogui_runner, "CancellationGuard", return_value=guard),
            patch.object(zhuogui_runner, "parse_args", return_value=args),
            patch.object(zhuogui_runner, "run_workflow", side_effect=workflow),
            redirect_stdout(output),
        ):
            return_code = zhuogui_runner.main()
        return (
            return_code,
            self.protocol_results(output.getvalue()),
            guard,
            output.getvalue(),
        )

    def test_completed_result_has_stable_round_counts(self):
        state = zhuogui_runner.WorkflowState(completed_rounds=2, steps=24)
        result = zhuogui_runner.build_result("completed", state, 2)
        self.assertEqual(
            result,
            {
                "task_id": "zhuogui",
                "status": "completed",
                "rounds": 2,
                "target_rounds": 2,
                "steps": 24,
            },
        )

    def test_limit_result_preserves_partial_progress(self):
        state = zhuogui_runner.WorkflowState(
            completed_rounds=1,
            steps=20,
            last_ghost_progress=7,
        )
        result = zhuogui_runner.build_result(
            "limit_reached", state, 2, max_steps=20
        )
        self.assertEqual(result["rounds"], 1)
        self.assertEqual(result["target_rounds"], 2)
        self.assertEqual(result["ghost_progress"], 7)
        self.assertEqual(result["max_steps"], 20)

    def test_successful_main_emits_exactly_one_completed_result(self):
        def complete(args, _guard, state):
            state.completed_rounds = args.rounds
            state.steps = 24
            return zhuogui_runner.build_result("completed", state, args.rounds)

        return_code, results, _, _ = self.run_main(self.args(), complete)
        self.assertEqual(return_code, 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(results[0]["rounds"], 2)

    def test_exception_emits_exactly_one_safe_stop_result(self):
        def fail(_args, _guard, state):
            state.completed_rounds = 1
            state.steps = 9
            raise RuntimeError("连续三次无法识别当前画面，已停止")

        return_code, results, _, _ = self.run_main(self.args(), fail)
        self.assertEqual(return_code, 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "safe_stopped")
        self.assertEqual(results[0]["rounds"], 1)
        self.assertEqual(results[0]["target_rounds"], 2)
        self.assertEqual(results[0]["reason"], "连续三次无法识别当前画面，已停止")

    def test_cancellation_emits_exactly_one_cancelled_result(self):
        def cancel(_args, _guard, state):
            state.completed_rounds = 1
            state.steps = 12
            raise zhuogui_runner.CancellationRequested("收到 SIGTERM")

        return_code, results, guard, _ = self.run_main(self.args(), cancel)
        self.assertEqual(return_code, 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "cancelled")
        self.assertEqual(results[0]["rounds"], 1)
        guard.wait_for_tasker_stop.assert_called_once_with()

    def test_inspection_never_emits_task_result(self):
        def inspect(_args, _guard, _state):
            print('preflight_json={"decision":{"status":"main_screen"}}')
            return None

        return_code, results, _, output = self.run_main(
            self.args(inspect_scene=True), inspect
        )
        self.assertEqual(return_code, 0)
        self.assertEqual(results, [])
        self.assertIn("preflight_json=", output)

    def test_recognize_only_never_emits_task_result(self):
        def recognize(_args, _guard, _state):
            print('{"decision":{"status":"main_screen"}}')
            return None

        return_code, results, _, _ = self.run_main(
            self.args(recognize_only=True), recognize
        )
        self.assertEqual(return_code, 0)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
