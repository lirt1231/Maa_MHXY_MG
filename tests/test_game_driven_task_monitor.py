import unittest
from unittest.mock import Mock

from tools.automation_workflows import WorkflowContext
from tools.game_driven_task_monitor import (
    GameDrivenTaskMonitor,
    StableMainCompletionTracker,
)


class GameDrivenTaskMonitorTests(unittest.TestCase):
    def context(self):
        runtime = Mock()
        waiter = Mock()
        events = []
        context = WorkflowContext(
            runtime,
            waiter,
            lambda event, **payload: events.append((event, payload)),
        )
        return context, runtime, waiter, events

    def test_active_monitor_waits_and_never_sends_input(self):
        context, runtime, waiter, events = self.context()
        monitor = GameDrivenTaskMonitor("宝图任务", 5, 3)

        monitor.activate(context, "任务已接取")
        self.assertTrue(monitor.observe(context, "continue_task"))

        runtime.click.assert_not_called()
        runtime.click_box.assert_not_called()
        runtime.drag.assert_not_called()
        runtime.input_text.assert_not_called()
        waiter.wait.assert_called_once_with(2.0)
        self.assertEqual(events[0][0], "game_auto_monitor_started")
        self.assertEqual(events[1][0], "game_auto_monitor")

    def test_stable_main_completion_requires_prior_task_activity(self):
        tracker = StableMainCompletionTracker(required_polls=3)

        self.assertFalse(tracker.observe("open_activity"))
        self.assertFalse(tracker.observe("open_activity"))
        self.assertFalse(tracker.observe("continue_task"))
        self.assertFalse(tracker.observe("open_activity"))
        self.assertFalse(tracker.observe("open_activity"))
        self.assertTrue(tracker.observe("open_activity"))

    def test_stable_main_completion_resets_when_task_resumes(self):
        tracker = StableMainCompletionTracker(required_polls=3)

        tracker.observe("wait", "battle")
        tracker.observe("open_activity")
        tracker.observe("open_activity")
        self.assertFalse(tracker.observe("wait", "auto_pathing"))
        self.assertEqual(tracker.main_screen_polls, 0)
        self.assertFalse(tracker.observe("open_activity"))

    def test_unknown_budget_stops_without_input(self):
        context, runtime, _waiter, _events = self.context()
        monitor = GameDrivenTaskMonitor("师门任务", 8, 2)
        monitor.activate(context, "已交接")
        monitor.observe(context, "unknown")

        with self.assertRaisesRegex(RuntimeError, "连续无法识别"):
            monitor.observe(context, "unknown")
        runtime.click_box.assert_not_called()

    def test_total_observation_budget_requires_explicit_completion(self):
        context, runtime, _waiter, _events = self.context()
        monitor = GameDrivenTaskMonitor("宝图任务", 2, 2)
        monitor.activate(context, "已交接")
        monitor.observe(context, "wait", "battle")

        with self.assertRaisesRegex(RuntimeError, "尚无明确完成证据"):
            monitor.observe(context, "continue_task")
        runtime.click_box.assert_not_called()


if __name__ == "__main__":
    unittest.main()
