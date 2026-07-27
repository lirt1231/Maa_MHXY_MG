#!/usr/bin/env python3
"""Answer an already-open 科举乡试/会试 page on the macOS client."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "tools"))

from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from maa.pipeline import JCustomRecognition  # noqa: E402


RESULT_PREFIX = "result_json="
RESULT_STATUSES = {
    "completed",
    "already_completed",
    "limit_reached",
    "safe_stopped",
    "cancelled",
}


def task_result(status: str, **payload) -> dict:
    """Build a non-sensitive terminal result for one formal 科举 run."""
    if status not in RESULT_STATUSES:
        raise ValueError(f"unsupported result status: {status}")
    return {"task_id": "keju", "status": status, **payload}


def emit_result(result: dict) -> None:
    """Emit the sole terminal protocol line for a formal run."""
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False), flush=True)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--recognize-only", action="store_true")
    parser.add_argument(
        "--inspect-scene",
        action="store_true",
        help="只读取当前画面并判断科举页面状态，不查询题库或模型，不执行点击",
    )
    parser.add_argument(
        "--wait-for-round-one",
        action="store_true",
        help="忽略赛前课堂复习，直到检测到第1题才开始答题",
    )
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


def stable_question_key(question: str) -> str:
    """Remove the live round/timer prefix so one question is clicked only once."""
    round_match = re.match(r"^第\s*(\d+)\s*/\s*\d+\s*题", question or "")
    if round_match:
        return f"round:{round_match.group(1)}"
    text = re.sub(r"^第\s*\d+\s*/\s*\d+\s*题", "", question or "")
    text = re.sub(r"^剩余时间\s*\d*", "", text)
    return re.sub(r"\s+", "", text)


def parse_question_round(question: str) -> tuple[int, int] | None:
    """Return the current and total question numbers for a formal round."""
    match = re.match(r"^第\s*(\d+)\s*/\s*(\d+)\s*题", question or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def classify_keju_inspection(texts: list[dict]) -> dict:
    joined = "".join(re.sub(r"\s+", "", item.get("text", "")) for item in texts)
    if re.search(r"第\d+/\d+题", joined):
        return {"status": "formal_question_ready"}
    if re.search(r"课堂复习|等待答题|即将开始|准备开始", joined):
        return {"status": "waiting_for_question"}
    if re.search(r"科举乡试|科举会试|乡试|会试", joined):
        return {"status": "exam_page_without_question"}
    return {"status": "unknown"}


def should_end_loop(
    answered: int,
    max_steps: int,
    question_round: tuple[int, int] | None = None,
) -> bool:
    """Decide completion from answered questions, including the final round."""
    if answered >= max_steps:
        return True
    if question_round is None:
        return False
    current, total = question_round
    return current >= total


def recognize_keju_frame(tasker, capture, recognizer, guard):
    """Recognize exactly the frame captured from the bound game window."""
    recognizer.last_status = "capturing"
    try:
        image = capture.capture(guard)
    except CancellationRequested:
        recognizer.last_status = "cancelled"
        raise
    except Exception as exc:
        recognizer.last_status = "capture_failed"
        reason = str(exc).strip() or exc.__class__.__name__
        raise RuntimeError(f"科举窗口截图失败：{reason}") from exc

    height, width = image.shape[:2]
    recognizer.last_status = "recognizing"
    try:
        task = tasker.post_recognition(
            "Custom",
            JCustomRecognition(
                custom_recognition="KejuHybridAnswer",
                roi=(0, 0, width, height),
            ),
            image,
        ).wait()
        guard.checkpoint()
        if not task.succeeded:
            raise RuntimeError("Maa 自定义识别任务未成功")
        detail = task.get()
        if detail is None or not detail.nodes:
            raise RuntimeError("Maa 自定义识别未返回结果")
    except CancellationRequested:
        recognizer.last_status = "cancelled"
        raise
    except Exception as exc:
        if recognizer.last_status in {"capturing", "recognizing", "running"}:
            recognizer.last_status = "recognition_failed"
        reason = str(exc).strip() or exc.__class__.__name__
        raise RuntimeError(f"科举识别失败：{reason}") from exc

    if recognizer.last_status in {"capturing", "recognizing", "running"}:
        recognizer.last_status = "recognition_failed"
        raise RuntimeError("科举识别失败：识别器未返回明确状态")
    return detail.nodes[-1].recognition


def run(args, guard: CancellationGuard, progress: dict | None = None) -> dict | None:
    progress = progress if progress is not None else {
        "answers": 0,
        "clicks": 0,
        "limit": args.max_steps,
    }
    if args.inspect_scene:
        from macos_task_runtime import MacOSTaskRuntime

        runtime = MacOSTaskRuntime(guard)
        scene = runtime.read_scene()
        payload = {
            "image_size": scene.image_size,
            "decision": classify_keju_inspection(scene.texts),
            "texts": scene.texts,
            "templates": scene.templates,
        }
        print("preflight_json=" + json.dumps(payload, ensure_ascii=False), flush=True)
        return None

    from maa.controller import (
        MacOSController,
        MaaMacOSInputMethodEnum,
        MaaMacOSScreencapMethodEnum,
    )
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from run_sanjieqiyuan_macos import NativeMacOSInput, WindowCapture

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
    if local_config.get("extra_body"):
        os.environ["MAA_KEJU_MODEL_EXTRA_BODY"] = json.dumps(
            local_config["extra_body"], ensure_ascii=False
        )
    if not os.environ.get("MAA_KEJU_API_KEY"):
        api_key = read_keychain_password(
            str(local_config.get("keychain_service", "")),
            str(local_config.get("keychain_account", "")),
        )
        if api_key:
            os.environ["MAA_KEJU_API_KEY"] = api_key
    if args.use_codex:
        os.environ["MAA_KEJU_USE_CODEX"] = "1"
    if args.wait_for_round_one:
        os.environ["MAA_KEJU_REQUIRE_ROUND_ONE"] = "1"

    Toolkit.init_option(str(ROOT))
    windows = [
        window
        for window in Toolkit.find_desktop_windows()
        if window.class_name == "com.netease.my" and window.window_name == "梦幻西游"
    ]
    if not windows:
        raise RuntimeError("未找到已打开的梦幻西游窗口")

    window_id = windows[0].hwnd
    controller = MacOSController(
        window_id,
        MaaMacOSScreencapMethodEnum.ScreenCaptureKit,
        MaaMacOSInputMethodEnum.GlobalEvent,
    )
    connection = controller.post_connection().wait()
    guard.checkpoint()
    if not connection.succeeded:
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

    capture = WindowCapture(controller, window_id)
    native_input = NativeMacOSInput(window_id)
    last_question = None
    step = 0
    answered = 0
    while not should_end_loop(answered, args.max_steps):
        step += 1
        guard.checkpoint()
        recognize_keju_frame(tasker, capture, recognizer, guard)
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
            question_round = parse_question_round(question)
            if question_round is None:
                print("non_question_page_waiting=true", flush=True)
                guard.wait(min(args.delay, 1.0))
                continue
            question_key = stable_question_key(question)
            if question_key == last_question:
                print("same_question_waiting=true", flush=True)
                guard.wait(min(args.delay, 1.0))
                continue
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
                return None
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
            last_question = question_key
            answered += 1
            progress["answers"] = answered
            progress["clicks"] = answered
            if question_round[0] == 1:
                os.environ.pop("MAA_KEJU_REQUIRE_ROUND_ONE", None)
            if should_end_loop(answered, args.max_steps, question_round):
                if question_round[0] < question_round[1]:
                    break
                print("final_question_answered=true", flush=True)
                return task_result(
                    "completed",
                    answers=answered,
                    clicks=answered,
                    limit=args.max_steps,
                )
            guard.wait(args.delay)
            continue

        if status == "no_question":
            raise RuntimeError("科举页面未识别到题目或明确完成状态，已保留页面")
        if status == "waiting":
            print("waiting_for_formal_question=true", flush=True)
            guard.wait(min(args.delay, 1.0))
            continue
        if status == "unreliable":
            raise RuntimeError("当前题目无法可靠决策，已保留页面")
        raise RuntimeError(f"未处理的科举状态：{status}")

    print(f"达到本次最大答题数：{args.max_steps}", flush=True)
    return task_result(
        "limit_reached",
        answers=answered,
        clicks=answered,
        limit=args.max_steps,
    )


def main() -> int:
    args = parse_args()
    read_only = args.inspect_scene or args.recognize_only
    guard = CancellationGuard()
    progress = {
        "answers": 0,
        "clicks": 0,
        "limit": getattr(args, "max_steps", 0),
    }
    try:
        guard.install_signal_handlers()
        result = run(args, guard, progress)
    except CancellationRequested:
        guard.wait_for_tasker_stop()
        if not read_only:
            emit_result(task_result("cancelled", reason="收到停止请求", **progress))
        print("stop_ack=true", flush=True)
        return 0
    except Exception as exc:
        if not read_only:
            emit_result(task_result(
                "safe_stopped",
                reason=str(exc).strip() or exc.__class__.__name__,
                **progress,
            ))
        else:
            print(f"read_only_error={exc}", flush=True)
        return 1

    if read_only:
        return 0
    if result is None:
        emit_result(task_result(
            "safe_stopped",
            reason="科举运行未返回最终结果",
            **progress,
        ))
        return 1
    emit_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
