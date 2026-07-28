#!/usr/bin/env python3
"""Run a bounded, evidence-backed 宝图获得 workflow on macOS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from baotu_macos_core import classify_baotu_scene  # noqa: E402
from daily_activity_scroll import DailyActivityScrollAction  # noqa: E402
from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from macos_task_runtime import MacOSTaskRuntime, log_event  # noqa: E402


TEMPLATES = {
    "activity_toolbar": ["zonghe/huodong1.png", "zonghe/huodong2.png"],
    "activity_header": ["zonghe/huodong_jiemian_panduan.png"],
    "battle": ["zhandou/zidong.png", "zhandou/quxiao.png"],
    "auto_pathing": [
        "zonghe/zidongxunlu1.png",
        "zonghe/zidongxunlu2.png",
        "zonghe/zidongxunlu3.png",
        "zonghe/zidongxunlu4.png",
    ],
}
RESULT_PREFIX = "result_json="
RESULT_STATUSES = {"completed", "already_completed", "safe_stopped", "cancelled"}
CLICK_STATUSES = {"open_activity", "open_daily", "join_task", "accept_dialogue"}
MAX_NAVIGATION_INPUTS = 14
MAX_TASK_CLICKS = 12


def emit_result(status, **payload):
    if status not in RESULT_STATUSES:
        raise ValueError(f"unsupported result status: {status}")
    print(
        RESULT_PREFIX
        + json.dumps({"task_id": "baotu", "status": status, **payload}, ensure_ascii=False),
        flush=True,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect-scene", action="store_true")
    parser.add_argument("--recognize-only", action="store_true")
    parser.add_argument("--max-steps", type=int, default=180)
    args = parser.parse_args()
    if args.max_steps <= 0:
        parser.error("--max-steps 必须是正整数")
    return args


def run(args, guard):
    runtime = MacOSTaskRuntime(guard)
    actions = 0
    clicks = 0
    navigation_inputs = 0
    task_clicks = 0
    unknown_polls = 0
    daily_tab_confirmed = False
    daily_scroller = DailyActivityScrollAction()
    repeated = {}

    for step in range(1, args.max_steps + 1):
        scene = runtime.read_scene(TEMPLATES)
        decision = classify_baotu_scene(
            scene.texts,
            scene.templates,
            scene.image_size,
            daily_tab_confirmed=daily_tab_confirmed,
        )
        status = decision["status"]
        log_event("scene", step=step, status=status, reason=decision.get("reason"))
        if args.inspect_scene or args.recognize_only:
            print(
                "preflight_json="
                + json.dumps(
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

        if status in {"completed", "already_completed"}:
            emit_result(status, actions=actions, clicks=clicks)
            return 0
        if status == "continue_task":
            daily_tab_confirmed = False
            task_clicks += 1
            if task_clicks > MAX_TASK_CLICKS:
                raise RuntimeError("宝图任务栏点击达到上限，已停止以避免循环")
            runtime.click_box(decision["box"], scene, "继续宝图任务")
            actions += 1
            clicks += 1
            unknown_polls = 0
            guard.wait(7.0)
            continue
        if status == "scroll_daily":
            daily_scroller.perform(runtime, scene, "浏览日常活动列表")
            actions += 1
            guard.wait(1.2)
            continue
        if status in CLICK_STATUSES:
            navigation_inputs += 1
            if navigation_inputs > MAX_NAVIGATION_INPUTS:
                raise RuntimeError("宝图活动导航达到输入上限，已安全停止")
            signature = (status, tuple(decision["box"]))
            repeated[signature] = repeated.get(signature, 0) + 1
            if repeated[signature] > 2:
                raise RuntimeError(f"宝图任务重复执行 {status}，已安全停止")
            runtime.click_box(decision["box"], scene, f"宝图任务：{status}")
            if status == "open_daily":
                daily_scroller.reset()
            daily_tab_confirmed = status == "open_daily"
            actions += 1
            clicks += 1
            unknown_polls = 0
            guard.wait(2.0 if status in {"open_daily", "join_task"} else 1.0)
            continue
        if status == "wait":
            unknown_polls = 0
            guard.wait(2.0)
            continue

        unknown_polls += 1
        if unknown_polls >= 3:
            raise RuntimeError("连续三次无法识别宝图任务页面，已停止以避免无证据点击")
        guard.wait(1.0)

    raise RuntimeError("宝图任务超过最大识别步骤，已安全停止")


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
