import json
import unittest

from dashboard.runner import RunnerManager


class ShimenDashboardPreflightTests(unittest.TestCase):
    def test_dialogue_and_sidebar_start_scenes_are_ready(self):
        scenes = {
            "accept_task_dialogue": "选择经过识别的师门 NPC 任务",
            "start_task": "点击右侧师门任务正式开始",
        }

        for status, expected_action in scenes.items():
            payload = {
                "image_size": [1020, 718],
                "decision": {"status": status, "box": [800, 160, 150, 20]},
                "texts": [],
                "templates": {},
            }
            output = "preflight_json=" + json.dumps(payload, ensure_ascii=False)

            with self.subTest(status=status):
                result = RunnerManager._parse_inspection("shimen", {}, output)
                self.assertTrue(result["canStart"])
                self.assertEqual(result["proposedAction"], expected_action)


if __name__ == "__main__":
    unittest.main()
