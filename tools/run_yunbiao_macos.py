#!/usr/bin/env python3
"""Run bounded ordinary escort missions on macOS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from daily_activity_scroll import DailyActivityScrollAction  # noqa: E402
from macos_task_runtime import MacOSTaskRuntime, log_event  # noqa: E402
from yunbiao_macos_core import classify_yunbiao_scene  # noqa: E402


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
MAX_TASK_CLICKS = 12


def emit_result(status, **payload):
    if status not in RESULT_STATUSES:
        raise ValueError(f"unsupported result status: {status}")
    print(
        RESULT_PREFIX
        + json.dumps({"task_id": "yunbiao", "status": status, **payload}, ensure_ascii=False),
        flush=True,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect-scene", action="store_true")
    parser.add_argument("--recognize-only", action="store_true")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=240)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 3:
        parser.error("--rounds 必须在 1–3 之间")
    if args.max_steps <= 0:
        parser.error("--max-steps 必须是正整数")
    return args


def run(args, guard):
    runtime = MacOSTaskRuntime(guard)
    phase = "enter"
    actions = 0
    clicks = 0
    accepted_rounds = 0
    completed_rounds = 0
    navigation_inputs = 0
    task_clicks = 0
    unknown_polls = 0
    daily_tab_confirmed = False
    daily_scroller = DailyActivityScrollAction()
    repeated = {}

    for step in range(1, args.max_steps + 1):
        scene = runtime.read_scene(TEMPLATES)
        decision = classify_yunbiao_scene(
            scene.texts,
            scene.templates,
            scene.image_size,
            phase,
            daily_tab_confirmed=daily_tab_confirmed,
        )
        status = decision["status"]
        log_event(
            "scene",
            step=step,
            phase=phase,
            status=status,
            acceptedRounds=accepted_rounds,
            completedRounds=completed_rounds,
        )
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
            result_status = "completed" if completed_rounds or accepted_rounds else status
            emit_result(
                result_status,
                rounds=max(completed_rounds, accepted_rounds),
                target_rounds=args.rounds,
                actions=actions,
                clicks=clicks,
            )
            return 0
        if status == "blocked_resource":
            raise RuntimeError(f"普通运镖资源条件不满足：{decision['reason']}")
        if status == "unsupported_delivery":
            raise RuntimeError(f"只允许普通运镖，当前未识别到普通镖选项：{decision['reason']}")
        if status == "delivery_active":
            if phase != "delivering":
                accepted_rounds = max(accepted_rounds, completed_rounds + 1)
                phase = "delivering"
            unknown_polls = 0
            guard.wait(2.0)
            continue
        if status == "select_normal":
            daily_tab_confirmed = False
            if phase == "delivering":
                completed_rounds += 1
                if completed_rounds >= args.rounds:
                    emit_result(
                        "completed",
                        rounds=completed_rounds,
                        target_rounds=args.rounds,
                        actions=actions,
                        clicks=clicks,
                    )
                    return 0
                phase = "enter"
            if accepted_rounds >= args.rounds:
                raise RuntimeError("普通运镖达到目标轮数后仍出现领取选项，已保留现场")
            signature = (accepted_rounds + 1, tuple(decision["box"]))
            repeated[signature] = repeated.get(signature, 0) + 1
            if repeated[signature] > 1:
                raise RuntimeError("普通镖选项点击后页面未变化，已安全停止")
            runtime.click_box(decision["box"], scene, "选择押送普通镖银")
            actions += 1
            clicks += 1
            phase = "confirm_normal"
            unknown_polls = 0
            guard.wait(1.0)
            continue
        if status == "confirm_normal":
            daily_tab_confirmed = False
            runtime.click_box(decision["box"], scene, "确认押送普通镖银")
            accepted_rounds += 1
            actions += 1
            clicks += 1
            phase = "await_delivery"
            unknown_polls = 0
            guard.wait(2.0)
            continue
        if status == "continue_task":
            daily_tab_confirmed = False
            task_clicks += 1
            if task_clicks > MAX_TASK_CLICKS:
                raise RuntimeError("运镖任务栏点击达到上限，已停止以避免循环")
            runtime.click_box(decision["box"], scene, "继续普通运镖任务")
            actions += 1
            clicks += 1
            unknown_polls = 0
            guard.wait(2.0)
            continue
        if status == "scroll_daily":
            daily_scroller.perform(runtime, scene, "浏览日常活动列表")
            actions += 1
            guard.wait(1.2)
            continue
        if status in NAVIGATION_CLICK_STATUSES:
            navigation_inputs += 1
            if navigation_inputs > MAX_NAVIGATION_INPUTS:
                raise RuntimeError("运镖活动导航达到输入上限，已安全停止")
            signature = (status, tuple(decision["box"]))
            repeated[signature] = repeated.get(signature, 0) + 1
            if repeated[signature] > 2:
                raise RuntimeError(f"运镖任务重复执行 {status}，已安全停止")
            runtime.click_box(decision["box"], scene, f"运镖任务：{status}")
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
        unknown_limit = 6 if phase in {"await_delivery", "delivering"} else 3
        if unknown_polls >= unknown_limit:
            raise RuntimeError("连续多次无法识别普通运镖页面，已停止以避免无证据点击")
        guard.wait(1.0)

    raise RuntimeError("普通运镖超过最大识别步骤，已安全停止")


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
