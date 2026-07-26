#!/usr/bin/env python3
"""Answer an already-open 科举乡试/会试 page on the macOS client."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "tools"))

from maa.controller import (  # noqa: E402
    MacOSController,
    MaaMacOSInputMethodEnum,
    MaaMacOSScreencapMethodEnum,
)
from maa.resource import Resource  # noqa: E402
from maa.tasker import Tasker  # noqa: E402
from maa.toolkit import Toolkit  # noqa: E402

from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from run_sanjieqiyuan_macos import NativeMacOSInput  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--recognize-only", action="store_true")
    parser.add_argument(
        "--use-codex",
        action="store_true",
        help="未配置 OpenAI 兼容 API 时，使用临时只读 Codex 子进程复核答案",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("MAA_KEJU_URL", ""),
        help="OpenAI 兼容 chat completions URL",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MAA_KEJU_MODEL", ""),
    )
    return parser.parse_args()


def load_local_model_config():
    path = ROOT / "config/keju_model.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_keychain_password(service, account):
    if not service or not account:
        return ""
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            account,
            "-s",
            service,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def run(args, guard: CancellationGuard) -> int:
    local_config = load_local_model_config()
    os.environ["MAA_DIRECT_RESOURCE"] = "1"
    os.environ["MAA_KEJU_RECOGNIZE_ONLY"] = "1"
    url = args.url or str(local_config.get("url", ""))
    model = args.model or str(local_config.get("model", ""))
    if url:
        os.environ["MAA_KEJU_URL"] = url
    if model:
        os.environ["MAA_KEJU_MODEL"] = model
    if local_config.get("timeout"):
        os.environ["MAA_KEJU_TIMEOUT"] = str(local_config["timeout"])
    if not os.environ.get("MAA_KEJU_API_KEY"):
        api_key = read_keychain_password(
            str(local_config.get("keychain_service", "")),
            str(local_config.get("keychain_account", "")),
        )
        if api_key:
            os.environ["MAA_KEJU_API_KEY"] = api_key
    if args.use_codex:
        os.environ["MAA_KEJU_USE_CODEX"] = "1"

    Toolkit.init_option(str(ROOT))
    windows = [
        window
        for window in Toolkit.find_desktop_windows()
        if window.class_name == "com.netease.my" and window.window_name == "梦幻西游"
    ]
    if not windows:
        raise RuntimeError("未找到已打开的梦幻西游窗口")

    controller = MacOSController(
        windows[0].hwnd,
        MaaMacOSScreencapMethodEnum.ScreenCaptureKit,
        MaaMacOSInputMethodEnum.GlobalEvent,
    )
    if not controller.post_connection().wait().succeeded:
        raise RuntimeError("连接梦幻西游窗口失败")

    from custom.recognition.keju_hybrid import KejuHybridAnswer

    resource = Resource()
    for bundle in (ROOT / "assets/resource/base", ROOT / "assets/resource/mac"):
        if not resource.post_bundle(bundle).wait().succeeded:
            raise RuntimeError(f"加载资源失败：{bundle}")

    recognizer = KejuHybridAnswer()
    if not resource.register_custom_recognition("KejuHybridAnswer", recognizer):
        raise RuntimeError("注册 KejuHybridAnswer 失败")

    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise RuntimeError("初始化 Maa Tasker 失败")
    guard.bind_tasker(tasker)

    native_input = NativeMacOSInput()
    last_question = None
    for step in range(1, args.max_steps + 1):
        guard.checkpoint()
        recognizer.last_status = "running"
        task = tasker.post_task("活动-科举乡试-开始答题混合").wait()
        guard.checkpoint()
        detail = task.get()
        status = getattr(recognizer, "last_status", "unknown")
        payload = {
            "step": step,
            "status": status,
            "question": getattr(recognizer, "last_question", ""),
            "options": getattr(recognizer, "last_options", {}),
            "answer": getattr(recognizer, "last_answer", ""),
            "source": getattr(recognizer, "last_source", ""),
        }
        print(json.dumps(payload, ensure_ascii=False), flush=True)

        if status == "ready":
            question = recognizer.last_question
            if question == last_question:
                raise RuntimeError("乡试题目未变化，停止重复点击")
            if args.recognize_only:
                print(
                    "action_json="
                    + json.dumps(
                        {
                            **payload,
                            "click": recognizer.last_click,
                            "image_size": recognizer.last_image_size,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return 0
            screen_point = guard.perform_input(
                native_input.click,
                *recognizer.last_click,
                recognizer.last_image_size,
            )
            print(
                f"native_click answer={recognizer.last_answer} "
                f"source={recognizer.last_click} screen={screen_point}",
                flush=True,
            )
            last_question = question
            guard.wait(args.delay)
            continue

        if status == "no_question":
            print("科举页面已没有可识别题目，运行结束", flush=True)
            return 0
        if status == "unreliable":
            raise RuntimeError("当前题目无法可靠决策，已保留页面")
        if detail is None:
            raise RuntimeError("Maa 科举任务执行失败")
        raise RuntimeError(f"未处理的科举状态：{status}")

    print(f"达到本次最大答题步数：{args.max_steps}", flush=True)
    return 0


def main() -> int:
    guard = CancellationGuard()
    guard.install_signal_handlers()
    try:
        return run(parse_args(), guard)
    except CancellationRequested:
        guard.wait_for_tasker_stop()
        print("stop_ack=true", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
