import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


RECOGNITION_DIR = Path(__file__).resolve().parents[1] / "agent" / "custom" / "recognition"
sys.path.insert(0, str(RECOGNITION_DIR))

from keju_knowledge import KejuKnowledgeStore  # noqa: E402
from keju_web_search import clean_search_question  # noqa: E402


class KejuKnowledgeStoreTests(unittest.TestCase):
    def make_store(self, directory, seed_content='{"documents": []}', bank_content='"火神是谁？":["祝融"]'):
        directory = Path(directory)
        seed_path = directory / "seed.json"
        bank_path = directory / "bank.txt"
        seed_path.write_text(seed_content, encoding="utf-8")
        bank_path.write_text(bank_content, encoding="utf-8")
        return KejuKnowledgeStore(directory / "knowledge.sqlite3", seed_path, bank_path)

    def test_empty_or_damaged_seed_still_indexes_question_bank(self):
        for seed_content in ("", "{damaged", '{"documents": [null, "bad"]}'):
            with self.subTest(seed_content=seed_content), tempfile.TemporaryDirectory() as directory:
                store = self.make_store(directory, seed_content=seed_content)
                self.assertEqual(store.stats()["by_origin"], {"question_bank": 1})
                self.assertEqual(store.search("火神是谁", limit=1)[0]["answer"], "祝融")

    def test_search_falls_back_when_fts_table_is_unusable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            store.ensure_current()
            with sqlite3.connect(store.db_path) as connection:
                connection.execute("DROP TABLE IF EXISTS documents_fts")
                connection.execute("CREATE TABLE documents_fts (id TEXT, tokens TEXT)")
            results = store.search("火神是谁", limit=1)
            self.assertEqual(results[0]["answer"], "祝融")

    def test_search_recovers_when_fts_table_was_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            store.ensure_current()
            with sqlite3.connect(store.db_path) as connection:
                connection.execute("DROP TABLE documents_fts")
            self.assertEqual(store.search("火神是谁", limit=1)[0]["answer"], "祝融")
            with sqlite3.connect(store.db_path) as connection:
                fts_count = connection.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
            self.assertEqual(fts_count, store.stats()["documents"])

    def test_malformed_seed_fields_are_normalized(self):
        document = {
            "documents": [{
                "id": "malformed",
                "title": None,
                "content": 123,
                "answer": None,
                "aliases": "数字内容",
                "tags": 7,
                "trust": "nan",
            }]
        }
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(
                directory,
                seed_content=json.dumps(document, ensure_ascii=False),
                bank_content="",
            )
            results = store.search("数字内容", limit=1)
            self.assertEqual(results[0]["id"], "malformed")
            self.assertEqual(results[0]["trust"], 0.5)

    def test_force_rebuild_restores_managed_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            store.ensure_current()
            with sqlite3.connect(store.db_path) as connection:
                connection.execute("UPDATE documents SET answer='错误答案'")
            store.ensure_current(force=True)
            self.assertEqual(store.search("火神是谁", limit=1)[0]["answer"], "祝融")

    def test_fingerprint_distinguishes_missing_and_empty_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory, seed_content="")
            empty_fingerprint = store._source_fingerprint()
            store.seed_path.unlink()
            self.assertNotEqual(empty_fingerprint, store._source_fingerprint())

    def test_limit_boundaries_do_not_create_database(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            self.assertEqual(store.search("火神是谁", limit=0), [])
            self.assertEqual(store.search("火神是谁", limit=-2), [])
            self.assertFalse(store.db_path.exists())
            with self.assertRaisesRegex(ValueError, "limit must be an integer"):
                store.search("火神是谁", limit=None)

    def test_equal_scores_have_reproducible_id_order(self):
        documents = {
            "documents": [
                {"id": "z-last", "title": "相同问题", "content": "相同内容", "answer": "甲"},
                {"id": "a-first", "title": "相同问题", "content": "相同内容", "answer": "甲"},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory, seed_content=json.dumps(documents, ensure_ascii=False), bank_content="")
            first = [item["id"] for item in store.search("相同问题", limit=2)]
            second = [item["id"] for item in store.search("相同问题", limit=2)]
            self.assertEqual(first, ["a-first", "z-last"])
            self.assertEqual(second, first)

    def test_default_seed_answers_shedi_shemei_without_web_search(self):
        with tempfile.TemporaryDirectory() as directory:
            empty_bank = Path(directory) / "bank.txt"
            empty_bank.write_text("", encoding="utf-8")
            store = KejuKnowledgeStore(
                Path(directory) / "knowledge.sqlite3",
                RECOGNITION_DIR / "keju_knowledge_seed.json",
                empty_bank,
            )

            result = store.search(
                "向别人介绍自己的弟弟妹妹应该用舍弟舍妹？",
                limit=1,
            )[0]

        self.assertEqual(result["id"], "xiangshi-shedi-shemei-honorific")
        self.assertEqual(result["answer"], "是的")
        self.assertGreaterEqual(result["similarity"], 90)

    def test_default_seed_answers_liubei_couplet_without_web_search(self):
        with tempfile.TemporaryDirectory() as directory:
            empty_bank = Path(directory) / "bank.txt"
            empty_bank.write_text("", encoding="utf-8")
            store = KejuKnowledgeStore(
                Path(directory) / "knowledge.sqlite3",
                RECOGNITION_DIR / "keju_knowledge_seed.json",
                empty_bank,
            )

            result = store.search(
                "《三国演义》中刘备见到诸葛亮家悬挂了一幅对联，"
                "写的是淡泊以明志宁静而致远？",
                limit=1,
            )[0]

        self.assertEqual(result["id"], "xiangshi-liubei-zhuge-couplet")
        self.assertEqual(result["answer"], "是的")
        self.assertGreaterEqual(result["similarity"], 90)

    def test_default_seed_answers_recorded_huishi_round(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "huishi_2026_08_22.json")
            .read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as directory:
            empty_bank = Path(directory) / "bank.txt"
            empty_bank.write_text("", encoding="utf-8")
            store = KejuKnowledgeStore(
                Path(directory) / "knowledge.sqlite3",
                RECOGNITION_DIR / "keju_knowledge_seed.json",
                empty_bank,
            )
            for item in fixture["questions"]:
                question = clean_search_question(item["rawQuestion"])
                result = store.search(question, limit=1)[0]
                self.assertEqual(
                    result["answer"], item["options"][item["expectedAnswer"]],
                    f"round {item['round']}: {result}",
                )
                self.assertGreaterEqual(
                    result["similarity"], 90,
                    f"round {item['round']}: {result}",
                )


if __name__ == "__main__":
    unittest.main()
