import unittest

from tools.daily_activity_scroll import (
    DailyActivityScrollAction,
    DailyActivityScrollExhausted,
    activity_view_signature,
)


IMAGE_SIZE = (1020, 718)


def text(value, box):
    return {"text": value, "box": list(box)}


def activity_view(offset=0):
    return [
        text("宝图任务", (280, 180 + offset, 100, 25)),
        text("参加", (500, 210 + offset, 60, 25)),
        text("世界[玩家]聊天内容", (10, 650, 250, 24)),
    ]


class DailyActivityScrollTests(unittest.TestCase):
    def test_signature_tracks_list_movement_and_ignores_chat(self):
        first = activity_view_signature(activity_view(), IMAGE_SIZE)
        moved = activity_view_signature(activity_view(-80), IMAGE_SIZE)
        noisy = activity_view_signature(
            activity_view() + [text("系统新消息", (10, 680, 200, 24))],
            IMAGE_SIZE,
        )

        self.assertNotEqual(first, moved)
        self.assertEqual(first, noisy)

    def test_uses_small_downward_search_drag(self):
        action = DailyActivityScrollAction()

        step = action.plan(activity_view(), IMAGE_SIZE)

        self.assertEqual(step.direction, "down")
        self.assertGreater(step.start[1], step.end[1])
        self.assertLessEqual(step.start[1] - step.end[1], round(IMAGE_SIZE[1] * 0.12))

    def test_repeated_down_view_reverses_to_small_upward_drag(self):
        action = DailyActivityScrollAction()
        action.plan(activity_view(), IMAGE_SIZE)

        step = action.plan(activity_view(), IMAGE_SIZE)

        self.assertEqual(step.direction, "up")
        self.assertTrue(step.reversed)
        self.assertLess(step.start[1], step.end[1])
        self.assertIn("未继续移动", step.reason)

    def test_repeated_view_in_both_directions_stops_cycle(self):
        action = DailyActivityScrollAction()
        action.plan(activity_view(), IMAGE_SIZE)
        action.plan(activity_view(), IMAGE_SIZE)

        with self.assertRaisesRegex(DailyActivityScrollExhausted, "双向搜索完毕"):
            action.plan(activity_view(), IMAGE_SIZE)

    def test_empty_ocr_still_has_bounded_bidirectional_search(self):
        action = DailyActivityScrollAction(max_down_steps=2, max_up_steps=2)

        directions = [action.plan([], IMAGE_SIZE).direction for _ in range(4)]

        self.assertEqual(directions, ["down", "down", "up", "up"])
        with self.assertRaisesRegex(DailyActivityScrollExhausted, "双向搜索完毕"):
            action.plan([], IMAGE_SIZE)

    def test_reset_starts_a_fresh_downward_search(self):
        action = DailyActivityScrollAction(max_down_steps=1, max_up_steps=1)
        action.plan([], IMAGE_SIZE)
        self.assertEqual(action.plan([], IMAGE_SIZE).direction, "up")

        action.reset()

        self.assertEqual(action.plan([], IMAGE_SIZE).direction, "down")


if __name__ == "__main__":
    unittest.main()
