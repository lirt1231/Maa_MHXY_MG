"""SQLite-backed evidence retrieval for 科举 questions."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path

try:
    from .searchAnswer import load_question_bank, normalize_text
except ImportError:  # Allow standalone tools/tests to import this module.
    from searchAnswer import load_question_bank, normalize_text


MODULE_DIR = Path(__file__).resolve().parent
ROOT = MODULE_DIR.parents[2]
DEFAULT_SEED_PATH = MODULE_DIR / "keju_knowledge_seed.json"
DEFAULT_QUESTION_BANK_PATH = MODULE_DIR / "tiku.txt"
DEFAULT_DB_PATH = ROOT / "debug" / "custom" / "keju_knowledge.sqlite3"
INDEX_FORMAT_VERSION = "2"


def _ngrams(value, sizes=(2, 3)):
    text = normalize_text(value)
    grams = set()
    for size in sizes:
        grams.update(
            text[index : index + size]
            for index in range(max(0, len(text) - size + 1))
        )
    grams.update(re.findall(r"[a-z0-9]{2,}", text))
    return grams


def _searchable_text(document):
    return " ".join(
        str(value)
        for value in (
            document.get("title", ""),
            document.get("content", ""),
            document.get("answer", ""),
            *(document.get("aliases") or []),
            *(document.get("tags") or []),
        )
        if value
    )


def _document_id(prefix, *values):
    digest = hashlib.sha256("\0".join(map(str, values)).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:20]}"


def _text(value):
    return "" if value is None else str(value)


class KejuKnowledgeStore:
    def __init__(self, db_path=None, seed_path=DEFAULT_SEED_PATH, question_bank_path=DEFAULT_QUESTION_BANK_PATH):
        configured = os.environ.get("MAA_KEJU_KNOWLEDGE_DB", "").strip()
        self.db_path = Path(db_path or configured or DEFAULT_DB_PATH).resolve()
        self.seed_path = Path(seed_path).resolve()
        self.question_bank_path = Path(question_bank_path).resolve()

    def _source_fingerprint(self):
        digest = hashlib.sha256()
        digest.update(f"keju-knowledge-index:{INDEX_FORMAT_VERSION}\0".encode("ascii"))
        for path in (self.seed_path, self.question_bank_path):
            digest.update(str(path).encode("utf-8"))
            digest.update(b"\0")
            try:
                payload = path.read_bytes()
            except (OSError, ValueError):
                digest.update(b"missing\0")
            else:
                digest.update(b"present\0")
                digest.update(str(len(payload)).encode("ascii"))
                digest.update(b"\0")
                digest.update(payload)
        return digest.hexdigest()

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                answer TEXT NOT NULL DEFAULT '', aliases_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]', source_url TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '', source_kind TEXT NOT NULL DEFAULT '',
                trust REAL NOT NULL DEFAULT 0.5, updated_at TEXT NOT NULL DEFAULT '',
                origin TEXT NOT NULL DEFAULT 'seed', indexed_text TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        try:
            connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(id UNINDEXED, tokens)")
        except sqlite3.OperationalError:
            pass
        return connection

    def _load_seed_documents(self):
        try:
            payload = json.loads(self.seed_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        if isinstance(payload, dict):
            payload = payload.get("documents", [])
        if not isinstance(payload, list):
            return []
        return [document for document in payload if isinstance(document, dict)]

    def _load_question_bank_documents(self):
        if not self.question_bank_path.exists():
            return []
        documents = []
        for question, answers in load_question_bank(self.question_bank_path).items():
            answer = "、".join(answers)
            documents.append({
                "id": _document_id("bank", question, answer),
                "title": question,
                "content": f"科举题目：{question} 已收录答案：{answer}。",
                "answer": answer,
                "aliases": [question],
                "tags": ["既有题库"],
                "source_name": "项目既有题库",
                "source_kind": "question_bank",
                "trust": 0.72,
                "origin": "question_bank",
            })
        return documents

    def ensure_current(self, force=False):
        fingerprint = self._source_fingerprint()
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='source_fingerprint'").fetchone()
            count_row = connection.execute("SELECT value FROM metadata WHERE key='document_count'").fetchone()
            actual_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            try:
                fts_count = connection.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
            except sqlite3.OperationalError:
                fts_count = None
            if not force and row and row[0] == fingerprint and count_row:
                try:
                    expected_count = int(count_row[0])
                except (TypeError, ValueError):
                    expected_count = -1
                if actual_count == expected_count and fts_count in (None, actual_count):
                    return
            documents = self._load_seed_documents() + self._load_question_bank_documents()
            connection.execute("DELETE FROM documents WHERE origin IN ('seed', 'question_bank')")
            for document in documents:
                aliases = document.get("aliases") or []
                tags = document.get("tags") or []
                if not isinstance(aliases, (list, tuple)):
                    aliases = [aliases]
                if not isinstance(tags, (list, tuple)):
                    tags = [tags]
                aliases = [str(value) for value in aliases if value]
                tags = [str(value) for value in tags if value]
                normalized_document = {
                    "title": _text(document.get("title")),
                    "content": _text(document.get("content")),
                    "answer": _text(document.get("answer")),
                    "aliases": aliases,
                    "tags": tags,
                }
                indexed_tokens = " ".join(sorted(_ngrams(_searchable_text(normalized_document))))
                document_id = _text(document.get("id")) or _document_id(
                    "seed", normalized_document["title"], document.get("source_url", "")
                )
                origin = "question_bank" if document.get("origin") == "question_bank" else "seed"
                try:
                    trust = float(document.get("trust", 0.5))
                except (TypeError, ValueError):
                    trust = 0.5
                if not math.isfinite(trust):
                    trust = 0.5
                connection.execute(
                    """INSERT OR REPLACE INTO documents (
                        id,title,content,answer,aliases_json,tags_json,source_url,
                        source_name,source_kind,trust,updated_at,origin,indexed_text
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (document_id, normalized_document["title"], normalized_document["content"],
                     normalized_document["answer"], json.dumps(aliases, ensure_ascii=False),
                     json.dumps(tags, ensure_ascii=False), _text(document.get("source_url")),
                     _text(document.get("source_name")), _text(document.get("source_kind")),
                     trust, _text(document.get("updated_at")), origin, indexed_tokens),
                )
            try:
                connection.execute("DELETE FROM documents_fts")
                connection.execute("INSERT INTO documents_fts(id,tokens) SELECT id,indexed_text FROM documents")
            except sqlite3.OperationalError:
                pass
            connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('source_fingerprint',?)", (fingerprint,))
            document_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('document_count',?)", (str(document_count),))

    def search(self, query, limit=4):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise ValueError("limit must be an integer") from None
        if limit <= 0:
            return []
        query_normalized = normalize_text(query)
        query_grams = _ngrams(query)
        if not query_normalized or not query_grams:
            return []
        self.ensure_current()
        with self._connect() as connection:
            terms = sorted(_ngrams(query, sizes=(3,)), key=len, reverse=True)[:24] or sorted(query_grams)[:24]
            fts_query = " OR ".join(f'"{term}"' for term in terms)
            try:
                rows = connection.execute(
                    """SELECT d.* FROM documents_fts f JOIN documents d ON d.id=f.id
                       WHERE documents_fts MATCH ? ORDER BY bm25(documents_fts), d.id LIMIT ?""",
                    (fts_query, max(20, limit * 6)),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = connection.execute("SELECT * FROM documents").fetchall()
            if not rows:
                rows = connection.execute("SELECT * FROM documents ORDER BY id").fetchall()
        ranked = []
        for row in rows:
            searchable = " ".join([row["title"], row["content"], row["answer"], row["aliases_json"]])
            document_normalized = normalize_text(searchable)
            document_grams = _ngrams(searchable)
            overlap = len(query_grams & document_grams) / max(1, len(query_grams))
            sequence = SequenceMatcher(None, query_normalized, normalize_text(row["title"])).ratio()
            contains = query_normalized in document_normalized
            score = min(100, round(overlap * 72 + sequence * 18 + float(row["trust"]) * 6 + (8 if contains else 0)))
            if score >= 12:
                ranked.append({
                    "id": row["id"], "title": row["title"], "content": row["content"],
                    "answer": row["answer"], "similarity": int(score),
                    "source_url": row["source_url"], "source_name": row["source_name"],
                    "source_kind": row["source_kind"], "trust": float(row["trust"]),
                })
        ranked.sort(key=lambda item: (-item["similarity"], -item["trust"], item["id"]))
        return ranked[:limit]

    def stats(self):
        self.ensure_current()
        with self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            by_origin = dict(connection.execute("SELECT origin,COUNT(*) FROM documents GROUP BY origin").fetchall())
        return {"path": str(self.db_path), "documents": total, "by_origin": by_origin}


_default_store = None


def get_knowledge_store():
    global _default_store
    if _default_store is None:
        _default_store = KejuKnowledgeStore()
    return _default_store


def search_knowledge(query, limit=4):
    return get_knowledge_store().search(query, limit=limit)


__all__ = ["KejuKnowledgeStore", "get_knowledge_store", "search_knowledge"]
