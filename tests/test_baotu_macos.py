import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.baotu_macos_core import classify_baotu_scene
from tools.daily_activity_navigation_core import classify_daily_navigation
from tools.run_baotu_macos import run


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


class DailyNavigationTests(unittest.TestCase):
    def test_target_action_must_share_the_activity_card(self):
        target = text("宝图任务", (250, 180, 100, 25))
        correct = text("参加", (500, 210, 60, 25))
        other_column = text("参加", (900, 210, 60, 25))

        decision = classify_daily_navigation(
            daily_page(target, other_column, correct),
            {},
            (1020, 718),
            target_pattern=r"宝图任务",
            daily_tab_confirmed=True,
        )

        self.assertEqual(decision, {"status": "join_task", "box": correct["box"]})

    def test_chat_keyword_without_daily_page_is_not_actionable(self):
        decision = classify_daily_navigation(
            [text("世界[玩家]宝图任务参加", (200, 650, 300, 24))],
            {},
            (1020, 718),
            target_pattern=r"宝图任务",
        )
        self.assertEqual(decision["status"], "unknown")

    def test_main_activity_button_uses_strict_toolbar_region(self):
        decision = classify_daily_navigation(
            [text("活动", (260, 45, 50, 22))],
            {},
            (1020, 718),
            target_pattern=r"宝图任务",
        )
        self.assertEqual(decision["status"], "open_activity")

    def test_challenge_activity_page_must_open_daily_before_scanning(self):
        daily_tab = text("日常活动", (130, 161, 88, 27))
        decision = classify_daily_navigation(
            [
                text("活动", (474, 100, 71, 23)),
                daily_tab,
                text("挑战活动", (127, 217, 90, 27)),
                text("宝图任务", (669, 248, 84, 26)),
                text("参加", (838, 269, 47, 29)),
            ],
            {},
            (1020, 718),
            target_pattern=r"宝图任务",
            daily_tab_confirmed=False,
        )
        self.assertEqual(decision, {"status": "open_daily", "box": daily_tab["box"]})

    def test_confirmed_daily_tab_recovers_when_activity_title_is_missed(self):
        daily_tab = text("日常活动", (130, 161, 88, 27))
        target = text("师门任务", (670, 173, 83, 22))
        join = text("参加", (839, 193, 46, 24))
        decision = classify_daily_navigation(
            [daily_tab, target, join],
            {},
            (1020, 718),
            target_pattern=r"师门任务",
            daily_tab_confirmed=True,
        )
        self.assertEqual(decision, {"status": "join_task", "box": join["box"]})

    def test_confirmed_daily_tab_waits_while_content_is_loading(self):
        decision = classify_daily_navigation(
            [text("日常活动", (130, 161, 88, 27))],
            {},
            (1020, 718),
            target_pattern=r"师门任务",
            daily_tab_confirmed=True,
        )
        self.assertEqual(
            decision,
            {"status": "wait", "reason": "daily_page_loading"},
        )


class BaotuSceneTests(unittest.TestCase):
    def test_sidebar_task_and_dialogue_use_expected_regions(self):
        self.assertEqual(
            classify_baotu_scene(
                [text("宝图任务", (830, 180, 110, 25))], {}, (1020, 718)
            )["status"],
            "continue_task",
        )
        self.assertEqual(
            classify_baotu_scene(
                [text("听听无妨", (760, 300, 120, 30))], {}, (1020, 718)
            )["status"],
            "accept_dialogue",
        )

    def test_out_of_place_task_text_is_not_clicked(self):
        decision = classify_baotu_scene(
            [text("宝图任务", (100, 650, 110, 25))], {}, (1020, 718)
        )
        self.assertEqual(decision["status"], "unknown")

    def test_battle_and_pathing_are_wait_only(self):
        self.assertEqual(
            classify_baotu_scene([], {"battle": (1, 2, 3, 4)}, (1020, 718))["status"],
            "wait",
        )
        self.assertEqual(
            classify_baotu_scene([], {"auto_pathing": (1, 2, 3, 4)}, (1020, 718))["status"],
            "wait",
        )

    def test_current_battle_cancel_is_wait_only(self):
        decision = classify_baotu_scene(
            [text("取消", (961, 680, 44, 25))], {}, (1020, 718)
        )
        self.assertEqual(decision, {"status": "wait", "reason": "battle_ocr"})


class BaotuRunnerTests(unittest.TestCase):
    @patch("tools.run_baotu_macos.MacOSTaskRuntime")
    def test_bounded_flow_clicks_only_recognized_boxes(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.side_effect = [
            scene([text("活动", (260, 45, 50, 22))]),
            scene(daily_page(text("挑战活动", (120, 150, 100, 30)))),
            scene(daily_page(
                text("宝图任务", (250, 180, 100, 25)),
                text("参加", (500, 210, 60, 25)),
            )),
            scene([text("听听无妨", (760, 300, 120, 30))]),
            scene([text("宝图任务", (830, 180, 110, 25))]),
            scene([text("宝图任务完成", (420, 200, 180, 30))]),
        ]
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=8)
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(run(args, Mock()), 0)

        self.assertEqual(runtime.click_box.call_count, 5)
        clicked_boxes = [call.args[0] for call in runtime.click_box.call_args_list]
        self.assertEqual(clicked_boxes[1], [80, 90, 100, 30])
        self.assertEqual(clicked_boxes[2], [500, 210, 60, 25])
        payloads = [
            json.loads(line[len("result_json="):])
            for line in output.getvalue().splitlines()
            if line.startswith("result_json=")
        ]
        self.assertEqual(payloads[0]["status"], "completed")
        self.assertEqual(payloads[0]["clicks"], 5)

    @patch("tools.run_baotu_macos.MacOSTaskRuntime")
    def test_unknown_scene_stops_without_clicking(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = scene()
        args = SimpleNamespace(inspect_scene=False, recognize_only=False, max_steps=4)

        with self.assertRaisesRegex(RuntimeError, "连续三次"):
            run(args, Mock())
        runtime.click_box.assert_not_called()
        runtime.drag.assert_not_called()


if __name__ == "__main__":
    unittest.main()
