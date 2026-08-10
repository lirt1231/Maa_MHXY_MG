import unittest

from tools.keju_reward import parse_huishi_settlement_reward


IMAGE_SIZE = (1020, 718)


class HuishiSettlementRewardTests(unittest.TestCase):
    def test_reads_experience_silver_and_optional_gold_from_settlement_rows(self):
        reward = parse_huishi_settlement_reward([
            {"text": "科举会试完成", "box": [420, 120, 160, 28]},
            {"text": "获得经验", "box": [350, 390, 100, 30]},
            {"text": "12,500", "box": [470, 390, 76, 30]},
            {"text": "获得银币 30,000", "box": [350, 440, 180, 30]},
            {"text": "本次会获得奖金", "box": [350, 490, 160, 30]},
            {"text": "1,635", "box": [520, 490, 76, 30]},
        ], IMAGE_SIZE)

        self.assertEqual(reward["status"], "confirmed")
        self.assertEqual(reward["experience"], 12500)
        self.assertEqual(reward["silver"], 30000)
        self.assertEqual(reward["gold"], 1635)
        self.assertEqual(reward["evidence"]["sourcePage"], "huishi_settlement")

    def test_rejects_wallet_values_without_huishi_settlement_identity(self):
        reward = parse_huishi_settlement_reward([
            {"text": "经验", "box": [180, 610, 48, 22]},
            {"text": "99999", "box": [245, 610, 80, 22]},
            {"text": "银币 99999", "box": [300, 610, 100, 22]},
        ], IMAGE_SIZE)
        self.assertEqual(reward, {"status": "unavailable", "reason": "huishi_settlement_identity_missing"})

    def test_requires_both_requested_reward_fields(self):
        reward = parse_huishi_settlement_reward([
            {"text": "科举会试完成", "box": [420, 120, 160, 28]},
            {"text": "获得经验 12,500", "box": [420, 390, 170, 30]},
        ], IMAGE_SIZE)
        self.assertEqual(reward["status"], "unavailable")
        self.assertEqual(reward["reason"], "settlement_experience_or_silver_missing")


if __name__ == "__main__":
    unittest.main()
