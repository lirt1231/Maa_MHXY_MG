import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
RECOGNITION_DIR = ROOT / "agent" / "custom" / "recognition"
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(RECOGNITION_DIR))

from custom.recognition.keju_hybrid import _clean_question_text  # noqa: E402
from keju_web_search import clean_search_question  # noqa: E402
from tools.automation_workflows import WorkflowContext
from tools.keju_workflow import KejuWorkflow, stable_question_key
from tools.macos_quiz_adapter import MacOSQuizInputAdapter, MacOSQuizScene
from tools.quiz_workflows import QuizObservation


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "huishi_2026_08_22.json").read_text(
        encoding="utf-8"
    )
)


class _ReplaySource:
    def __init__(self, observations):
        self.observations = iter(observations)

    def observe(self, selected_question_key=None, selected_answers=frozenset()):
        return next(self.observations)


class _Waiter:
    def wait(self, _seconds):
        return None


def _observation(decision):
    return QuizObservation(MacOSQuizScene((923, 720)), decision)


class HuishiAugust22ReplayTests(unittest.TestCase):
    def test_replays_all_answers_and_completes_on_elimination_result(self):
        observations = []
        for item in FIXTURE["questions"]:
            question = _clean_question_text(item["rawQuestion"])
            observations.append(_observation({
                "status": "ready",
                "question": question,
                "question_key": f"round:{item['round']}",
                "question_number": item["round"],
                "options": item["options"],
                "answer": item["answer"],
                "source": "recorded_replay",
                "click": (450, 350),
            }))
        terminal = FIXTURE["terminal"]
        observations.append(_observation({
            "status": "completed",
            "question": terminal["question"],
            "options": terminal["options"],
            "result": {
                "correctAnswers": terminal["correctAnswers"],
                "settlementReward": {
                    "status": "confirmed",
                    "gold": terminal["gold"],
                    "evidence": {"sourcePage": "huishi_terminal_text"},
                },
            },
        }))
        clicks = []
        adapter = MacOSQuizInputAdapter(
            lambda point, image_size, reason: clicks.append(
                (point, image_size, reason)
            )
        )

        outcome = KejuWorkflow(
            _ReplaySource(observations), max_answers=20,
            final_question_number=20, delay=0,
        ).execute(WorkflowContext(adapter, _Waiter()))

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.payload, {
            "answers": 14,
            "clicks": 14,
            "limit": 20,
            "correct_answers": 13,
            "settlement_reward": {
                "status": "confirmed",
                "gold": 382,
                "evidence": {"sourcePage": "huishi_terminal_text"},
            },
        })
        self.assertEqual(len(clicks), 14)

    def test_every_recorded_question_has_a_clean_stable_search_query(self):
        queries = []
        for item in FIXTURE["questions"]:
            question = _clean_question_text(item["rawQuestion"])
            query = clean_search_question(question)
            queries.append(query)
            self.assertTrue(query)
            self.assertNotIn("剩余时间", query)
            self.assertFalse(query.startswith("圆"), query)
            self.assertEqual(stable_question_key(f"第{item['round']}/20题{question}"), f"round:{item['round']}")

        self.assertEqual(len(set(queries)), 14)


if __name__ == "__main__":
    unittest.main()
