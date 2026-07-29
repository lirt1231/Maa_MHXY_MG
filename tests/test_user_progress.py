import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard.database import Database
from dashboard.events import EventBroker
from dashboard.runner import RunnerManager
from dashboard.user_progress import user_progress


class UserProgressTranslationTests(unittest.TestCase):
    def test_translates_scene_click_and_round_without_technical_fields(self):
        scene = user_progress(
            "zhuogui",
            json.dumps({
                "event": "scene", "status": "battle", "step": 28,
                "windowId": 9988, "source": [120, 240],
            }),
        )
        click = user_progress(
            "mijingxiangyao",
            json.dumps({
                "event": "click", "reason": "秘境降妖：select_stage",
                "screen": [800, 300],
            }, ensure_ascii=False),
        )
        round_done = user_progress(
            "zhuogui", '{"event":"round_complete","round":2,"action":"finish"}'
        )

        self.assertEqual(scene["message"], "战斗进行中，正在等待结束")
        self.assertEqual(click["message"], "正在选择当前可挑战关卡")
        self.assertEqual(round_done["message"], "第 2 轮捉鬼已完成")
        self.assertNotIn("windowId", str(scene))
        self.assertNotIn("select_stage", str(click))

    def test_translates_exam_progress_and_hides_protocol_lines(self):
        ready = user_progress(
            "keju",
            '{"step":3,"status":"ready","question":"内部题目",'
            '"options":{"A":"甲"},"answer":"A"}',
        )
        sanjie = user_progress(
            "sanjieqiyuan", "click=4 completed=3 status=ready question=内部题目"
        )

        self.assertEqual(ready["message"], "已识别第 3 题，正在选择答案")
        self.assertEqual(sanjie["message"], "正在回答第 4 题")
        for line in (
            'result_json={"task_id":"keju","status":"completed"}',
            'preflight_json={"image_size":[1020,718]}',
            "safe_stop_reason=内部错误",
            "unrecognized_debug_field=123",
        ):
            self.assertIsNone(user_progress("keju", line))


class _FinishedProcess:
    def __init__(self, lines):
        self.pid = 43210
        self.stdout = lines
        self.returncode = 0

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode


class UserProgressBoundaryTests(unittest.TestCase):
    def test_runner_publishes_only_friendly_deduplicated_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "dashboard.sqlite3")
            manager = RunnerManager(database, EventBroker())
            process = _FinishedProcess([
                '{"event":"target_bound","windowId":9988}',
                '{"event":"scene","status":"battle","step":1}',
                '{"event":"scene","status":"battle","step":2}',
                '{"event":"click","reason":"继续当前捉鬼任务",'
                '"source":[100,200],"screen":[300,400]}',
                'result_json={"task_id":"zhuogui","status":"completed",'
                '"rounds":1,"target_rounds":1,"steps":3}',
            ])
            with patch("dashboard.runner.subprocess.Popen", return_value=process):
                manager.start(
                    "zhuogui",
                    {"rounds": 1, "levelRange": "90-115", "formation": "current"},
                    checklist_confirmed=True,
                )
                deadline = time.monotonic() + 1
                while manager.current["status"] not in {"succeeded", "failed"}:
                    if time.monotonic() >= deadline:
                        self.fail("runner did not finish")
                    time.sleep(0.002)

            events = database.events_after(0)
            messages = [event["message"] for event in events]
            self.assertEqual(messages.count("战斗进行中，正在等待结束"), 1)
            self.assertIn("已连接到游戏窗口", messages)
            self.assertIn("正在继续当前捉鬼任务", messages)
            visible = " ".join(messages)
            self.assertNotIn("result_json", visible)
            self.assertNotIn("windowId", visible)
            self.assertNotIn("source", visible)


if __name__ == "__main__":
    unittest.main()
