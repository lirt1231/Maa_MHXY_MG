import threading
import unittest
import sys
from pathlib import Path

import requests

RECOGNITION_DIR = Path(__file__).resolve().parents[1] / "agent" / "custom" / "recognition"
sys.path.insert(0, str(RECOGNITION_DIR))

from keju_web_search import (  # noqa: E402
    clean_search_question,
    parse_bing_html,
    parse_so360_html,
    search_web_evidence,
)


class SearchQuestionTests(unittest.TestCase):
    def test_live_score_and_ocr_prefix_are_removed(self):
        noisy = "4第3题正确率：1/2当前得分：10千言万语，猜一字"
        self.assertEqual(clean_search_question(noisy), "千言万语,猜一字")

    def test_fullwidth_text_is_normalized(self):
        self.assertEqual(clean_search_question("第2题 ＡＢＣ"), "ABC")


class SearchParserTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
