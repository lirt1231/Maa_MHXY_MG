import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dashboard.game_data import parse_daily_task_data
from tools.daily_schedule_macos_core import (
    group_ocr_rows,
    parse_daily_schedule,
    parse_daily_task_row,
)
from tools import run_daily_schedule_macos as schedule_runner


def item(text, x, y, width=90, height=24):
    return {"text": text, "box": [x, y, width, height]}


def protocol_payloads(output: str) -> list[dict]:
    return [
        json.loads(line[len(schedule_runner.PROTOCOL_PREFIX):])
        for line in output.splitlines()
        if line.startswith(schedule_runner.PROTOCOL_PREFIX)
    ]


class DailyScheduleParsingTests(unittest.TestCase):
    def test_split_ocr_items_are_combined_by_row(self):
        tasks = parse_daily_schedule([
            item("师门任务", 100, 100),
            item("10 / 20", 420, 103),
            item("前往", 700, 101),
            item("捉鬼任务", 100, 150),
            item("20/20", 420, 148),
            item("已完成", 700, 152),
        ])

        self.assertEqual(tasks, [
            {
                "name": "师门任务",
                "completed": 10,
                "target": 20,
                "status": "available",
                "reward_status": "unknown",
            },
            {
                "name": "捉鬼任务",
                "completed": 20,
                "target": 20,
                "status": "completed",
                "reward_status": "unknown",
            },
        ])

    def test_locked_and_reward_states_are_explicit(self):
        tasks = parse_daily_schedule([
            item("秘境降妖", 100, 100),
            item("未开启", 500, 101),
            item("科举任务", 100, 150),
            item("0/1", 420, 150),
            item("可领取", 700, 151),
            item("帮派任务", 100, 200),
            item("1/1", 420, 199),
            item("已领取", 700, 201),
        ])

        self.assertEqual(tasks[0]["status"], "locked")
        self.assertEqual(tasks[0]["reward_status"], "unknown")
        self.assertEqual(tasks[1]["reward_status"], "claimable")
        self.assertEqual(tasks[2]["status"], "completed")
        self.assertEqual(tasks[2]["reward_status"], "claimed")

    def test_adjacent_rows_and_invalid_progress_do_not_merge_or_claim_completion(self):
        rows = group_ocr_rows([
            item("师门任务", 100, 100, height=20),
            item("10/20", 420, 102, height=20),
            item("捉鬼任务", 100, 126, height=20),
            item("21/20", 420, 128, height=20),
        ])
        self.assertEqual(len(rows), 2)
        first = parse_daily_task_row(rows[0])
        second = parse_daily_task_row(rows[1])
        self.assertEqual(first["name"], "师门任务")
        self.assertEqual(second["name"], "捉鬼任务")
        self.assertEqual(second["completed"], 21)
        self.assertEqual(second["target"], 20)
        self.assertEqual(second["status"], "unknown")

    def test_headers_and_invalid_boxes_are_ignored(self):
        tasks = parse_daily_schedule([
            item("日程中心", 100, 20),
            {"text": "坏数据", "box": [0, 0, 0, 20]},
            item("活跃度", 100, 60),
        ])
        self.assertEqual(tasks, [])


class DailyScheduleRunnerTests(unittest.TestCase):
    @patch("tools.run_daily_schedule_macos.MacOSTaskRuntime")
    def test_single_frame_emits_one_protocol_and_never_sends_input(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = SimpleNamespace(
            image_size=(1020, 718),
            texts=[item("师门任务", 100, 100), item("10/20", 420, 100)],
        )
        guard = Mock()
        output = io.StringIO()

        with redirect_stdout(output):
            payload = schedule_runner.collect_daily_schedule(guard)
            print(
                schedule_runner.PROTOCOL_PREFIX + json.dumps(payload, ensure_ascii=False)
            )

        results = protocol_payloads(output.getvalue())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["category"], "daily_tasks")
        self.assertEqual(results[0]["source_page"], "daily_schedule")
        self.assertEqual(results[0]["status"], "success")
        task = results[0]["data"]["tasks"][0]
        self.assertEqual(set(task), {
            "taskId", "name", "completed", "target", "status", "rewardStatus",
        })
        self.assertTrue(task["taskId"].startswith("daily-"))
        self.assertEqual(task["rewardStatus"], "unknown")
        self.assertEqual(parse_daily_task_data(output.getvalue()), results[0])
        self.assertEqual(results[0]["evidence"], {
            "image_size": [1020, 718],
            "ocr_item_count": 2,
        })
        runtime.read_scene.assert_called_once_with()
        runtime.click.assert_not_called()
        runtime.click_box.assert_not_called()
        runtime.drag.assert_not_called()

    @patch("tools.run_daily_schedule_macos.MacOSTaskRuntime")
    def test_invalid_progress_is_not_exposed_to_strict_protocol(self, runtime_class):
        runtime_class.return_value.read_scene.return_value = SimpleNamespace(
            image_size=(1020, 718),
            texts=[item("捉鬼任务", 100, 100), item("21/20", 420, 100)],
        )

        payload = schedule_runner.collect_daily_schedule(Mock())
        task = payload["data"]["tasks"][0]

        self.assertIsNone(task["completed"])
        self.assertIsNone(task["target"])
        self.assertEqual(task["status"], "unknown")
        encoded = schedule_runner.PROTOCOL_PREFIX + json.dumps(
            payload, ensure_ascii=False
        )
        self.assertEqual(parse_daily_task_data(encoded), payload)

    def test_exception_is_nonzero_and_emits_no_protocol(self):
        guard = Mock()
        output = io.StringIO()
        error = io.StringIO()
        with (
            patch.object(schedule_runner, "CancellationGuard", return_value=guard),
            patch.object(
                schedule_runner,
                "collect_daily_schedule",
                side_effect=RuntimeError("截图失败"),
            ),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            self.assertEqual(schedule_runner.main(), 1)

        self.assertEqual(protocol_payloads(output.getvalue()), [])
        self.assertIn("daily_schedule_error=截图失败", error.getvalue())

    def test_main_emits_the_only_protocol_line(self):
        payload = {
            "category": "daily_tasks",
            "source_page": "daily_schedule",
            "status": "partial",
            "data": {"tasks": []},
            "evidence": {"image_size": [1020, 718], "ocr_item_count": 0},
        }
        output = io.StringIO()
        with (
            patch.object(schedule_runner, "CancellationGuard", return_value=Mock()),
            patch.object(schedule_runner, "collect_daily_schedule", return_value=payload),
            redirect_stdout(output),
        ):
            self.assertEqual(schedule_runner.main(), 0)

        self.assertEqual(protocol_payloads(output.getvalue()), [payload])


if __name__ == "__main__":
    unittest.main()
