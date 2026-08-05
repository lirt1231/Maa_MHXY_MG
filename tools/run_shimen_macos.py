#!/usr/bin/env python3
"""Start a sect mission and hand the remaining automation back to the game."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from automation_workflows import (  # noqa: E402
    WorkflowContext,
    WorkflowInspection,
    WorkflowResult,
)
from macos_task_runtime import MacOSTaskRuntime, log_event  # noqa: E402
from shimen_recognition import classify_shimen_scene  # noqa: E402
from shimen_workflow import ShimenWorkflow  # noqa: E402


RESULT_PREFIX = "result_json="
RESULT_STATUSES = {"completed", "already_completed", "safe_stopped", "cancelled"}
MAX_TASK_CLICKS = 40
MAX_DAILY_LOADING_POLLS = 8
MAX_DAILY_RESELECTS = 2


def emit_result(status, **payload):
    if status not in RESULT_STATUSES:
        raise ValueError(f"unsupported result status: {status}")
    print(
        RESULT_PREFIX
        + json.dumps({"task_id": "shimen", "status": status, **payload}, ensure_ascii=False),
        flush=True,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect-scene", action="store_true")
    parser.add_argument("--recognize-only", action="store_true")
    parser.add_argument("--max-steps", type=int, default=360)
    args = parser.parse_args()
    if args.max_steps <= 0:
        parser.error("--max-steps 必须是正整数")
    return args


def run(args, guard):
    runtime = MacOSTaskRuntime(guard)
    workflow = ShimenWorkflow(
        max_steps=args.max_steps,
        max_task_clicks=MAX_TASK_CLICKS,
        max_daily_loading_polls=MAX_DAILY_LOADING_POLLS,
        max_daily_reselects=MAX_DAILY_RESELECTS,
        classifier=classify_shimen_scene,
    )
    outcome = workflow.execute(
        WorkflowContext(runtime, guard, log_event, runtime.inspection_evidence),
        inspect=args.inspect_scene or args.recognize_only,
    )
    if isinstance(outcome, WorkflowInspection):
        print(
            "preflight_json="
            + json.dumps(outcome.to_payload(), ensure_ascii=False),
            flush=True,
        )
        return 0
    if not isinstance(outcome, WorkflowResult):
        raise RuntimeError("师门 Workflow 返回了未知结果")
    emit_result(outcome.status, **outcome.payload)
    return 0


def main():
    args = parse_args()
    guard = CancellationGuard()
    guard.install_signal_handlers()
    read_only = args.inspect_scene or args.recognize_only
    try:
        return run(args, guard)
    except CancellationRequested:
        guard.wait_for_tasker_stop()
        if not read_only:
            emit_result("cancelled", reason="收到停止请求")
        print("stop_ack=true", flush=True)
        return 0
    except Exception as exc:
        if not read_only:
            emit_result("safe_stopped", reason=str(exc).strip() or exc.__class__.__name__)
        print(f"safe_stop_reason={exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
