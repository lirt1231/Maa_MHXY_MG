#!/usr/bin/env python3
"""Run the existing 三界奇缘 custom recognizer against the open macOS client."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import re
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "tools"))

from maa.controller import (  # noqa: E402
    MacOSController,
    MaaMacOSInputMethodEnum,
    MaaMacOSScreencapMethodEnum,
)
from maa.pipeline import JOCR, JTemplateMatch  # noqa: E402
from maa.resource import Resource  # noqa: E402
from maa.tasker import Tasker  # noqa: E402
from maa.toolkit import Toolkit  # noqa: E402
from daily_schedule_macos_core import daily_schedule_page_evidence  # noqa: E402
from daily_activity_navigation_core import ACTIVITY_UNAVAILABLE_PATTERN  # noqa: E402
from automation_workflows import WorkflowContext, WorkflowInspection  # noqa: E402
from macos_quiz_adapter import MacOSQuizInputAdapter, MacOSQuizScene  # noqa: E402
from quiz_workflows import QuizObservation  # noqa: E402
from sanjieqiyuan_workflow import (  # noqa: E402
    SanjieNavigationWorkflow,
    SanjieqiyuanWorkflow,
    advance_question_count,
)
from sanjieqiyuan_recognition import find_unique_answer_match  # noqa: E402
try:  # noqa: E402
    from .macos_cancellation import CancellationGuard, CancellationRequested
    from .macos_window_adapter import (
        CapturedFrame,
        NativeMacOSInput,
        SYSTEM_CAPTURE_SIZE,
        WindowCapture,
        system_window_capture,
        system_window_capture_frame,
    )
except ImportError:  # Direct execution through tools/run_sanjieqiyuan_macos.py.
    from macos_cancellation import CancellationGuard, CancellationRequested
    from macos_window_adapter import (
        CapturedFrame,
        NativeMacOSInput,
        SYSTEM_CAPTURE_SIZE,
        WindowCapture,
        system_window_capture,
        system_window_capture_frame,
    )


RESULT_PREFIX = "result_json="
SUCCESS_RESULT_STATUSES = {"completed", "already_completed", "limit_reached"}
MAX_NAVIGATION_INPUTS = 12
MAX_REPEATED_NAVIGATION_ACTIONS = 2


def emit_result(status: str, **payload) -> None:
    """Emit the single structured terminal result consumed by the dashboard."""
    if status not in SUCCESS_RESULT_STATUSES | {"safe_stopped", "cancelled"}:
        raise ValueError(f"unsupported result status: {status}")
    print(
        RESULT_PREFIX
        + json.dumps(
            {"task_id": "sanjieqiyuan", "status": status, **payload},
            ensure_ascii=False,
        ),
        flush=True,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument(
        "--input-method",
        choices=("native", "global", "pid"),
        default="native",
    )
    parser.add_argument("--recognize-only", action="store_true")
    parser.add_argument(
        "--navigate",
        action="store_true",
        help="可选：从当前游戏画面自动导航到三界奇缘；默认要求手动打开答题界面",
    )
    parser.add_argument("--entry-timeout", type=int, default=180)
    parser.add_argument(
        "--inspect-scene",
        action="store_true",
        help="只读取当前画面并输出 OCR/战斗状态，不执行点击",
    )
    return parser.parse_args()


def recognition_detail(
    tasker,
    recognition_type,
    recognition_param,
    image,
    guard,
):
    """Run one direct recognition and return its parsed detail."""
    task = tasker.post_recognition(
        recognition_type,
        recognition_param,
        image,
    ).wait()
    guard.checkpoint()
    detail = task.get()
    if detail is None or not detail.nodes:
        return None
    return detail.nodes[-1].recognition


def read_scene(tasker, capture, guard, *, daily_tab_confirmed=False):
    """Read the current client scene without causing any input."""
    image = capture.capture(guard)
    height, width = image.shape[:2]
    ocr_detail = recognition_detail(
        tasker,
        "OCR",
        JOCR(expected=[".*"], roi=(0, 0, width, height), threshold=0.2),
        image,
        guard,
    )
    texts = []
    if ocr_detail is not None:
        for result in ocr_detail.filtered_results:
            texts.append({"text": result.text, "box": list(result.box)})

    battle_detail = recognition_detail(
        tasker,
        "TemplateMatch",
        JTemplateMatch(
            template=["zhandou/zidong.png", "zhandou/quxiao.png"],
            roi=(0, 0, width, height),
            threshold=[0.75],
        ),
        image,
        guard,
    )
    activity_detail = recognition_detail(
        tasker,
        "TemplateMatch",
        JTemplateMatch(
            template=["zonghe/huodong1.png", "zonghe/huodong2.png"],
            roi=(0, 0, width, height),
            threshold=[0.8],
        ),
        image,
        guard,
    )
    scene = {
        "image_size": [width, height],
        "battle": bool(battle_detail and battle_detail.hit),
        "battle_box": list(battle_detail.box) if battle_detail and battle_detail.hit else None,
        "activity_box": list(activity_detail.box)
        if activity_detail and activity_detail.hit
        else None,
        "texts": texts,
    }
    scene["decision"] = classify_scene(
        scene, daily_tab_confirmed=daily_tab_confirmed
    )
    return scene


def _center(item):
    x, y, width, height = item["box"]
    return [x + width // 2, y + height // 2]


def _same_activity_card(anchor, candidate, image_size):
    """Require an action to be inside the target's card, not the other column."""
    width, height = image_size
    anchor_x, anchor_y = _center(anchor)
    candidate_x, candidate_y = _center(candidate)
    return (
        anchor_x + width * 0.04 <= candidate_x <= anchor_x + width * 0.30
        and anchor_y - height * 0.04 <= candidate_y <= anchor_y + height * 0.14
    )


def _navigation_action_signature(decision, image_size):
    point = decision.get("click")
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        return (decision["status"],)
    width, height = image_size
    return (
        decision["status"],
        round(point[0] / width, 2),
        round(point[1] / height, 2),
    )


def classify_scene(scene, *, daily_tab_confirmed=False):
    """Choose only actions that are justified by current OCR evidence."""
    width, height = scene["image_size"]
    items = scene["texts"]

    def matching(pattern):
        regex = re.compile(pattern)
        return [item for item in items if regex.search(item["text"])]

    if matching(r"第\s*\d+\s*题"):
        return {"status": "answering"}
    if matching(r"恭喜少侠完成所有题目|完成所有题目|正确率"):
        return {"status": "completed"}

    continue_ghost = matching(r"是否继续捉鬼")
    cancel_buttons = matching(r"^取消(?:（?\d+）?)?$")
    if continue_ghost and cancel_buttons:
        return {
            "status": "dismiss_known_popup",
            "reason": "continue_ghost",
            "click": _center(cancel_buttons[0]),
        }

    bag_overlay = matching(r"^包裹$") and matching(r"^物品$") and matching(r"任\s*务")
    if bag_overlay:
        return {
            "status": "close_bag_overlay",
            "click": [round(width * 0.865), round(height * 0.155)],
        }

    daily_tabs = matching(r"日常活动")
    daily_page = daily_schedule_page_evidence(items, scene["image_size"])
    if daily_tab_confirmed and daily_tabs:
        # Some themes make the stylized 活动 title unreadable to OCR. Once this
        # runner clicked 日常活动, the still-visible tab is sufficient stateful
        # evidence to inspect task cards without clicking the tab again.
        daily_page = daily_page or {
            "page_title": "confirmed_navigation",
            "daily_tab_detected": True,
        }
    activity_buttons = [
        item
        for item in matching(r"^活动$")
        if (
            width * 0.20 <= _center(item)[0] <= width * 0.45
            and _center(item)[1] <= height * 0.18
        )
    ]
    battle_cancel = [
        item
        for item in matching(r"^取消$")
        if item["box"][0] > width * 0.8 and item["box"][1] > height * 0.8
    ]
    if not daily_tabs and not activity_buttons and (
        scene["battle"] or battle_cancel or len(matching(r"准备中")) >= 2
    ):
        return {
            "status": "open_battle_menu",
            # Maa screenshot excludes the macOS title bar. The in-game return
            # button is near y=40 at a 720 px content height.
            "click": [round(width * 0.025), round(height * 0.055)],
        }

    sanjie = matching(r"三界奇缘") if daily_page else []
    if sanjie:
        item = sanjie[0]
        same_row_done = [
            candidate
            for candidate in matching(r"已完成|完成")
            if _same_activity_card(item, candidate, scene["image_size"])
        ]
        if same_row_done:
            return {"status": "already_completed"}
        same_row_join = [
            candidate
            for candidate in matching(r"^参加$")
            if _same_activity_card(item, candidate, scene["image_size"])
        ]
        if same_row_join:
            return {
                "status": "click_join",
                "click": _center(same_row_join[0]),
            }
        unavailable = [
            candidate
            for candidate in matching(ACTIVITY_UNAVAILABLE_PATTERN)
            if _same_activity_card(item, candidate, scene["image_size"])
        ]
        if unavailable:
            return {
                "status": "unavailable",
                "reason": unavailable[0]["text"],
            }
        return {
            "status": "scroll_daily",
            "reason": "target_action_not_visible",
        }

    if daily_page:
        return {"status": "scroll_daily", "reason": "target_not_visible"}

    if daily_tabs:
        return {"status": "click_daily", "click": _center(daily_tabs[0])}

    if activity_buttons:
        return {
            "status": "click_activity",
            "click": _center(activity_buttons[0]),
        }
    guide_buttons = [
        item
        for item in matching(r"^指引$")
        if item["box"][1] < height * 0.2
    ]
    idle_buttons = [
        item
        for item in matching(r"^挂机$|^挂$")
        if item["box"][1] < height * 0.2
    ]
    if guide_buttons and idle_buttons:
        guide_center = _center(guide_buttons[0])
        idle_center = _center(idle_buttons[0])
        if abs(guide_center[1] - idle_center[1]) <= 30:
            return {
                "status": "click_activity_toolbar",
                "click": [
                    round(guide_center[0] + (idle_center[0] - guide_center[0]) * 0.32),
                    round((guide_center[1] + idle_center[1]) / 2),
                ],
            }
    if scene.get("activity_box"):
        return {
            "status": "click_activity_template",
            "click": _center({"box": scene["activity_box"]}),
        }
    return {"status": "unknown"}


def perform_click(controller, native_input, point, image_size, guard):
    if native_input is not None:
        return guard.perform_input(native_input.click, *point, image_size)
    task = guard.perform_input(controller.post_click, *point).wait()
    guard.checkpoint()
    result = task
    if not result.succeeded:
        raise RuntimeError(f"Maa 点击失败：{point}")
    return point


def stable_answer_click(point, image_size, question=""):
    """Choose a reliable hit area for the current macOS answer card."""
    x, y = point
    if str(question).strip().startswith("找出"):
        return x, y
    width, height = image_size
    if width / height < 1.5 and height * 0.48 <= y <= height * 0.60:
        return x, round(y - height * 0.22)
    return x, y


def recognize_answer(
    tasker,
    image,
    guard,
    selected_question_key=None,
    selected_answers=(),
):
    """Recognize one question and its answer from an explicit screenshot."""
    from custom.recognition.reco_sjqy import (
        parse_answer_candidates,
        remaining_answer_candidates,
        sjqy_rois,
        stable_question_key,
    )
    from custom.recognition.searchAnswer import SearchQuestions

    question_roi, answer_roi = sjqy_rois(image)
    question_detail = recognition_detail(
        tasker,
        "OCR",
        JOCR(
            expected=[".*"],
            roi=tuple(question_roi),
            replace=[
                ["味", "昧"],
                ["邮", "邺"],
                ["尺", "尸"],
                ["频", "濒"],
                ["铜", "锢"],
            ],
        ),
        image,
        guard,
    )
    if question_detail is None or not question_detail.hit:
        return {"status": "finished", "question": ""}

    results = sorted(
        question_detail.filtered_results,
        key=lambda item: (round(item.box[1] / 10), item.box[0]),
    )
    question_text = "".join(item.text for item in results if item.text).strip()
    question_number_match = re.search(r"第\s*(\d+)\s*题", question_text)
    question_number = (
        int(question_number_match.group(1)) if question_number_match else None
    )
    question_key = (
        f"第{question_number}题"
        if question_number is not None
        else stable_question_key(question_text)
    )
    question = re.sub(r"第\d+题：|[（(]\d+/\d+[）)]", "", question_text).strip()
    answers, confidence, _ = SearchQuestions(question)
    candidates = parse_answer_candidates(answers)
    if confidence < 80:
        return {
            "status": "low_confidence",
            "question": question,
            "question_key": question_key,
            "question_number": question_number,
            "confidence": confidence,
        }

    already_selected = selected_answers if selected_question_key == question_key else ()
    remaining_candidates = remaining_answer_candidates(candidates, already_selected)
    if not remaining_candidates:
        return {
            "status": "awaiting_transition",
            "question": question,
            "question_key": question_key,
            "question_number": question_number,
            "candidates": candidates,
            "confidence": confidence,
        }

    answer_detail = recognition_detail(
        tasker,
        "OCR",
        JOCR(
            expected=remaining_candidates,
            roi=tuple(answer_roi),
            threshold=0.7,
            replace=[
                ["屍", "尸"],
                ["殭", "僵"],
                ["戶", "户"],
                ["僵户", "僵尸"],
            ],
        ),
        image,
        guard,
    )
    answer_match = None
    if answer_detail is not None and answer_detail.hit:
        answer_match = find_unique_answer_match(
            [answer_detail.best_result],
            remaining_candidates,
            answer_roi,
        )
    if answer_match is None and answer_detail is not None:
        answer_match = find_unique_answer_match(
            getattr(answer_detail, "all_results", ()),
            remaining_candidates,
            answer_roi,
        )
    if answer_match is None:
        return {
            "status": "answer_not_found",
            "question": question,
            "question_key": question_key,
            "question_number": question_number,
            "candidates": remaining_candidates,
            "confidence": confidence,
        }
    x, y, width, height = answer_match.box
    return {
        "status": "ready",
        "question": question,
        "question_key": question_key,
        "question_number": question_number,
        "click": (x + width // 2, y + height // 2),
        "image_size": (image.shape[1], image.shape[0]),
        "candidates": candidates,
        "answer": answer_match.candidate,
        "confidence": confidence,
    }


class SanjieMacOSObservationSource:
    """Adapt explicit macOS screenshots to the platform-neutral quiz contract."""

    def __init__(self, tasker, capture, guard):
        self.tasker = tasker
        self.capture = capture
        self.guard = guard

    def observe(self, selected_question_key=None, selected_answers=frozenset()):
        image = self.capture.capture(self.guard)
        decision = dict(
            recognize_answer(
                self.tasker,
                image,
                self.guard,
                selected_question_key,
                selected_answers,
            )
        )
        image_size = tuple(
            decision.get("image_size") or (image.shape[1], image.shape[0])
        )
        if decision.get("status") == "ready":
            decision["recognized_click"] = decision["click"]
            decision["click"] = stable_answer_click(
                decision["click"],
                image_size,
                decision.get("question", ""),
            )
        return QuizObservation(MacOSQuizScene(image_size), decision)


def navigate_to_exam(tasker, controller, capture, native_input, timeout, guard):
    """Navigate by fresh OCR evidence until answer, completion, or a safe stop."""
    deadline = time.monotonic() + timeout

    class NavigationSource:
        def observe(self, daily_tab_confirmed):
            if time.monotonic() >= deadline:
                raise TimeoutError("等待进入三界奇缘超时")
            guard.checkpoint()
            raw_scene = read_scene(
                tasker,
                capture,
                guard,
                daily_tab_confirmed=daily_tab_confirmed,
            )
            return QuizObservation(
                MacOSQuizScene(
                    tuple(raw_scene["image_size"]),
                    list(raw_scene.get("texts", [])),
                    {
                        "battle": raw_scene.get("battle_box"),
                        "activity_toolbar": raw_scene.get("activity_box"),
                    },
                ),
                raw_scene["decision"],
            )

    def click_point(point, image_size, reason):
        return perform_click(
            controller,
            native_input,
            point,
            image_size,
            guard,
        )

    def drag_points(start, end, image_size, reason):
        if native_input is None:
            raise RuntimeError("活动列表浏览要求 --input-method native")
        return guard.perform_input(native_input.drag, start, end, list(image_size))

    def workflow_event(event, **payload):
        if event == "navigation_scene":
            print(
                "scene=" + json.dumps(payload["decision"], ensure_ascii=False),
                flush=True,
            )
        elif event == "navigation_action":
            if payload["normalizedStatus"] == "scroll_daily":
                print(
                    f"navigation_scroll count={payload['drags']}",
                    flush=True,
                )
            else:
                print(
                    f"navigation_click source={payload.get('source')}",
                    flush=True,
                )

    outcome = SanjieNavigationWorkflow(
        NavigationSource(),
        max_inputs=MAX_NAVIGATION_INPUTS,
        max_repeated_actions=MAX_REPEATED_NAVIGATION_ACTIONS,
    ).execute(
        WorkflowContext(
            MacOSQuizInputAdapter(click_point, drag_points),
            guard,
            workflow_event,
        )
    )
    return outcome.status


def verify_answering_completion(tasker, capture, guard, attempts=3):
    """Require explicit completion evidence after the question area disappears."""
    for attempt in range(attempts):
        scene = read_scene(tasker, capture, guard)
        status = scene["decision"]["status"]
        if status in {"completed", "already_completed"}:
            return status
        if attempt + 1 < attempts:
            guard.wait(0.5)
    return None


def main() -> int:
    args = parse_args()
    read_only = args.inspect_scene or args.recognize_only
    guard = CancellationGuard()
    guard.install_signal_handlers()
    try:
        return run(guard, args)
    except CancellationRequested:
        guard.wait_for_tasker_stop()
        if not read_only:
            emit_result("cancelled", reason="收到停止请求")
        print("stop_ack=true", flush=True)
        return 0
    except Exception as exc:
        if not read_only:
            emit_result(
                "safe_stopped",
                reason=str(exc).strip() or exc.__class__.__name__,
            )
        print(f"safe_stop_reason={exc}", flush=True)
        return 1


def run(guard: CancellationGuard, args=None) -> int:
    args = args or parse_args()
    if args.recognize_only or args.input_method == "native":
        os.environ["MAA_SJQY_RECOGNIZE_ONLY"] = "1"
    Toolkit.init_option(str(ROOT))

    windows = [
        window
        for window in Toolkit.find_desktop_windows()
        if window.class_name == "com.netease.my" and window.window_name == "梦幻西游"
    ]
    native_input = None
    window_id = windows[0].hwnd if windows else 0
    if args.input_method == "native" and not args.recognize_only:
        native_input = NativeMacOSInput(window_id) if window_id else NativeMacOSInput()
        if not window_id:
            window_id = native_input.find_window_id()
    if not window_id:
        raise RuntimeError("未找到已打开的梦幻西游窗口")
    if native_input is not None:
        native_input.bind_window(window_id)
    print(
        json.dumps(
            {
                "event": "target_bound",
                "targetId": "local-primary",
                "windowId": window_id,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    input_method = (
        MaaMacOSInputMethodEnum.PostToPid
        if args.input_method == "pid"
        else MaaMacOSInputMethodEnum.GlobalEvent
    )
    controller = MacOSController(
        window_id,
        MaaMacOSScreencapMethodEnum.ScreenCaptureKit,
        input_method,
    )
    connection = controller.post_connection().wait()
    guard.checkpoint()
    if not connection.succeeded:
        raise RuntimeError("连接梦幻西游窗口失败")

    # Importing the project module registers AgentServer decorators. Keep it
    # in direct-resource mode so this process stays in MaaFramework context.
    os.environ["MAA_DIRECT_RESOURCE"] = "1"
    from custom.recognition.reco_sjqy import sjqy_tiku_V3

    resource = Resource()
    for bundle in (ROOT / "assets/resource/base", ROOT / "assets/resource/mac"):
        bundle_task = resource.post_bundle(bundle).wait()
        guard.checkpoint()
        if not bundle_task.succeeded:
            raise RuntimeError(f"加载资源失败：{bundle}")

    recognizer = sjqy_tiku_V3()
    if not resource.register_custom_recognition("sjqy_tiku_V3", recognizer):
        raise RuntimeError("注册 sjqy_tiku_V3 失败")

    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise RuntimeError("初始化 Maa Tasker 失败")
    guard.bind_tasker(tasker)
    guard.checkpoint()
    capture = WindowCapture(controller, window_id)

    if args.inspect_scene:
        print(
            "preflight_json=" + json.dumps(
                read_scene(tasker, capture, guard),
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0

    if args.navigate:
        entry_status = navigate_to_exam(
            tasker,
            controller,
            capture,
            native_input,
            args.entry_timeout,
            guard,
        )
        print(f"entry_status={entry_status}", flush=True)
        if entry_status != "answering":
            emit_result(entry_status, questions=0, clicks=0)
            return 0
    else:
        print("entry_status=manual_answer_screen", flush=True)
    source = SanjieMacOSObservationSource(tasker, capture, guard)

    def click_answer(point, image_size, reason):
        screen_point = perform_click(
            controller,
            native_input,
            point,
            image_size,
            guard,
        )
        print(
            f"answer_click reason={reason} source={point} screen={screen_point}",
            flush=True,
        )
        return screen_point

    def workflow_event(event, **payload):
        if event == "quiz_observation":
            print(
                f"click={payload['clicks'] + 1} "
                f"completed={payload['questions']} "
                f"status={payload['status']} question={payload['question']}",
                flush=True,
            )
        elif event == "answer_recognition_retry":
            print(
                f"answer_recognition_retry={payload['attempt']}/"
                f"{payload['limit']} question={payload['question']}",
                flush=True,
            )

    outcome = SanjieqiyuanWorkflow(
        source,
        lambda: verify_answering_completion(tasker, capture, guard),
        max_questions=args.max_steps,
    ).execute(
        WorkflowContext(
            MacOSQuizInputAdapter(click_answer),
            guard,
            workflow_event,
        ),
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
        print("recognize_only_complete=true", flush=True)
        return 0

    if outcome.status in {"completed", "already_completed"}:
        print(
            f"answering_complete questions={outcome.payload['questions']} "
            f"clicks={outcome.payload['clicks']}",
            flush=True,
        )
    else:
        print(
            f"question_limit_reached questions={outcome.payload['questions']} "
            f"clicks={outcome.payload['clicks']}",
            flush=True,
        )
    emit_result(outcome.status, **outcome.payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
