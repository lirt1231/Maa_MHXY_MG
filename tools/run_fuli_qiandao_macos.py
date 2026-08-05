#!/usr/bin/env python3
"""Safely complete the daily 福利 scratch-card sign-in on macOS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from automation_workflows import (  # noqa: E402
    WorkflowContext,
    WorkflowInspection,
    WorkflowResult,
)
from fuli_qiandao_recognition import classify_welfare_scene  # noqa: E402
from fuli_qiandao_workflow import WelfareSignInWorkflow  # noqa: E402
from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from macos_task_runtime import MacOSTaskRuntime, log_event  # noqa: E402


RESULT_PREFIX = "result_json="
RESULT_STATUSES = {"completed", "already_completed", "safe_stopped", "cancelled"}


def emit_result(status: str, **payload) -> None:
    if status not in RESULT_STATUSES:
        raise ValueError(f"unsupported result status: {status}")
    print(
        RESULT_PREFIX
        + json.dumps(
            {"task_id": "fuli_qiandao", "status": status, **payload},
            ensure_ascii=False,
        ),
        flush=True,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect-scene", action="store_true")
    parser.add_argument("--recognize-only", action="store_true")
    parser.add_argument("--max-steps", type=int, default=20)
    args = parser.parse_args()
    if args.max_steps <= 0:
        parser.error("--max-steps 必须是正整数")
    return args


def run(args, guard: CancellationGuard) -> int:
    runtime = MacOSTaskRuntime(guard)
    outcome = WelfareSignInWorkflow(
        max_steps=args.max_steps,
        classifier=classify_welfare_scene,
    ).execute(
        WorkflowContext(runtime, guard, log_event, runtime.inspection_evidence),
        inspect=args.inspect_scene or args.recognize_only,
    )
    if isinstance(outcome, WorkflowInspection):
        print(
            "preflight_json=" + json.dumps(outcome.to_payload(), ensure_ascii=False),
            flush=True,
        )
        return 0
    if not isinstance(outcome, WorkflowResult):
        raise RuntimeError("福利签到 Workflow 返回了未知结果")
    emit_result(outcome.status, **outcome.payload)
    return 0


def main() -> int:
    guard = CancellationGuard()
    guard.install_signal_handlers()
    args = parse_args()
    read_only = args.inspect_scene or args.recognize_only
    try:
        return run(args, guard)
    except CancellationRequested as exc:
        guard.wait_for_tasker_stop()
        if not read_only:
            emit_result("cancelled", reason=str(exc) or "收到停止请求")
        print("stop_ack=true", flush=True)
        return 0
    except Exception as exc:
        if not read_only:
            emit_result("safe_stopped", reason=str(exc).strip() or exc.__class__.__name__)
        print(f"safe_stop_reason={exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
