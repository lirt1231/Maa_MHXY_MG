"""Shared, bounded macOS runtime for native game automation tasks."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from maa.controller import (
    MacOSController,
    MaaMacOSInputMethodEnum,
    MaaMacOSScreencapMethodEnum,
)
from maa.pipeline import JOCR, JTemplateMatch
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

try:
    from .automation_calibration import CaptureSignature, CaptureStabilityGate
    from .automation_geometry import center, valid_box
    from .macos_cancellation import CancellationGuard
    from .macos_window_adapter import (
        LegacyMacOSWindowBackend,
        NativeMacOSInput,
        WindowCapture,
    )
    from .macos_window_backend import CapturedFrame
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_calibration import CaptureSignature, CaptureStabilityGate
    from automation_geometry import center, valid_box
    from macos_cancellation import CancellationGuard
    from macos_window_adapter import LegacyMacOSWindowBackend, NativeMacOSInput, WindowCapture
    from macos_window_backend import CapturedFrame


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class MacOSScene:
    image_size: tuple[int, int]
    texts: list[dict]
    templates: dict[str, tuple[int, int, int, int] | None]
    capture_signature: CaptureSignature | None = None
    frame: CapturedFrame | None = None


def box_center(box):
    x, y, width, height = box
    return x + width // 2, y + height // 2


def relative_roi(ratio, image_size):
    if not isinstance(ratio, (list, tuple)) or len(ratio) != 4:
        raise ValueError("模板相对识别区域必须包含四个数值")
    if not all(isinstance(value, (int, float)) for value in ratio):
        raise ValueError("模板相对识别区域必须是数值")
    left, top, roi_width, roi_height = ratio
    if (
        left < 0
        or top < 0
        or roi_width <= 0
        or roi_height <= 0
        or left + roi_width > 1
        or top + roi_height > 1
    ):
        raise ValueError("模板相对识别区域必须位于当前画面内")
    width, height = image_size
    x = round(width * left)
    y = round(height * top)
    return (
        x,
        y,
        max(1, min(width - x, round(width * roi_width))),
        max(1, min(height - y, round(height * roi_height))),
    )


def log_event(event: str, **payload) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


class MacOSTaskRuntime:
    """Connect once and expose only evidence-backed recognition and input APIs."""

    def __init__(self, guard: CancellationGuard):
        self.guard = guard
        self.target_id = "local-primary"
        Toolkit.init_option(str(ROOT))
        windows = [
            window
            for window in Toolkit.find_desktop_windows()
            if window.class_name == "com.netease.my" and window.window_name == "梦幻西游"
        ]
        if len(windows) > 1:
            raise RuntimeError("检测到多个梦幻西游窗口，无法保证截图与输入属于同一窗口")
        if windows:
            self.window_id = windows[0].hwnd
            self.native_input = NativeMacOSInput(self.window_id)
        else:
            self.native_input = NativeMacOSInput()
            self.window_id = self.native_input.find_window_id()
        if not self.window_id:
            raise RuntimeError("未找到已打开且可见的梦幻西游窗口")
        self.native_input.bind_window(self.window_id)
        log_event("target_bound", targetId=self.target_id, windowId=self.window_id)

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
        self.backend = LegacyMacOSWindowBackend(
            target_id=self.target_id,
            window_id=self.window_id,
            controller=self.controller,
            capture_driver=self.capture,
            native_input=self.native_input,
        )
        self.capture_gate = CaptureStabilityGate()

    def _recognize(self, kind, params, image):
        task = self.tasker.post_recognition(kind, params, image).wait()
        self.guard.checkpoint()
        if not task.succeeded:
            raise RuntimeError(f"{kind} 识别任务失败")
        detail = task.get()
        if detail is None or not detail.nodes:
            return None
        return detail.nodes[-1].recognition

    def read_scene(
        self,
        templates: dict[str, list[str] | Mapping[str, Any]] | None = None,
    ) -> MacOSScene:
        frame = self.backend.capture(self.guard)
        image = frame.image
        first_capture = self.capture_gate.baseline is None
        try:
            signature = self.capture_gate.observe(frame.signature)
        except RuntimeError:
            if not self.capture_gate.can_relock_to(frame.signature):
                raise
            # A window crossing display scale spaces can change raw pixels
            # between input steps. Re-capture before re-locking so no changed
            # frame can immediately authorize an action.
            candidate = frame.signature
            self.guard.wait(0.25)
            frame = self.backend.capture(self.guard)
            image = frame.image
            if not candidate.is_compatible_with(frame.signature):
                raise RuntimeError("截图几何变化尚未稳定，已阻止重新标定")
            signature = self.capture_gate.relock(frame.signature)
            log_event(
                "capture_calibration_relocked",
                targetId=self.target_id,
                windowId=self.window_id,
                **signature.to_payload(),
                **frame.geometry_payload(),
            )
        if first_capture:
            log_event(
                "capture_calibration_locked",
                targetId=self.target_id,
                windowId=self.window_id,
                **signature.to_payload(),
                **frame.geometry_payload(),
            )
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
        for name, template_spec in (templates or {}).items():
            threshold = 0.72
            green_mask = False
            roi = (0, 0, width, height)
            template_files = template_spec
            if isinstance(template_spec, Mapping):
                template_files = template_spec.get("templates", [])
                threshold = float(template_spec.get("threshold", threshold))
                green_mask = bool(template_spec.get("green_mask", False))
                if "roi_ratio" in template_spec:
                    roi = relative_roi(template_spec["roi_ratio"], (width, height))
            detail = self._recognize(
                "TemplateMatch",
                JTemplateMatch(
                    template=template_files,
                    roi=roi,
                    threshold=[threshold],
                    green_mask=green_mask,
                ),
                image,
            )
            matches[name] = tuple(detail.box) if detail is not None and detail.hit else None
        return MacOSScene((width, height), texts, matches, signature, frame)

    def _validate_scene_calibration(self, scene: MacOSScene) -> None:
        self.capture_gate.validate(scene.capture_signature)
        if scene.frame is None:
            raise RuntimeError("当前场景缺少原始截图帧，已阻止输入")
        if self.backend.last_frame is None or self.backend.last_frame.frame_id != scene.frame.frame_id:
            raise RuntimeError("当前场景已不是最新截图，已阻止使用过期证据输入")

    def inspection_evidence(self, scene: MacOSScene, decision) -> dict:
        """Build a read-only coordinate preview from the exact recognized frame."""
        self._validate_scene_calibration(scene)
        evidence = {
            **scene.frame.geometry_payload(),
            "anchorValidation": "pending",
        }
        box = valid_box({"box": decision.get("box")})
        if box is None:
            return evidence
        point = center({"box": box})
        self._validate_point(point, scene.image_size, "预检预测点")
        screen_point = self.backend.preview_point(point, scene.frame)
        evidence.update(
            {
                "recognizedBoxCenterPixels": point,
                "predictedScreenPoint": list(screen_point),
            }
        )
        return evidence

    def click(self, point, scene: MacOSScene, reason: str):
        self._validate_point(point, scene.image_size, reason)
        self._validate_scene_calibration(scene)
        screen_point = self.backend.click(point, scene.frame, self.guard)
        log_event(
            "click",
            targetId=self.target_id,
            windowId=self.window_id,
            reason=reason,
            source=point,
            screen=screen_point,
            frameId=scene.frame.frame_id,
            captureMethod=scene.frame.capture_method,
        )
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
        self._validate_scene_calibration(scene)
        self.backend.drag(start, end, scene.frame, self.guard)
        log_event(
            "drag",
            targetId=self.target_id,
            windowId=self.window_id,
            reason=reason,
            start=start,
            end=end,
            frameId=scene.frame.frame_id,
            captureMethod=scene.frame.capture_method,
        )

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
