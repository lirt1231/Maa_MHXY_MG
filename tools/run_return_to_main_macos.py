#!/usr/bin/env python3
"""Independent macOS process for bounded return-to-main recovery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from macos_task_runtime import MacOSTaskRuntime  # noqa: E402
from return_to_main_workflow import ReturnToMainWorkflow  # noqa: E402

PREFIX = "home_recovery_json="


def emit(payload):
    print(PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    guard = CancellationGuard()
    guard.install_signal_handlers()
    try:
        result = ReturnToMainWorkflow(args.task_id).execute(MacOSTaskRuntime(guard), guard)
        emit(result.to_payload())
        return 0 if result.status == "confirmed" else 1
    except CancellationRequested as exc:
        guard.wait_for_tasker_stop()
        emit({"status": "cancelled", "reason": str(exc), "actions": 0, "clicks": 0})
        return 0
    except Exception as exc:
        emit({"status": "failed", "reason": str(exc).strip() or exc.__class__.__name__, "actions": 0, "clicks": 0})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
