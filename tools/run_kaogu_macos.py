#!/usr/bin/env python3
"""Run a bounded, evidence-backed archaeology collection workflow on macOS."""

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
from kaogu_recognition import classify_kaogu_scene  # noqa: E402
from kaogu_purchase_guard import FilePurchaseGuard  # noqa: E402
from kaogu_workflow import DEFAULT_ACTION_LIMITS, KaoguWorkflow  # noqa: E402
from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from macos_task_runtime import MacOSTaskRuntime, log_event  # noqa: E402


RESULT_PREFIX = "result_json="
RESULT_STATUSES = {"completed", "already_completed", "safe_stopped", "cancelled"}
TARGET_DIGS = 10
MAX_STEPS = 80
MAX_PURCHASES = 1
MAX_UNKNOWN_POLLS = 3
MAX_WAIT_POLLS = 18
ACTION_LIMITS = DEFAULT_ACTION_LIMITS
PURCHASE_GUARD_PATH = ROOT / "config" / "kaogu_purchase_guard.json"


def emit_result(status, **payload):
    if status not in RESULT_STATUSES:
        raise ValueError(f"unsupported result status: {status}")
    print(
        RESULT_PREFIX
        + json.dumps({"task_id": "kaogu", "status": status, **payload}, ensure_ascii=False),
        flush=True,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect-scene", action="store_true")
    parser.add_argument("--recognize-only", action="store_true")
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    parser.add_argument("--target-digs", type=int, default=TARGET_DIGS)
    parser.add_argument(
        "--tool-mode",
        choices=("selected", "luoyang_shovel"),
        default="selected",
    )
    args = parser.parse_args()
    if not 1 <= args.max_steps <= MAX_STEPS:
        parser.error(f"--max-steps 必须在 1–{MAX_STEPS} 之间")
    if not 1 <= args.target_digs <= TARGET_DIGS:
        parser.error(f"--target-digs 必须在 1–{TARGET_DIGS} 之间")
    return args


def run(args, guard, purchase_guard=None):
    runtime = MacOSTaskRuntime(guard)
    target_digs = getattr(args, "target_digs", TARGET_DIGS)
    tool_mode = getattr(args, "tool_mode", "luoyang_shovel")
    if purchase_guard is None:
        purchase_guard = FilePurchaseGuard(PURCHASE_GUARD_PATH)
    if not (args.inspect_scene or args.recognize_only):
        reset_orphan = getattr(purchase_guard, "reset_if_owner_exited", None)
        if callable(reset_orphan) and reset_orphan():
            print("purchase_guard_recovered=orphaned_owner", flush=True)
    outcome = KaoguWorkflow(
        target_digs=target_digs,
        tool_mode=tool_mode,
        max_steps=args.max_steps,
        max_purchases=MAX_PURCHASES,
        max_unknown_polls=MAX_UNKNOWN_POLLS,
        max_wait_polls=MAX_WAIT_POLLS,
        action_limits=ACTION_LIMITS,
        classifier=classify_kaogu_scene,
        purchase_guard=purchase_guard,
    ).execute(
        WorkflowContext(
            runtime,
            guard,
            log_event,
            getattr(runtime, "inspection_evidence", None),
        ),
        inspect=args.inspect_scene or args.recognize_only,
    )
    if isinstance(outcome, WorkflowInspection):
        payload = outcome.to_payload()
        payload["proposed_action"] = (
            outcome.decision["status"] if "box" in outcome.decision else None
        )
        print("preflight_json=" + json.dumps(payload, ensure_ascii=False), flush=True)
        return 0
    if not isinstance(outcome, WorkflowResult):
        raise RuntimeError("考古 Workflow 返回了未知结果")
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
            emit_result(
                "cancelled",
                reason="收到停止请求",
                digs=0,
                target_digs=args.target_digs,
                purchases=0,
                actions=0,
                clicks=0,
            )
        print("stop_ack=true", flush=True)
        return 0
    except Exception as exc:
        if not read_only:
            emit_result(
                "safe_stopped",
                reason=str(exc).strip() or exc.__class__.__name__,
                digs=locals().get("digs", 0),
                target_digs=args.target_digs,
                purchases=locals().get("purchases", 0),
                actions=locals().get("actions", 0),
                clicks=locals().get("clicks", 0),
            )
        print(f"safe_stop_reason={exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
