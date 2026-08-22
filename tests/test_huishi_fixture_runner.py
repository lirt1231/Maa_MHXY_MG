import json
import unittest
from unittest.mock import patch

from tools.replay_huishi_fixture import (
    DEFAULT_FIXTURE,
    keju_hybrid,
    replay_fixture,
    replay_question,
)


FIXTURE = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))


class HuishiFixtureRunnerTests(unittest.TestCase):
    def test_replay_uses_production_recognizer_without_game_input(self):
        item = FIXTURE["questions"][0]
        decision = {
            "answer": "B", "confidence": 0.95,
            "evidence_ids": [], "reason": "依据常识选择东方1号。",
        }

        with (
            patch.object(keju_hybrid, "search_candidates", return_value=[]),
            patch.object(keju_hybrid, "search_knowledge", return_value=[]),
            patch.object(keju_hybrid, "search_web_evidence", return_value={
                "query": "首次载人太空飞船", "queries": [], "evidence": [],
                "providers": {}, "elapsed_ms": 0,
            }),
            patch.object(
                keju_hybrid, "_request_model",
                return_value=__import__("json").dumps(decision),
            ),
            patch.dict("os.environ", {
                "MAA_KEJU_API_KEY": "test-key",
                "MAA_KEJU_URL": "https://example.test",
                "MAA_KEJU_MODEL": "test-model",
                "MAA_KEJU_FALLBACK_MODEL": "",
            }),
        ):
            result = replay_question(item)

        self.assertEqual(result["round"], 1)
        self.assertEqual(result["answer"], "B", result)
        self.assertEqual(result["source"], "huishi_forced_model_guess")

    def test_replay_can_select_specific_rounds(self):
        with patch(
            "tools.replay_huishi_fixture.replay_question",
            side_effect=lambda item: {"round": item["round"]},
        ):
            results = replay_fixture(DEFAULT_FIXTURE, {2, 14})

        self.assertEqual(results, [{"round": 2}, {"round": 14}])


if __name__ == "__main__":
    unittest.main()
