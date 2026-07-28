#!/usr/bin/env python3
"""Run the low-risk subset of sect missions on macOS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from macos_task_runtime import MacOSTaskRuntime, log_event  # noqa: E402
from shimen_macos_core import classify_shimen_scene  # noqa: E402


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
NAVIGATION_CLICK_STATUSES = {"open_activity", "open_daily", "join_task"}
MAX_NAVIGATION_INPUTS = 14
MAX_TASK_CLICKS = 40


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
    actions = 0
    clicks = 0
    task_clicks = 0
    navigation_inputs = 0
    unknown_polls = 0
    daily_reset_done = False
    last_click_signature = None
    consecutive_same_clicks = 0
    recent_statuses = []

    for step in range(1, args.max_steps + 1):
        scene = runtime.read_scene(TEMPLATES)
        decision = classify_shimen_scene(
            scene.texts, scene.templates, scene.image_size
        )
        status = decision["status"]
        recent_statuses.append(status)
        recent_statuses = recent_statuses[-10:]
        if len(recent_statuses) == 10 and recent_statuses[:5] == recent_statuses[5:]:
            raise RuntimeError(
                "师门重复相同的五步页面序列，已停止以避免导航循环"
            )
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
        if status == "unsupported_asset_branch":
            raise RuntimeError(
                f"师门进入需要资产操作的分支，首版不会自动处理：{decision['reason']}"
            )
        if status in {"continue_task", "progress_task"}:
            task_clicks += 1
            if task_clicks > MAX_TASK_CLICKS:
                raise RuntimeError("师门推进点击达到上限，已停止以避免循环")
            signature = (status, tuple(decision["box"]))
            if signature == last_click_signature:
                consecutive_same_clicks += 1
            else:
                last_click_signature = signature
                consecutive_same_clicks = 1
            if consecutive_same_clicks > 2:
                raise RuntimeError(f"师门连续重复执行 {status}，已安全停止")
            reason = "继续右侧师门任务" if status == "continue_task" else "执行师门去完成按钮"
            runtime.click_box(decision["box"], scene, reason)
            actions += 1
            clicks += 1
            unknown_polls = 0
            guard.wait(7.0 if status == "continue_task" else 2.0)
            continue
        if status == "scroll_daily":
            navigation_inputs += 1
            if navigation_inputs > MAX_NAVIGATION_INPUTS:
                raise RuntimeError("师门活动导航达到输入上限，已安全停止")
            width, height = scene.image_size
            if not daily_reset_done:
                start = (round(width * 0.62), round(height * 0.34))
                end = (round(width * 0.62), round(height * 0.66))
                daily_reset_done = True
            else:
                start = (round(width * 0.62), round(height * 0.65))
                end = (round(width * 0.62), round(height * 0.34))
            runtime.drag(start, end, scene, "浏览日常活动列表")
            actions += 1
            last_click_signature = None
            consecutive_same_clicks = 0
            guard.wait(1.2)
            continue
        if status in NAVIGATION_CLICK_STATUSES:
            navigation_inputs += 1
            if navigation_inputs > MAX_NAVIGATION_INPUTS:
                raise RuntimeError("师门活动导航达到输入上限，已安全停止")
            signature = (status, tuple(decision["box"]))
            if signature == last_click_signature:
                consecutive_same_clicks += 1
            else:
                last_click_signature = signature
                consecutive_same_clicks = 1
            if consecutive_same_clicks > 2:
                raise RuntimeError(f"师门连续重复执行 {status}，已安全停止")
            runtime.click_box(decision["box"], scene, f"师门任务：{status}")
            actions += 1
            clicks += 1
            unknown_polls = 0
            guard.wait(2.0 if status == "join_task" else 1.0)
            continue
        if status == "wait":
            last_click_signature = None
            consecutive_same_clicks = 0
            unknown_polls = 0
            guard.wait(2.0)
            continue

        last_click_signature = None
        consecutive_same_clicks = 0
        unknown_polls += 1
        if unknown_polls >= 5:
            raise RuntimeError("连续五次无法识别师门任务页面，已停止以避免无证据点击")
        guard.wait(1.0)

    raise RuntimeError("师门任务超过最大识别步骤，已安全停止")


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
