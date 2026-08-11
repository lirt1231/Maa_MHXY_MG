import sys
import tempfile
import unittest
import json
import os
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
    infer_direct_evidence_option,
    local_fallback,
    looks_like_answer_result,
    parse_model_answer,
    parse_model_decision,
    resolve_model_decision,
    scale_roi,
    validate_forced_model_decision,
    validate_model_decision,
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

    def test_duplicate_question_entries_merge_answers_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bank.txt"
            path.write_text(
                '"动态题":["答案甲","答案乙"]\n'
                '"动态题":["答案丙，答案甲"]',
                encoding="utf-8",
            )

            bank = load_question_bank(path)

        self.assertEqual(bank["动态题"], ["答案甲", "答案乙", "答案丙"])

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
            "古琴最初只有五根弦，代表着金、木、水、火、土。后来又增加了两根弦，这两根弦代表阴、阳？": ["不是"],
            "下列选项中不属于传统知识产权范畴的是？": ["使用权"],
            "星盘技能“平衡之力”可以给人物一共提供多少属性？": ["10"],
            "下列神兽中，初始技能没有感知的是？": ["超级赤焰兽"],
            "火车连续发出两声长鸣，这表示？": ["倒退"],
            "《红楼梦》中王熙凤讲“子放鞭炮”的笑话是在什么": ["元宵节"],
        }
        for question, answers in expected.items():
            self.assertEqual(bank[question], answers)

    def test_guqin_true_false_question_uses_local_answer_instead_of_model(self):
        question = (
            "古琴最初只有五根弦，代表着金、木、水、火、土。"
            "后来又增加了两根弦，这两根弦代表阴、阳？"
        )
        bank = load_question_bank(RECOGNITION_DIR / "tiku.txt")
        candidates = search_candidates(question, 3, bank)

        self.assertEqual(
            local_fallback(candidates, {"A": "是的", "B": "不是"}),
            "B",
        )

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

    def test_prompt_contains_source_aware_web_evidence(self):
        web = [{
            "id": "web-1",
            "provider": "bing",
            "kind": "direct_answer",
            "title": "Bing 最佳匹配答案",
            "snippet": "9 个徒弟",
            "url": "https://www.bing.com/search",
            "rank": 0,
        }]
        prompt = build_verification_prompt(
            "一个师父最多同时拥有几个徒弟？",
            {"A": "3", "B": "6", "C": "9", "D": "7"},
            [],
            [],
            web,
        )
        self.assertIn("direct_answer", prompt)
        self.assertIn("9 个徒弟", prompt)
        self.assertIn("evidence_ids", prompt)
        self.assertIn("必须采用该结论", prompt)
        self.assertIn("判断题必须逐字比较", prompt)
        self.assertIn("已有明确总计时", prompt)
        self.assertIn("严禁相加", prompt)
        self.assertIn("绝不能选择数值最接近的选项", prompt)
        self.assertIn("没有、不含、不属于", prompt)
        self.assertIn("不能证明该项不存在", prompt)
        self.assertIn("不得照抄输出示例中的数值", prompt)
        self.assertIn("reason", prompt)

    def test_prompt_removes_timer_ui_noise_from_question(self):
        prompt = build_verification_prompt(
            "剩余时间20星盘技能平衡之力一共提供多少属性?",
            {"A": "10", "B": "8", "C": "14", "D": "12"},
            [],
        )

        self.assertNotIn("剩余时间", prompt)
        self.assertIn("星盘技能平衡之力一共提供多少属性", prompt)

    def test_prompt_explicitly_prevents_the_guqin_evidence_inversion(self):
        prompt = build_verification_prompt(
            "古琴后来增加的两根弦代表阴、阳？",
            {"A": "是的", "B": "不是"},
            [],
            [],
            [{
                "id": "web-2",
                "provider": "so360",
                "kind": "result",
                "title": "古琴七弦答案",
                "snippet": "后来增加的两根弦代表文、武。",
                "url": "https://example.com/guqin",
                "rank": 1,
            }],
        )
        self.assertIn("文、武", prompt)
        self.assertIn("阴、阳", prompt)
        self.assertIn("应选择“不是”", prompt)

    def test_huishi_prompt_requires_an_answer_without_fabricating_citations(self):
        prompt = build_verification_prompt(
            "冷门会试题",
            {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
            [],
            [],
            [],
            force_answer=True,
        )

        self.assertIn("限时会试", prompt)
        self.assertIn("必须选择最可能的一个选项", prompt)
        self.assertIn("不得伪造引用", prompt)

    def test_huishi_can_transparently_force_a_reasoned_model_guess(self):
        guess = {
            "answer": "D",
            "confidence": 0.35,
            "evidence_ids": [],
            "reason": "检索结果无关，根据游戏知识低置信度选择D。",
        }

        self.assertIsNone(validate_model_decision(guess, set())[0])
        self.assertEqual(
            validate_forced_model_decision(guess, ("A", "B", "C", "D")),
            ("D", None),
        )

    def test_forced_model_guess_still_requires_an_explanation(self):
        answer, error = validate_forced_model_decision({
            "answer": "B",
            "confidence": 0.2,
            "evidence_ids": [],
            "reason": "",
        })

        self.assertIsNone(answer)
        self.assertIn("猜测依据", error)

    def test_same_uncited_guess_stops_xiangshi_but_answers_huishi(self):
        guess = {
            "answer": "D",
            "confidence": 0.35,
            "evidence_ids": [],
            "reason": "检索结果未覆盖选项，根据排除法低置信度选择D。",
        }
        kwargs = {
            "decision": guess,
            "valid_evidence_ids": set(),
            "minimum_confidence": 0.62,
            "label": "模型",
            "evidence": [],
            "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
        }

        xiangshi = resolve_model_decision(**kwargs, force_answer=False)
        huishi = resolve_model_decision(**kwargs, force_answer=True)

        self.assertIsNone(xiangshi[0])
        self.assertFalse(xiangshi[1])
        self.assertEqual(huishi[0], "D")
        self.assertTrue(huishi[1])
        self.assertIn("限时会试强制作答", huishi[2])

    def test_unique_repeated_web_option_overrides_unsupported_model_choice(self):
        evidence = [
            {"id": "web-1", "title": "历史事件", "snippet": "国民政府宣布迁都重庆。"},
            {"id": "web-2", "title": "抗战史", "snippet": "1937年11月迁都重庆市。"},
        ]
        options = {"A": "成都", "B": "长沙", "C": "重庆", "D": "武汉"}
        decision = {
            "answer": "D", "confidence": 0.95,
            "evidence_ids": ["web-1"], "reason": "错误选择武汉。",
        }

        inferred = infer_direct_evidence_option(evidence, options)
        answer, forced, error = resolve_model_decision(
            decision, {"web-1", "web-2"}, 0.62, "模型",
            evidence=evidence, options=options, force_answer=True,
        )

        self.assertEqual(inferred, {"answer": "C", "evidence_ids": ["web-1", "web-2"]})
        self.assertEqual(answer, "C")
        self.assertFalse(forced)
        self.assertIn("证据直接纠偏", error)

    def test_multiple_supported_options_do_not_trigger_direct_override(self):
        evidence = [
            {"id": "web-1", "title": "说法甲", "snippet": "可能是成都，也可能是重庆。"},
            {"id": "web-2", "title": "说法乙", "snippet": "成都与重庆均被讨论。"},
        ]
        options = {"A": "成都", "B": "长沙", "C": "重庆", "D": "武汉"}

        self.assertIsNone(infer_direct_evidence_option(evidence, options))

    def test_parse_json_and_plain_model_answers(self):
        self.assertEqual(parse_model_answer('{"answer":"C","confidence":0.9}'), "C")
        self.assertEqual(parse_model_answer("答案为 B。"), "B")
        self.assertIsNone(parse_model_answer("无法判断"))

    def test_structured_model_decision_preserves_confidence_and_citations(self):
        decision = parse_model_decision(
            '{"answer":"C","confidence":0.91,"evidence_ids":["web-1", "web-3"],'
            '"reason":"web-1 明确支持 C。"}'
        )
        self.assertEqual(decision["answer"], "C")
        self.assertEqual(decision["confidence"], 0.91)
        self.assertEqual(decision["evidence_ids"], ["web-1", "web-3"])
        self.assertEqual(decision["reason"], "web-1 明确支持 C。")

    def test_model_decision_requires_confidence_and_valid_web_citation(self):
        low = {
            "answer": "C",
            "confidence": 0.4,
            "evidence_ids": ["web-1"],
            "reason": "证据支持 C。",
        }
        answer, error = validate_model_decision(low, {"web-1"}, 0.62)
        self.assertIsNone(answer)
        self.assertIn("置信度不足", error)

        unexplained = {
            "answer": "C",
            "confidence": 0.9,
            "evidence_ids": ["web-1"],
            "reason": "",
        }
        answer, error = validate_model_decision(unexplained, {"web-1"}, 0.62)
        self.assertIsNone(answer)
        self.assertIn("没有提供证据解释", error)

        uncited = {
            "answer": "C",
            "confidence": 0.9,
            "evidence_ids": ["web-9"],
            "reason": "证据支持 C。",
        }
        answer, error = validate_model_decision(uncited, {"web-1"}, 0.62)
        self.assertIsNone(answer)
        self.assertIn("没有引用", error)

        verified = {
            "answer": "C",
            "confidence": 0.9,
            "evidence_ids": ["web-1"],
            "reason": "web-1 明确支持 C。",
        }
        self.assertEqual(
            validate_model_decision(verified, {"web-1"}, 0.62),
            ("C", None),
        )

    def test_model_decision_rejects_unsupported_option_guess(self):
        decision = {
            "answer": "D",
            "confidence": 0.95,
            "evidence_ids": ["web-1", "web-2"],
            "reason": "把10点和额外2点拼成12点。",
        }
        evidence = [
            {
                "id": "web-1",
                "title": "星盘调整",
                "snippet": "每条属性加2点，合10点。",
                "url": "https://ds.163.com/a",
            },
            {
                "id": "web-2",
                "title": "平衡之力",
                "snippet": "所有属性点加2。",
                "url": "https://my.163.com/b",
            },
        ]

        answer, error = validate_model_decision(
            decision,
            {"web-1", "web-2"},
            0.62,
            evidence=evidence,
            options={"A": "10", "B": "8", "C": "14", "D": "12"},
        )

        self.assertIsNone(answer)
        self.assertIn("未明确支持", error)

    def test_true_false_decision_accepts_substantive_cited_fact(self):
        question = "向别人介绍自己的弟弟妹妹应该用舍弟舍妹？"
        options = {"A": "不是", "B": "是的"}
        evidence = [{
            "id": "web-3",
            "title": '"舍弟舍妹"的正确用法',
            "snippet": (
                "向别人介绍自己的弟弟妹妹，正确且得体的谦辞确实是"
                "舍弟和舍妹。这两个词专用于称呼自己辈分低或年纪小的"
                "弟弟和妹妹。"
            ),
        }]
        decision = {
            "answer": "B",
            "confidence": 0.95,
            "evidence_ids": ["web-3"],
            "reason": (
                "向别人介绍自己辈分低或年纪小的弟弟妹妹时，应该使用"
                "舍弟和舍妹，因此题目中的陈述是正确的。"
            ),
        }

        self.assertEqual(
            validate_model_decision(
                decision,
                {"web-3"},
                evidence=evidence,
                options=options,
                question=question,
            ),
            ("B", None),
        )

    def test_true_false_decision_rejects_question_echo_without_fact(self):
        question = "向别人介绍自己的弟弟妹妹应该用舍弟舍妹？"
        decision = {
            "answer": "B",
            "confidence": 0.95,
            "evidence_ids": ["web-1"],
            "reason": "题目原句声称应该使用舍弟舍妹，因此陈述正确。",
        }

        answer, error = validate_model_decision(
            decision,
            {"web-1"},
            evidence=[{"id": "web-1", "title": question, "snippet": ""}],
            options={"A": "不是", "B": "是的"},
            question=question,
        )

        self.assertIsNone(answer)
        self.assertIn("未明确支持", error)

    def test_true_false_decision_rejects_answer_reason_polarity_mismatch(self):
        question = "向别人介绍自己的弟弟妹妹应该用舍弟舍妹？"
        decision = {
            "answer": "A",
            "confidence": 0.95,
            "evidence_ids": ["web-3"],
            "reason": "舍弟舍妹是正确谦辞，因此题目陈述正确。",
        }
        evidence = [{
            "id": "web-3",
            "title": "亲属谦称",
            "snippet": "舍弟舍妹是称呼自己弟弟妹妹的正确传统谦辞。",
        }]

        answer, error = validate_model_decision(
            decision,
            {"web-3"},
            evidence=evidence,
            options={"A": "不是", "B": "是的"},
            question=question,
        )

        self.assertIsNone(answer)
        self.assertIn("未明确支持", error)

    def test_negative_skill_question_requires_a_complete_list_without_target(self):
        question = "下列神兽中初始技能没有感知的是"
        options = {"A": "超级大熊猫", "D": "超级赤焰兽"}
        evidence = [
            {
                "id": "web-a", "title": "超级大熊猫技能",
                "snippet": "超级大熊猫必带5个技能，包括高级连击、高级感知等。",
            },
            {
                "id": "web-d", "title": "超级赤焰兽技能",
                "snippet": "超级赤焰兽必带5个技能：八凶法阵、高级法术连击、高级法术波动、神佑复生、火属性吸收。",
            },
        ]

        wrong = validate_model_decision(
            {"answer": "A", "confidence": 0.95, "evidence_ids": ["web-a"], "reason": "A没有感知。"},
            {"web-a", "web-d"}, evidence=evidence, options=options, question=question,
        )
        right = validate_model_decision(
            {"answer": "D", "confidence": 0.95, "evidence_ids": ["web-d"], "reason": "D的完整必带列表没有感知。"},
            {"web-a", "web-d"}, evidence=evidence, options=options, question=question,
        )

        self.assertIsNone(wrong[0])
        self.assertEqual(right, ("D", None))

    def test_model_decision_remaps_external_answer_letter_by_text(self):
        evidence = [{
            "id": "web-1",
            "title": "火车鸣笛题",
            "snippet": "A.前进 C.倒退 B.停留 D.故障 答案:C",
            "url": "https://example.com/train",
        }]
        wrong = {
            "answer": "C",
            "confidence": 0.95,
            "evidence_ids": ["web-1"],
            "reason": "直接沿用网页字母C。",
        }
        correct = {**wrong, "answer": "B", "reason": "网页C对应倒退，映射为当前B。"}
        options = {"A": "故障", "B": "倒退", "C": "停留", "D": "前进"}

        self.assertIsNone(validate_model_decision(
            wrong,
            {"web-1"},
            evidence=evidence,
            options=options,
        )[0])
        self.assertEqual(validate_model_decision(
            correct,
            {"web-1"},
            evidence=evidence,
            options=options,
        ), ("B", None))

    def test_fullwidth_option_prefix_is_removed(self):
        self.assertEqual(keju_hybrid._clean_option_text("B", "Ｂ６"), "6")

    def test_question_ocr_drops_low_confidence_decorative_fragments(self):
        results = [
            SimpleNamespace(text="1", box=[394, 196, 24, 21], score=0.21),
            SimpleNamespace(text=",i", box=[412, 200, 12, 14], score=0.50),
            SimpleNamespace(text="第3题", box=[415, 193, 63, 28], score=1.0),
            SimpleNamespace(
                text="正确率：1/2当前得分：10",
                box=[622, 200, 243, 20],
                score=1.0,
            ),
            SimpleNamespace(
                text="召唤灵内丹，最高可以升至几级？",
                box=[389, 230, 290, 22],
                score=0.98,
            ),
        ]

        raw_question = keju_hybrid._question_ocr_text(results)

        self.assertNotIn(",i", raw_question)
        self.assertEqual(
            keju_hybrid._clean_question_text(raw_question),
            "召唤灵内丹,最高可以升至几级?",
        )

    def test_question_cleanup_handles_round_without_total_and_icon_noise(self):
        self.assertEqual(
            keju_hybrid._clean_question_text(
                "国第2题正确率：1/1当前得分：10“环装”指的是多少级的普通装备？"
            ),
            '“环装”指的是多少级的普通装备?',
        )
        self.assertEqual(
            keju_hybrid._clean_question_text(
                "第4/15题剩余时间12白娘娘盗仙草盗的是？"
            ),
            "白娘娘盗仙草盗的是?",
        )

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

    def test_live_question_with_bare_numeric_options_is_not_a_result_page(self):
        self.assertFalse(
            looks_like_answer_result(
                "以下哪一年是国际天文年？",
                {"A": "2008", "B": "2009", "C": "2010", "D": "2012"},
            )
        )

    def test_macos_question_roi_covers_two_lines_and_stops_before_options(self):
        question_bottom = (
            keju_hybrid.MAC_QUESTION_ROI[1]
            + keju_hybrid.MAC_QUESTION_ROI[3]
        )
        self.assertLessEqual(question_bottom, keju_hybrid.MAC_OPTION_ROIS["A"][1])
        self.assertGreaterEqual(keju_hybrid.MAC_QUESTION_ROI[3], 80)

    def test_title_uses_expanded_fallback_before_rejecting_exam_type(self):
        recognizer = object.__new__(keju_hybrid.KejuHybridAnswer)
        image = np.ones((720, 923, 3), dtype=np.uint8)

        with (
            patch.dict("os.environ", {"MAA_KEJU_EXAM_TYPE": "xiangshi"}),
            patch.object(
                keju_hybrid,
                "_ocr_region",
                side_effect=["", "科举乡试", ""],
            ),
        ):
            recognizer._analyze(
                SimpleNamespace(),
                SimpleNamespace(image=image),
                keju_hybrid._StageTimings(),
            )

        self.assertEqual(recognizer.last_exam_type, "xiangshi")
        self.assertEqual(recognizer.last_status, "no_question")

    def test_formal_question_without_round_prefix_can_be_answered(self):
        recognizer = object.__new__(keju_hybrid.KejuHybridAnswer)
        image = np.ones((720, 923, 3), dtype=np.uint8)
        context = SimpleNamespace(get_node_data=lambda _name: {"attach": {}})
        option_results = iter([
            ("A.泥鳅", [340, 315, 225, 80]),
            ("B.鲤鱼", [570, 315, 230, 80]),
            ("C.河豚", [340, 395, 225, 75]),
            ("D.鲶鱼", [570, 395, 230, 75]),
        ])
        candidates = [{
            "question": "身上滑腻腻喜欢钻河底张嘴吐泡泡可以测天气打一动物",
            "answers": ["泥鳅"],
            "similarity": 100,
        }]

        with (
            patch.dict("os.environ", {
                "MAA_KEJU_EXAM_TYPE": "xiangshi",
                "MAA_KEJU_RECOGNIZE_ONLY": "1",
                "MAA_KEJU_REQUIRE_ROUND_ONE": "1",
            }),
            patch.object(
                keju_hybrid,
                "_ocr_region",
                side_effect=[
                    "科举乡试",
                    "剩余时间20身上滑腻腻喜欢钻河底张嘴吐泡泡可以测天气打一动物",
                ],
            ),
            patch.object(
                keju_hybrid,
                "_ocr_region_with_box",
                side_effect=lambda *_args: next(option_results),
            ),
            patch.object(keju_hybrid, "search_candidates", return_value=candidates),
            patch.object(keju_hybrid, "search_knowledge", return_value=[]),
        ):
            recognizer._analyze(
                context,
                SimpleNamespace(image=image),
                keju_hybrid._StageTimings(),
            )
            self.assertNotIn("MAA_KEJU_REQUIRE_ROUND_ONE", os.environ)

        self.assertEqual(recognizer.last_status, "ready")
        self.assertEqual(recognizer.last_answer, "A")
        self.assertEqual(recognizer.last_options["A"], "泥鳅")

    def test_huishi_recognizer_answers_reasoned_model_guess_without_evidence(self):
        """Timed 会试 must not inherit 乡试's strict-evidence stop behavior."""
        recognizer = object.__new__(keju_hybrid.KejuHybridAnswer)
        image = np.ones((720, 923, 3), dtype=np.uint8)
        context = SimpleNamespace(get_node_data=lambda _name: {"attach": {}})
        option_results = iter([
            ("A.甲", [340, 315, 225, 80]),
            ("B.乙", [570, 315, 230, 80]),
            ("C.丙", [340, 395, 225, 75]),
            ("D.丁", [570, 395, 230, 75]),
        ])

        with (
            patch.dict("os.environ", {
                "MAA_KEJU_EXAM_TYPE": "huishi",
                "MAA_KEJU_RECOGNIZE_ONLY": "1",
                "MAA_KEJU_API_KEY": "test-key",
                "MAA_KEJU_URL": "https://example.test/v1/chat/completions",
                "MAA_KEJU_MODEL": "test-model",
                "MAA_KEJU_WEB_SEARCH_ENABLED": "0",
            }),
            patch.object(
                keju_hybrid,
                "_ocr_region",
                side_effect=[
                    "科举会试",
                    "第3/20题剩余时间20冷门会试题",
                ],
            ),
            patch.object(
                keju_hybrid,
                "_ocr_region_with_box",
                side_effect=lambda *_args: next(option_results),
            ),
            patch.object(keju_hybrid, "search_candidates", return_value=[]),
            patch.object(keju_hybrid, "search_knowledge", return_value=[]),
            patch.object(
                keju_hybrid,
                "_request_model",
                return_value=json.dumps({
                    "answer": "C",
                    "confidence": 0.35,
                    "evidence_ids": [],
                    "reason": "无检索证据，根据题意低置信度选择 C。",
                }),
            ),
        ):
            recognizer._analyze(
                context,
                SimpleNamespace(image=image),
                keju_hybrid._StageTimings(),
            )

        self.assertEqual(recognizer.last_status, "ready")
        self.assertEqual(recognizer.last_answer, "C")
        self.assertEqual(recognizer.last_source, "huishi_forced_model_guess")

    def test_recognizer_uses_codex_when_configured_api_fails(self):
        recognizer = object.__new__(keju_hybrid.KejuHybridAnswer)
        image = np.ones((720, 923, 3), dtype=np.uint8)
        context = SimpleNamespace(get_node_data=lambda _name: {"attach": {}})
        option_results = iter([
            ("A.随遇而安", [340, 315, 225, 80]),
            ("B.出生入死", [570, 315, 230, 80]),
            ("C.一路顺风", [340, 395, 225, 75]),
            ("D.荣归故里", [570, 395, 230, 75]),
        ])

        with (
            patch.dict("os.environ", {
                "MAA_KEJU_EXAM_TYPE": "xiangshi",
                "MAA_KEJU_RECOGNIZE_ONLY": "1",
                "MAA_KEJU_API_KEY": "test-key",
                "MAA_KEJU_URL": "https://example.test/v1/chat/completions",
                "MAA_KEJU_MODEL": "test-model",
                "MAA_KEJU_USE_CODEX": "1",
            }),
            patch.object(
                keju_hybrid,
                "_ocr_region",
                side_effect=["科举乡试", "第4/10题小李广回乡猜一成语"],
            ),
            patch.object(
                keju_hybrid,
                "_ocr_region_with_box",
                side_effect=lambda *_args: next(option_results),
            ),
            patch.object(keju_hybrid, "search_candidates", return_value=[]),
            patch.object(keju_hybrid, "search_knowledge", return_value=[]),
            patch.object(keju_hybrid, "search_web_evidence", return_value={
                "query": "小李广回乡 猜成语", "queries": [],
                "evidence": [{
                    "id": "web-1", "provider": "test", "kind": "result",
                    "title": "谜底", "snippet": "荣归故里",
                    "url": "https://example.test/riddle", "rank": 1,
                }],
                "providers": {}, "elapsed_ms": 1,
            }),
            patch.object(
                keju_hybrid,
                "_request_model",
                side_effect=keju_hybrid.requests.HTTPError("400 Bad Request"),
            ),
            patch.object(
                keju_hybrid,
                "_request_codex_model",
                return_value=json.dumps({
                    "answer": "D", "confidence": 0.95,
                    "evidence_ids": ["web-1"], "reason": "谜底为荣归故里。",
                }),
            ),
        ):
            recognizer._analyze(
                context,
                SimpleNamespace(image=image),
                keju_hybrid._StageTimings(),
            )

        self.assertEqual(recognizer.last_status, "ready")
        self.assertEqual(recognizer.last_answer, "D")
        self.assertEqual(recognizer.last_source, "web_search_verified_codex")

    def test_recognizer_preserves_raw_round_metadata_after_cleaning_question(self):
        recognizer = object.__new__(keju_hybrid.KejuHybridAnswer)
        image = np.ones((720, 923, 3), dtype=np.uint8)
        context = SimpleNamespace(get_node_data=lambda _name: {"attach": {}})
        option_results = iter([
            ("A.五行", [340, 315, 225, 80]),
            ("B.人物", [570, 315, 230, 80]),
            ("C.星宿", [340, 395, 225, 75]),
            ("D.动物", [570, 395, 230, 75]),
        ])
        candidates = [{
            "question": "人生不相见动如参与商里的参与商是指什么",
            "answers": ["星宿"],
            "similarity": 100,
        }]

        with (
            patch.dict("os.environ", {
                "MAA_KEJU_EXAM_TYPE": "huishi",
                "MAA_KEJU_RECOGNIZE_ONLY": "1",
            }),
            patch.object(
                keju_hybrid,
                "_ocr_region",
                side_effect=[
                    "科举会试",
                    "第15/15题剩余时间20人生不相见动如参与商里的参与商是指什么",
                ],
            ),
            patch.object(
                keju_hybrid,
                "_ocr_region_with_box",
                side_effect=lambda *_args: next(option_results),
            ),
            patch.object(keju_hybrid, "search_candidates", return_value=candidates),
            patch.object(keju_hybrid, "search_knowledge", return_value=[]),
        ):
            recognizer._analyze(
                context,
                SimpleNamespace(image=image),
                keju_hybrid._StageTimings(),
            )

        self.assertEqual(recognizer.last_round_number, 15)
        self.assertEqual(recognizer.last_question_round, (15, 15))

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

    def test_wait_for_entry_keeps_main_screen_read_only_until_exam_page_appears(self):
        recognizer = object.__new__(keju_hybrid.KejuHybridAnswer)
        image = np.ones((720, 923, 3), dtype=np.uint8)

        with (
            patch.dict("os.environ", {
                "MAA_KEJU_EXAM_TYPE": "huishi",
                "MAA_KEJU_WAIT_FOR_ENTRY": "1",
            }),
            patch.object(keju_hybrid, "_ocr_region", return_value=""),
            patch.object(keju_hybrid, "search_candidates") as search,
        ):
            result = recognizer._analyze(
                SimpleNamespace(),
                SimpleNamespace(image=image),
                keju_hybrid._StageTimings(),
            )

        self.assertEqual(recognizer.last_status, "waiting")
        self.assertEqual(recognizer.last_reason, "等待手动进入科举正式答题页面")
        self.assertEqual(tuple(result.box), (0, 0, 0, 0))
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
