import unittest
from types import SimpleNamespace

from tools.automation_workflows import WorkflowContext
from tools.fun_news_recognition import classify_fun_news_scene
from tools.fun_news_workflow import FunNewsWorkflow


def text(value, box=(10, 20, 80, 24)):
    return {"text": value, "box": list(box)}


def scene(*, texts=None, templates=None):
    return SimpleNamespace(
        image_size=(1020, 718),
        texts=texts or [],
        templates=templates or {},
    )


def verified_feed_texts():
    return [
        *[text(channel, (20, 110 + index * 40, 48, 25)) for index, channel in enumerate(
            ("系统", "世界", "当前", "门派", "帮派", "队伍", "空间")
        )],
        text("打开空间", (15, 75, 58, 16)),
        text("关注", (95, 66, 39, 19)), text("图文", (136, 65, 50, 21)),
        text("视频", (259, 68, 36, 15)), text("论坛", (302, 66, 39, 19)),
        text("+关注", (359, 118, 52, 20)),
    ]


class Runtime:
    def __init__(self, scenes):
        self.scenes = iter(scenes)
        self.trace = []

    def read_scene(self, _templates):
        return next(self.scenes)

    def click_box(self, box, _scene, reason):
        self.trace.append(("click", tuple(box), reason))

    def drag(self, start, end, _scene, reason):
        self.trace.append(("drag", start, end, reason))

    def wait(self, seconds):
        self.trace.append(("wait", seconds))


class FunNewsRecognitionTests(unittest.TestCase):
    def test_daily_card_uses_same_card_join_button(self):
        target = text("趣闻鉴赏", (250, 180, 100, 25))
        join = text("参加", (500, 210, 60, 25))

        decision = classify_fun_news_scene(
            [text("活动", (450, 30, 80, 30)), text("日常活动", (80, 90, 100, 30)), target, join],
            {},
            (1020, 718),
            daily_tab_confirmed=True,
        )

        self.assertEqual(decision, {"status": "join_task", "box": join["box"]})

    def test_like_uses_only_the_heart_region_of_the_dedicated_template(self):
        decision = classify_fun_news_scene(
            [],
            {"fun_news_like": (180, 400, 76, 24)},
            (1020, 718),
        )

        self.assertEqual(
            decision,
            {"status": "like", "box": [180, 400, 36, 24], "source": "template"},
        )

    def test_live_space_page_never_clicks_an_ocr_heart_without_template_evidence(self):
        decision = classify_fun_news_scene(
            [
                *[text(channel, (20, 110 + index * 40, 48, 25)) for index, channel in enumerate(("系统", "世界", "当前", "门派", "帮派", "队伍", "空间"))],
                text("打开空间", (15, 75, 58, 16)),
                text("关注", (95, 66, 39, 19)), text("图文", (136, 65, 50, 21)),
                text("视频", (259, 68, 36, 15)), text("论坛", (302, 66, 39, 19)),
                text("+关注", (359, 118, 52, 20)), text("心", (178, 495, 32, 7)),
            ],
            {},
            (923, 720),
        )

        self.assertEqual(decision, {"status": "refresh_feed"})

    def test_live_space_page_never_derives_a_heart_from_counter_coordinates(self):
        decision = classify_fun_news_scene(
            [
                *[text(channel, (20, 110 + index * 40, 48, 25)) for index, channel in enumerate(("系统", "世界", "当前", "门派", "帮派", "队伍", "空间"))],
                text("打开空间", (15, 75, 58, 16)),
                text("关注", (96, 68, 40, 15)), text("造梦星图文视频", (134, 63, 164, 23)),
                text("页论坛手*", (288, 65, 101, 20)), text("十关注", (359, 134, 52, 21)),
                text("3.1万", (160, 308, 43, 19)), text("84", (255, 310, 19, 16)),
            ],
            {},
            (923, 720),
        )

        self.assertEqual(decision, {"status": "refresh_feed"})

    def test_template_evidence_wins_over_misleading_feed_ocr(self):
        decision = classify_fun_news_scene(
            [
                *[text(channel, (20, 110 + index * 40, 48, 25)) for index, channel in enumerate(("系统", "世界", "当前", "门派", "帮派", "队伍", "空间"))],
                text("打开空间", (15, 75, 58, 16)),
                text("关注", (95, 66, 39, 19)), text("图文", (136, 65, 50, 21)),
                text("视频", (259, 68, 36, 15)), text("论坛", (302, 66, 39, 19)),
                text("心", (178, 495, 32, 7)),
            ],
            {"fun_news_like": (180, 400, 76, 24)},
            (923, 720),
        )

        self.assertEqual(
            decision,
            {"status": "like", "box": [180, 400, 36, 24], "source": "template"},
        )

    def test_dedicated_heart_template_uses_its_exact_click_box(self):
        decision = classify_fun_news_scene(
            [],
            {
                "fun_news_like": (180, 400, 76, 24),
                "fun_news_heart": (146, 220, 50, 50),
            },
            (1020, 718),
        )

        self.assertEqual(
            decision,
            {"status": "like", "box": [146, 220, 50, 50], "source": "heart_template"},
        )

    def test_completed_template_wins_over_visible_like(self):
        self.assertEqual(
            classify_fun_news_scene(
                [],
                {
                    "fun_news_complete": (320, 120, 300, 80),
                    "fun_news_like": (180, 400, 76, 24),
                },
                (1020, 718),
            ),
            {"status": "completed"},
        )


class FunNewsWorkflowTests(unittest.TestCase):
    @staticmethod
    def context(runtime):
        return WorkflowContext(runtime, runtime)

    def test_likes_five_distinct_feed_entries_with_scrolls_between_them(self):
        like = {"fun_news_like": (180, 400, 76, 24)}
        completed = {"fun_news_complete": (320, 120, 300, 80)}
        feed = lambda number: scene(
            texts=[text(f"第 {number} 条趣闻动态", (160, 240, 160, 24))],
            templates=like,
        )
        runtime = Runtime([
            feed(1), feed(1), feed(2), feed(2), feed(3), feed(3),
            feed(4), feed(4), feed(5), scene(templates=completed),
        ])

        result = FunNewsWorkflow(max_steps=10).execute(self.context(runtime))

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            result.payload,
            {"likes": 5, "scrolls": 4, "actions": 9, "clicks": 5},
        )
        self.assertEqual(
            [entry[0] for entry in runtime.trace if entry[0] in {"click", "drag"}],
            ["click", "drag", "click", "drag", "click", "drag", "click", "drag", "click"],
        )

    def test_already_completed_has_zero_input(self):
        runtime = Runtime([scene(templates={"fun_news_complete": (320, 120, 300, 80)})])

        result = FunNewsWorkflow().execute(self.context(runtime))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["likes"], 0)
        self.assertFalse(any(entry[0] in {"click", "drag"} for entry in runtime.trace))

    def test_five_verified_likes_are_the_completion_evidence(self):
        like = {"fun_news_like": (180, 400, 76, 24)}
        feed = lambda number: scene(
            texts=[text(f"第 {number} 条趣闻动态", (160, 240, 160, 24))],
            templates=like,
        )
        runtime = Runtime([
            feed(1), feed(1), feed(2), feed(2), feed(3), feed(3),
            feed(4), feed(4), feed(5), scene(templates={"fun_news_complete": (320, 120, 300, 80)}),
        ])

        result = FunNewsWorkflow(max_steps=10).execute(self.context(runtime))

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            len([entry for entry in runtime.trace if entry[0] == "click"]),
            5,
        )

    def test_clicks_the_heart_portion_not_the_template_center(self):
        runtime = Runtime([
            scene(templates={"fun_news_like": (180, 400, 76, 24)}),
            scene(templates={"fun_news_complete": (320, 120, 300, 80)}),
        ])

        result = FunNewsWorkflow(target_likes=1).execute(self.context(runtime))

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            [entry for entry in runtime.trace if entry[0] == "click"],
            [("click", (180, 400, 36, 24), "点赞当前趣闻动态")],
        )

    def test_requires_activity_page_completion_after_the_final_like(self):
        runtime = Runtime([
            scene(templates={"fun_news_like": (180, 400, 76, 24)}),
            scene(
                texts=[*verified_feed_texts(), text("第 1 条趣闻动态", (160, 240, 160, 24))],
                templates={"fun_news_feed_close": (470, 290, 32, 76)},
            ),
            scene(templates={"fun_news_complete": (320, 120, 300, 80)}),
        ])

        result = FunNewsWorkflow(target_likes=1).execute(self.context(runtime))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload, {"likes": 1, "scrolls": 0, "actions": 2, "clicks": 2})
        self.assertEqual(
            [entry[1] for entry in runtime.trace if entry[0] == "click"],
            [(180, 400, 36, 24), (470, 290, 32, 76)],
        )

    def test_stops_when_final_likes_cannot_be_confirmed_in_the_activity_page(self):
        runtime = Runtime([
            scene(templates={"fun_news_like": (180, 400, 76, 24)}),
            *[scene(texts=[text("动态仍在加载", (160, 240, 160, 24))]) for _ in range(3)],
        ])

        with self.assertRaisesRegex(RuntimeError, "未能在活动页面确认趣闻鉴赏已完成"):
            FunNewsWorkflow(
                target_likes=1, max_completion_verification_polls=3,
            ).execute(self.context(runtime))

    def test_unchanged_feed_after_scroll_never_receives_a_second_like(self):
        like = {"fun_news_like": (180, 400, 76, 24)}
        current = scene(
            texts=[text("同一条趣闻动态", (160, 240, 160, 24))],
            templates=like,
        )
        runtime = Runtime([current, current, current, current, current])

        with self.assertRaisesRegex(RuntimeError, "同一条趣闻动态"):
            FunNewsWorkflow(max_steps=5).execute(self.context(runtime))

        self.assertEqual(
            len([entry for entry in runtime.trace if entry[0] == "click"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
