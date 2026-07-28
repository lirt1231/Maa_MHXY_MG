#!/usr/bin/env python3
"""Run the existing 三界奇缘 custom recognizer against the open macOS client."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import re
from pathlib import Path

import numpy as np
from PIL import Image


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
from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402


SYSTEM_CAPTURE_SIZE = (1020, 718)
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


def system_window_capture(window_id: int) -> np.ndarray:
    """Capture only the game window without relying on Maa ScreenCaptureKit."""
    with tempfile.NamedTemporaryFile(suffix=".png") as output:
        completed = subprocess.run(
            ["/usr/sbin/screencapture", "-x", "-l", str(window_id), output.name],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if completed.returncode != 0:
            raise RuntimeError("游戏窗口截图失败，请检查屏幕录制权限和窗口可见性")
        image = Image.open(output.name).convert("RGB")
        raw = np.asarray(image)
        visible = raw.max(axis=2) > 20
        ys, xs = np.where(visible)
        if not len(xs):
            raise RuntimeError("游戏窗口截图为空，请确认窗口未最小化且完整可见")
        pixel_scale = 2 if image.width >= 1600 else 1
        left, right = int(xs.min()), int(xs.max()) + 1
        top = min(int(ys.max()), int(ys.min()) + 24 * pixel_scale)
        bottom = int(ys.max()) + 1
        content = image.crop((left, top, right, bottom)).resize(SYSTEM_CAPTURE_SIZE)
        return np.asarray(content)[:, :, ::-1].copy()


class WindowCapture:
    """Use Maa when available, then keep a stable system-capture fallback."""

    def __init__(self, controller, window_id, fallback=system_window_capture):
        self.controller = controller
        self.window_id = window_id
        self.fallback = fallback
        self.maa_available = True

    def capture(self, guard):
        guard.checkpoint()
        if self.maa_available:
            try:
                job = self.controller.post_screencap().wait()
                guard.checkpoint()
                image = job.get()
                if image is None or image.size == 0:
                    raise RuntimeError("Maa 截图为空")
                return image
            except CancellationRequested:
                raise
            except Exception as exc:
                self.maa_available = False
                print(f"screencap_fallback=system reason={exc}", flush=True)
        image = self.fallback(self.window_id)
        guard.checkpoint()
        return image

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
        return {
            "status": "scroll_daily",
            "reason": "target_action_not_visible",
        }

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


def stable_answer_click(point, image_size):
    """Move a macOS answer-label hit into the same card's portrait body."""
    x, y = point
    width, height = image_size
    if width / height < 1.5 and height * 0.48 <= y <= height * 0.60:
        return x, round(y - height * 0.22)
    return x, y


def advance_question_count(current_question, next_question, completed_questions):
    """Count completed questions, not individual choices in multi-select questions."""
    if current_question and next_question != current_question:
        completed_questions += 1
    return next_question, completed_questions


def recognize_answer(tasker, image, guard):
    """Recognize one question and its answer from an explicit screenshot."""
    from custom.recognition.reco_sjqy import (
        parse_answer_candidates,
        sjqy_rois,
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
    question_key = "".join(item.text for item in results if item.text).strip()
    question = re.sub(r"第\d+题：|[（(]\d+/\d+[）)]", "", question_key).strip()
    answers, confidence, _ = SearchQuestions(question)
    candidates = parse_answer_candidates(answers)
    if confidence < 80:
        return {
            "status": "low_confidence",
            "question": question,
            "question_key": question_key,
            "confidence": confidence,
        }

    answer_detail = recognition_detail(
        tasker,
        "OCR",
        JOCR(expected=candidates, roi=tuple(answer_roi)),
        image,
        guard,
    )
    if answer_detail is None or not answer_detail.hit:
        return {
            "status": "answer_not_found",
            "question": question,
            "question_key": question_key,
            "candidates": candidates,
            "confidence": confidence,
        }
    x, y, width, height = answer_detail.box
    return {
        "status": "ready",
        "question": question,
        "question_key": question_key,
        "click": (x + width // 2, y + height // 2),
        "image_size": (image.shape[1], image.shape[0]),
        "candidates": candidates,
        "confidence": confidence,
    }


def navigate_to_exam(tasker, controller, capture, native_input, timeout, guard):
    """Navigate by fresh OCR evidence until answer, completion, or a safe stop."""
    deadline = time.monotonic() + timeout
    daily_selected = False
    scroll_count = 0
    battle_return_attempts = 0
    navigation_inputs = 0
    action_counts = {}
    while time.monotonic() < deadline:
        guard.checkpoint()
        scene = read_scene(
            tasker,
            capture,
            guard,
            daily_tab_confirmed=daily_selected,
        )
        decision = scene["decision"]
        status = decision["status"]
        print(
            "scene=" + json.dumps(decision, ensure_ascii=False),
            flush=True,
        )
        if status == "answering":
            return "answering"
        if status in ("completed", "already_completed"):
            return status
        if status == "open_battle_menu":
            if battle_return_attempts == 0:
                battle_return_attempts = 1
            elif battle_return_attempts == 1:
                # The expanded battle shortcut menu is dark and OCR may miss
                # its labels. The activity icon has a stable slot below the
                # top row, so use it only after a freshly verified battle and
                # one successful menu-open click.
                width, height = scene["image_size"]
                decision = {
                    "status": "click_activity_side_menu",
                    "click": [round(width * 0.038), round(height * 0.161)],
                }
                status = decision["status"]
                battle_return_attempts = 2
            else:
                raise RuntimeError("战斗快捷菜单打开后仍无法进入活动界面")
        if status == "scroll_daily" or (status == "click_daily" and daily_selected):
            if native_input is None:
                raise RuntimeError("活动列表浏览要求 --input-method native")
            if scroll_count >= 5:
                raise RuntimeError("浏览活动列表五次后仍未找到三界奇缘")
            if navigation_inputs >= MAX_NAVIGATION_INPUTS:
                raise RuntimeError("活动导航已达到输入上限，已停止以避免死循环")
            width, height = scene["image_size"]
            scroll_end_y = 0.52 if status == "scroll_daily" else 0.27
            guard.perform_input(
                native_input.drag,
                (round(width * 0.62), round(height * 0.62)),
                (round(width * 0.62), round(height * scroll_end_y)),
                scene["image_size"],
            )
            scroll_count += 1
            navigation_inputs += 1
            daily_selected = True
            print(f"navigation_scroll count={scroll_count}", flush=True)
            guard.wait(1.2)
            continue
        if "click" in decision:
            signature = _navigation_action_signature(decision, scene["image_size"])
            action_counts[signature] = action_counts.get(signature, 0) + 1
            if action_counts[signature] > MAX_REPEATED_NAVIGATION_ACTIONS:
                raise RuntimeError(
                    f"检测到重复导航动作 {status}，已停止以避免死循环"
                )
            if navigation_inputs >= MAX_NAVIGATION_INPUTS:
                raise RuntimeError("活动导航已达到输入上限，已停止以避免死循环")
            screen_point = perform_click(
                controller,
                native_input,
                decision["click"],
                scene["image_size"],
                guard,
            )
            navigation_inputs += 1
            print(
                f"navigation_click source={decision['click']} screen={screen_point}",
                flush=True,
            )
            if status == "click_daily":
                daily_selected = True
            guard.wait(1.2)
            continue
        raise RuntimeError(
            "当前画面没有足够证据执行安全点击："
            + json.dumps(scene, ensure_ascii=False)
        )
    raise TimeoutError("等待进入三界奇缘超时")


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


class NativeMacOSInput:
    """Point-aware macOS input for the iOS-wrapped client."""

    def __init__(self, window_id=None):
        source = ROOT / "tools/macos_window_input.mm"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        library_path = Path(tempfile.gettempdir()) / f"libmaa_mhxy_input_{digest}.dylib"
        if not library_path.exists():
            subprocess.run(
                [
                    "xcrun",
                    "clang++",
                    "-std=c++17",
                    "-fobjc-arc",
                    "-dynamiclib",
                    str(source),
                    "-framework",
                    "AppKit",
                    "-framework",
                    "ApplicationServices",
                    "-framework",
                    "CoreGraphics",
                    "-framework",
                    "Foundation",
                    "-o",
                    str(library_path),
                ],
                check=True,
                cwd=ROOT,
            )
        self.library = ctypes.CDLL(str(library_path))
        self.find_window_function = self.library.MaaMHXYFindWindow
        self.find_window_function.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self.find_window_function.restype = ctypes.c_uint32
        self.click_function = self.library.MaaMHXYClickWindow
        self.click_function.argtypes = [
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
        ]
        self.click_function.restype = ctypes.c_int
        self.drag_function = self.library.MaaMHXYDragWindow
        self.drag_function.argtypes = [
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
        ]
        self.drag_function.restype = ctypes.c_int
        self.window_id = 0
        if window_id is not None:
            self.bind_window(window_id)

    def find_window_id(self):
        return int(self.find_window_function(b"com.netease.my", "梦幻西游".encode()))

    def bind_window(self, window_id):
        if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0:
            raise ValueError("原生输入需要有效的窗口编号")
        self.window_id = window_id
        return self.window_id

    def _bound_window_id(self):
        if not self.window_id:
            # Compatibility for older task runners. Resolve once and keep the
            # result so later input cannot silently switch to another window.
            self.bind_window(self.find_window_id())
        return self.window_id

    def click(self, x, y, source_size):
        output_x = ctypes.c_double()
        output_y = ctypes.c_double()
        result = self.click_function(
            self._bound_window_id(),
            b"com.netease.my",
            float(x),
            float(y),
            float(source_size[0]),
            float(source_size[1]),
            ctypes.byref(output_x),
            ctypes.byref(output_y),
        )
        if result != 0:
            raise RuntimeError(f"原生窗口点击失败，错误码：{result}")
        return output_x.value, output_y.value

    def drag(self, from_point, to_point, source_size):
        result = self.drag_function(
            self._bound_window_id(),
            b"com.netease.my",
            float(from_point[0]),
            float(from_point[1]),
            float(to_point[0]),
            float(to_point[1]),
            float(source_size[0]),
            float(source_size[1]),
        )
        if result != 0:
            raise RuntimeError(f"原生窗口拖动失败，错误码：{result}")


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
    last_question_key = None
    current_question = None
    completed_questions = 0
    click_count = 0
    unchanged_polls = 0
    max_clicks = max(20, args.max_steps * 4)
    while completed_questions < args.max_steps and click_count < max_clicks:
        guard.checkpoint()
        if click_count == 0:
            guard.wait(1.5)
        result = recognize_answer(tasker, capture.capture(guard), guard)
        status = result["status"]
        question = result.get("question", "")
        question_key = result.get("question_key", question)
        print(
            f"click={click_count + 1} completed={completed_questions} "
            f"status={status} question={question}",
            flush=True,
        )
        if status == "finished":
            questions = completed_questions + bool(current_question)
            completion_status = verify_answering_completion(
                tasker, capture, guard
            )
            if completion_status is None:
                raise RuntimeError(
                    "题目区域已消失，但未识别到三界奇缘完成状态，已安全停止"
                )
            print(
                f"answering_complete questions={questions} "
                f"clicks={click_count}",
                flush=True,
            )
            emit_result(completion_status, questions=questions, clicks=click_count)
            return 0
        if status == "ready" and question_key == last_question_key:
            unchanged_polls += 1
            if unchanged_polls >= 6:
                raise RuntimeError("点击后题目状态持续未变化，已停止以避免重复点击")
            print(f"question_unchanged retry={unchanged_polls}", flush=True)
            guard.wait(0.5)
            continue
        unchanged_polls = 0
        if status == "ready" and native_input is not None:
            current_question, completed_questions = advance_question_count(
                current_question,
                question,
                completed_questions,
            )
            if completed_questions >= args.max_steps:
                break
            click_point = stable_answer_click(
                result["click"],
                result["image_size"],
            )
            screen_point = guard.perform_input(
                native_input.click,
                *click_point,
                result["image_size"],
            )
            last_question_key = question_key
            click_count += 1
            print(
                f"native_click source={result['click']} "
                f"stable={click_point} screen={screen_point}",
                flush=True,
            )
            guard.wait(1.2)
            continue
        if status == "ready":
            print(
                "action_json="
                + json.dumps(
                    {
                        "status": status,
                        "question": question,
                        "question_key": question_key,
                        "click": result["click"],
                        "image_size": result["image_size"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            break
        if status == "low_confidence":
            raise RuntimeError(
                f"题库匹配度不足，停止答题：{question}（{result.get('confidence', 0)}%）"
            )
        if status == "answer_not_found":
            raise RuntimeError(
                f"题库答案未在当前选项中找到：{question}，候选：{result.get('candidates', [])}"
            )
        raise RuntimeError(f"三界奇缘进入未知识别状态：{status}")

    if args.recognize_only:
        print("recognize_only_complete=true", flush=True)
        return 0
    if click_count >= max_clicks:
        raise RuntimeError(f"已执行 {click_count} 次选择但活动仍未完成，已停止以避免异常循环")
    print(
        f"question_limit_reached questions={completed_questions} clicks={click_count}",
        flush=True,
    )
    emit_result(
        "limit_reached",
        questions=click_count,
        clicks=click_count,
        limit=args.max_steps,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
