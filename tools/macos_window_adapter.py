"""macOS-specific window capture and native input adapter."""

from __future__ import annotations

import ctypes
import hashlib
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

try:
    from .automation_calibration import CaptureSignature
    from .macos_cancellation import CancellationRequested
    from .macos_window_backend import (
        CapturedFrame,
        WindowTarget,
        map_frame_point_to_screen,
    )
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_calibration import CaptureSignature
    from macos_cancellation import CancellationRequested
    from macos_window_backend import CapturedFrame, WindowTarget, map_frame_point_to_screen


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_CAPTURE_SIZE = (1020, 718)
MACOS_TITLE_BAR_HEIGHT_POINTS = 24
GEOMETRY_CAPTURE_ATTEMPTS = 8
GEOMETRY_RETRY_WAIT_SECONDS = 1.0


class _TransientGeometryMismatch(RuntimeError):
    """The screenshot and CGWindow bounds were sampled from different states."""


def _window_content_rect(captured: Image.Image, pixel_scale: int):
    """Find opaque window bounds while excluding shadow and title bar."""
    raw = np.asarray(captured.convert("RGB"))
    if "A" in captured.getbands():
        # Non-zero alpha includes the large translucent macOS window shadow.
        # Fully opaque pixels describe the actual rounded window bounds.
        visible = np.asarray(captured.getchannel("A")) >= 250
    else:
        visible = raw.max(axis=2) > 20
    ys, xs = np.where(visible)
    if not len(xs):
        raise RuntimeError("游戏窗口截图为空，请确认窗口未最小化且完整可见")
    left, right = int(xs.min()), int(xs.max()) + 1
    window_top, bottom = int(ys.min()), int(ys.max()) + 1
    top = window_top + MACOS_TITLE_BAR_HEIGHT_POINTS * pixel_scale
    if top >= bottom:
        raise RuntimeError("游戏窗口内容区域无效")
    return left, top, right - left, bottom - top


def system_window_capture_frame(window_id: int) -> CapturedFrame:
    """Capture the game content and retain its unscaled window signature."""
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "window.png"
        completed = subprocess.run(
            ["/usr/sbin/screencapture", "-x", "-l", str(window_id), str(output)],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if completed.returncode != 0:
            raise RuntimeError("游戏窗口截图失败，请检查屏幕录制权限和窗口可见性")
        captured = Image.open(output)
        image = captured.convert("RGB")
        pixel_scale = 2 if image.width >= 1600 else 1
        content_rect = _window_content_rect(captured, pixel_scale)
        left, top, width, height = content_rect
        content = image.crop((left, top, left + width, top + height)).resize(
            SYSTEM_CAPTURE_SIZE
        )
        array = np.asarray(content)[:, :, ::-1].copy()
        return CapturedFrame(
            image=array,
            signature=CaptureSignature(
                source="system",
                image_size=SYSTEM_CAPTURE_SIZE,
                source_size=(image.width, image.height),
                content_rect=content_rect,
                pixel_scale=float(pixel_scale),
            ),
            capture_method="system",
        )


def system_window_capture(window_id: int) -> np.ndarray:
    """Compatibility wrapper returning only the normalized game image."""
    return system_window_capture_frame(window_id).image


class WindowCapture:
    """Use Maa when available, then keep a stable system-capture fallback."""

    def __init__(self, controller, window_id, fallback=system_window_capture_frame):
        self.controller = controller
        self.window_id = window_id
        self.fallback = fallback
        self.maa_available = True
        self.last_signature = None
        self.last_frame = None

    def _record(self, image, source):
        if isinstance(image, CapturedFrame):
            self.last_signature = image.signature
            self.last_frame = image
            return image.image
        height, width = image.shape[:2]
        self.last_signature = CaptureSignature(
            source=source,
            image_size=(width, height),
            source_size=(width, height),
            content_rect=(0, 0, width, height),
        )
        self.last_frame = CapturedFrame(
            image=image,
            signature=self.last_signature,
            capture_method=source,
        )
        return image

    def capture(self, guard):
        guard.checkpoint()
        if self.maa_available:
            try:
                job = self.controller.post_screencap().wait()
                guard.checkpoint()
                image = job.get()
                if image is None or image.size == 0:
                    raise RuntimeError("Maa 截图为空")
                return self._record(image, "maa")
            except CancellationRequested:
                raise
            except Exception as exc:
                self.maa_available = False
                print(f"screencap_fallback=system reason={exc}", flush=True)
        image = self.fallback(self.window_id)
        guard.checkpoint()
        return self._record(image, "system")


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
        self.geometry_function = self.library.MaaMHXYGetWindowGeometry
        self.geometry_function.argtypes = [
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_int),
        ]
        self.geometry_function.restype = ctypes.c_int
        self.screen_click_function = self.library.MaaMHXYClickScreenPoint
        self.screen_click_function.argtypes = [
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
        ]
        self.screen_click_function.restype = ctypes.c_int
        self.screen_drag_function = self.library.MaaMHXYDragScreenPoints
        self.screen_drag_function.argtypes = [
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_double,
        ]
        self.screen_drag_function.restype = ctypes.c_int
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
            # Older task runners resolve once, then retain the exact target.
            self.bind_window(self.find_window_id())
        return self.window_id

    def window_geometry(self):
        output_x = ctypes.c_double()
        output_y = ctypes.c_double()
        output_width = ctypes.c_double()
        output_height = ctypes.c_double()
        output_pid = ctypes.c_int()
        result = self.geometry_function(
            self._bound_window_id(),
            b"com.netease.my",
            ctypes.byref(output_x),
            ctypes.byref(output_y),
            ctypes.byref(output_width),
            ctypes.byref(output_height),
            ctypes.byref(output_pid),
        )
        if result != 0:
            raise RuntimeError(f"读取原生窗口几何失败，错误码：{result}")
        return (
            output_x.value,
            output_y.value,
            output_width.value,
            output_height.value,
            output_pid.value,
        )

    def click_screen(self, point):
        output_x = ctypes.c_double()
        output_y = ctypes.c_double()
        result = self.screen_click_function(
            self._bound_window_id(),
            b"com.netease.my",
            float(point[0]),
            float(point[1]),
            ctypes.byref(output_x),
            ctypes.byref(output_y),
        )
        if result != 0:
            raise RuntimeError(f"原生屏幕点点击失败，错误码：{result}")
        return output_x.value, output_y.value

    def drag_screen(self, start, end):
        result = self.screen_drag_function(
            self._bound_window_id(),
            b"com.netease.my",
            float(start[0]),
            float(start[1]),
            float(end[0]),
            float(end[1]),
        )
        if result != 0:
            raise RuntimeError(f"原生屏幕点拖动失败，错误码：{result}")

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


class LegacyMacOSWindowBackend:
    """Frame-aware facade over the current Maa/system capture and CGEvent input."""

    def __init__(
        self,
        *,
        target_id,
        window_id,
        controller,
        capture_driver=None,
        native_input=None,
    ):
        self.controller = controller
        self.native_input = native_input or NativeMacOSInput(window_id)
        self.native_input.bind_window(window_id)
        self.capture_driver = capture_driver or WindowCapture(controller, window_id)
        x, y, width, height, pid = self.native_input.window_geometry()
        self.target = WindowTarget(
            target_id=target_id,
            window_id=window_id,
            pid=pid,
            bundle_id="com.netease.my",
            title="梦幻西游",
        )
        self._initial_bounds = (x, y, width, height)
        self.last_frame = None

    def _window_bounds(self):
        x, y, width, height, pid = self.native_input.window_geometry()
        if pid != self.target.pid:
            raise RuntimeError("目标窗口所属进程已变化，已阻止继续操作")
        if width <= 0 or height <= 0:
            raise RuntimeError("目标窗口尺寸无效")
        return x, y, width, height

    @staticmethod
    def _attach_geometry(frame, bounds):
        window_x, window_y, window_width, window_height = bounds
        signature = frame.signature
        if signature.source == "system" and signature.pixel_scale is not None:
            scale = signature.pixel_scale
            pixel_width = signature.content_rect[2]
            pixel_height = signature.content_rect[3]
        else:
            pixel_width, pixel_height = signature.image_size
            scale = pixel_width / window_width
        if scale <= 0:
            raise RuntimeError("无法从截图与窗口尺寸计算有效缩放")
        content_width = pixel_width / scale
        content_height = pixel_height / scale
        if content_width > window_width + 2 or content_height > window_height + 2:
            raise _TransientGeometryMismatch(
                "截图内容尺寸超出目标窗口；"
                f"截图内容={content_width:g}x{content_height:g}，"
                f"目标窗口={window_width:g}x{window_height:g}"
            )
        content_rect = (
            window_x + (window_width - content_width) / 2,
            window_y + window_height - content_height,
            content_width,
            content_height,
        )
        return replace(
            frame,
            window_bounds_points=bounds,
            content_rect_points=content_rect,
            scale_factor=scale,
            capture_method=signature.source,
        )

    def health(self):
        bounds = self._window_bounds()
        return {
            "targetId": self.target.target_id,
            "windowId": self.target.window_id,
            "pid": self.target.pid,
            "windowBoundsPoints": list(bounds),
            "captureMethod": None
            if self.last_frame is None
            else self.last_frame.capture_method,
        }

    def capture(self, guard):
        last_mismatch = None
        for attempt in range(1, GEOMETRY_CAPTURE_ATTEMPTS + 1):
            self.capture_driver.capture(guard)
            frame = self.capture_driver.last_frame
            if frame is None:
                raise RuntimeError("截图后端未返回结构化帧")
            try:
                self.last_frame = self._attach_geometry(frame, self._window_bounds())
                return self.last_frame
            except _TransientGeometryMismatch as exc:
                last_mismatch = exc
                if attempt < GEOMETRY_CAPTURE_ATTEMPTS:
                    guard.wait(GEOMETRY_RETRY_WAIT_SECONDS)
        raise RuntimeError(
            f"{last_mismatch}；连续 {GEOMETRY_CAPTURE_ATTEMPTS} 次不一致，"
            "已阻止建立坐标映射"
        )

    def _frame_at_current_origin(self, frame):
        if self.last_frame is None or frame.frame_id != self.last_frame.frame_id:
            raise RuntimeError("输入帧已过期，已阻止使用旧识别证据")
        current = self._window_bounds()
        if frame.window_bounds_points is None:
            raise RuntimeError("输入帧缺少窗口边界")
        old_x, old_y, old_width, old_height = frame.window_bounds_points
        new_x, new_y, new_width, new_height = current
        if abs(old_width - new_width) > 0.5 or abs(old_height - new_height) > 0.5:
            raise RuntimeError("输入前窗口尺寸已变化，旧帧坐标映射已失效")
        if frame.content_rect_points is None:
            raise RuntimeError("输入帧缺少内容矩形")
        content_x, content_y, content_width, content_height = frame.content_rect_points
        return replace(
            frame,
            window_bounds_points=current,
            content_rect_points=(
                content_x + new_x - old_x,
                content_y + new_y - old_y,
                content_width,
                content_height,
            ),
        )

    def click(self, point, frame, guard):
        screen_point = self.preview_point(point, frame)
        return guard.perform_input(self.native_input.click_screen, screen_point)

    def preview_point(self, point, frame):
        """Map fresh frame evidence without emitting any native input."""
        current_frame = self._frame_at_current_origin(frame)
        return map_frame_point_to_screen(point, current_frame)

    def drag(self, start, end, frame, guard):
        current_frame = self._frame_at_current_origin(frame)
        screen_start = map_frame_point_to_screen(start, current_frame)
        screen_end = map_frame_point_to_screen(end, current_frame)
        guard.perform_input(self.native_input.drag_screen, screen_start, screen_end)


__all__ = [
    "CapturedFrame",
    "LegacyMacOSWindowBackend",
    "MACOS_TITLE_BAR_HEIGHT_POINTS",
    "NativeMacOSInput",
    "SYSTEM_CAPTURE_SIZE",
    "WindowCapture",
    "_window_content_rect",
    "system_window_capture",
    "system_window_capture_frame",
]
