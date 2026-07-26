"""Shared, bounded macOS runtime for native game automation tasks."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from maa.controller import (
    MacOSController,
    MaaMacOSInputMethodEnum,
    MaaMacOSScreencapMethodEnum,
)
from maa.pipeline import JOCR, JTemplateMatch
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

from macos_cancellation import CancellationGuard
from run_sanjieqiyuan_macos import NativeMacOSInput, WindowCapture


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class MacOSScene:
    image_size: tuple[int, int]
    texts: list[dict]
    templates: dict[str, tuple[int, int, int, int] | None]


def box_center(box):
    x, y, width, height = box
    return x + width // 2, y + height // 2


def log_event(event: str, **payload) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


class MacOSTaskRuntime:
    """Connect once and expose only evidence-backed recognition and input APIs."""

    def __init__(self, guard: CancellationGuard):
        self.guard = guard
        Toolkit.init_option(str(ROOT))
        self.native_input = NativeMacOSInput()
        windows = [
            window
            for window in Toolkit.find_desktop_windows()
            if window.class_name == "com.netease.my" and window.window_name == "梦幻西游"
        ]
        if len(windows) > 1:
            raise RuntimeError("检测到多个梦幻西游窗口，无法保证截图与输入属于同一窗口")
        self.window_id = windows[0].hwnd if windows else self.native_input.find_window_id()
        if not self.window_id:
            raise RuntimeError("未找到已打开且可见的梦幻西游窗口")

        self.controller = MacOSController(
            self.window_id,
            MaaMacOSScreencapMethodEnum.ScreenCaptureKit,
            MaaMacOSInputMethodEnum.GlobalEvent,
        )
        connection = self.controller.post_connection().wait()
        guard.checkpoint()
        if not connection.succeeded:
            raise RuntimeError("连接梦幻西游窗口失败")

        self.resource = Resource()
        for bundle in (ROOT / "assets/resource/base", ROOT / "assets/resource/mac"):
            loading = self.resource.post_bundle(bundle).wait()
            guard.checkpoint()
            if not loading.succeeded:
                raise RuntimeError(f"加载资源失败：{bundle.name}")
        self.tasker = Tasker()
        if not self.tasker.bind(self.resource, self.controller) or not self.tasker.inited:
            raise RuntimeError("初始化 Maa Tasker 失败")
        guard.bind_tasker(self.tasker)
        self.capture = WindowCapture(self.controller, self.window_id)

    def _recognize(self, kind, params, image):
        task = self.tasker.post_recognition(kind, params, image).wait()
        self.guard.checkpoint()
        if not task.succeeded:
            raise RuntimeError(f"{kind} 识别任务失败")
        detail = task.get()
        if detail is None or not detail.nodes:
            return None
        return detail.nodes[-1].recognition

    def read_scene(self, templates: dict[str, list[str]] | None = None) -> MacOSScene:
        image = self.capture.capture(self.guard)
        height, width = image.shape[:2]
        ocr = self._recognize(
            "OCR",
            JOCR(expected=[".*"], roi=(0, 0, width, height), threshold=0.2),
            image,
        )
        texts = [] if ocr is None else [
            {"text": item.text, "box": list(item.box)}
            for item in ocr.filtered_results
        ]
        matches = {}
        for name, template_files in (templates or {}).items():
            detail = self._recognize(
                "TemplateMatch",
                JTemplateMatch(
                    template=template_files,
                    roi=(0, 0, width, height),
                    threshold=[0.72],
                ),
                image,
            )
            matches[name] = tuple(detail.box) if detail is not None and detail.hit else None
        return MacOSScene((width, height), texts, matches)

    def click(self, point, scene: MacOSScene, reason: str):
        self._validate_point(point, scene.image_size, reason)
        screen_point = self.guard.perform_input(
            self.native_input.click,
            point[0],
            point[1],
            scene.image_size,
        )
        log_event("click", reason=reason, source=point, screen=screen_point)
        return screen_point

    def click_box(self, box, scene: MacOSScene, reason: str):
        if box is None:
            raise RuntimeError(f"{reason}缺少经过识别的点击区域")
        if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
            raise RuntimeError(f"{reason}的识别区域无效")
        return self.click(box_center(box), scene, reason)

    def drag(self, start, end, scene: MacOSScene, reason: str):
        self._validate_point(start, scene.image_size, reason)
        self._validate_point(end, scene.image_size, reason)
        self.guard.perform_input(
            self.native_input.drag,
            start,
            end,
            scene.image_size,
        )
        log_event("drag", reason=reason, start=start, end=end)

    @staticmethod
    def _validate_point(point, image_size, reason: str) -> None:
        if len(point) != 2 or len(image_size) != 2:
            raise RuntimeError(f"{reason}的输入坐标无效")
        x, y = point
        width, height = image_size
        values = (x, y, width, height)
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
            raise RuntimeError(f"{reason}的输入坐标无效")
        if width <= 0 or height <= 0 or not (0 <= x < width and 0 <= y < height):
            raise RuntimeError(f"{reason}的输入坐标超出当前画面")
