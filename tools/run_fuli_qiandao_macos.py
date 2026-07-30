#!/usr/bin/env python3
"""Safely complete the daily 福利 scratch-card sign-in on macOS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from fuli_qiandao_macos_core import classify_welfare_scene, scratch_gestures  # noqa: E402
from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from macos_task_runtime import MacOSTaskRuntime, log_event  # noqa: E402


TEMPLATES = {
    "welfare_header": ["qiandao/fuli.png"],
    "scratch": ["qiandao/qiandaohuadong.png"],
}
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
    phase = "enter"
    idle_polls = 0
    unknown_polls = 0
    scratch_attempts = 0
    actions = 0
    clicks = 0

    for step in range(1, args.max_steps + 1):
        scene = runtime.read_scene(TEMPLATES)
        decision = classify_welfare_scene(
            scene.texts, scene.templates, phase, scene.image_size
        )
        status = decision["status"]
        log_event("scene", step=step, phase=phase, status=status)
        if args.inspect_scene or args.recognize_only:
            print(
                "preflight_json=" + json.dumps(
                    {
                        "image_size": scene.image_size,
                        "decision": decision,
                        "texts": scene.texts,
                        "templates": scene.templates,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0

        if status != "unknown":
            unknown_polls = 0
        if status != "welfare_idle":
            idle_polls = 0

        if status == "completed":
            emit_result("completed", actions=actions, clicks=clicks)
            return 0
        if status == "already_completed":
            emit_result("already_completed", actions=actions, clicks=clicks)
            return 0
        if status == "open_welfare":
            runtime.click_box(decision["box"], scene, "打开福利")
            actions += 1
            clicks += 1
            phase = "welfare"
        elif status == "open_scratch":
            runtime.click_box(decision["box"], scene, "打开刮刮乐")
            actions += 1
            clicks += 1
            phase = "scratch"
        elif status == "scratch_ready":
            scratch_attempts += 1
            if scratch_attempts > 2:
                raise RuntimeError("刮奖区域连续两次未发生变化，已安全停止")
            for index, (start, end) in enumerate(scratch_gestures(decision["box"]), 1):
                runtime.drag(start, end, scene, f"刮奖区域第 {index} 行")
                actions += 1
                guard.wait(0.15)
            phase = "verify"
        elif status == "confirm_reward":
            runtime.click_box(decision["box"], scene, "确认签到奖励")
            actions += 1
            clicks += 1
            phase = "verify"
        elif status == "welfare_idle":
            idle_polls += 1
            if idle_polls >= 4:
                raise RuntimeError("福利页面未显示今日签到入口或明确完成状态，已安全停止")
        else:
            unknown_polls += 1
            unknown_limit = 8 if phase in {"welfare", "scratch", "verify"} else 3
            if unknown_polls >= unknown_limit:
                raise RuntimeError(
                    f"连续{unknown_limit}次无法识别福利签到页面，已停止以避免无证据点击"
                )
        wait_seconds = 2.5 if status == "open_scratch" else (
            1.2 if status == "open_welfare" else 0.8
        )
        guard.wait(wait_seconds)

    raise RuntimeError("福利签到超过最大识别步骤，已安全停止")


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
