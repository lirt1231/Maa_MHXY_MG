import unittest

from tools.keju_reward import (
    parse_huishi_settlement_reward,
    parse_xiangshi_settlement_reward,
)


IMAGE_SIZE = (1020, 718)


class KejuSettlementRewardTests(unittest.TestCase):
    def test_reads_xiangshi_experience_and_silver(self):
        reward = parse_xiangshi_settlement_reward([
            {"text": "科举乡试", "box": [457, 138, 128, 30]},
            {"text": "获得经验", "box": [459, 538, 75, 22]},
            {"text": "142923", "box": [585, 538, 58, 22]},
            {"text": "获得银币", "box": [459, 562, 75, 22]},
            {"text": "7347", "box": [584, 564, 41, 20]},
        ], IMAGE_SIZE)
        self.assertEqual(reward["status"], "confirmed")
        self.assertEqual(reward["experience"], 142923)
        self.assertEqual(reward["silver"], 7347)
        self.assertEqual(reward["evidence"]["sourcePage"], "xiangshi_settlement")

    def test_accepts_xiangshi_title_with_ocr_trailing_separator(self):
        reward = parse_xiangshi_settlement_reward([
            {"text": "科举乡试·", "box": [456, 153, 135, 29]},
            {"text": "获得经验", "box": [460, 525, 74, 19]},
            {"text": "151417", "box": [586, 527, 56, 18]},
            {"text": "获得银币", "box": [460, 548, 74, 18]},
            {"text": "7268", "box": [585, 548, 40, 20]},
        ], IMAGE_SIZE)

        self.assertEqual(reward["status"], "confirmed")
        self.assertEqual(reward["experience"], 151417)
        self.assertEqual(reward["silver"], 7268)

    def test_huishi_remains_gold_only(self):
        reward = parse_huishi_settlement_reward([
            {"text": "科举会试完成", "box": [420, 120, 160, 28]},
            {"text": "本次会获得奖金", "box": [350, 390, 160, 30]},
            {"text": "1,635", "box": [520, 390, 76, 30]},
        ], IMAGE_SIZE)
        self.assertEqual(reward["status"], "confirmed")
        self.assertEqual(reward["gold"], 1635)
        self.assertNotIn("experience", reward)

    def test_huishi_rejects_xiangshi_settlement(self):
        reward = parse_huishi_settlement_reward([
            {"text": "科举乡试", "box": [457, 138, 128, 30]},
            {"text": "获得经验", "box": [459, 538, 75, 22]},
            {"text": "142923", "box": [585, 538, 58, 22]},
        ], IMAGE_SIZE)
        self.assertEqual(reward["reason"], "huishi_settlement_identity_missing")


if __name__ == "__main__":
    unittest.main()
