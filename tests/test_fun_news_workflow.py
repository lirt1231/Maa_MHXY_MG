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

    def test_like_requires_the_dedicated_heart_template(self):
        decision = classify_fun_news_scene(
            [],
            {"fun_news_like": (180, 400, 76, 24)},
            (1020, 718),
        )

        self.assertEqual(decision, {"status": "like", "box": [180, 400, 76, 24]})

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
        runtime = Runtime([
            scene(templates=like),
            scene(templates=like),
            scene(templates=like),
            scene(templates=like),
            scene(templates=like),
            scene(templates=like),
            scene(templates=like),
            scene(templates=like),
            scene(templates=like),
            scene(templates=completed),
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

    def test_five_likes_require_completion_evidence_before_success(self):
        like = {"fun_news_like": (180, 400, 76, 24)}
        runtime = Runtime([scene(templates=like) for _ in range(13)])

        with self.assertRaisesRegex(RuntimeError, "已点赞 5 条动态"):
            FunNewsWorkflow(max_steps=13, max_completion_polls=4).execute(
                self.context(runtime)
            )

        self.assertEqual(
            len([entry for entry in runtime.trace if entry[0] == "click"]),
            5,
        )


if __name__ == "__main__":
    unittest.main()
