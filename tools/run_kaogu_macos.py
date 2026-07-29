#!/usr/bin/env python3
"""Run a bounded, evidence-backed archaeology collection workflow on macOS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from kaogu_macos_core import classify_kaogu_scene  # noqa: E402
from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from macos_task_runtime import MacOSTaskRuntime, log_event  # noqa: E402


TEMPLATES = {
    "menu_plus": ["zonghe/jiahao.png"],
    "skills_icon": ["jineng/jineng.png"],
    "character_skills": ["jineng/renwujineng.png"],
    "clan_skills": ["jineng/bangpaijineng.png"],
    "training_skills": ["jineng/xiulianjineng.png"],
    "auxiliary_skills": ["jineng/fuzhujineng.png"],
    "auxiliary_tab": ["jineng/fuzhu.png", "jineng/fuzhu2.png"],
    "archaeology_entry": ["jineng/kaogu_rukou.png"],
    "archaeology_header": ["jineng/kaogu_ZJM.png"],
    "production_tab": ["jineng/jinengshengchan.png", "jineng/jinengshengchan2.png"],
    "tier_one": ["jineng/yijie.png"],
    "tier_two": ["jineng/erjie.png"],
    "tier_three": ["jineng/sanjie.png"],
    "tier_four": ["jineng/sijie.png"],
    "tier_five": ["jineng/wujie.png"],
    "tier_left_arrow": ["jineng/gongfang_jiantou_zuo.png"],
    "tool_plus": ["jineng/gongfang_jiahao.png"],
    "purchase_slot": ["jineng/kaogu_goumai.png"],
    "shovel": ["jineng/luoyangchan.png", "jineng/luoyangchan2.png"],
    "shop_close": ["jineng/zahuopu_guanbi.png"],
    "selected_shovel": ["jineng/yixuan_luoyangchan.png"],
}
RESULT_PREFIX = "result_json="
RESULT_STATUSES = {"completed", "already_completed", "safe_stopped", "cancelled"}
TARGET_DIGS = 10
MAX_STEPS = 80
MAX_PURCHASES = 1
MAX_UNKNOWN_POLLS = 3
MAX_WAIT_POLLS = 18
ACTION_LIMITS = {
    "expand_menu": 1,
    "open_skills": 2,
    "open_auxiliary": 2,
    "open_archaeology": 2,
    "open_production": 2,
    "switch_tier": 4,
    "open_tool_selection": 2,
    "open_shovel_shop": 1,
    "close_shop": 2,
    "select_shovel": 2,
    "use_shovel": 2,
    "start_archaeology": 1,
    "close_temporary_bag": 3,
}


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
    args = parser.parse_args()
    if not 1 <= args.max_steps <= MAX_STEPS:
        parser.error(f"--max-steps 必须在 1–{MAX_STEPS} 之间")
    return args


def _next_phase(status, phase):
    return {
        "expand_menu": "menu",
        "open_skills": "skills",
        "open_auxiliary": "skills",
        "open_archaeology": "archaeology",
        "open_production": "production",
        "switch_tier": "production",
        "open_tool_selection": "tool_select",
        "open_shovel_shop": "shop",
        "buy_shovel": "shop_closing",
        "close_shop": "tool_select",
        "select_shovel": "tool_select",
        "use_shovel": "tool_ready",
        "start_archaeology": "digging",
        "close_temporary_bag": phase,
    }.get(status, phase)


def run(args, guard):
    runtime = MacOSTaskRuntime(guard)
    phase = "enter"
    actions = 0
    clicks = 0
    digs = 0
    purchases = 0
    unknown_polls = 0
    wait_polls = 0
    action_counts = {}

    for step in range(1, args.max_steps + 1):
        scene = runtime.read_scene(TEMPLATES)
        decision = classify_kaogu_scene(
            scene.texts, scene.templates, scene.image_size, phase=phase
        )
        status = decision["status"]
        log_event(
            "scene",
            step=step,
            phase=phase,
            status=status,
            digs=digs,
            targetDigs=TARGET_DIGS,
        )
        if args.inspect_scene or args.recognize_only:
            print(
                "preflight_json="
                + json.dumps(
                    {
                        "image_size": scene.image_size,
                        "decision": decision,
                        "proposed_action": status if "box" in decision else None,
                        "texts": scene.texts,
                        "templates": scene.templates,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0

        if status == "blocked_resource":
            raise RuntimeError("当前活力不足，考古已安全停止")
        if status in {"tool_selection_idle", "production_idle"}:
            wait_polls += 1
            if wait_polls >= 4:
                raise RuntimeError("考古页面连续未出现可执行按钮，已安全停止")
            guard.wait(0.8)
            continue
        if status == "wait_for_dig":
            wait_polls += 1
            if digs >= TARGET_DIGS:
                emit_result(
                    "completed",
                    digs=digs,
                    target_digs=TARGET_DIGS,
                    purchases=purchases,
                    actions=actions,
                    clicks=clicks,
                )
                return 0
            if wait_polls >= MAX_WAIT_POLLS:
                raise RuntimeError("长时间未出现下一次挖掘按钮，已安全停止")
            guard.wait(0.6)
            continue
        if status == "unknown":
            unknown_polls += 1
            if unknown_polls >= MAX_UNKNOWN_POLLS:
                raise RuntimeError("连续三次无法识别考古页面，已停止以避免无证据点击")
            guard.wait(0.8)
            continue

        unknown_polls = 0
        wait_polls = 0
        if status == "buy_shovel":
            if purchases >= MAX_PURCHASES:
                raise RuntimeError("洛阳铲购买已达到一次上限，已停止以避免重复购买")
            runtime.click_box(decision["box"], scene, "购买一个洛阳铲")
            purchases += 1
        elif status == "dig":
            if digs >= TARGET_DIGS:
                emit_result(
                    "completed",
                    digs=digs,
                    target_digs=TARGET_DIGS,
                    purchases=purchases,
                    actions=actions,
                    clicks=clicks,
                )
                return 0
            runtime.click_box(decision["box"], scene, f"进行第 {digs + 1} 次挖掘")
            digs += 1
        else:
            limit = ACTION_LIMITS.get(status)
            if limit is None:
                raise RuntimeError(f"考古流程返回了不支持的动作：{status}")
            action_counts[status] = action_counts.get(status, 0) + 1
            if action_counts[status] > limit:
                raise RuntimeError(f"考古步骤 {status} 重复达到上限，已安全停止")
            runtime.click_box(decision["box"], scene, f"考古流程：{status}")

        actions += 1
        clicks += 1
        phase = _next_phase(status, phase)
        if digs >= TARGET_DIGS:
            guard.wait(2.0)
            emit_result(
                "completed",
                digs=digs,
                target_digs=TARGET_DIGS,
                purchases=purchases,
                actions=actions,
                clicks=clicks,
            )
            return 0
        guard.wait(1.8 if status in {"buy_shovel", "start_archaeology", "dig"} else 1.0)

    raise RuntimeError("考古流程超过最大识别步骤，已安全停止")


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
                target_digs=TARGET_DIGS,
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
                target_digs=TARGET_DIGS,
                purchases=locals().get("purchases", 0),
                actions=locals().get("actions", 0),
                clicks=locals().get("clicks", 0),
            )
        print(f"safe_stop_reason={exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
