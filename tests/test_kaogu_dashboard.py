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
        self.assertEqual(validate_options("kaogu", {}), {"toolMode": "selected"})
        self.assertEqual(
            validate_options("kaogu", {"toolMode": "luoyang_shovel"}),
            {"toolMode": "luoyang_shovel"},
        )
        task = TASKS["kaogu"]
        self.assertEqual(task["risk"]["level"], "medium")
        self.assertTrue(any("购买一个洛阳铲" in item for item in task["checklist"]))
        self.assertTrue(any("购买硬限制为一次" in item for item in task["warnings"]))
        self.assertTrue(any("跨进程保留" in item for item in task["warnings"]))
        self.assertTrue(any("挖掘硬限制为 10 次" in item for item in task["warnings"]))
        self.assertTrue(any("非真品古董" in item for item in task["warnings"]))
        self.assertTrue(any("绝不会确认出售" in item for item in task["warnings"]))

    def test_runner_command_and_inspection_are_registered(self):
        command = self.manager._command("kaogu", {"toolMode": "selected"})
        inspection = self.manager._inspection_command(
            "kaogu", {"toolMode": "selected"}
        )

        self.assertTrue(command[1].endswith("tools/run_kaogu_macos.py"))
        self.assertEqual(command[2:], ["--tool-mode", "selected"])
        self.assertTrue(inspection[1].endswith("tools/run_kaogu_macos.py"))
        self.assertEqual(
            inspection[2:],
            ["--inspect-scene", "--tool-mode", "selected"],
        )

        with self.assertRaisesRegex(ValueError, "选项无效"):
            validate_options("kaogu", {"toolMode": "unknown"})

    def test_result_contract_requires_ten_digs_and_one_purchase_at_most(self):
        base = {
            "task_id": "kaogu", "status": "completed", "digs": 10,
            "target_digs": 10, "purchases": 1, "sale_clicks": 7,
            "sale_batches": 3, "drags": 29,
            "sale_outcome": "all_non_genuine_sold_genuine_retained",
            "genuine_protected": True, "actions": 24, "clicks": 25,
        }
        parsed = self.manager._parse_task_result(
            "kaogu", ["result_json=" + json.dumps(base)]
        )
        self.assertEqual(parsed, base)

        with self.assertRaisesRegex(RuntimeError, "购买次数超过上限"):
            self.manager._parse_task_result(
                "kaogu", ["result_json=" + json.dumps({**base, "purchases": 3})]
            )
        with self.assertRaisesRegex(RuntimeError, "完成次数与目标次数不一致"):
            self.manager._parse_task_result(
                "kaogu", ["result_json=" + json.dumps({**base, "digs": 9})]
            )
        with self.assertRaisesRegex(RuntimeError, "未确认真品保护"):
            self.manager._parse_task_result(
                "kaogu",
                ["result_json=" + json.dumps({**base, "genuine_protected": False})],
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

        selling = user_progress(
            "kaogu", '{"event":"scene","status":"sell_antique"}'
        )
        self.assertEqual(selling["message"], "正在将非真品古董兑换为银币")

        scanning = user_progress(
            "kaogu", '{"event":"scene","status":"scan_inventory"}'
        )
        self.assertEqual(
            scanning["message"], "正在查找非真品古董，请勿操作游戏窗口"
        )

    def test_manual_tool_preflight_explains_required_start_page(self):
        output = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"manual_tool_required",'
            '"reason":"请先手动选好工具"},'
            '"proposed_action":null,"texts":[],"templates":{}}'
        )

        inspection = self.manager._parse_inspection(
            "kaogu", {"toolMode": "selected"}, output
        )

        self.assertFalse(inspection["canStart"])
        self.assertEqual(inspection["sceneLabel"], "未检测到已选工具")
        self.assertEqual(inspection["reason"], "请先手动选好工具")


if __name__ == "__main__":
    unittest.main()
