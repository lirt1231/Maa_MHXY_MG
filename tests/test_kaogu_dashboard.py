import json
import tempfile
import unittest
from pathlib import Path

from dashboard.database import Database
from dashboard.events import EventBroker
from dashboard.registry import TASKS, task_list, validate_options
from dashboard.runner import RunnerManager
from dashboard.user_progress import user_progress


class KaoguDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.manager = RunnerManager(
            Database(Path(self.temporary_directory.name) / "dashboard.sqlite3"),
            EventBroker(),
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_registry_discloses_purchase_and_hard_limits(self):
        self.assertIn("kaogu", {task["id"] for task in task_list()})
        self.assertEqual(validate_options("kaogu", {}), {})
        task = TASKS["kaogu"]
        self.assertEqual(task["risk"]["level"], "medium")
        self.assertTrue(any("购买一个洛阳铲" in item for item in task["checklist"]))
        self.assertTrue(any("购买硬限制为一次" in item for item in task["warnings"]))
        self.assertTrue(any("挖掘硬限制为 10 次" in item for item in task["warnings"]))
        self.assertTrue(any("不会出售" in item for item in task["warnings"]))

    def test_runner_command_and_inspection_are_registered(self):
        command = self.manager._command("kaogu", {})
        inspection = self.manager._inspection_command("kaogu", {})

        self.assertTrue(command[-1].endswith("tools/run_kaogu_macos.py"))
        self.assertEqual(len(command), 2)
        self.assertTrue(inspection[1].endswith("tools/run_kaogu_macos.py"))
        self.assertEqual(inspection[2:], ["--inspect-scene"])

    def test_result_contract_requires_ten_digs_and_one_purchase_at_most(self):
        base = {
            "task_id": "kaogu", "status": "completed", "digs": 10,
            "target_digs": 10, "purchases": 1, "actions": 17, "clicks": 17,
        }
        parsed = self.manager._parse_task_result(
            "kaogu", ["result_json=" + json.dumps(base)]
        )
        self.assertEqual(parsed, base)

        with self.assertRaisesRegex(RuntimeError, "购买次数超过上限"):
            self.manager._parse_task_result(
                "kaogu", ["result_json=" + json.dumps({**base, "purchases": 2})]
            )
        with self.assertRaisesRegex(RuntimeError, "完成次数与目标次数不一致"):
            self.manager._parse_task_result(
                "kaogu", ["result_json=" + json.dumps({**base, "digs": 9})]
            )

    def test_preflight_and_live_progress_use_user_facing_copy(self):
        output = (
            'preflight_json={"image_size":[1280,720],'
            '"decision":{"status":"open_skills","box":[1048,638,44,39]},'
            '"proposed_action":"open_skills","texts":[],"templates":{}}'
        )
        inspection = self.manager._parse_inspection("kaogu", {}, output)
        progress = user_progress(
            "kaogu", '{"event":"scene","status":"buy_shovel"}'
        )

        self.assertTrue(inspection["canStart"])
        self.assertEqual(inspection["proposedAction"], "打开技能页面")
        self.assertEqual(progress["message"], "正在购买一个洛阳铲")


if __name__ == "__main__":
    unittest.main()
