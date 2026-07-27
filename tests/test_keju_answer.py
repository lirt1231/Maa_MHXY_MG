import sys
import tempfile
import unittest
import json
from unittest.mock import Mock, patch

import numpy as np
from pathlib import Path
from types import SimpleNamespace

RECOGNITION_DIR = Path(__file__).parents[1] / "agent" / "custom" / "recognition"
AGENT_DIR = Path(__file__).parents[1] / "agent"
sys.path.insert(0, str(AGENT_DIR))
sys.path.insert(0, str(RECOGNITION_DIR))

from keju_answer_core import (  # noqa: E402
    answer_to_option,
    build_verification_prompt,
    knowledge_fallback,
    local_fallback,
    looks_like_answer_result,
    parse_model_answer,
    scale_roi,
)
from searchAnswer import (  # noqa: E402
    load_question_bank,
    normalize_text,
    search_candidates,
)
from keju_knowledge import KejuKnowledgeStore  # noqa: E402
from custom.recognition import keju_hybrid  # noqa: E402


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

    def test_new_huishi_questions_are_available_in_runtime_bank(self):
        bank = load_question_bank(RECOGNITION_DIR / "tiku.txt")
        expected = {
            "我国古代信奉的“火神”是以下哪位？": ["祝融"],
            "以下哪个技能是阴曹地府的万象星盘技能？": ["无常力"],
            "“降低1点使用灵气”是《梦幻西游》手游中醒神笔的什么属性？": ["太阴"],
            "“风雨巴山遗恨远，至今人念大将军”说的人物是？": ["邹容"],
            "神话《白蛇传》中“白娘娘盗仙草”盗的是？": ["灵芝"],
            "我们经常使用手机扫描二维码，“扫一扫”这一功能使用了什么方式？": ["图像识别"],
            "视听作品中的电影作品、电视剧作品的著作权由谁享有？": ["制作者"],
            "启明星是指太阳系中的？": ["金星"],
            "唐朝文宗御封的“三绝”指的是李白的诗歌、张旭的草书和？": ["裴旻的剑舞"],
            "古代五音——宫、商、角、徵、羽相当于现代简谱上的？": ["1、2、3、5、6"],
            "汽车品牌“劳斯莱斯”创始于哪个国家？": ["英国"],
            "与“四大名旦”同时期，专攻“花旦”行的表演艺术流派是？": ["筱派"],
        }
        for question, answers in expected.items():
            self.assertEqual(bank[question], answers)

    def test_live_round_and_timer_noise_is_removed_before_matching(self):
        clean = "我国古代信奉的“火神”是以下哪位？"
        noisy = "第1/15题剩余时间0我国古代信奉的“火神”是以下哪位？"
        self.assertEqual(normalize_text(noisy), normalize_text(clean))


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
        knowledge = [{
            "title": "历史资料",
            "content": "资料明确记载答案为李世民。",
            "answer": "李世民",
            "similarity": 70,
            "source_name": "可信资料",
            "source_url": "https://example.com",
            "trust": 0.9,
        }]
        prompt = build_verification_prompt(
            "当前题目", self.options, self.candidates, knowledge
        )
        self.assertIn("当前题目", prompt)
        self.assertIn("similar_questions", prompt)
        self.assertIn("knowledge_evidence", prompt)
        self.assertIn("可信资料", prompt)
        self.assertIn("李世民", prompt)

    def test_parse_json_and_plain_model_answers(self):
        self.assertEqual(parse_model_answer('{"answer":"C","confidence":0.9}'), "C")
        self.assertEqual(parse_model_answer("答案为 B。"), "B")
        self.assertIsNone(parse_model_answer("无法判断"))

    def test_local_fallback_requires_both_confidences(self):
        self.assertEqual(local_fallback(self.candidates, self.options), "A")
        low_question = [dict(self.candidates[0], similarity=70)]
        self.assertIsNone(local_fallback(low_question, self.options))

    def test_knowledge_fallback_requires_high_retrieval_confidence(self):
        evidence = [{"answer": "李世民", "similarity": 95}]
        self.assertEqual(knowledge_fallback(evidence, self.options), "A")
        evidence[0]["similarity"] = 65
        self.assertIsNone(knowledge_fallback(evidence, self.options))

    def test_answer_text_maps_to_current_option(self):
        self.assertEqual(answer_to_option("李世民", self.options), ("A", 100))

    def test_legacy_roi_can_scale_to_narrow_macos_window(self):
        self.assertEqual(scale_roi([509, 306, 269, 91], 923, 720), [367, 306, 194, 91])

    def test_review_page_with_crowd_counts_is_not_answered_again(self):
        options = {
            "A": "图像识别20094",
            "B": "语音识别1071",
            "C": "人脸识别361",
            "D": "文字识别180人",
        }
        self.assertTrue(looks_like_answer_result("第5/15题扫一扫使用什么技术？", options))
        self.assertFalse(
            looks_like_answer_result(
                "第5/15题剩余时间8扫一扫使用什么技术？",
                {"A": "图像识别", "B": "语音识别", "C": "人脸识别", "D": "文字识别"},
            )
        )

    def test_macos_question_roi_covers_two_lines_and_stops_before_options(self):
        question_bottom = (
            keju_hybrid.MAC_QUESTION_ROI[1]
            + keju_hybrid.MAC_QUESTION_ROI[3]
        )
        self.assertLessEqual(question_bottom, keju_hybrid.MAC_OPTION_ROIS["A"][1])
        self.assertGreaterEqual(keju_hybrid.MAC_QUESTION_ROI[3], 80)

    def test_recognizer_prefers_explicit_argv_image(self):
        image = np.ones((20, 30, 3), dtype=np.uint8)
        context = SimpleNamespace(
            tasker=SimpleNamespace(
                controller=SimpleNamespace(post_screencap=Mock())
            )
        )

        result = keju_hybrid._recognition_image(
            context,
            SimpleNamespace(image=image),
        )

        self.assertIs(result, image)
        context.tasker.controller.post_screencap.assert_not_called()

    def test_wrong_exam_type_stops_before_question_lookup(self):
        recognizer = object.__new__(keju_hybrid.KejuHybridAnswer)
        image = np.ones((720, 923, 3), dtype=np.uint8)

        with (
            patch.dict("os.environ", {"MAA_KEJU_EXAM_TYPE": "xiangshi"}),
            patch.object(keju_hybrid, "_ocr_region", return_value="科举会试"),
            patch.object(keju_hybrid, "search_candidates") as search,
        ):
            result = recognizer._analyze(
                SimpleNamespace(),
                SimpleNamespace(image=image),
                keju_hybrid._StageTimings(),
            )

        self.assertEqual(recognizer.last_status, "wrong_exam_type")
        self.assertEqual(recognizer.last_exam_type, "huishi")
        self.assertEqual(tuple(result.box), (0, 0, 0, 0))
        search.assert_not_called()

    def test_missing_exam_title_stops_before_question_lookup(self):
        recognizer = object.__new__(keju_hybrid.KejuHybridAnswer)
        image = np.ones((720, 923, 3), dtype=np.uint8)

        with (
            patch.dict("os.environ", {"MAA_KEJU_EXAM_TYPE": "xiangshi"}),
            patch.object(keju_hybrid, "_ocr_region", return_value=""),
            patch.object(keju_hybrid, "search_candidates") as search,
        ):
            recognizer._analyze(
                SimpleNamespace(),
                SimpleNamespace(image=image),
                keju_hybrid._StageTimings(),
            )

        self.assertEqual(recognizer.last_status, "unverified_exam_type")
        search.assert_not_called()


class KnowledgeStoreTests(unittest.TestCase):
    def test_paraphrase_retrieves_sourced_game_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            seed_path = directory / "seed.json"
            bank_path = directory / "bank.txt"
            seed_path.write_text(
                json.dumps({"documents": [{
                    "id": "bubble",
                    "title": "恶魔泡泡幼年提升规则",
                    "content": "小恶魔泡泡幼年时期总共可以提升7次。",
                    "answer": "7个",
                    "aliases": ["恶魔泡泡成年前提升次数"],
                    "source_name": "测试来源",
                    "source_url": "https://example.com/bubble",
                    "trust": 0.9,
                }]}, ensure_ascii=False),
                encoding="utf-8",
            )
            bank_path.write_text('"火神是谁？":["祝融"]', encoding="utf-8")
            store = KejuKnowledgeStore(
                db_path=directory / "knowledge.sqlite3",
                seed_path=seed_path,
                question_bank_path=bank_path,
            )
            results = store.search("小恶魔泡泡成年以前能提升多少次", limit=3)
        self.assertTrue(results)
        self.assertEqual(results[0]["answer"], "7个")
        self.assertEqual(results[0]["source_name"], "测试来源")


class WaitingCaptureTests(unittest.TestCase):
    def test_waiting_capture_is_persisted_and_deduplicated_by_round(self):
        with tempfile.TemporaryDirectory() as directory:
            original_path = keju_hybrid.WAITING_CAPTURE_PATH
            keju_hybrid.WAITING_CAPTURE_PATH = Path(directory) / "capture.jsonl"
            try:
                recognizer = SimpleNamespace()
                keju_hybrid._record_waiting_capture(
                    recognizer,
                    4,
                    "第4/15题白娘娘盗仙草盗的是？",
                    {"A": "灵芝", "B": "人参"},
                    "等待第1题期间捕获答题页",
                )
                keju_hybrid._record_waiting_capture(
                    recognizer,
                    4,
                    "第4/15题白娘娘盗仙草盗的是？",
                    {"A": "灵芝", "B": "人参"},
                    "重复捕获",
                )
                lines = keju_hybrid.WAITING_CAPTURE_PATH.read_text(
                    encoding="utf-8"
                ).splitlines()
            finally:
                keju_hybrid.WAITING_CAPTURE_PATH = original_path
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["round"], 4)
        self.assertEqual(payload["options"]["A"], "灵芝")


if __name__ == "__main__":
    unittest.main()
