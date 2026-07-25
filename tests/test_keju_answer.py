import sys
import tempfile
import unittest
from pathlib import Path

RECOGNITION_DIR = Path(__file__).parents[1] / "agent" / "custom" / "recognition"
sys.path.insert(0, str(RECOGNITION_DIR))

from keju_answer_core import (  # noqa: E402
    answer_to_option,
    build_verification_prompt,
    local_fallback,
    parse_model_answer,
    scale_roi,
)
from searchAnswer import (  # noqa: E402
    load_question_bank,
    search_candidates,
)


class QuestionBankTests(unittest.TestCase):
    def test_top_three_candidates_are_sorted(self):
        bank = {
            "科举殿试是谁主持进行的": ["李世民"],
            "科举乡试每天可以完成多少题": ["20题"],
            "长安城是谁建立的": ["未知"],
            "科举会试在哪里参加": ["长安城"],
        }
        results = search_candidates("科举殿试是谁主持进行的？", 3, bank)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["similarity"], 100)
        self.assertEqual(results[0]["answers"], ["李世民"])
        self.assertGreaterEqual(results[0]["similarity"], results[1]["similarity"])

    def test_question_bank_is_loaded_from_existing_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bank.txt"
            path.write_text('"题目一":["答案甲，答案乙"]\n"题目二":["答案丙"]', encoding="utf-8")
            bank = load_question_bank(path)
        self.assertEqual(bank["题目一"], ["答案甲", "答案乙"])


class HybridAnswerTests(unittest.TestCase):
    def setUp(self):
        self.options = {"A": "李世民", "B": "唐太宗", "C": "玉皇大帝", "D": "阎罗王"}
        self.candidates = [
            {
                "question": "科举殿试是谁主持进行的？",
                "answers": ["李世民"],
                "similarity": 97,
                "match_type": "相似度匹配",
            }
        ]

    def test_prompt_contains_current_options_and_evidence(self):
        prompt = build_verification_prompt("当前题目", self.options, self.candidates)
        self.assertIn("当前题目", prompt)
        self.assertIn("similar_questions", prompt)
        self.assertIn("李世民", prompt)

    def test_parse_json_and_plain_model_answers(self):
        self.assertEqual(parse_model_answer('{"answer":"C","confidence":0.9}'), "C")
        self.assertEqual(parse_model_answer("答案为 B。"), "B")
        self.assertIsNone(parse_model_answer("无法判断"))

    def test_local_fallback_requires_both_confidences(self):
        self.assertEqual(local_fallback(self.candidates, self.options), "A")
        low_question = [dict(self.candidates[0], similarity=70)]
        self.assertIsNone(local_fallback(low_question, self.options))

    def test_answer_text_maps_to_current_option(self):
        self.assertEqual(answer_to_option("李世民", self.options), ("A", 100))

    def test_legacy_roi_can_scale_to_narrow_macos_window(self):
        self.assertEqual(scale_roi([509, 306, 269, 91], 923, 720), [367, 306, 194, 91])


if __name__ == "__main__":
    unittest.main()
