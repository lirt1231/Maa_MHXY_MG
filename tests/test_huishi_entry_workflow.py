import unittest
from types import SimpleNamespace

from tools.automation_workflows import WorkflowContext, WorkflowResult
from tools.huishi_entry_workflow import (
    HuishiEntryWorkflow,
    classify_huishi_entry_scene,
    classify_keju_entry_scene,
)


def item(text, box):
    return {"text": text, "box": list(box)}


def scene(*texts):
    return SimpleNamespace(image_size=(1020, 718), texts=list(texts), templates={})


class Adapter:
    def __init__(self, scenes):
        self.scenes = list(scenes)
        self.trace = []

    def read_scene(self, _templates=None):
        return self.scenes.pop(0)

    def click_box(self, box, current, reason):
        self.trace.append(("click", tuple(box), reason))

    def click(self, point, current, reason):
        self.trace.append(("click", tuple(point), reason))

    def drag(self, start, end, current, reason):
        self.trace.append(("drag", reason))

    def input_text(self, text, current, reason):
        raise AssertionError("unexpected text input")

    def wait(self, seconds):
        self.trace.append(("wait", seconds))


class HuishiEntryWorkflowTests(unittest.TestCase):
    def test_enters_from_main_and_waits_for_exact_huishi_title(self):
        main = scene(item("活动", (260, 45, 50, 22)))
        activity = scene(item("活动", (450, 30, 80, 30)), item("日常活动", (80, 90, 100, 30)))
        daily = scene(
            item("活动", (450, 30, 80, 30)), item("日常活动", (80, 90, 100, 30)),
            item("科举会试", (250, 180, 100, 25)), item("参加", (500, 210, 60, 25)),
        )
        transition = scene(item("活动", (260, 45, 50, 22)))
        exam = scene(item("科举会试", (430, 120, 130, 30)))
        adapter = Adapter([main, activity, daily, transition, exam])

        result = HuishiEntryWorkflow().execute(WorkflowContext(adapter, adapter))

        self.assertIsInstance(result, WorkflowResult)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload, {"actions": 3, "clicks": 3})
        self.assertEqual([event[0] for event in adapter.trace].count("click"), 3)

    def test_off_card_join_button_never_causes_a_click(self):
        current = scene(
            item("活动", (450, 30, 80, 30)), item("日常活动", (80, 90, 100, 30)),
            item("科举会试", (250, 180, 100, 25)), item("参加", (900, 210, 60, 25)),
        )
        adapter = Adapter([current, current, current, current])
        with self.assertRaisesRegex(RuntimeError, "连续三帧"):
            HuishiEntryWorkflow().execute(WorkflowContext(adapter, adapter))
        self.assertEqual([event[0] for event in adapter.trace].count("click"), 1)

    def test_wrong_exam_type_stops_without_input(self):
        adapter = Adapter([scene(item("科举乡试", (430, 120, 130, 30)))])
        with self.assertRaisesRegex(RuntimeError, "乡试"):
            HuishiEntryWorkflow().execute(WorkflowContext(adapter, adapter))
        self.assertEqual(adapter.trace, [])

    def test_classifier_requires_title_in_title_band(self):
        decision = classify_huishi_entry_scene(
            [item("科举会试", (330, 260, 120, 30))], {}, (1020, 718)
        )
        self.assertEqual(decision["status"], "unknown")

    def test_xiangshi_entry_uses_its_own_activity_row_and_title(self):
        main = scene(item("活动", (260, 45, 50, 22)))
        activity = scene(item("活动", (450, 30, 80, 30)), item("日常活动", (80, 90, 100, 30)))
        daily = scene(
            item("活动", (450, 30, 80, 30)), item("日常活动", (80, 90, 100, 30)),
            item("科举乡试", (250, 180, 100, 25)), item("参加", (500, 210, 60, 25)),
        )
        exam = scene(item("科举乡试", (430, 120, 130, 30)))
        adapter = Adapter([main, activity, daily, exam])
        result = HuishiEntryWorkflow(exam_type="xiangshi").execute(WorkflowContext(adapter, adapter))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload, {"actions": 3, "clicks": 3})
        self.assertEqual(
            classify_keju_entry_scene(exam.texts, {}, exam.image_size, exam_type="xiangshi")["status"],
            "exam_ready",
        )

    def test_dianshi_entry_requires_its_own_title(self):
        exam = scene(item("科举殿试", (430, 120, 130, 30)))

        self.assertEqual(
            classify_keju_entry_scene(
                exam.texts, {}, exam.image_size, exam_type="dianshi"
            )["status"],
            "exam_ready",
        )
        self.assertEqual(
            classify_keju_entry_scene(
                exam.texts, {}, exam.image_size, exam_type="huishi"
            )["status"],
            "wrong_exam_type",
        )


if __name__ == "__main__":
    unittest.main()
