import tempfile
import unittest
from pathlib import Path

from dashboard.database import Database
from dashboard.events import EventBroker
from dashboard.runner import RunnerManager
from dashboard.user_progress import user_progress


class SignInDashboardRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.manager = RunnerManager(
            Database(Path(self.temporary_directory.name) / "dashboard.sqlite3"),
            EventBroker(),
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_clan_sign_in_can_start_by_closing_leftover_welfare_page(self):
        output = (
            'preflight_json={"image_size":[1020,718],'
            '"decision":{"status":"close_known_welfare_overlay",'
            '"box":[898,39,61,72]},'
            '"texts":[{"text":"梦幻刮刮乐"}],'
            '"templates":{"welfare_header":[420,20,300,60]}}'
        )

        result = self.manager._parse_inspection("bangpai_qiandao", {}, output)
        progress = user_progress(
            "bangpai_qiandao",
            '{"event":"scene","status":"close_known_welfare_overlay"}',
        )

        self.assertTrue(result["canStart"])
        self.assertEqual(result["proposedAction"], "关闭已识别的福利弹窗")
        self.assertEqual(progress["message"], "正在关闭上一任务遗留的福利页面")


if __name__ == "__main__":
    unittest.main()
