#!/usr/bin/env python3
"""Answer 科举乡试 or enter and answer 科举会试 on the macOS client."""

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
from automation_workflows import WorkflowContext, WorkflowInspection  # noqa: E402
from keju_workflow import (  # noqa: E402
    KejuWorkflow,
    parse_question_round,
    should_end_loop,
    stable_question_key,
)
from macos_quiz_adapter import MacOSQuizInputAdapter, MacOSQuizScene  # noqa: E402
from quiz_workflows import QuizObservation  # noqa: E402
from maa.pipeline import JCustomRecognition, JOCR  # noqa: E402
from keju_reward import parse_huishi_settlement_reward  # noqa: E402
from huishi_entry_workflow import HuishiEntryWorkflow  # noqa: E402


RESULT_PREFIX = "result_json="
RESULT_STATUSES = {
    "completed",
    "already_completed",
    "limit_reached",
    "safe_stopped",
    "cancelled",
}
EXAM_TASK_IDS = {
    "xiangshi": "keju",
    "huishi": "keju_huishi",
}
EXAM_LABELS = {
    "xiangshi": "乡试",
    "huishi": "会试",
}
EXAM_FINAL_QUESTION_NUMBERS = {
    "xiangshi": 10,
    "huishi": 20,
}
MAX_WAITING_POLLS = 180
MAX_SAME_QUESTION_POLLS = 8
HUI_SHI_REWARD_POLLS = 8
HUI_SHI_REWARD_POLL_SECONDS = 1.0
_SEMANTIC_RECOGNITION_STATUSES = frozenset(
    {"waiting", "wrong_exam_type", "unverified_exam_type", "no_question", "unreliable"}
)


def task_result(status: str, *, exam_type: str = "xiangshi", **payload) -> dict:
    """Build a non-sensitive terminal result for one formal 科举 run."""
    if status not in RESULT_STATUSES:
        raise ValueError(f"unsupported result status: {status}")
    if exam_type not in EXAM_TASK_IDS:
        raise ValueError(f"unsupported exam type: {exam_type}")
    return {"task_id": EXAM_TASK_IDS[exam_type], "status": status, **payload}


def emit_result(result: dict) -> None:
    """Emit the sole terminal protocol line for a formal run."""
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False), flush=True)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--exam-type",
        choices=tuple(EXAM_TASK_IDS),
        default="xiangshi",
        help="严格限定为科举乡试或科举会试，不允许混用页面",
    )
    parser.add_argument("--max-steps", type=int)
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
        "--wait-for-entry",
        action="store_true",
        help="从主界面启动后，仅等待手动进入正确的科举页面，不执行输入",
    )
    parser.add_argument(
        "--navigate-from-main",
        action="store_true",
        help="从游戏主界面依次打开活动、日常活动并参加科举会试",
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
    args = parser.parse_args()
    if args.max_steps is None:
        args.max_steps = 10 if args.exam_type == "xiangshi" else 20
    maximum = 10 if args.exam_type == "xiangshi" else 30
    if args.max_steps < 1 or args.max_steps > maximum:
        parser.error(f"--max-steps 必须在 1–{maximum} 之间")
    return args


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


def _title_texts(texts: list[dict], image_size: tuple[int, int]) -> list[str]:
    width, height = image_size
    title_texts = []
    for item in texts:
        box = item.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        center_x = box[0] + box[2] / 2
        center_y = box[1] + box[3] / 2
        inside_x = width * 0.25 <= center_x <= width * 0.75
        inside_y = height * 0.08 <= center_y <= height * 0.32
        if inside_x and inside_y:
            title_texts.append(re.sub(r"\s+", "", item.get("text", "")))
    return title_texts


def detect_exam_type(texts: list[dict], image_size: tuple[int, int]) -> str | None:
    """Detect only from the title band so question text cannot spoof the type."""
    joined = "".join(_title_texts(texts, image_size))
    matches = {
        exam_type
        for exam_type, label in EXAM_LABELS.items()
        if f"科举{label}" in joined
    }
    return matches.pop() if len(matches) == 1 else None


def classify_keju_inspection(
    texts: list[dict], image_size: tuple[int, int] = (1280, 720)
) -> dict:
    joined = "".join(re.sub(r"\s+", "", item.get("text", "")) for item in texts)
    exam_type = detect_exam_type(texts, image_size)
    if re.search(r"第\d+(?:/\d+)?题", joined):
        return {"status": "formal_question_ready", "examType": exam_type}
    if re.search(r"课堂复习|等待答题|即将开始|准备开始", joined):
        return {"status": "waiting_for_question", "examType": exam_type}
    if exam_type:
        return {"status": "exam_page_without_question", "examType": exam_type}
    return {"status": "unknown", "examType": None}


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
        if not task.succeeded and getattr(recognizer, "last_status", "") not in _SEMANTIC_RECOGNITION_STATUSES:
            raise RuntimeError("Maa 自定义识别任务未成功")
        if not task.succeeded:
            # The recognizer deliberately returns an empty box for a known,
            # non-clickable page state.  Its status is the workflow result.
            return None
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


def capture_huishi_settlement_reward(tasker, capture, guard) -> dict:
    """Wait briefly for a post-answer settlement page without any input."""
    last_reason = "settlement_not_observed"
    for poll in range(1, HUI_SHI_REWARD_POLLS + 1):
        guard.checkpoint()
        image = capture.capture(guard)
        height, width = image.shape[:2]
        task = tasker.post_recognition(
            "OCR", JOCR(expected=[".*"], roi=(0, 0, width, height), threshold=0.2), image,
        ).wait()
        guard.checkpoint()
        if not task.succeeded:
            last_reason = "settlement_ocr_failed"
        else:
            detail = task.get()
            texts = [] if detail is None or not detail.nodes else [
                {"text": item.text, "box": list(item.box)}
                for item in detail.nodes[-1].recognition.filtered_results
            ]
            result = parse_huishi_settlement_reward(texts, (width, height))
            if result["status"] == "confirmed":
                result["evidence"].update({
                    "frameId": capture.last_signature.frame_id,
                    "captureMethod": capture.last_signature.capture_method,
                    "imageSize": [width, height],
                    "poll": poll,
                })
                print("huishi_settlement_reward=" + json.dumps(result, ensure_ascii=False), flush=True)
                return result
            last_reason = result["reason"]
        if poll < HUI_SHI_REWARD_POLLS:
            time.sleep(HUI_SHI_REWARD_POLL_SECONDS)
    result = {"status": "unavailable", "reason": last_reason}
    print("huishi_settlement_reward=" + json.dumps(result, ensure_ascii=False), flush=True)
    return result


class KejuMacOSObservationSource:
    """Wrap the macOS/Maa recognizer without leaking it into the workflow."""

    def __init__(self, tasker, capture, recognizer, guard, exam_type: str):
        self.tasker = tasker
        self.capture = capture
        self.recognizer = recognizer
        self.guard = guard
        self.exam_type = exam_type
        self.step = 0

    def observe(self, selected_question_key=None, selected_answers=frozenset()):
        self.step += 1
        recognize_keju_frame(
            self.tasker,
            self.capture,
            self.recognizer,
            self.guard,
        )
        status = getattr(self.recognizer, "last_status", "unknown")
        question = getattr(self.recognizer, "last_question", "")
        image_size = getattr(self.recognizer, "last_image_size", None)
        if not image_size:
            image_size = self.capture.last_signature.image_size
        decision = {
            "status": status,
            "question": question,
            "question_key": stable_question_key(question),
            "question_round": getattr(
                self.recognizer,
                "last_question_round",
                None,
            ) or parse_question_round(question),
            "question_number": getattr(
                self.recognizer,
                "last_round_number",
                None,
            ),
            "options": getattr(self.recognizer, "last_options", {}),
            "answer": getattr(self.recognizer, "last_answer", ""),
            "source": getattr(self.recognizer, "last_source", ""),
            "click": getattr(self.recognizer, "last_click", None),
        }
        if status == "wrong_exam_type":
            expected_label = EXAM_LABELS[self.exam_type]
            observed_type = getattr(self.recognizer, "last_exam_type", None)
            observed_label = EXAM_LABELS.get(observed_type, "其他考试")
            decision["reason"] = (
                f"当前是科举{observed_label}页面，不能启动科举{expected_label}"
            )
        elif status == "unverified_exam_type":
            expected_label = EXAM_LABELS[self.exam_type]
            decision["reason"] = (
                f"无法确认当前是科举{expected_label}页面，已停止且未执行点击"
            )
        elif status in {"unreliable", "no_question"}:
            decision["reason"] = str(
                getattr(self.recognizer, "last_reason", "")
            )
        print(
            json.dumps(
                {
                    "step": self.step,
                    "status": status,
                    "questionNumber": decision["question_number"],
                    "question": question,
                    "options": decision["options"],
                    "answer": decision["answer"],
                    "source": decision["source"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return QuizObservation(
            MacOSQuizScene(tuple(image_size)),
            decision,
        )


def run(args, guard: CancellationGuard, progress: dict | None = None) -> dict | None:
    progress = progress if progress is not None else {
        "answers": 0,
        "clicks": 0,
        "limit": args.max_steps,
    }
    if getattr(args, "navigate_from_main", False):
        from macos_task_runtime import MacOSTaskRuntime

        runtime = MacOSTaskRuntime(guard)
        entry = HuishiEntryWorkflow().execute(
            WorkflowContext(runtime, guard, inspection_evidence_provider=runtime.inspection_evidence),
            inspect=args.inspect_scene or args.recognize_only,
        )
        if isinstance(entry, WorkflowInspection):
            print("preflight_json=" + json.dumps(entry.to_payload(), ensure_ascii=False), flush=True)
            return None
        if entry.status == "already_completed":
            return task_result(
                "already_completed", exam_type="huishi", answers=0, clicks=0, limit=args.max_steps
            )
    elif args.inspect_scene:
        from macos_task_runtime import MacOSTaskRuntime

        runtime = MacOSTaskRuntime(guard)
        scene = runtime.read_scene()
        payload = {
            "image_size": scene.image_size,
            "decision": classify_keju_inspection(scene.texts, scene.image_size),
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
    from macos_window_adapter import NativeMacOSInput, WindowCapture

    local_config = load_local_model_config()
    os.environ["MAA_DIRECT_RESOURCE"] = "1"
    os.environ["MAA_KEJU_RECOGNIZE_ONLY"] = "1"
    os.environ["MAA_KEJU_EXAM_TYPE"] = args.exam_type
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
    web_search = local_config.get("web_search") or {}
    if "enabled" in web_search:
        os.environ["MAA_KEJU_WEB_SEARCH_ENABLED"] = (
            "1" if web_search["enabled"] else "0"
        )
    if web_search.get("timeout"):
        os.environ["MAA_KEJU_WEB_SEARCH_TIMEOUT"] = str(web_search["timeout"])
    if web_search.get("limit_per_provider"):
        os.environ["MAA_KEJU_WEB_SEARCH_LIMIT"] = str(
            web_search["limit_per_provider"]
        )
    if web_search.get("providers"):
        os.environ["MAA_KEJU_WEB_SEARCH_PROVIDERS"] = ",".join(
            map(str, web_search["providers"])
        )
    if local_config.get("minimum_confidence") is not None:
        os.environ["MAA_KEJU_MODEL_MIN_CONFIDENCE"] = str(
            local_config["minimum_confidence"]
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
    if args.wait_for_entry:
        os.environ["MAA_KEJU_WAIT_FOR_ENTRY"] = "1"

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
    source = KejuMacOSObservationSource(
        tasker,
        capture,
        recognizer,
        guard,
        args.exam_type,
    )

    def click_answer(point, image_size, reason):
        screen_point = guard.perform_input(
            native_input.click,
            *point,
            image_size,
        )
        print(
            f"native_click reason={reason} source={point} screen={screen_point}",
            flush=True,
        )
        return screen_point

    def workflow_event(event, **payload):
        if event == "quiz_answered":
            progress["answers"] = payload["answers"]
            progress["clicks"] = payload["clicks"]
            question_round = payload.get("questionRound")
            if question_round and question_round[0] == 1:
                os.environ.pop("MAA_KEJU_REQUIRE_ROUND_ONE", None)
        elif event == "same_question_waiting":
            print("same_question_waiting=true", flush=True)
        elif event == "waiting_for_formal_question":
            print("waiting_for_formal_question=true", flush=True)
        elif event == "recognition_retry":
            reason = str(payload.get("reason") or "")
            print(
                f"recognition_retry={payload['polls']}/{payload['limit']} "
                f"status={payload['status']} reason={reason}",
                flush=True,
            )

    runtime = MacOSQuizInputAdapter(click_answer)
    outcome = KejuWorkflow(
        source,
        max_answers=args.max_steps,
        final_question_number=EXAM_FINAL_QUESTION_NUMBERS[args.exam_type],
        delay=args.delay,
        max_waiting_polls=MAX_WAITING_POLLS,
        max_same_question_polls=MAX_SAME_QUESTION_POLLS,
    ).execute(
        WorkflowContext(runtime, guard, workflow_event),
        preview=args.recognize_only,
    )
    if isinstance(outcome, WorkflowInspection):
        print(
            "action_json="
            + json.dumps(
                {
                    **outcome.decision,
                    "image_size": outcome.image_size,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return None
    if outcome.status == "completed":
        print("final_question_answered=true", flush=True)
    else:
        print(f"达到本次最大答题数：{args.max_steps}", flush=True)
    result_payload = {
        **outcome.payload,
    }
    if outcome.status == "completed" and args.exam_type == "huishi":
        result_payload["settlement_reward"] = capture_huishi_settlement_reward(
            tasker, capture, guard
        )
    return task_result(
        outcome.status,
        exam_type=args.exam_type,
        **result_payload,
    )


def main() -> int:
    args = parse_args()
    exam_type = getattr(args, "exam_type", "xiangshi")
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
            emit_result(
                task_result(
                    "cancelled",
                    exam_type=exam_type,
                    reason="收到停止请求",
                    **progress,
                )
            )
        print("stop_ack=true", flush=True)
        return 0
    except Exception as exc:
        if not read_only:
            emit_result(
                task_result(
                    "safe_stopped",
                    exam_type=exam_type,
                    reason=str(exc).strip() or exc.__class__.__name__,
                    **progress,
                )
            )
        else:
            print(f"read_only_error={exc}", flush=True)
        return 1

    if read_only:
        return 0
    if result is None:
        emit_result(
            task_result(
                "safe_stopped",
                exam_type=exam_type,
                reason="科举运行未返回最终结果",
                **progress,
            )
        )
        return 1
    emit_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
