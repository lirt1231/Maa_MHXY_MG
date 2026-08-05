#!/usr/bin/env python3
"""Run a bounded 抓鬼 workflow against the open macOS game client."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agent" / "custom" / "recognition"))

from maa.controller import (  # noqa: E402
    MacOSController,
    MaaMacOSInputMethodEnum,
    MaaMacOSScreencapMethodEnum,
)
from maa.pipeline import JOCR, JTemplateMatch  # noqa: E402
from maa.resource import Resource  # noqa: E402
from maa.tasker import Tasker  # noqa: E402
from maa.toolkit import Toolkit  # noqa: E402

from zhuogui_recognition import (  # noqa: E402
    FORMATIONS,
    classify_scene,
    proposed_action,
)
from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from macos_window_adapter import LegacyMacOSWindowBackend  # noqa: E402
from macos_window_backend import CapturedFrame  # noqa: E402
from automation_workflows import WorkflowContext  # noqa: E402
from macos_quiz_adapter import MacOSQuizInputAdapter, MacOSQuizScene  # noqa: E402
from quiz_workflows import QuizObservation  # noqa: E402
from zhuogui_workflow import (  # noqa: E402
    MAX_TARGET_LEVEL_SWIPES,
    MAX_TASKBAR_CLICK_ATTEMPTS,
    WorkflowState,
    ZhuoguiWorkflow,
    level_drag_end,
)


RESULT_PREFIX = "result_json="
RESULT_STATUSES = {
    "completed",
    "already_completed",
    "limit_reached",
    "safe_stopped",
    "cancelled",
}
LEVEL_RANGES = {
    "90-115": (90, 115),
    "20-115": (20, 115),
}
FORMATION_CHOICES = ("current", *FORMATIONS)


def build_result(
    status: str,
    state: WorkflowState,
    target_rounds: int,
    *,
    reason: str | None = None,
    max_steps: int | None = None,
) -> dict:
    """Build the stable, non-sensitive terminal result for one task run."""
    if status not in RESULT_STATUSES:
        raise ValueError(f"unsupported result status: {status}")
    if (
        isinstance(target_rounds, bool)
        or not isinstance(target_rounds, int)
        or target_rounds <= 0
    ):
        raise ValueError("target_rounds must be positive")
    if status in {"safe_stopped", "cancelled"} and not str(reason or "").strip():
        raise ValueError(f"{status} requires a reason")

    result = {
        "task_id": "zhuogui",
        "status": status,
        "rounds": state.completed_rounds,
        "target_rounds": target_rounds,
        "steps": state.steps,
    }
    if state.last_ghost_progress is not None:
        result["ghost_progress"] = state.last_ghost_progress
    if max_steps is not None:
        result["max_steps"] = max_steps
    if reason:
        result["reason"] = str(reason)
    return result


def emit_result(result: dict) -> None:
    """Emit the sole terminal protocol line consumed by the dashboard."""
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False), flush=True)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument(
        "--level-range",
        choices=tuple(LEVEL_RANGES),
        default="90-115",
    )
    parser.add_argument(
        "--formation",
        choices=FORMATION_CHOICES,
        default="current",
    )
    parser.add_argument("--inspect-scene", action="store_true")
    parser.add_argument("--recognize-only", action="store_true")
    parser.add_argument("--max-steps", type=int, default=600)
    parser.add_argument("--poll-interval", type=float, default=1.2)
    parser.add_argument("--recruit-timeout", type=float, default=180.0)
    parser.add_argument("--max-rebuilds", type=int, default=3)
    args = parser.parse_args()
    if args.rounds <= 0:
        parser.error("--rounds 必须是正整数；第一版不支持无限抓鬼")
    if args.max_steps <= 0:
        parser.error("--max-steps 必须是正整数")
    return args


def recognition_detail(tasker, recognition_type, recognition_param, image, guard):
    task = tasker.post_recognition(recognition_type, recognition_param, image).wait()
    guard.checkpoint()
    if not task.succeeded:
        raise RuntimeError(f"{recognition_type} 识别任务失败")
    detail = task.get()
    if detail is None or not detail.nodes:
        return None
    return detail.nodes[-1].recognition


def template_box(tasker, image, templates, guard, threshold=0.75, roi=None):
    height, width = image.shape[:2]
    search_roi = roi or (0, 0, width, height)
    detail = recognition_detail(
        tasker,
        "TemplateMatch",
        JTemplateMatch(template=templates, roi=search_roi, threshold=[threshold]),
        image,
        guard,
    )
    if detail is None or not detail.hit:
        return None
    return tuple(detail.box)


def read_scene(tasker, capture, guard):
    captured = capture.capture(guard)
    image = captured.image if isinstance(captured, CapturedFrame) else captured
    height, width = image.shape[:2]
    ocr = recognition_detail(
        tasker,
        "OCR",
        JOCR(expected=[".*"], roi=(0, 0, width, height), threshold=0.2),
        image,
        guard,
    )
    items = []
    if ocr is not None:
        items = [
            {"text": result.text, "box": list(result.box)}
            for result in ocr.filtered_results
        ]

    template_boxes = {
        "battle": template_box(
            tasker,
            image,
            ["zhandou/zidong.png", "zhandou/quxiao.png"],
            guard,
        ),
        "auto_pathing": template_box(
            tasker,
            image,
            [
                "zonghe/zidongxunlu1.png",
                "zonghe/zidongxunlu2.png",
                "zonghe/zidongxunlu3.png",
                "zonghe/zidongxunlu4.png",
            ],
            guard,
            0.75,
        ),
        "welfare": template_box(
            tasker,
            image,
            ["qiandao/fuli.png"],
            guard,
            0.8,
            (round(width * 0.30), 0, round(width * 0.48), round(height * 0.18)),
        ),
        "team_header": template_box(
            tasker,
            image,
            ["duiwu/duiwu-zudui.png", "duiwu/duiwu-duiwu.png"],
            guard,
            0.8,
        ),
        "target_icon": template_box(
            tasker, image, ["duiwu/duiwu-mubiao.png"], guard, 0.8
        ),
        "team_close": template_box(
            tasker, image, ["duiwu/duiwu-guanbi.png"], guard, 0.8
        ),
        "main_ready": template_box(
            tasker,
            image,
            ["zonghe/chenghao.png", "zonghe/jiahao.png"],
            guard,
            0.75,
        ),
        "small_map": template_box(
            tasker,
            image,
            ["zonghe/xiaoditu_taiyang.png", "zonghe/xiaoditu_yueliang.png"],
            guard,
            0.75,
        ),
        "generic_close": template_box(
            tasker,
            image,
            ["zonghe/guanbi3.png", "zonghe/guanbi4.png", "zonghe/guanbi2.png"],
            guard,
            0.75,
        ),
    }
    scene = {
        "image_size": [width, height],
        "texts": items,
        "templates": template_boxes,
    }
    scene["decision"] = classify_scene(items, (width, height), template_boxes)
    return scene


def box_center(box):
    x, y, width, height = box
    return x + width // 2, y + height // 2


def log_event(event, **payload):
    print(
        json.dumps(
            {"event": event, "time": round(time.time(), 3), **payload},
            ensure_ascii=False,
        ),
        flush=True,
    )


def scene_summary(scene):
    return {
        "image_size": scene["image_size"],
        "decision": scene["decision"],
        "proposed_action": proposed_action(scene["decision"]),
        "evidence": {
            "ocr_item_count": len(scene["texts"]),
            "matched_templates": sorted(
                name for name, box in scene["templates"].items() if box is not None
            ),
        },
    }


class ZhuoguiMacOSObservationSource:
    def __init__(self, tasker, capture, guard):
        self.tasker = tasker
        self.capture = capture
        self.guard = guard

    def observe(self):
        raw = read_scene(self.tasker, self.capture, self.guard)
        return QuizObservation(
            MacOSQuizScene(
                tuple(raw["image_size"]),
                list(raw["texts"]),
                dict(raw["templates"]),
            ),
            raw["decision"],
        )


def run_workflow(
    args,
    guard: CancellationGuard,
    state: WorkflowState | None = None,
) -> dict | None:
    state = state or WorkflowState()
    guard.checkpoint()
    os.environ["MAA_DIRECT_RESOURCE"] = "1"
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
    connection = controller.post_connection().wait()
    guard.checkpoint()
    if not connection.succeeded:
        raise RuntimeError("连接梦幻西游窗口失败")

    resource = Resource()
    for bundle in (ROOT / "assets/resource/base", ROOT / "assets/resource/mac"):
        guard.checkpoint()
        loading = resource.post_bundle(bundle).wait()
        guard.checkpoint()
        if not loading.succeeded:
            raise RuntimeError(f"加载资源失败：{bundle}")
    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise RuntimeError("初始化 Maa Tasker 失败")
    guard.bind_tasker(tasker)
    guard.checkpoint()

    window_id = windows[0].hwnd
    backend = LegacyMacOSWindowBackend(
        target_id="local-primary",
        window_id=window_id,
        controller=controller,
    )
    first_scene = read_scene(tasker, backend, guard)
    if args.inspect_scene:
        print(
            "preflight_json="
            + json.dumps(scene_summary(first_scene), ensure_ascii=False),
            flush=True,
        )
        return None
    if args.recognize_only:
        print(
            json.dumps(
                {
                    "image_size": first_scene["image_size"],
                    "decision": first_scene["decision"],
                    "proposed_action": proposed_action(first_scene["decision"]),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return None

    target_min_level, target_max_level = LEVEL_RANGES[args.level_range]
    source = ZhuoguiMacOSObservationSource(tasker, backend, guard)

    def click_point(point, image_size, reason):
        frame = backend.last_frame
        if frame is None or tuple(image_size) != frame.pixel_size:
            raise RuntimeError(f"{reason}缺少与识别画面一致的窗口帧")
        screen_point = backend.click(point, frame, guard)
        log_event(
            "click",
            reason=reason,
            source=point,
            screen=screen_point,
            phase=state.phase,
        )
        return screen_point

    def drag_points(start, end, image_size, reason):
        frame = backend.last_frame
        if frame is None or tuple(image_size) != frame.pixel_size:
            raise RuntimeError(f"{reason}缺少与识别画面一致的窗口帧")
        backend.drag(start, end, frame, guard)
        log_event("drag", reason=reason, start=start, end=end, phase=state.phase)
        return None

    def input_text(text, _image_size, reason):
        job = guard.perform_input(controller.post_input_text, text).wait()
        guard.checkpoint()
        if not job.succeeded:
            raise RuntimeError(f"{reason}失败")
        log_event(
            "input_text",
            reason=reason,
            characters=len(text),
            phase=state.phase,
        )
        return text

    def workflow_event(event, **payload):
        log_event(event, **payload)

    outcome = ZhuoguiWorkflow(
        source,
        target_rounds=args.rounds,
        target_min_level=target_min_level,
        target_max_level=target_max_level,
        level_range_label=args.level_range,
        formation=args.formation,
        max_steps=args.max_steps,
        poll_interval=args.poll_interval,
        recruit_timeout=args.recruit_timeout,
        max_rebuilds=args.max_rebuilds,
    ).execute(
        WorkflowContext(
            MacOSQuizInputAdapter(click_point, drag_points, input_text),
            guard,
            workflow_event,
        ),
        state,
    )
    return build_result(outcome.status, state, args.rounds)


def main() -> int:
    guard = CancellationGuard()
    guard.install_signal_handlers()
    args = parse_args()
    state = WorkflowState()
    try:
        result = run_workflow(args, guard, state)
        if args.inspect_scene or args.recognize_only:
            return 0
        if result is None:
            raise RuntimeError("捉鬼任务没有返回结构化最终结果")
        emit_result(result)
        return 0
    except CancellationRequested as exc:
        reason = str(exc) or "收到停止请求"
        try:
            guard.wait_for_tasker_stop()
        except Exception as stop_exc:
            reason = f"{reason}；停止确认失败：{stop_exc}"
        if not (args.inspect_scene or args.recognize_only):
            emit_result(
                build_result(
                    "cancelled",
                    state,
                    args.rounds,
                    reason=reason,
                )
            )
        log_event("stop_ack", status="cancelled", reason=reason)
        return 0
    except Exception as exc:
        if not (args.inspect_scene or args.recognize_only):
            emit_result(
                build_result(
                    "safe_stopped",
                    state,
                    args.rounds,
                    reason=str(exc) or "捉鬼任务异常停止",
                    max_steps=args.max_steps,
                )
            )
        print(f"safe_stop_reason={exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
