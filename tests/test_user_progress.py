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
            '{"step":3,"questionNumber":7,"status":"ready","question":"内部题目",'
            '"options":{"A":"甲"},"answer":"A"}',
        )
        sanjie = user_progress(
            "sanjieqiyuan", "click=4 completed=3 status=ready question=内部题目"
        )

        self.assertEqual(ready["message"], "已识别第 7 题，正在选择答案")
        self.assertEqual(sanjie["message"], "正在回答第 4 题")
        retry = user_progress("keju", "recognition_retry=1 status=unreliable")
        self.assertEqual(retry["message"], "题面识别不稳定，正在重新获取游戏画面")
        for line in (
            'result_json={"task_id":"keju","status":"completed"}',
            'preflight_json={"image_size":[1020,718]}',
            "safe_stop_reason=内部错误",
            "unrecognized_debug_field=123",
        ):
            self.assertIsNone(user_progress("keju", line))

    def test_exam_progress_never_labels_an_observation_as_a_question_number(self):
        progress = user_progress(
            "keju_huishi",
            '{"step":40,"status":"ready","question":"内部题目",'
            '"options":{"A":"甲"},"answer":"A"}',
        )

        self.assertEqual(progress["message"], "已识别题目，正在选择答案")

    def test_game_driven_monitor_reports_read_only_auto_completion(self):
        started = user_progress(
            "baotu",
            json.dumps({
                "event": "game_auto_monitor_started",
                "task": "宝图任务",
                "reason": "任务已接取",
            }, ensure_ascii=False),
        )
        observing = user_progress(
            "shimen",
            json.dumps({
                "event": "game_auto_monitor",
                "task": "师门任务",
                "status": "continue_task",
            }, ensure_ascii=False),
        )

        self.assertEqual(
            started["message"],
            "宝图任务正在由游戏自动完成，请勿操作游戏窗口",
        )
        self.assertEqual(
            observing["message"],
            "师门任务正在由游戏自动完成，请勿操作游戏窗口",
        )

    def test_baotu_sidebar_scene_does_not_claim_a_read_only_click(self):
        progress = user_progress(
            "baotu",
            '{"event":"scene","status":"continue_task"}',
        )

        self.assertEqual(progress["message"], "已识别到宝图任务正在执行")

    def test_monitoring_scene_keeps_the_do_not_touch_reminder(self):
        progress = user_progress(
            "shimen",
            '{"event":"scene","status":"continue_task","monitoring":true}',
        )

        self.assertEqual(
            progress["message"],
            "师门任务正在由游戏自动完成，请勿操作游戏窗口",
        )

    def test_archaeology_settlement_reports_confirmed_progress(self):
        progress = user_progress(
            "kaogu",
            '{"event":"scene","status":"dig_confirmed","digs":8}',
        )

        self.assertEqual(
            progress["message"],
            "本次考古已结算，正在等待下一次挖掘",
        )

    def test_fun_news_keeps_a_do_not_touch_reminder_during_likes_and_scrolls(self):
        liking = user_progress(
            "fun_news",
            '{"event":"scene","status":"like","likes":2}',
        )
        scrolled = user_progress(
            "fun_news",
            '{"event":"fun_news_liked","likes":3,"targetLikes":5}',
        )

        self.assertEqual(liking["message"], "正在点赞第 3 条动态，请勿操作游戏窗口")
        self.assertEqual(
            scrolled["message"],
            "已点赞 3/5 条动态，正在下滑加载下一条，请勿操作游戏窗口",
        )
        waiting = user_progress("fun_news", '{"event":"feed_refresh_wait","poll":1}')
        self.assertEqual(waiting["message"], "正在等待新的趣闻动态加载，请勿操作游戏窗口")
        loading = user_progress("fun_news", '{"event":"feed_loading_wait","poll":1}')
        self.assertEqual(loading["message"], "正在等待趣闻动态加载，请勿操作游戏窗口")

    def test_fun_news_reports_activity_page_completion_verification(self):
        returning = user_progress(
            "fun_news", '{"event":"scene","status":"return_to_activity","likes":5}'
        )
        waiting = user_progress(
            "fun_news", '{"event":"completion_verification_wait","poll":1}'
        )

        self.assertEqual(
            returning["message"], "已完成 5 次点赞，正在返回活动页面确认任务完成"
        )
        self.assertEqual(
            waiting["message"], "正在等待活动页面确认趣闻鉴赏已完成，请勿操作游戏窗口"
        )


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
    def test_final_delivery_candidate_has_completion_copy(self):
        progress = user_progress(
            "yunbiao",
            '{"event":"scene","status":"final_delivery_main_candidate"}',
        )

        self.assertEqual(
            progress["message"],
            "最后一轮普通运镖已结束，正在确认任务完成",
        )

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
