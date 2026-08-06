import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import Mock

from tools.automation_actions import (
    ActionBudget,
    ActionContext,
    ActionMetrics,
    EvidenceClickAction,
    EvidenceDoubleClickAction,
    EvidenceThenRelativeClickAction,
    RelativeClickAction,
    TextInputAction,
)
from tools.daily_activity_actions import DailyActivityActions


def context(status, *, box=(100, 120, 80, 30), texts=None):
    runtime = Mock()
    guard = Mock()
    scene = SimpleNamespace(
        image_size=(1020, 718),
        texts=texts or [],
        templates={},
    )
    decision = {"status": status}
    if box is not None:
        decision["box"] = list(box)
    return ActionContext(runtime, guard, scene, decision, "测试任务")


class PlatformNeutralEvidenceClickActionTests(unittest.TestCase):
    def test_text_input_action_uses_platform_neutral_adapter(self):
        current = context("text_focused")
        action = TextInputAction(
            name="clear_description",
            statuses=("text_focused",),
            text="",
            reason="清空描述",
            wait_seconds=0.5,
        )

        result = action.execute(current)

        current.input.input_text.assert_called_once_with(
            "", current.scene, "清空描述"
        )
        current.waiter.wait.assert_called_once_with(0.5)
        self.assertEqual(result.actions, 1)
        self.assertEqual((result.clicks, result.drags), (0, 0))

    def test_same_action_contract_accepts_independent_platform_adapters(self):
        @dataclass
        class RecordingInput:
            platform: str
            calls: list = field(default_factory=list)

            def click_box(self, box, scene, reason):
                self.calls.append(("click", box, scene.image_size, reason))

            def click(self, point, scene, reason):
                self.calls.append(("click", point, scene.image_size, reason))

            def drag(self, start, end, scene, reason):
                self.calls.append(("drag", start, end, scene.image_size, reason))

        class RecordingWaiter:
            def __init__(self):
                self.calls = []

            def wait(self, seconds):
                self.calls.append(seconds)

        outcomes = []
        for platform in ("desktop-a", "desktop-b"):
            driver = RecordingInput(platform)
            waiter = RecordingWaiter()
            scene = SimpleNamespace(image_size=(1280, 720), texts=[], templates={})
            current = ActionContext(
                driver,
                waiter,
                scene,
                {"status": "open", "box": [100, 120, 80, 30]},
                "测试任务",
            )
            action = EvidenceClickAction(
                name="open_panel",
                statuses=("open",),
                reason="打开面板",
                wait_seconds=1.0,
            )

            result = action.execute(current)
            outcomes.append((result, driver.calls, waiter.calls))

        self.assertEqual(outcomes[0], outcomes[1])

    def test_relative_click_scales_from_live_scene_size(self):
        outcomes = []

        for image_size in ((1020, 718), (1530, 1077)):
            action = RelativeClickAction(
                name="expand_menu",
                statuses=("main_menu_collapsed",),
                point_ratio=(0.95, 0.92),
                reason="展开右下角菜单",
                wait_seconds=0,
            )
            current = context("main_menu_collapsed")
            current.scene.image_size = image_size

            action.execute(current)
            outcomes.append(current.input.click.call_args.args[0])

        self.assertEqual(outcomes, [(969, 661), (1454, 991)])

    def test_relative_click_stops_when_evidence_does_not_change(self):
        action = RelativeClickAction(
            name="expand_menu",
            statuses=("main_menu_collapsed",),
            point_ratio=(0.95, 0.92),
            reason="展开右下角菜单",
            wait_seconds=0,
            repeat_limit=1,
        )
        current = context("main_menu_collapsed")

        action.execute(current)
        with self.assertRaisesRegex(RuntimeError, "连续重复执行"):
            action.execute(current)

        current.input.click.assert_called_once()

    def test_evidence_then_relative_click_runs_before_transient_menu_closes(self):
        action = EvidenceThenRelativeClickAction(
            name="open_skills_from_main_menu",
            statuses=("expand_menu",),
            point_ratio=(0.849, 0.94),
            first_reason="展开主界面快捷菜单",
            second_reason="打开技能页面",
            between_seconds=1.2,
            wait_seconds=1.0,
            repeat_limit=1,
        )
        current = context("expand_menu", box=(967, 659, 40, 39))
        calls = []
        current.input.click_box.side_effect = lambda *args: calls.append(
            ("click_box", args)
        )
        current.input.click.side_effect = lambda *args: calls.append(("click", args))
        current.waiter.wait.side_effect = lambda seconds: calls.append(("wait", seconds))

        result = action.execute(current)

        self.assertEqual(
            [entry[0] for entry in calls],
            ["click_box", "wait", "click", "wait"],
        )
        self.assertEqual(calls[2][1][0], (866, 675))
        self.assertEqual((calls[1][1], calls[3][1]), (1.2, 1.0))
        self.assertEqual((result.actions, result.clicks), (1, 2))

    def test_click_requires_fresh_valid_evidence_box(self):
        action = EvidenceClickAction(
            name="open_panel",
            statuses=("open",),
            reason="打开面板",
            wait_seconds=1.0,
        )
        missing = context("open", box=None)

        with self.assertRaisesRegex(RuntimeError, "有效点击区域"):
            action.execute(missing)

        missing.input.click_box.assert_not_called()
        missing.waiter.wait.assert_not_called()

    def test_double_click_is_one_bounded_action_and_two_clicks(self):
        action = EvidenceDoubleClickAction(
            name="activate_item",
            statuses=("antique",),
            reason="双击古董",
            click_interval_seconds=0.12,
            wait_seconds=0.8,
            repeat_limit=1,
        )
        current = context("antique")

        result = action.execute(current)

        self.assertEqual(current.input.click_box.call_count, 2)
        self.assertEqual(
            current.waiter.wait.call_args_list,
            [unittest.mock.call(0.12), unittest.mock.call(0.8)],
        )
        self.assertEqual((result.actions, result.clicks), (1, 2))
        with self.assertRaisesRegex(RuntimeError, "连续重复执行"):
            action.execute(current)
        self.assertEqual(current.input.click_box.call_count, 2)

    def test_repeated_evidence_stops_before_extra_click(self):
        action = EvidenceClickAction(
            name="open_panel",
            statuses=("open",),
            reason="打开面板",
            wait_seconds=0.0,
            repeat_limit=2,
        )
        current = context("open")

        action.execute(current)
        action.execute(current)
        with self.assertRaisesRegex(RuntimeError, "连续重复执行"):
            action.execute(current)

        self.assertEqual(current.input.click_box.call_count, 2)

    def test_shared_budget_applies_across_distinct_actions(self):
        budget = ActionBudget(1, "导航预算已用完")
        first = EvidenceClickAction(
            name="first",
            statuses=("first",),
            reason="第一步",
            wait_seconds=0,
            budget=budget,
        )
        second = EvidenceClickAction(
            name="second",
            statuses=("second",),
            reason="第二步",
            wait_seconds=0,
            budget=budget,
        )

        first.execute(context("first"))
        second_context = context("second")
        with self.assertRaisesRegex(RuntimeError, "导航预算已用完"):
            second.execute(second_context)

        second_context.input.click_box.assert_not_called()


class DailyActivityActionsTests(unittest.TestCase):
    def test_daily_navigation_owns_page_state_and_semantic_reasons(self):
        actions = DailyActivityActions("宝图任务")
        metrics = ActionMetrics()

        opened_activity = context("open_activity")
        metrics.record(actions.execute(opened_activity))
        self.assertFalse(actions.daily_tab_confirmed)
        self.assertEqual(
            opened_activity.input.click_box.call_args.args[2], "打开活动面板"
        )

        opened_daily = context("open_daily")
        metrics.record(actions.execute(opened_daily))
        self.assertTrue(actions.daily_tab_confirmed)
        self.assertEqual(
            opened_daily.input.click_box.call_args.args[2], "打开日常活动页签"
        )

        joined = context("join_task")
        metrics.record(actions.execute(joined))
        self.assertFalse(actions.daily_tab_confirmed)
        self.assertEqual(joined.input.click_box.call_args.args[2], "参加测试任务")
        self.assertEqual((metrics.actions, metrics.clicks, metrics.drags), (3, 3, 0))

    def test_scroll_is_a_counted_atomic_drag(self):
        actions = DailyActivityActions("宝图任务")
        current = context(
            "scroll_daily",
            box=None,
            texts=[{"text": "师门任务", "box": [300, 180, 100, 25]}],
        )

        result = actions.execute(current)

        self.assertEqual((result.actions, result.clicks, result.drags), (1, 0, 1))
        current.input.drag.assert_called_once()
        current.input.click_box.assert_not_called()
        current.waiter.wait.assert_called_once_with(1.2)

    def test_unrelated_status_is_not_consumed(self):
        actions = DailyActivityActions("宝图任务")
        current = context("battle", box=None)

        self.assertIsNone(actions.execute(current))
        current.input.click_box.assert_not_called()
        current.input.drag.assert_not_called()


if __name__ == "__main__":
    unittest.main()
