import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dashboard.game_data import parse_daily_task_data
from tools.daily_schedule_macos_core import (
    daily_schedule_page_evidence,
    group_ocr_rows,
    parse_daily_schedule,
    parse_daily_task_row,
)
from tools import run_daily_schedule_macos as schedule_runner


def item(text, x, y, width=90, height=24):
    return {"text": text, "box": [x, y, width, height]}


IMAGE_SIZE = (1020, 718)


def page_items(*rows):
    return [
        item("活动", 455, 20, 90, 30),
        item("日常活动", 90, 85, 110, 26),
        *rows,
    ]


def protocol_payloads(output: str) -> list[dict]:
    return [
        json.loads(line[len(schedule_runner.PROTOCOL_PREFIX):])
        for line in output.splitlines()
        if line.startswith(schedule_runner.PROTOCOL_PREFIX)
    ]


class DailyScheduleParsingTests(unittest.TestCase):
    def test_fields_are_associated_with_their_card_in_a_two_column_row(self):
        tasks = parse_daily_schedule(page_items(
            item("师门任务", 250, 150),
            item("10 / 20", 255, 182),
            item("前往", 430, 166),
            # A second card shares the same horizontal row.
            item("捉鬼任务", 600, 150),
            item("20/20", 605, 182),
            item("已完成", 780, 166),
        ), IMAGE_SIZE)

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
        tasks = parse_daily_schedule(page_items(
            item("秘境降妖", 250, 150),
            item("未开启", 430, 166),
            item("科举任务", 600, 150),
            item("0/1", 605, 182),
            item("可领取", 780, 166),
            item("帮派任务", 250, 265),
            item("1/1", 255, 297),
            item("已领取", 430, 281),
        ), IMAGE_SIZE)

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
        tasks = parse_daily_schedule(page_items(
            item("日程中心", 100, 20),
            {"text": "坏数据", "box": [0, 0, 0, 20]},
            item("活跃度", 100, 60),
        ), IMAGE_SIZE)
        self.assertEqual(tasks, [])

    def test_page_requires_title_and_daily_tab_in_expected_regions(self):
        valid = page_items(item("师门任务", 250, 150), item("10/20", 570, 150))
        evidence = daily_schedule_page_evidence(valid, IMAGE_SIZE)
        self.assertEqual(evidence["page_title"], "ocr")
        self.assertTrue(evidence["daily_tab_detected"])

        scenes = [
            [item("日常活动", 90, 85)],
            [item("活动", 455, 20)],
            [item("活动", 180, 20), item("日常活动", 90, 85)],
            [item("活动", 455, 20), item("日常活动", 700, 85)],
        ]
        for scene in scenes:
            with self.subTest(scene=scene):
                self.assertIsNone(
                    daily_schedule_page_evidence(scene, IMAGE_SIZE)
                )

    def test_activity_header_template_can_supply_title_evidence(self):
        evidence = daily_schedule_page_evidence(
            [item("日常活动", 90, 85, 110, 26)],
            IMAGE_SIZE,
            {"activity_header": (330, 10, 500, 50)},
        )
        self.assertEqual(evidence["page_title"], "template")

    def test_world_chat_pollution_is_never_a_task(self):
        # De-identified text shape from the bad snapshot persisted on 2026-07-27.
        polluted_scene = page_items(
            item("世界[玩家]69紫薇封妖来个奶", 28, 535, 315, 24),
            item("师门任务", 250, 165),
            item("10/20", 255, 197),
            item("前往", 430, 181),
        )

        tasks = parse_daily_schedule(polluted_scene, IMAGE_SIZE)

        self.assertEqual([task["name"] for task in tasks], ["师门任务"])

    def test_task_keyword_without_a_separate_progress_or_state_column_is_ignored(self):
        tasks = parse_daily_schedule(page_items(
            item("世界[玩家]师门任务10/20前往", 250, 165, 310, 24),
        ), IMAGE_SIZE)
        self.assertEqual(tasks, [])


class DailyScheduleRunnerTests(unittest.TestCase):
    @patch("tools.run_daily_schedule_macos.MacOSTaskRuntime")
    def test_single_frame_emits_one_protocol_and_never_sends_input(self, runtime_class):
        runtime = runtime_class.return_value
        runtime.read_scene.return_value = SimpleNamespace(
            image_size=IMAGE_SIZE,
            texts=page_items(
                item("师门任务", 250, 165),
                item("10/20", 255, 197),
            ),
            templates={"activity_header": None},
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
            "ocr_item_count": 4,
            "page_title": "ocr",
            "daily_tab_detected": True,
            "task_roi": [0.2, 0.13, 0.96, 0.84],
        })
        runtime.read_scene.assert_called_once_with({
            "activity_header": ["zonghe/huodong_jiemian_panduan.png"],
        })
        runtime.click.assert_not_called()
        runtime.click_box.assert_not_called()
        runtime.drag.assert_not_called()

    @patch("tools.run_daily_schedule_macos.MacOSTaskRuntime")
    def test_invalid_progress_is_not_exposed_to_strict_protocol(self, runtime_class):
        runtime_class.return_value.read_scene.return_value = SimpleNamespace(
            image_size=IMAGE_SIZE,
            texts=page_items(
                item("捉鬼任务", 250, 165),
                item("21/20", 255, 197),
            ),
            templates={"activity_header": None},
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

    @patch("tools.run_daily_schedule_macos.MacOSTaskRuntime")
    def test_main_chat_and_battle_scenes_do_not_emit_fake_tasks(self, runtime_class):
        scenes = [
            [
                item("活动", 170, 20),
                item("世界[玩家]69紫薇封妖来个奶", 25, 530, 320),
            ],
            [
                item("准备中", 300, 300),
                item("准备中", 600, 300),
                item("捉鬼任务", 250, 165),
                item("10/20", 255, 197),
            ],
            [item("师门任务", 250, 165), item("10/20", 255, 197)],
        ]
        runtime = runtime_class.return_value
        for texts in scenes:
            with self.subTest(texts=texts):
                runtime.read_scene.return_value = SimpleNamespace(
                    image_size=IMAGE_SIZE,
                    texts=texts,
                    templates={"activity_header": None},
                )
                with self.assertRaisesRegex(RuntimeError, "未识别到完整"):
                    schedule_runner.collect_daily_schedule(Mock())

        runtime.click.assert_not_called()
        runtime.click_box.assert_not_called()
        runtime.drag.assert_not_called()

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
            "evidence": {
                "image_size": [1020, 718],
                "ocr_item_count": 2,
                "page_title": "template",
                "daily_tab_detected": True,
                "task_roi": [0.2, 0.13, 0.96, 0.84],
            },
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
