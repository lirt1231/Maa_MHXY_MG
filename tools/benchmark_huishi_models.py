#!/usr/bin/env python3
"""Benchmark OpenAI-compatible models against recorded 会试 questions.

This command is deliberately offline with respect to the game: it uses the
production 会试 prompt and recorded OCR-clean questions, but never captures or
clicks the client.  It emits one JSON record per model and a final ranking.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from custom.recognition.keju_answer_core import (  # noqa: E402
    build_verification_prompt,
    parse_model_decision,
)


DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "huishi_2026_08_22.json"
DEFAULT_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash-nothinking",
    "gemini-3-flash-preview-nothinking",
    "gemini-2.5-flash-lite",
    "qwen-turbo",
    "qwen3.5-flash",
    "deepseek-v4-flash",
    "glm-4.5-flash",
    "gpt-4.1-mini",
    "gpt-5-nano",
)


def _load_runner_module():
    spec = importlib.util.spec_from_file_location(
        "keju_runner", ROOT / "tools" / "run_keju_macos.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载会试运行器配置")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request_answer(url, api_key, model, prompt, timeout, max_tokens):
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是严谨的中文单选题答题助手。检索证据明确时必须服从证据，"
                    "并按要求返回包含引用和解释的 JSON。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    started_at = time.perf_counter()
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return elapsed_ms, content, None
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        return round((time.perf_counter() - started_at) * 1000), "", exc


def benchmark_model(model, questions, *, url, api_key, timeout, max_tokens):
    rows = []
    for item in questions:
        prompt = build_verification_prompt(
            item["rawQuestion"], item["options"], [], [], [], force_answer=True
        )
        elapsed_ms, content, error = _request_answer(
            url, api_key, model, prompt, timeout, max_tokens
        )
        decision = (
            parse_model_decision(content, tuple(item["options"]))
            if error is None
            else None
        )
        answer = decision["answer"] if decision else None
        rows.append(
            {
                "round": item["round"],
                "expected": item["expectedAnswer"],
                "answer": answer,
                "correct": answer == item["expectedAnswer"],
                "latency_ms": elapsed_ms,
                "status": (
                    type(error).__name__
                    if error is not None
                    else "ok"
                    if decision is not None
                    else "invalid_json"
                ),
            }
        )
    latencies = [row["latency_ms"] for row in rows]
    return {
        "model": model,
        "questions": len(rows),
        "correct": sum(row["correct"] for row in rows),
        "valid_responses": sum(row["status"] == "ok" for row in rows),
        "within_timeout": sum(
            row["status"] == "ok" and row["latency_ms"] <= timeout * 1000
            for row in rows
        ),
        "p50_ms": round(statistics.median(latencies)) if latencies else 0,
        "max_ms": max(latencies, default=0),
        "rows": rows,
    }


def _ranking_key(result):
    return (
        -result["correct"],
        -result["within_timeout"],
        -result["valid_responses"],
        result["p50_ms"],
        result["max_ms"],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--round", type=int, action="append", dest="rounds")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-tokens", type=int, default=220)
    args = parser.parse_args()
    if args.timeout <= 0 or args.max_tokens <= 0:
        parser.error("--timeout 和 --max-tokens 必须为正数")

    runner = _load_runner_module()
    profile = runner.model_profile_for_exam(
        runner.load_local_model_config(), "huishi"
    )
    api_key = runner.read_keychain_password(
        str(profile.get("keychain_service", "")),
        str(profile.get("keychain_account", "")),
    )
    if not api_key:
        raise RuntimeError("会试模型钥匙串凭据不可用")
    url = str(profile.get("url", ""))
    if not url:
        raise RuntimeError("会试模型 URL 未配置")

    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    selected_rounds = set(args.rounds) if args.rounds else None
    questions = [
        item
        for item in payload["questions"]
        if selected_rounds is None or item["round"] in selected_rounds
    ]
    if not questions:
        parser.error("没有匹配的回放题目")

    results = []
    for model in args.models or DEFAULT_MODELS:
        result = benchmark_model(
            model,
            questions,
            url=url,
            api_key=api_key,
            timeout=args.timeout,
            max_tokens=args.max_tokens,
        )
        results.append(result)
        print("benchmark_json=" + json.dumps(result, ensure_ascii=False), flush=True)

    ranked = sorted(results, key=_ranking_key)
    print(
        "benchmark_summary="
        + json.dumps(
            {
                "questions": len(questions),
                "timeout_seconds": args.timeout,
                "ranking": [
                    {
                        key: result[key]
                        for key in (
                            "model",
                            "correct",
                            "within_timeout",
                            "valid_responses",
                            "p50_ms",
                            "max_ms",
                        )
                    }
                    for result in ranked
                ],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
