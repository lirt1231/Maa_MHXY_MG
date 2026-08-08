import unittest

from tools.keju_reward import parse_huishi_settlement_reward


IMAGE_SIZE = (1020, 718)


class HuishiSettlementRewardTests(unittest.TestCase):
    def test_reads_gold_from_a_confirmed_settlement_row(self):
        reward = parse_huishi_settlement_reward([
            {"text": "科举会试完成", "box": [420, 120, 160, 28]},
            {"text": "获得金币 12,500", "box": [420, 390, 170, 30]},
        ], IMAGE_SIZE)

        self.assertEqual(reward["status"], "confirmed")
        self.assertEqual(reward["gold"], 12500)
        self.assertEqual(reward["evidence"]["sourcePage"], "huishi_settlement")

    def test_reads_gold_from_an_adjacent_amount_box(self):
        reward = parse_huishi_settlement_reward([
            {"text": "会试成绩", "box": [430, 110, 130, 28]},
            {"text": "金币", "box": [405, 400, 48, 28]},
            {"text": "30000", "box": [470, 400, 76, 28]},
        ], IMAGE_SIZE)

        self.assertEqual(reward["status"], "confirmed")
        self.assertEqual(reward["gold"], 30000)

    def test_rejects_wallet_gold_without_the_huishi_settlement_identity(self):
        reward = parse_huishi_settlement_reward([
            {"text": "金币", "box": [180, 610, 48, 22]},
            {"text": "99999", "box": [245, 610, 80, 22]},
        ], IMAGE_SIZE)

        self.assertEqual(reward, {
            "status": "unavailable",
            "reason": "huishi_settlement_identity_missing",
        })

    def test_rejects_a_settlement_without_a_gold_amount(self):
        reward = parse_huishi_settlement_reward([
            {"text": "科举会试完成", "box": [420, 120, 160, 28]},
            {"text": "恭喜完成答题", "box": [430, 380, 160, 28]},
        ], IMAGE_SIZE)

        self.assertEqual(reward["status"], "unavailable")
        self.assertEqual(reward["reason"], "settlement_gold_missing")


if __name__ == "__main__":
    unittest.main()
