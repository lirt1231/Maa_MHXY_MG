#!/usr/bin/env python3
"""Read one macOS daily schedule frame without sending game input."""

from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from daily_schedule_macos_core import parse_daily_schedule  # noqa: E402
from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from macos_task_runtime import MacOSTaskRuntime  # noqa: E402


PROTOCOL_PREFIX = "game_data_json="


def _task_id(name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return f"daily-{digest}"


def _protocol_task(task: dict) -> dict:
    completed = task["completed"]
    target = task["target"]
    if completed is not None and target is not None and completed > target:
        completed = None
        target = None
    return {
        "taskId": _task_id(task["name"]),
        "name": task["name"],
        "completed": completed,
        "target": target,
        "status": task["status"],
        "rewardStatus": task["reward_status"],
    }


def collect_daily_schedule(guard: CancellationGuard) -> dict:
    runtime = MacOSTaskRuntime(guard)
    scene = runtime.read_scene()
    parsed_tasks = parse_daily_schedule(scene.texts)
    complete_parse = bool(parsed_tasks) and all(
        task["status"] != "unknown" for task in parsed_tasks
    )
    tasks = [_protocol_task(task) for task in parsed_tasks]
    return {
        "category": "daily_tasks",
        "source_page": "daily_schedule",
        "status": "success" if complete_parse else "partial",
        "data": {"tasks": tasks},
        "evidence": {
            "image_size": list(scene.image_size),
            "ocr_item_count": len(scene.texts),
        },
    }


def main() -> int:
    guard = CancellationGuard()
    guard.install_signal_handlers()
    try:
        payload = collect_daily_schedule(guard)
    except CancellationRequested:
        guard.wait_for_tasker_stop()
        print("daily_schedule_error=cancelled", file=sys.stderr, flush=True)
        return 1
    except Exception as exc:
        print(f"daily_schedule_error={exc}", file=sys.stderr, flush=True)
        return 1
    print(PROTOCOL_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
