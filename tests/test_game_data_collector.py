import json
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch

from dashboard.game_data import collect_daily_task_data, parse_daily_task_data


def protocol(payload: dict) -> str:
    return "game_data_json=" + json.dumps(payload, ensure_ascii=False)


def valid_payload() -> dict:
    return {
        "category": "daily_tasks",
        "source_page": "daily_schedule",
        "status": "success",
        "data": {
            "tasks": [{
                "taskId": "shimen",
                "name": "师门任务",
                "completed": 10,
                "target": 20,
                "status": "available",
                "rewardStatus": "unknown",
            }]
        },
        "evidence": {
            "image_size": [1020, 718],
            "ocr_item_count": 32,
            "page_title": "template",
            "daily_tab_detected": True,
            "task_roi": [0.2, 0.13, 0.96, 0.84],
        },
    }


class DailyTaskDataParserTests(unittest.TestCase):
    def test_accepts_one_non_sensitive_protocol_result(self):
        payload = valid_payload()
        result = parse_daily_task_data("runtime_log=true\n" + protocol(payload))
        self.assertEqual(result, payload)

    def test_rejects_missing_duplicate_or_invalid_protocol(self):
        line = protocol(valid_payload())
        for output in ("", f"{line}\n{line}", "game_data_json={"):
            with self.subTest(output=output):
                with self.assertRaises(RuntimeError):
                    parse_daily_task_data(output)

    def test_rejects_unknown_fields_and_invalid_progress(self):
        fixtures = []
        unknown = valid_payload()
        unknown["data"]["tasks"][0]["rawText"] = "不应持久化"
        fixtures.append(unknown)
        overflow = valid_payload()
        overflow["data"]["tasks"][0]["completed"] = 21
        fixtures.append(overflow)
        invalid_evidence = valid_payload()
        invalid_evidence["evidence"]["ocr_item_count"] = True
        fixtures.append(invalid_evidence)
        missing_page_evidence = valid_payload()
        del missing_page_evidence["evidence"]["daily_tab_detected"]
        fixtures.append(missing_page_evidence)
        invalid_roi = valid_payload()
        invalid_roi["evidence"]["task_roi"] = [0.9, 0.1, 0.2, 0.8]
        fixtures.append(invalid_roi)
        for payload in fixtures:
            with self.subTest(payload=payload):
                with self.assertRaises(RuntimeError):
                    parse_daily_task_data(protocol(payload))


class DailyTaskCollectorTests(unittest.TestCase):
    @patch("dashboard.game_data.subprocess.Popen")
    def test_collector_uses_read_only_script_and_parses_result(self, popen):
        process = Mock(pid=321, returncode=0)
        process.communicate.return_value = (protocol(valid_payload()), None)
        popen.return_value = process

        result = collect_daily_task_data()

        command = popen.call_args.args[0]
        self.assertTrue(command[-1].endswith("tools/run_daily_schedule_macos.py"))
        self.assertEqual(result["category"], "daily_tasks")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    @patch("dashboard.game_data.os.killpg")
    @patch("dashboard.game_data.subprocess.Popen")
    def test_timeout_terminates_the_process_group(self, popen, killpg):
        process = Mock(pid=654)
        process.communicate.side_effect = subprocess.TimeoutExpired("daily", 0.1)
        process.wait.return_value = 0
        popen.return_value = process

        with self.assertRaisesRegex(TimeoutError, "15 秒"):
            collect_daily_task_data(timeout=0.1)

        killpg.assert_called_once_with(654, signal.SIGTERM)

    @patch("dashboard.game_data.subprocess.Popen")
    def test_nonzero_exit_does_not_parse_logs_as_data(self, popen):
        process = Mock(pid=321, returncode=1)
        process.communicate.return_value = ("raw OCR should stay transient", None)
        popen.return_value = process

        with self.assertRaisesRegex(RuntimeError, "日程数据采集失败"):
            collect_daily_task_data()


if __name__ == "__main__":
    unittest.main()
