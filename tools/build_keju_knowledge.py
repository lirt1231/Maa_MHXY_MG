#!/usr/bin/env python3
"""Build, inspect, and query the local 科举 SQLite knowledge database."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent" / "custom" / "recognition"))
from keju_knowledge import KejuKnowledgeStore  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--query", default="")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--db", default="")
    args = parser.parse_args()
    store = KejuKnowledgeStore(db_path=args.db or None)
    store.ensure_current(force=args.rebuild)
    print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    if args.query:
        print(json.dumps(store.search(args.query, limit=args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
