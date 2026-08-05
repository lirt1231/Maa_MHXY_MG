"""Contracts and geometry for a frame-aware macOS window backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence
from uuid import uuid4

try:
    from .automation_calibration import CaptureSignature
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_calibration import CaptureSignature


Point = tuple[float, float]
Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class WindowTarget:
    target_id: str
    window_id: int
    pid: int
    bundle_id: str
    title: str

    def __post_init__(self) -> None:
        if not self.target_id or not self.bundle_id or not self.title:
            raise ValueError("窗口目标标识不能为空")
        if self.window_id <= 0 or self.pid <= 0:
            raise ValueError("窗口编号和进程编号必须为正整数")


@dataclass(frozen=True)
class CapturedFrame:
    image: Any = field(compare=False, repr=False)
    signature: CaptureSignature
    window_bounds_points: Rect | None = None
    content_rect_points: Rect | None = None
    scale_factor: float | None = None
    capture_method: str = "unknown"
    frame_id: str = field(default_factory=lambda: uuid4().hex)
    captured_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def pixel_size(self) -> tuple[int, int]:
        return self.signature.image_size

    def require_geometry(self) -> tuple[Rect, float]:
        if self.content_rect_points is None or self.scale_factor is None:
            raise RuntimeError("当前帧缺少内容矩形，已阻止坐标映射")
        if self.scale_factor <= 0:
            raise RuntimeError("当前帧缩放因子无效，已阻止坐标映射")
        return self.content_rect_points, self.scale_factor

    def geometry_payload(self) -> dict:
        return {
            "frameId": self.frame_id,
            "captureMethod": self.capture_method,
            "windowBoundsPoints": None
            if self.window_bounds_points is None
            else list(self.window_bounds_points),
            "contentRectPoints": None
            if self.content_rect_points is None
            else list(self.content_rect_points),
            "scaleFactor": self.scale_factor,
        }


def map_frame_point_to_screen(point: Sequence[float], frame: CapturedFrame) -> Point:
    if len(point) != 2:
        raise ValueError("帧坐标必须包含 x 和 y")
    x, y = point
    width, height = frame.pixel_size
    if not all(isinstance(value, (int, float)) for value in (x, y)):
        raise ValueError("帧坐标必须是数值")
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError("帧坐标超出当前画面")
    content_rect, _scale = frame.require_geometry()
    content_x, content_y, content_width, content_height = content_rect
    return (
        content_x + x / width * content_width,
        content_y + y / height * content_height,
    )


class MacOSWindowBackend(Protocol):
    target: WindowTarget

    def health(self) -> dict: ...

    def capture(self, guard) -> CapturedFrame: ...

    def preview_point(self, point, frame: CapturedFrame) -> Point: ...

    def click(self, point, frame: CapturedFrame, guard) -> Point: ...

    def drag(self, start, end, frame: CapturedFrame, guard) -> None: ...


__all__ = [
    "CapturedFrame",
    "MacOSWindowBackend",
    "Point",
    "Rect",
    "WindowTarget",
    "map_frame_point_to_screen",
]
