import json
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard.database import Database
from dashboard.events import EventBroker
from dashboard.runner import RunnerManager


class _BlockingOutput:
    def __init__(self, exited: threading.Event, lines: list[str] | None = None):
        self._exited = exited
        self._lines = iter(lines or [])

    def __iter__(self):
        return self

    def __next__(self):
        self._exited.wait()
        return next(self._lines)


class _FakeProcess:
    def __init__(self, pid: int = 43210, output_lines: list[str] | None = None):
        self.pid = pid
        self.exited = threading.Event()
        self.return_code = 0
        self.stdout = _BlockingOutput(self.exited, output_lines)

    def poll(self):
        return self.return_code if self.exited.is_set() else None

    def wait(self):
        self.exited.wait()
        return self.return_code

    def exit(self, return_code: int = -signal.SIGTERM):
        self.return_code = return_code
        self.exited.set()


class _FinishedProcess:
    def __init__(self, output_lines: list[str], return_code: int = 0):
        self.pid = 54321
        self.stdout = output_lines
        self.return_code = return_code

    def poll(self):
        return self.return_code

    def wait(self):
        return self.return_code


def _confirmed_recovery_process():
    return _FinishedProcess([
        'home_recovery_json={"status":"confirmed","outcome":"recovered",'
        '"scene":"main_screen","actions":2,"clicks":1,'
        '"evidence":{"targetId":"local-primary","firstFrameId":"first",'
        '"lastFrameId":"last","anchors":["task","team","bag"]}}'
    ])


class RunnerStopBarrierTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "dashboard.sqlite3")
        self.manager = RunnerManager(
            self.database,
            EventBroker(),
            stop_term_timeout=0.03,
            stop_kill_timeout=0.2,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _start(self):
        return self.manager.start("zhuogui", {"rounds": 1}, checklist_confirmed=True)

    def _wait_for_status(self, expected: str, timeout: float = 1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.manager.current
            if current and current["status"] == expected:
                return current
            time.sleep(0.002)
        self.fail(f"runner did not reach {expected!r}; current={self.manager.current!r}")

    def test_stop_before_spawn_prevents_process_from_starting(self):
        command_entered = threading.Event()
        release_command = threading.Event()

        def delayed_command(*_args):
            command_entered.set()
            release_command.wait()
            return ["unused"]

        self.manager._command = delayed_command
        with patch("dashboard.runner.subprocess.Popen") as popen:
            run = self._start()
            self.assertTrue(command_entered.wait(1))
            result = {}
            stop_thread = threading.Thread(
                target=lambda: result.update(self.manager.stop(run["id"])),
                daemon=True,
            )
            stop_thread.start()
            self._wait_for_status("stopping")
            release_command.set()
            stop_thread.join(1)

        self.assertFalse(stop_thread.is_alive())
        self.assertEqual(result["status"], "cancelled")
        popen.assert_not_called()
        self.assertEqual(self.database.recent_runs(1)[0]["status"], "cancelled")

    def test_stop_waits_for_sigterm_exit_and_cancelled_persistence(self):
        process = _FakeProcess()
        signals = []

        def killpg(pid, sig):
            signals.append((pid, sig))
            process.exit(-sig)

        with (
            patch("dashboard.runner.subprocess.Popen", return_value=process),
            patch("dashboard.runner.os.killpg", side_effect=killpg),
        ):
            run = self._start()
            self._wait_for_status("running")
            stopped = self.manager.stop(run["id"])

        self.assertEqual(signals, [(process.pid, signal.SIGTERM)])
        self.assertEqual(stopped["status"], "cancelled")
        self.assertEqual(stopped["stage"], "已停止")
        self.assertIn("lastOperationAt", stopped)
        self.assertEqual(self.database.recent_runs(1)[0]["status"], "cancelled")

    def test_stop_escalates_to_sigkill_after_term_timeout(self):
        process = _FakeProcess()
        signals = []

        def killpg(pid, sig):
            signals.append((pid, sig))
            if sig == signal.SIGKILL:
                process.exit(-sig)

        with (
            patch("dashboard.runner.subprocess.Popen", return_value=process),
            patch("dashboard.runner.os.killpg", side_effect=killpg),
        ):
            run = self._start()
            self._wait_for_status("running")
            stopped = self.manager.stop(run["id"])

        self.assertEqual(
            signals,
            [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)],
        )
        self.assertEqual(stopped["status"], "cancelled")
        events = self.database.events_after(0)
        kill_events = [event for event in events if event["payload"].get("phase") == "kill"]
        self.assertEqual(len(kill_events), 1)
        self.assertEqual(kill_events[0]["payload"]["signal"], "SIGKILL")

    def test_stop_persists_valid_cancelled_progress_from_runner(self):
        process = _FakeProcess(output_lines=[
            'result_json={"task_id":"zhuogui","status":"cancelled",'
            '"rounds":1,"target_rounds":2,"steps":18,"reason":"收到停止请求"}'
        ])

        def killpg(_pid, sig):
            process.exit(-sig)

        with (
            patch("dashboard.runner.subprocess.Popen", return_value=process),
            patch("dashboard.runner.os.killpg", side_effect=killpg),
        ):
            run = self._start()
            self._wait_for_status("running")
            stopped = self.manager.stop(run["id"])

        self.assertEqual(stopped["status"], "cancelled")
        self.assertEqual(stopped["result"]["rounds"], 1)
        self.assertEqual(stopped["result"]["steps"], 18)
        self.assertEqual(
            self.database.recent_runs(1)[0]["result"], stopped["result"]
        )

    def test_accepted_stop_wins_over_result_parsing_race(self):
        process = _FinishedProcess([
            'result_json={"task_id":"zhuogui","status":"completed",'
            '"rounds":1,"target_rounds":1,"steps":18}'
        ])
        parser_entered = threading.Event()
        release_parser = threading.Event()
        original_parser = self.manager._parse_task_result

        def delayed_parser(task_id, lines):
            parser_entered.set()
            release_parser.wait()
            return original_parser(task_id, lines)

        stopped = {}
        with (
            patch("dashboard.runner.subprocess.Popen", return_value=process),
            patch.object(self.manager, "_parse_task_result", side_effect=delayed_parser),
        ):
            run = self._start()
            self.assertTrue(parser_entered.wait(1))
            stop_thread = threading.Thread(
                target=lambda: stopped.update(self.manager.stop(run["id"])),
                daemon=True,
            )
            stop_thread.start()
            self._wait_for_status("stopping")
            release_parser.set()
            stop_thread.join(1)

        self.assertFalse(stop_thread.is_alive())
        self.assertEqual(stopped["status"], "cancelled")
        self.assertIsNone(stopped["result"])
        self.assertEqual(self.database.recent_runs(1)[0]["status"], "cancelled")


class RunnerTaskContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.manager = RunnerManager(
            Database(Path(self.temporary_directory.name) / "dashboard.sqlite3"),
            EventBroker(),
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _wait_for_terminal(self, timeout: float = 1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.manager.current
            if current and current["status"] in {"succeeded", "failed", "cancelled"}:
                return current
            time.sleep(0.002)
        self.fail(f"runner did not finish; current={self.manager.current!r}")

    def test_welfare_sign_in_uses_dedicated_safe_runner(self):
        command = self.manager._command("fuli_qiandao", {})
        self.assertTrue(command[-1].endswith("tools/run_fuli_qiandao_macos.py"))
        self.assertEqual(len(command), 2)

    def test_clan_sign_in_uses_dedicated_safe_runner(self):
        command = self.manager._command("bangpai_qiandao", {})
        self.assertTrue(command[-1].endswith("tools/run_bangpai_qiandao_macos.py"))
        self.assertEqual(len(command), 2)

    def test_treasure_map_uses_dedicated_safe_runner(self):
        command = self.manager._command("baotu", {})
        self.assertTrue(command[-1].endswith("tools/run_baotu_macos.py"))
        self.assertEqual(len(command), 2)

    def test_fun_news_uses_dedicated_bounded_runner(self):
        command = self.manager._command("fun_news", {})
        self.assertTrue(command[-1].endswith("tools/run_fun_news_macos.py"))
        self.assertEqual(len(command), 2)

    def test_ordinary_escort_uses_bounded_rounds(self):
        command = self.manager._command("yunbiao", {"rounds": 3})
        self.assertTrue(command[1].endswith("tools/run_yunbiao_macos.py"))
        self.assertEqual(command[2:], ["--rounds", "3"])

    def test_zhuogui_passes_selected_team_level_range(self):
        command = self.manager._command(
            "zhuogui",
            {"rounds": 2, "levelRange": "90-115", "formation": "虎翼阵"},
        )

        self.assertTrue(command[1].endswith("tools/run_zhuogui_macos.py"))
        self.assertEqual(
            command[2:],
            [
                "--rounds", "2", "--level-range", "90-115",
                "--formation", "虎翼阵",
            ],
        )

    def test_sect_task_uses_low_risk_runner(self):
        command = self.manager._command("shimen", {})
        self.assertTrue(command[-1].endswith("tools/run_shimen_macos.py"))
        self.assertEqual(len(command), 2)

    def test_mijing_runs_until_explicit_completion(self):
        command = self.manager._command("mijingxiangyao", {})
        self.assertTrue(command[1].endswith("tools/run_mijingxiangyao_macos.py"))
        self.assertEqual(command[2:], [])

    def test_imperial_exam_commands_keep_exam_types_separate(self):
        xiangshi_options = {}
        huishi_options = {
            "waitForRoundOne": True,
        }

        xiangshi = self.manager._command("keju", xiangshi_options)
        huishi = self.manager._command("keju_huishi", huishi_options)

        self.assertIn("xiangshi", xiangshi)
        self.assertNotIn("huishi", xiangshi)
        self.assertIn("huishi", huishi)
        self.assertNotIn("xiangshi", huishi)
        self.assertNotIn("--use-codex", xiangshi)
        self.assertNotIn("--use-codex", huishi)
        self.assertEqual(xiangshi[xiangshi.index("--max-steps") + 1], "10")
        self.assertNotIn("--wait-for-round-one", xiangshi)
        self.assertIn("--wait-for-round-one", huishi)

    def test_sanjie_always_uses_automatic_main_screen_navigation(self):
        command = self.manager._command("sanjieqiyuan", {})

        self.assertTrue(command[1].endswith("tools/run_sanjieqiyuan_macos.py"))
        self.assertEqual(
            command[2:],
            ["--max-steps", "10", "--input-method", "native", "--navigate"],
        )

    def test_huishi_start_checks_weekly_availability_before_spawning(self):
        with patch(
            "dashboard.runner.ensure_task_available",
            side_effect=ValueError("科举会试仅在每周六开放，请在周六再运行。"),
        ) as ensure_available, patch("dashboard.runner.subprocess.Popen") as popen:
            with self.assertRaisesRegex(ValueError, "仅在每周六开放"):
                self.manager.start(
                    "keju_huishi",
                    {"waitForRoundOne": True},
                    checklist_confirmed=True,
                )

        ensure_available.assert_called_once_with("keju_huishi")
        popen.assert_not_called()

    def test_xiangshi_start_checks_opening_time_before_spawning(self):
        with patch(
            "dashboard.runner.ensure_task_available",
            side_effect=ValueError(
                "科举乡试仅在周一至周五 17:00 后开放，请在开放时间再运行。"
            ),
        ) as ensure_available, patch("dashboard.runner.subprocess.Popen") as popen:
            with self.assertRaisesRegex(ValueError, "17:00 后开放"):
                self.manager.start(
                    "keju",
                    {},
                    checklist_confirmed=True,
                )

        ensure_available.assert_called_once_with("keju")
        popen.assert_not_called()

    def test_huishi_inspection_checks_weekly_availability_before_spawning(self):
        with patch(
            "dashboard.runner.ensure_task_available",
            side_effect=ValueError("科举会试仅在每周六开放，请在周六再运行。"),
        ) as ensure_available, patch("dashboard.runner.subprocess.Popen") as popen:
            with self.assertRaisesRegex(ValueError, "仅在每周六开放"):
                self.manager.inspect(
                    "keju_huishi",
                    {"waitForRoundOne": True},
                )

        ensure_available.assert_called_once_with("keju_huishi")
        popen.assert_not_called()
        self.assertFalse(self.manager.inspecting)

    def test_xiangshi_inspection_checks_opening_time_before_spawning(self):
        with patch(
            "dashboard.runner.ensure_task_available",
            side_effect=ValueError(
                "科举乡试仅在周一至周五 17:00 后开放，请在开放时间再运行。"
            ),
        ) as ensure_available, patch("dashboard.runner.subprocess.Popen") as popen:
            with self.assertRaisesRegex(ValueError, "17:00 后开放"):
                self.manager.inspect("keju", {})

        ensure_available.assert_called_once_with("keju")
        popen.assert_not_called()
        self.assertFalse(self.manager.inspecting)

    def test_welfare_progress_and_failure_copy_are_specific(self):
        payload = self.manager._progress_payload(
            "fuli_qiandao", '{"event": "scene", "status": "scratch_ready"}'
        )
        self.assertEqual(payload["stage"], "正在识别福利签到页面")
        completed = self.manager._progress_payload(
            "fuli_qiandao", 'result_json={"task_id":"fuli_qiandao","status":"completed"}'
        )
        self.assertEqual(completed["stage"], "签到流程完成")
        safe_stopped = self.manager._progress_payload(
            "fuli_qiandao",
            'result_json={"task_id":"fuli_qiandao","status":"safe_stopped","reason":"页面未知"}',
        )
        self.assertEqual(safe_stopped["stage"], "签到流程已安全停止")
        self.assertIn(
            "福利签到",
            self.manager._friendly_error("fuli_qiandao", 1, "unknown scene"),
        )

    def test_clan_sign_in_progress_and_failure_copy_are_specific(self):
        payload = self.manager._progress_payload(
            "bangpai_qiandao", '{"event": "scene", "status": "open_welfare"}'
        )
        self.assertEqual(payload["stage"], "正在识别帮派签到与分红页面")
        completed = self.manager._progress_payload(
            "bangpai_qiandao", 'result_json={"task_id":"bangpai_qiandao","status":"completed"}'
        )
        self.assertEqual(completed["stage"], "帮派签到与分红流程完成")
        already_completed = self.manager._progress_payload(
            "bangpai_qiandao",
            'result_json={"task_id":"bangpai_qiandao","status":"already_completed"}',
        )
        self.assertEqual(already_completed["stage"], "帮派签到与分红今日已完成")
        cancelled = self.manager._progress_payload(
            "bangpai_qiandao",
            'result_json={"task_id":"bangpai_qiandao","status":"cancelled"}',
        )
        self.assertEqual(cancelled["stage"], "帮派签到与分红流程已取消")
        self.assertIn(
            "帮派签到",
            self.manager._friendly_error("bangpai_qiandao", 1, "unknown scene"),
        )

    def test_shimen_progress_reports_handoff_to_game(self):
        running = self.manager._progress_payload(
            "shimen", '{"event": "scene", "status": "start_task"}'
        )
        self.assertEqual(running["stage"], "正在领取并启动师门任务")
        completed = self.manager._progress_payload(
            "shimen", 'result_json={"task_id":"shimen","status":"completed"}'
        )
        self.assertEqual(completed["stage"], "师门任务已启动，后续由游戏自动完成")
        safe_stopped = self.manager._progress_payload(
            "shimen", 'result_json={"task_id":"shimen","status":"safe_stopped"}'
        )
        self.assertEqual(safe_stopped["stage"], "师门任务已安全停止")
        final_message = self.manager._task_result_message(
            "shimen", {"status": "completed", "actions": 2, "clicks": 2}
        )
        self.assertEqual(final_message, "师门任务已启动，后续由游戏自动完成")

    def test_sanjie_completed_result_is_preserved_in_final_event(self):
        process = _FinishedProcess([
            'result_json={"task_id":"sanjieqiyuan","status":"completed","questions":10,"clicks":10}'
        ])
        with patch(
            "dashboard.runner.subprocess.Popen",
            side_effect=[process, _confirmed_recovery_process()],
        ):
            self.manager.start("sanjieqiyuan", {}, checklist_confirmed=True)
            current = self._wait_for_terminal()

        self.assertEqual(current["status"], "succeeded")
        self.assertEqual(current["result"]["status"], "completed")
        self.assertEqual(
            self.manager.database.recent_runs(1)[0]["result"],
            current["result"],
        )
        final_event = self.manager.database.events_after(0)[-1]
        self.assertEqual(final_event["message"], "三界奇缘答题完成")
        self.assertEqual(final_event["payload"]["result"]["questions"], 10)

    def test_sanjie_safe_stop_is_a_failed_run_with_exact_reason(self):
        process = _FinishedProcess([
            'result_json={"task_id":"sanjieqiyuan","status":"safe_stopped","reason":"未识别到完成状态"}'
        ], return_code=1)
        with patch("dashboard.runner.subprocess.Popen", return_value=process):
            self.manager.start("sanjieqiyuan", {}, checklist_confirmed=True)
            current = self._wait_for_terminal()

        self.assertEqual(current["status"], "failed")
        self.assertEqual(current["error"], "未识别到完成状态")
        self.assertEqual(current["result"]["status"], "safe_stopped")

    def test_sanjie_zero_exit_without_result_is_not_counted_as_success(self):
        process = _FinishedProcess(["answering_complete questions=10 clicks=10"])
        with patch("dashboard.runner.subprocess.Popen", return_value=process):
            self.manager.start("sanjieqiyuan", {}, checklist_confirmed=True)
            current = self._wait_for_terminal()

        self.assertEqual(current["status"], "failed")
        self.assertIn("结构化最终结果", current["error"])

    def test_sanjie_result_parser_rejects_invalid_safe_stop(self):
        with self.assertRaisesRegex(RuntimeError, "缺少原因"):
            self.manager._parse_task_result(
                "sanjieqiyuan",
                ['result_json={"task_id":"sanjieqiyuan","status":"safe_stopped"}'],
            )

    def test_result_parser_accepts_all_task_progress_shapes(self):
        fixtures = {
            "sanjieqiyuan": {"status": "completed", "questions": 10, "clicks": 10},
            "keju": {"status": "limit_reached", "answers": 20, "clicks": 20, "limit": 20},
            "keju_huishi": {"status": "completed", "answers": 15, "clicks": 15, "limit": 20},
            "zhuogui": {"status": "completed", "rounds": 2, "target_rounds": 2, "steps": 24},
            "fuli_qiandao": {"status": "already_completed", "actions": 0, "clicks": 0},
            "bangpai_qiandao": {"status": "completed", "actions": 1, "clicks": 1},
            "baotu": {"status": "completed", "actions": 8, "clicks": 6},
            "fun_news": {"status": "completed", "likes": 5, "scrolls": 4, "actions": 9, "clicks": 5},
            "yunbiao": {
                "status": "completed", "rounds": 3, "target_rounds": 3,
                "actions": 8, "clicks": 6,
            },
            "shimen": {"status": "completed", "actions": 20, "clicks": 20},
            "mijingxiangyao": {
                "status": "limit_reached", "battles": 18, "actions": 40,
                "clicks": 22, "max_battles": 18,
            },
        }
        for task_id, payload in fixtures.items():
            with self.subTest(task_id=task_id):
                line = "result_json=" + json.dumps(
                    {"task_id": task_id, **payload}, ensure_ascii=False
                )
                result = self.manager._parse_task_result(task_id, [line])
                self.assertEqual(result["status"], payload["status"])

    def test_result_parser_rejects_mismatched_task_and_non_string_reason(self):
        with self.assertRaisesRegex(RuntimeError, "任务标识"):
            self.manager._parse_task_result(
                "keju",
                ['result_json={"task_id":"zhuogui","status":"completed"}'],
            )
        with self.assertRaisesRegex(RuntimeError, "停止原因"):
            self.manager._parse_task_result(
                "keju",
                ['result_json={"task_id":"keju","status":"safe_stopped","reason":{},'
                 '"answers":0,"clicks":0,"limit":20}'],
            )

    def test_result_parser_rejects_task_specific_status_fields_and_counts(self):
        invalid = [
            ("fuli_qiandao", {"status": "limit_reached", "actions": 0, "clicks": 0}),
            ("zhuogui", {"status": "completed", "rounds": 1, "target_rounds": 2, "steps": 8}),
            ("keju", {"status": "completed", "answers": 1, "clicks": 1, "limit": 20, "api_key": "secret"}),
            ("sanjieqiyuan", {"status": "completed", "questions": 1}),
        ]
        for task_id, payload in invalid:
            with self.subTest(task_id=task_id, payload=payload):
                line = "result_json=" + json.dumps({"task_id": task_id, **payload})
                with self.assertRaises(RuntimeError):
                    self.manager._parse_task_result(task_id, [line])

    def test_result_parser_rejects_missing_and_duplicate_results(self):
        valid = (
            'result_json={"task_id":"sanjieqiyuan","status":"completed",'
            '"questions":1,"clicks":1}'
        )
        for lines in ([], [valid, valid]):
            with self.subTest(lines=len(lines)):
                with self.assertRaisesRegex(RuntimeError, "唯一"):
                    self.manager._parse_task_result("sanjieqiyuan", lines)

    def test_remaining_task_results_are_persisted(self):
        fixtures = [
            ("keju", {}, {"status": "completed", "answers": 10, "clicks": 10, "limit": 10}),
            ("keju_huishi", {}, {"status": "completed", "answers": 15, "clicks": 15, "limit": 20}),
            ("zhuogui", {"rounds": 2}, {"status": "completed", "rounds": 2, "target_rounds": 2, "steps": 24}),
            ("fuli_qiandao", {}, {"status": "completed", "actions": 5, "clicks": 3}),
            ("bangpai_qiandao", {}, {"status": "already_completed", "actions": 0, "clicks": 0}),
            ("baotu", {}, {"status": "completed", "actions": 8, "clicks": 6}),
            ("fun_news", {}, {"status": "completed", "likes": 5, "scrolls": 4, "actions": 9, "clicks": 5}),
            (
                "yunbiao", {},
                {"status": "completed", "rounds": 3, "target_rounds": 3, "actions": 8, "clicks": 6},
            ),
            ("shimen", {}, {"status": "completed", "actions": 20, "clicks": 20}),
            (
                "mijingxiangyao", {},
                {
                    "status": "completed", "battles": 18, "actions": 40,
                    "clicks": 22, "max_battles": 18,
                },
            ),
        ]
        with patch("dashboard.runner.ensure_task_available"):
            for task_id, options, payload in fixtures:
                with self.subTest(task_id=task_id):
                    process = _FinishedProcess([
                        "result_json=" + json.dumps(
                            {"task_id": task_id, **payload}, ensure_ascii=False
                        )
                    ])
                    with patch(
                        "dashboard.runner.subprocess.Popen",
                        side_effect=[process, _confirmed_recovery_process()],
                    ):
                        self.manager.start(task_id, options, checklist_confirmed=True)
                        current = self._wait_for_terminal()
                    self.assertEqual(current["status"], "succeeded")
                    self.assertEqual(current["result"]["status"], payload["status"])
                    self.assertEqual(
                        self.manager.database.recent_runs(1)[0]["result"],
                        current["result"],
                    )


class RunnerInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "dashboard.sqlite3")
        self.manager = RunnerManager(self.database, EventBroker())

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_inspection_commands_are_single_scene_and_read_only(self):
        expected = {
            "sanjieqiyuan": ["run_sanjieqiyuan_macos.py", "--inspect-scene", "--input-method", "global"],
            "keju": ["run_keju_macos.py", "--inspect-scene", "--exam-type", "xiangshi"],
            "keju_huishi": ["run_keju_macos.py", "--inspect-scene", "--exam-type", "huishi"],
            "zhuogui": ["run_zhuogui_macos.py", "--inspect-scene", "--rounds", "1"],
            "fuli_qiandao": ["run_fuli_qiandao_macos.py", "--inspect-scene"],
            "bangpai_qiandao": ["run_bangpai_qiandao_macos.py", "--inspect-scene"],
            "baotu": ["run_baotu_macos.py", "--inspect-scene"],
            "fun_news": ["run_fun_news_macos.py", "--inspect-scene"],
            "yunbiao": ["run_yunbiao_macos.py", "--inspect-scene", "--rounds", "3"],
            "shimen": ["run_shimen_macos.py", "--inspect-scene"],
            "mijingxiangyao": [
                "run_mijingxiangyao_macos.py", "--inspect-scene",
            ],
        }
        for task_id, suffix in expected.items():
            with self.subTest(task_id=task_id):
                command = self.manager._inspection_command(task_id, {})
                self.assertTrue(command[1].endswith(f"tools/{suffix[0]}"))
                self.assertEqual(command[2:], suffix[1:])
                self.assertNotIn("--navigate", command)
                self.assertNotIn("--recognize-only", command)
                self.assertNotIn("--use-codex", command)

    def test_parser_ignores_logs_and_accepts_only_protocol_line(self):
        output = "\n".join([
            "screencap_fallback=system reason=test",
            '{"event": "scene", "status": "unknown"}',
            'preflight_json={"image_size":[1020,718],"decision":{"status":"open_welfare"},"texts":[{"text":"福利"}],"templates":{"welfare_header":[1,2,3,4]}}',
        ])
        result = self.manager._parse_inspection("fuli_qiandao", {}, output)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["scene"], "open_welfare")
        self.assertEqual(result["evidence"]["ocrItemCount"], 1)
        self.assertEqual(result["evidence"]["matchedTemplates"], ["welfare_header"])

    def test_parser_rejects_missing_or_duplicate_protocol_results(self):
        with self.assertRaisesRegex(RuntimeError, "唯一"):
            self.manager._parse_inspection("fuli_qiandao", {}, '{"decision": {}}')
        line = 'preflight_json={"decision":{"status":"unknown"}}'
        with self.assertRaisesRegex(RuntimeError, "唯一"):
            self.manager._parse_inspection("fuli_qiandao", {}, f"{line}\n{line}")

    @patch("dashboard.runner.ensure_task_available")
    @patch("dashboard.runner.subprocess.Popen")
    def test_inspection_is_transient_and_uses_finite_timeout(
        self, popen, _ensure_available
    ):
        process = popen.return_value
        process.returncode = 0
        process.communicate.return_value = (
            'preflight_json={"image_size":[1020,718],"decision":{"status":"formal_question_ready","examType":"xiangshi"},"texts":[],"templates":{}}\n',
            None,
        )

        result = self.manager.inspect("keju", {})

        self.assertTrue(result["canStart"])
        self.assertEqual(self.database.recent_runs(), [])
        self.assertEqual(self.database.events_after(0), [])
        self.assertEqual(process.communicate.call_args.kwargs["timeout"], 15.0)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_keju_inspection_requires_matching_exam_type(self):
        xiangshi = (
            'preflight_json={"decision":{"status":"formal_question_ready",'
            '"examType":"xiangshi"},"texts":[],"templates":{}}'
        )
        huishi = (
            'preflight_json={"decision":{"status":"formal_question_ready",'
            '"examType":"huishi"},"texts":[],"templates":{}}'
        )
        unknown_type = (
            'preflight_json={"decision":{"status":"formal_question_ready"},'
            '"texts":[],"templates":{}}'
        )

        self.assertTrue(self.manager._parse_inspection("keju", {}, xiangshi)["canStart"])
        self.assertTrue(
            self.manager._parse_inspection("keju_huishi", {}, huishi)["canStart"]
        )
        wrong = self.manager._parse_inspection("keju", {}, huishi)
        self.assertFalse(wrong["canStart"])
        self.assertIn("当前是科举会试", wrong["summary"])
        unknown = self.manager._parse_inspection("keju_huishi", {}, unknown_type)
        self.assertFalse(unknown["canStart"])
        self.assertIn("无法确认", unknown["summary"])

    def test_parser_accepts_zhuogui_nested_evidence_and_action(self):
        output = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"main_screen","team":[900,100]},'
            '"proposed_action":"open_team",'
            '"evidence":{"ocr_item_count":1,"matched_templates":["main_ready"]}}'
        )
        result = self.manager._parse_inspection("zhuogui", {"rounds": 2}, output)
        self.assertTrue(result["canStart"])
        self.assertEqual(result["proposedAction"], "打开队伍面板")
        self.assertEqual(result["evidence"]["ocrItemCount"], 1)
        self.assertEqual(result["evidence"]["matchedTemplates"], ["main_ready"])

    def test_parser_accepts_claimable_clan_dividend_scene(self):
        output = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"claim_dividend","box":[738,576,82,24]},'
            '"texts":[{"text":"领取分红"}],"templates":{}}'
        )
        result = self.manager._parse_inspection("bangpai_qiandao", {}, output)
        self.assertTrue(result["canStart"])
        self.assertEqual(result["sceneLabel"], "可领取帮派分红")
        self.assertEqual(result["proposedAction"], "领取当前可用的帮派分红")

    def test_parser_accepts_treasure_map_daily_navigation(self):
        output = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"join_task","box":[500,210,60,25]},'
            '"texts":[{"text":"宝图任务"},{"text":"参加"}],"templates":{}}'
        )
        result = self.manager._parse_inspection("baotu", {}, output)
        self.assertTrue(result["canStart"])
        self.assertEqual(result["sceneLabel"], "日常任务卡片")
        self.assertIn("宝图任务", result["proposedAction"])

    def test_parser_preserves_read_only_coordinate_preview_evidence(self):
        output = (
            'preflight_json={"image_size":[923,720],'
            '"decision":{"status":"open_activity","box":[250,40,50,20]},'
            '"texts":[{"text":"活动"}],"templates":{},"evidence":{'
            '"frameId":"frame-1","captureMethod":"maa",'
            '"windowBoundsPoints":[-1786,-106,1051,820],'
            '"contentRectPoints":[-1786,-105.8483,1051,819.8483],'
            '"scaleFactor":0.8782112274,"anchorValidation":"pending",'
            '"recognizedBoxCenterPixels":[275,50],'
            '"predictedScreenPoint":[-1472.9,-48.9]}}'
        )

        result = self.manager._parse_inspection("baotu", {}, output)

        self.assertEqual(result["evidence"]["frameId"], "frame-1")
        self.assertEqual(result["evidence"]["captureMethod"], "maa")
        self.assertEqual(result["evidence"]["anchorValidation"], "pending")
        self.assertEqual(result["evidence"]["recognizedBoxCenterPixels"], [275, 50])
        self.assertEqual(result["evidence"]["predictedScreenPoint"], [-1472.9, -48.9])

    def test_parser_remains_compatible_without_coordinate_preview(self):
        output = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"open_activity","box":[250,40,50,20]},'
            '"texts":[{"text":"活动"}],"templates":{}}'
        )

        result = self.manager._parse_inspection("baotu", {}, output)

        self.assertEqual(
            result["evidence"],
            {
                "imageSize": [1020, 718],
                "ocrItemCount": 1,
                "matchedTemplates": [],
            },
        )

    def test_parser_accepts_active_ordinary_escort(self):
        output = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"delivery_active"},'
            '"texts":[{"text":"距离运镖完成：42秒"}],"templates":{}}'
        )
        result = self.manager._parse_inspection("yunbiao", {"rounds": 3}, output)
        self.assertTrue(result["canStart"])
        self.assertEqual(result["sceneLabel"], "普通运镖进行中")
        self.assertIn("等待", result["proposedAction"])

    def test_parser_reports_sect_asset_branch_as_unsupported(self):
        output = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"unsupported_asset_branch","reason":"购买一件装备"},'
            '"texts":[{"text":"购买一件装备"}],"templates":{}}'
        )
        result = self.manager._parse_inspection("shimen", {}, output)
        self.assertFalse(result["canStart"])
        self.assertIn("资产操作", result["summary"])
        self.assertIn("购买一件装备", result["reason"])

    def test_parser_accepts_mijing_stage_and_blocks_asset_branch(self):
        stage = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"select_stage","box":[780,220,90,35]},'
            '"proposed_action":"select_stage",'
            '"texts":[{"text":"第18关"}],"templates":{}}'
        )
        ready = self.manager._parse_inspection(
            "mijingxiangyao", {"maxBattles": 18}, stage
        )
        self.assertTrue(ready["canStart"])
        self.assertEqual(ready["sceneLabel"], "秘境关卡列表")
        self.assertIn("当前可挑战关卡", ready["proposedAction"])

        current_mode = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"enter_dungeon","box":[700,220,120,30]},'
            '"texts":[{"text":"云乐游"},{"text":"秘境降妖"}],"templates":{}}'
        )
        dungeon = self.manager._parse_inspection("mijingxiangyao", {}, current_mode)
        self.assertTrue(dungeon["canStart"])
        self.assertIn("当前已选择", dungeon["proposedAction"])

        asset = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"unsupported_asset_branch",'
            '"reason":"购买助战"},'
            '"texts":[{"text":"购买助战"}],"templates":{}}'
        )
        blocked = self.manager._parse_inspection("mijingxiangyao", {}, asset)
        self.assertFalse(blocked["canStart"])
        self.assertIn("秘境分支需要资产操作", blocked["summary"])
        self.assertIn("购买助战", blocked["reason"])

        mode = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"mode_selection_required",'
            '"reason":"请手动选择后重新启动任务"},'
            '"texts":[{"text":"选择模式"}],"templates":{}}'
        )
        manual = self.manager._parse_inspection("mijingxiangyao", {}, mode)
        self.assertFalse(manual["canStart"])
        self.assertIn("手动选择", manual["summary"])
        self.assertIsNone(manual["proposedAction"])

    def test_parser_allows_sanjie_activity_list_scroll(self):
        output = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"scroll_daily",'
            '"reason":"target_action_not_visible"},'
            '"texts":[{"text":"三界奇缘"}],"templates":{}}'
        )

        result = self.manager._parse_inspection(
            "sanjieqiyuan", {}, output
        )

        self.assertTrue(result["canStart"])
        self.assertEqual(result["sceneLabel"], "日常活动列表")
        self.assertEqual(result["proposedAction"], "向下浏览活动列表")

    def test_parser_rejects_invalid_protocol_schema(self):
        invalid_payloads = [
            "[]",
            '{"decision":[]}',
            '{"decision":{"status":null}}',
            '{"decision":{"status":"unknown"},"templates":[]}',
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaisesRegex(RuntimeError, "无效|缺少"):
                self.manager._parse_inspection("keju", {}, f"preflight_json={payload}")

    @patch("dashboard.runner.ensure_task_available")
    @patch("dashboard.runner.os.killpg")
    @patch("dashboard.runner.subprocess.Popen")
    def test_inspection_timeout_stops_process_group_and_releases_lock(
        self, popen, killpg, _ensure_available
    ):
        process = popen.return_value
        process.pid = 43210
        process.communicate.side_effect = subprocess.TimeoutExpired(["inspect"], 15.0)
        process.wait.return_value = 0

        with self.assertRaisesRegex(TimeoutError, "15 秒"):
            self.manager.inspect("keju", {})

        killpg.assert_called_once_with(process.pid, signal.SIGTERM)
        self.assertFalse(self.manager.inspecting)


if __name__ == "__main__":
    unittest.main()
