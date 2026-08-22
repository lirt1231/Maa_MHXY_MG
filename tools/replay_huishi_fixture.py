#!/usr/bin/env python3
"""Replay recorded 会试 OCR frames through the production answer recognizer."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "tools"))

from custom.recognition import keju_hybrid  # noqa: E402
from run_keju_macos import (  # noqa: E402
    load_local_model_config,
    model_profile_for_exam,
    read_keychain_password,
)


DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "huishi_2026_08_22.json"


def _configure_model() -> None:
    config = model_profile_for_exam(load_local_model_config(), "huishi")
    mappings = {
        "url": "MAA_KEJU_URL",
        "model": "MAA_KEJU_MODEL",
        "fallback_model": "MAA_KEJU_FALLBACK_MODEL",
        "timeout": "MAA_KEJU_TIMEOUT",
        "max_tokens": "MAA_KEJU_MAX_TOKENS",
        "minimum_confidence": "MAA_KEJU_MODEL_MIN_CONFIDENCE",
    }
    for key, environment_name in mappings.items():
        if config.get(key) not in (None, ""):
            os.environ[environment_name] = str(config[key])
    if config.get("extra_body"):
        os.environ["MAA_KEJU_MODEL_EXTRA_BODY"] = json.dumps(
            config["extra_body"], ensure_ascii=False
        )
    web_search = config.get("web_search") or {}
    if "enabled" in web_search:
        os.environ["MAA_KEJU_WEB_SEARCH_ENABLED"] = (
            "1" if web_search["enabled"] else "0"
        )
    if web_search.get("timeout"):
        os.environ["MAA_KEJU_WEB_SEARCH_TIMEOUT"] = str(web_search["timeout"])
    if web_search.get("providers"):
        os.environ["MAA_KEJU_WEB_SEARCH_PROVIDERS"] = ",".join(
            map(str, web_search["providers"])
        )
    if not os.environ.get("MAA_KEJU_API_KEY"):
        key = read_keychain_password(
            str(config.get("keychain_service", "")),
            str(config.get("keychain_account", "")),
        )
        if key:
            os.environ["MAA_KEJU_API_KEY"] = key
    os.environ.update({
        "MAA_DIRECT_RESOURCE": "1",
        "MAA_KEJU_EXAM_TYPE": "huishi",
        "MAA_KEJU_RECOGNIZE_ONLY": "1",
    })


def replay_question(item: dict) -> dict:
    recognizer = object.__new__(keju_hybrid.KejuHybridAnswer)
    image = np.ones((720, 923, 3), dtype=np.uint8)
    options = item["options"]
    option_results = iter(
        (f"{choice}.{text}", [340, 315, 225, 80])
        for choice, text in options.items()
    )
    started = time.perf_counter()
    with (
        patch.dict(os.environ, {
            "MAA_DIRECT_RESOURCE": "1",
            "MAA_KEJU_EXAM_TYPE": "huishi",
            "MAA_KEJU_RECOGNIZE_ONLY": "1",
        }),
        patch.object(
            keju_hybrid, "_ocr_region",
            side_effect=["科举会试", item["rawQuestion"]],
        ),
        patch.object(
            keju_hybrid, "_ocr_region_with_box",
            side_effect=lambda *_args: next(option_results),
        ),
    ):
        recognizer._analyze(
            SimpleNamespace(get_node_data=lambda _name: {"attach": {}}),
            SimpleNamespace(image=image),
            keju_hybrid._StageTimings(),
        )
    return {
        "round": item["round"],
        "status": getattr(recognizer, "last_status", "unknown"),
        "answer": getattr(recognizer, "last_answer", ""),
        "recordedAnswer": item.get("answer"),
        "source": getattr(recognizer, "last_source", ""),
        "reason": getattr(recognizer, "last_reason", ""),
        "elapsedMs": round((time.perf_counter() - started) * 1000),
    }


def replay_fixture(path: Path, rounds: set[int] | None = None) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        replay_question(item)
        for item in payload["questions"]
        if rounds is None or item["round"] in rounds
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--round", type=int, action="append", dest="rounds")
    args = parser.parse_args()
    _configure_model()
    results = replay_fixture(
        args.fixture, set(args.rounds) if args.rounds else None
    )
    for result in results:
        print("replay_json=" + json.dumps(result, ensure_ascii=False), flush=True)
    print("replay_summary=" + json.dumps({
        "questions": len(results),
        "changedAnswers": sum(
            item["answer"] != item["recordedAnswer"] for item in results
        ),
        "elapsedMs": sum(item["elapsedMs"] for item in results),
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
