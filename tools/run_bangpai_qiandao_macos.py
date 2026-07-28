#!/usr/bin/env python3
"""Safely complete daily clan sign-in and claim an available dividend on macOS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from bangpai_qiandao_macos_core import classify_clan_sign_in_scene  # noqa: E402
from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from macos_task_runtime import MacOSTaskRuntime, log_event  # noqa: E402


TEMPLATES = {
    "expand_menu": ["zonghe/jiahao.png"],
    "clan_entry": ["qiandao/bangpai.png", "qiandao/bangpai2.png"],
    "clan_header": [
        "qiandao/bangpai_jiemian_panduan.png",
        "qiandao/bangpai_jiemian_panduan2.png",
    ],
    "clan_welfare": ["qiandao/bangpaifuli.png"],
}
RESULT_PREFIX = "result_json="
RESULT_STATUSES = {"completed", "already_completed", "safe_stopped", "cancelled"}


def emit_result(status: str, **payload) -> None:
    if status not in RESULT_STATUSES:
        raise ValueError(f"unsupported result status: {status}")
    print(
        RESULT_PREFIX
        + json.dumps(
            {"task_id": "bangpai_qiandao", "status": status, **payload},
            ensure_ascii=False,
        ),
        flush=True,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect-scene", action="store_true")
    parser.add_argument("--recognize-only", action="store_true")
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args()
    if args.max_steps <= 0:
        parser.error("--max-steps 必须是正整数")
    return args


def run(args, guard: CancellationGuard) -> int:
    runtime = MacOSTaskRuntime(guard)
    phase = "enter"
    unknown_polls = 0
    idle_polls = 0
    sign_in_attempts = 0
    dividend_attempts = 0
    confirmation_attempts = 0
    actions = 0
    clicks = 0

    for step in range(1, args.max_steps + 1):
        scene = runtime.read_scene(TEMPLATES)
        decision = classify_clan_sign_in_scene(
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

        unknown_polls = unknown_polls + 1 if status == "unknown" else 0
        idle_polls = idle_polls + 1 if status == "clan_idle" else 0

        if status == "completed":
            emit_result("completed", actions=actions, clicks=clicks)
            return 0
        if status == "already_completed":
            emit_result("already_completed", actions=actions, clicks=clicks)
            return 0
        if status == "no_clan":
            raise RuntimeError("当前角色未加入帮派，无法执行帮派签到")
        if status == "expand_menu":
            runtime.click_box(decision["box"], scene, "展开主界面菜单")
            actions += 1
            clicks += 1
            phase = "menu"
        elif status == "open_clan":
            runtime.click_box(decision["box"], scene, "打开帮派")
            actions += 1
            clicks += 1
            phase = "clan"
        elif status == "open_welfare":
            runtime.click_box(decision["box"], scene, "打开帮派福利")
            actions += 1
            clicks += 1
            phase = "welfare"
        elif status == "sign_in":
            sign_in_attempts += 1
            if sign_in_attempts > 1:
                raise RuntimeError("帮派签到按钮点击后仍未变化，已安全停止")
            runtime.click_box(decision["box"], scene, "执行帮派签到")
            actions += 1
            clicks += 1
            phase = "verify_sign_in"
        elif status == "sign_in_confirmed":
            phase = "check_dividend"
        elif status == "claim_dividend":
            dividend_attempts += 1
            if dividend_attempts > 1:
                raise RuntimeError("帮派分红按钮点击后仍未变化，已安全停止")
            runtime.click_box(decision["box"], scene, "领取帮派分红")
            actions += 1
            clicks += 1
            phase = "dividend_confirm"
        elif status == "confirm_dividend":
            confirmation_attempts += 1
            if confirmation_attempts > 1:
                raise RuntimeError("帮派分红确认后页面仍未变化，已安全停止")
            runtime.click_box(decision["box"], scene, "确认领取帮派分红")
            actions += 1
            clicks += 1
            phase = "verify_dividend"
        elif status == "clan_idle" and idle_polls >= 4:
            raise RuntimeError("帮派页面未显示可识别的福利入口，已安全停止")
        elif status == "unknown" and unknown_polls >= 3:
            raise RuntimeError("连续三次无法识别帮派签到页面，已停止以避免无证据点击")
        guard.wait(0.8)

    raise RuntimeError("帮派签到超过最大识别步骤，已安全停止")


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
