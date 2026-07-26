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
from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402

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


def read_scene(tasker, controller, guard):
    """Read the current client scene without causing any input."""
    screencap = controller.post_screencap().wait()
    guard.checkpoint()
    image = screencap.get()
    if image is None:
        raise RuntimeError("截图失败")
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
    scene["decision"] = classify_scene(scene)
    return scene


def _center(item):
    x, y, width, height = item["box"]
    return [x + width // 2, y + height // 2]


def classify_scene(scene):
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
    activity_buttons = [
        item
        for item in matching(r"^活动$")
        if item["box"][1] < height * 0.35
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

    sanjie = matching(r"三界奇缘")
    if sanjie:
        item = sanjie[0]
        center_y = _center(item)[1]
        same_row_done = [
            candidate
            for candidate in matching(r"已完成|完成")
            if abs(_center(candidate)[1] - center_y) <= 70
        ]
        if same_row_done:
            return {"status": "already_completed"}
        same_row_join = [
            candidate
            for candidate in matching(r"^参加$")
            if abs(_center(candidate)[1] - center_y) <= 80
        ]
        if same_row_join:
            return {
                "status": "click_join",
                "click": _center(same_row_join[0]),
            }
        return {"status": "activity_item_without_action"}

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


def navigate_to_exam(tasker, controller, native_input, timeout, guard):
    """Navigate by fresh OCR evidence until answer, completion, or a safe stop."""
    deadline = time.monotonic() + timeout
    daily_selected = False
    scroll_count = 0
    battle_return_attempts = 0
    while time.monotonic() < deadline:
        guard.checkpoint()
        scene = read_scene(tasker, controller, guard)
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
        if status == "click_daily" and daily_selected:
            if native_input is None:
                raise RuntimeError("活动列表浏览要求 --input-method native")
            if scroll_count >= 5:
                raise RuntimeError("浏览活动列表五次后仍未找到三界奇缘")
            width, height = scene["image_size"]
            guard.perform_input(
                native_input.drag,
                (round(width * 0.62), round(height * 0.62)),
                (round(width * 0.62), round(height * 0.27)),
                scene["image_size"],
            )
            scroll_count += 1
            print(f"navigation_scroll count={scroll_count}", flush=True)
            guard.wait(1.2)
            continue
        if "click" in decision:
            screen_point = perform_click(
                controller,
                native_input,
                decision["click"],
                scene["image_size"],
                guard,
            )
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


class NativeMacOSInput:
    """Point-aware macOS input for the iOS-wrapped client."""

    def __init__(self):
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
        self.click_function = self.library.MaaMHXYClickWindow
        self.click_function.argtypes = [
            ctypes.c_char_p,
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
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
        ]
        self.drag_function.restype = ctypes.c_int

    def click(self, x, y, source_size):
        output_x = ctypes.c_double()
        output_y = ctypes.c_double()
        result = self.click_function(
            b"com.netease.my",
            "梦幻西游".encode(),
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
            b"com.netease.my",
            "梦幻西游".encode(),
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
    guard = CancellationGuard()
    guard.install_signal_handlers()
    try:
        return run(guard)
    except CancellationRequested:
        guard.wait_for_tasker_stop()
        print("stop_ack=true", flush=True)
        return 0


def run(guard: CancellationGuard) -> int:
    args = parse_args()
    if args.recognize_only or args.input_method == "native":
        os.environ["MAA_SJQY_RECOGNIZE_ONLY"] = "1"
    Toolkit.init_option(str(ROOT))

    windows = [
        window
        for window in Toolkit.find_desktop_windows()
        if window.class_name == "com.netease.my" and window.window_name == "梦幻西游"
    ]
    if not windows:
        raise RuntimeError("未找到已打开的梦幻西游窗口")

    input_method = (
        MaaMacOSInputMethodEnum.PostToPid
        if args.input_method == "pid"
        else MaaMacOSInputMethodEnum.GlobalEvent
    )
    controller = MacOSController(
        windows[0].hwnd,
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

    if args.inspect_scene:
        print(
            json.dumps(
                read_scene(tasker, controller, guard),
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0

    native_input = NativeMacOSInput() if args.input_method == "native" else None
    if args.navigate:
        entry_status = navigate_to_exam(
            tasker,
            controller,
            native_input,
            args.entry_timeout,
            guard,
        )
        print(f"entry_status={entry_status}", flush=True)
        if entry_status != "answering":
            return 0
    else:
        print("entry_status=manual_answer_screen", flush=True)
    last_question_key = None
    for step in range(1, args.max_steps + 1):
        guard.checkpoint()
        recognizer.last_status = "running"
        task = tasker.post_task("活动-三界奇缘-开始答题_agent").wait()
        guard.checkpoint()
        detail = task.get()
        status = getattr(recognizer, "last_status", "unknown")
        question = getattr(recognizer, "last_question", "")
        question_key = getattr(recognizer, "last_question_key", question)
        print(f"step={step} status={status} question={question}", flush=True)
        if status == "ready" and question_key == last_question_key:
            print("题目状态未变化，停止重复点击", flush=True)
            break
        if status == "ready" and native_input is not None:
            click_point = stable_answer_click(
                recognizer.last_click,
                recognizer.last_image_size,
            )
            screen_point = guard.perform_input(
                native_input.click,
                *click_point,
                recognizer.last_image_size,
            )
            last_question_key = question_key
            print(
                f"native_click source={recognizer.last_click} "
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
                        "click": recognizer.last_click,
                        "image_size": recognizer.last_image_size,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if detail is None:
            raise RuntimeError("Maa 任务执行失败")
        if status not in ("clicked",):
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
