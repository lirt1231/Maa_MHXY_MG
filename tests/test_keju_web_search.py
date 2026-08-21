import threading
import unittest
import sys
from pathlib import Path

import requests

RECOGNITION_DIR = Path(__file__).resolve().parents[1] / "agent" / "custom" / "recognition"
sys.path.insert(0, str(RECOGNITION_DIR))

from keju_web_search import (  # noqa: E402
    build_search_routes,
    clean_search_question,
    parse_bing_rss,
    parse_bing_html,
    parse_duckduckgo_html,
    parse_so360_html,
    search_web_evidence,
)


class SearchQuestionTests(unittest.TestCase):
    def test_live_score_and_ocr_prefix_are_removed(self):
        noisy = "4第3题正确率：1/2当前得分：10千言万语，猜一字"
        self.assertEqual(clean_search_question(noisy), "千言万语,猜一字")

    def test_fullwidth_text_is_normalized(self):
        self.assertEqual(clean_search_question("第2题 ＡＢＣ"), "ABC")

    def test_timer_label_without_ocr_number_is_removed(self):
        self.assertEqual(clean_search_question("剩余时间古代社会尊称"), "古代社会尊称")

    def test_contextual_route_contains_game_and_every_option(self):
        routes = build_search_routes(
            "剩余时间20下列神兽中初始技能没有感知的是",
            {"A": "超级大熊猫", "B": "超级神鼠", "C": "超级灵狐", "D": "超级赤焰兽"},
        )

        self.assertEqual([name for name, _query in routes], [
            "question",
            "option_evidence:A",
            "option_evidence:B",
            "option_evidence:C",
            "option_evidence:D",
        ])
        self.assertIn("梦幻西游手游", routes[1][1])
        self.assertIn("超级大熊猫", routes[1][1])
        self.assertIn("超级赤焰兽", routes[4][1])
        self.assertIn("必带技能 感知", routes[4][1])

    def test_numeric_options_do_not_pollute_game_context_query(self):
        routes = build_search_routes(
            "星盘技能平衡之力一共提供多少属性",
            {"A": "10", "B": "8", "C": "14", "D": "12"},
        )

        self.assertEqual(len(routes), 2)
        self.assertNotIn(" A 10", routes[1][1])
        self.assertNotIn(" 12", routes[1][1])

class SearchParserTests(unittest.TestCase):
    def test_duckduckgo_result_has_decoded_target_and_snippet(self):
        payload = """
        <div class="result"><a class="result__a"
          href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fds.163.com%2Fa">平衡之力</a>
          <div class="result__snippet">每种属性加2点，一共10点。</div></div>
        """
        evidence = parse_duckduckgo_html(payload)
        self.assertEqual(evidence[0]["provider"], "duckduckgo")
        self.assertEqual(evidence[0]["url"], "https://ds.163.com/a")
        self.assertIn("一共10点", evidence[0]["snippet"])

    def test_bing_rss_result_is_extracted(self):
        payload = """<?xml version="1.0"?><rss><channel><item>
          <title>元宵夜宴</title><link>https://example.com/red</link>
          <description>王熙凤在元宵节讲聋子放炮仗。</description>
        </item></channel></rss>"""
        evidence = parse_bing_rss(payload)
        self.assertEqual(evidence[0]["provider"], "bing_rss")
        self.assertIn("元宵节", evidence[0]["snippet"])

    def test_bing_direct_answer_xpath_is_prioritized(self):
        payload = """
        <html><body>
          <main id="gs_main">
            <div></div>
            <div><div></div><div><div><div><div></div><div><div>
              <span>9 个徒弟</span>
            </div></div></div></div></div></div>
          </main>
          <ol><li class="b_algo"><h2><a href="https://example.com/a">结果一</a></h2>
            <div class="b_caption"><p>最多同时拥有9个徒弟。</p></div>
          </li></ol>
        </body></html>
        """
        evidence = parse_bing_html(payload)
        self.assertEqual(evidence[0]["kind"], "direct_answer")
        self.assertEqual(evidence[0]["snippet"], "9 个徒弟")
        self.assertEqual(evidence[1]["title"], "结果一")

    def test_so360_result_has_title_snippet_and_source(self):
        payload = """
        <html><body><ul><li class="res-list">
          <h3 class="res-title"><a data-mdurl="https://example.com/current">当前规则</a></h3>
          <p class="res-desc">宗门等级提升后最多拥有9个徒弟。</p>
        </li></ul></body></html>
        """
        evidence = parse_so360_html(payload)
        self.assertEqual(evidence[0]["provider"], "so360")
        self.assertEqual(evidence[0]["url"], "https://example.com/current")
        self.assertIn("9个徒弟", evidence[0]["snippet"])


class SearchAggregationTests(unittest.TestCase):
    def test_game_question_discards_unrelated_search_results(self):
        def provider(_query, _timeout, _limit):
            return [
                {
                    "provider": "test", "kind": "result",
                    "title": "Best Vietnam Food", "snippet": "Pho and Banh Mi",
                    "url": "https://example.com/vietnam", "rank": 1,
                },
                {
                    "provider": "test", "kind": "result",
                    "title": "须弥海门派技能", "snippet": "须弥海技能说明",
                    "url": "https://example.com/xumihai", "rank": 2,
                },
            ]

        result = search_web_evidence(
            "下列选项中不属于须弥海门派技能的是",
            options={"A": "击水三千", "B": "扶摇直上"},
            providers={"test": provider},
        )

        self.assertEqual(len(result["evidence"]), 1)
        self.assertEqual(result["evidence"][0]["title"], "须弥海门派技能")

    def test_providers_start_concurrently_and_results_are_merged(self):
        barrier = threading.Barrier(2)

        def provider(name):
            def run(_query, _timeout, _limit):
                barrier.wait(timeout=1)
                return [{
                    "provider": name,
                    "kind": "result",
                    "title": f"{name} title",
                    "snippet": f"{name} evidence",
                    "url": f"https://{name}.example.com",
                    "rank": 1,
                }]
            return run

        result = search_web_evidence(
            "测试题目",
            providers={"first": provider("first"), "second": provider("second")},
        )
        self.assertEqual(len(result["evidence"]), 2)
        self.assertEqual(set(result["providers"]), {"first", "second"})
        self.assertEqual({item["id"] for item in result["evidence"]}, {"web-1", "web-2"})

    def test_one_provider_failure_keeps_other_provider_evidence(self):
        def failed(_query, _timeout, _limit):
            raise requests.Timeout("slow")

        def healthy(_query, _timeout, _limit):
            return [{
                "provider": "healthy",
                "kind": "result",
                "title": "answer",
                "snippet": "evidence",
                "url": "https://example.com",
                "rank": 1,
            }]

        result = search_web_evidence(
            "测试题目",
            providers={"failed": failed, "healthy": healthy},
        )
        self.assertEqual(len(result["evidence"]), 1)
        self.assertIn("Timeout", result["providers"]["failed"]["error"])

    def test_numeric_total_evidence_is_fetched_and_prioritized(self):
        observed_limits = []

        def duckduckgo(_query, _timeout, limit):
            observed_limits.append(limit)
            return [
                {
                    "provider": "duckduckgo", "kind": "result",
                    "title": f"结果{rank}",
                    "snippet": "每种类型加2点" if rank < 4 else "五种属性，一共10点",
                    "url": f"https://example.com/{rank}", "rank": rank,
                }
                for rank in range(1, limit + 1)
            ]

        result = search_web_evidence(
            "平衡之力一共提供多少属性",
            providers={"duckduckgo": duckduckgo},
            limit_per_provider=3,
        )

        self.assertEqual(observed_limits, [5])
        self.assertIn("一共10点", result["evidence"][0]["snippet"])
        self.assertEqual(len(result["evidence"]), 3)

    def test_question_and_contextual_routes_share_one_parallel_window(self):
        barrier = threading.Barrier(4)

        def provider(name):
            def run(query, _timeout, _limit):
                barrier.wait(timeout=1)
                return [{
                    "provider": name,
                    "kind": "result",
                    "title": f"{name} {query}",
                    "snippet": "route evidence",
                    "url": f"https://{name}.example.com/{len(query)}",
                    "rank": 1,
                }]
            return run

        result = search_web_evidence(
            "测试题目",
            options={"A": "答案甲", "B": "答案乙"},
            providers={"first": provider("first"), "second": provider("second")},
        )

        self.assertEqual(len(result["queries"]), 2)
        self.assertEqual(len(result["evidence"]), 4)
        self.assertEqual(set(result["providers"]), {
            "first", "second",
            "game_context:first", "game_context:second",
        })
        self.assertEqual(
            {item["query_route"] for item in result["evidence"]},
            {"question", "game_context"},
        )

if __name__ == "__main__":
    unittest.main()
