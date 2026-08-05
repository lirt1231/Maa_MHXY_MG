"""Platform-neutral capture signatures and runtime calibration gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


def _size(value: Sequence[int], label: str) -> tuple[int, int]:
    if len(value) != 2:
        raise ValueError(f"{label}必须包含宽和高")
    width, height = value
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise ValueError(f"{label}必须是正整数")
    return width, height


@dataclass(frozen=True)
class CaptureSignature:
    """Geometry facts that must remain stable while a task is accepting input."""

    source: str
    image_size: tuple[int, int]
    source_size: tuple[int, int]
    content_rect: tuple[int, int, int, int]
    pixel_scale: float | None = None

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("截图来源不能为空")
        image_width, image_height = _size(self.image_size, "输出画面尺寸")
        source_width, source_height = _size(self.source_size, "原始截图尺寸")
        if len(self.content_rect) != 4:
            raise ValueError("内容裁剪区必须包含 x、y、宽和高")
        x, y, width, height = self.content_rect
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in self.content_rect):
            raise ValueError("内容裁剪区必须使用整数")
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("内容裁剪区无效")
        if x + width > source_width or y + height > source_height:
            raise ValueError("内容裁剪区超出原始截图")
        if self.pixel_scale is not None and self.pixel_scale <= 0:
            raise ValueError("截图像素倍率必须为正数")
        if image_width / image_height <= 0 or width / height <= 0:
            raise ValueError("截图宽高比无效")

    def describe(self) -> str:
        scale = "unknown" if self.pixel_scale is None else f"{self.pixel_scale:g}"
        return (
            f"source={self.source}, image={self.image_size[0]}x{self.image_size[1]}, "
            f"raw={self.source_size[0]}x{self.source_size[1]}, "
            f"content={self.content_rect}, scale={scale}"
        )

    def to_payload(self) -> dict:
        return {
            "captureSource": self.source,
            "imageSize": list(self.image_size),
            "sourceSize": list(self.source_size),
            "contentRect": list(self.content_rect),
            "pixelScale": self.pixel_scale,
        }

    def calibration_key(self) -> tuple:
        """Return only geometry that affects normalized frame-to-window mapping."""
        return (
            self.source,
            self.image_size,
            self.content_rect[2:],
            self.pixel_scale,
        )

    def is_compatible_with(self, other: "CaptureSignature") -> bool:
        return self.calibration_key() == other.calibration_key()


class CaptureStabilityGate:
    """Reject input after capture geometry changes within one runtime session."""

    def __init__(self) -> None:
        self.baseline: CaptureSignature | None = None

    def observe(self, signature: CaptureSignature) -> CaptureSignature:
        if self.baseline is None:
            self.baseline = signature
            return signature
        if not signature.is_compatible_with(self.baseline):
            raise RuntimeError(
                "截图来源或窗口内容尺寸已变化，旧标定已失效；"
                f"原签名[{self.baseline.describe()}]，当前签名[{signature.describe()}]"
            )
        return signature

    def validate(self, signature: CaptureSignature | None) -> CaptureSignature:
        if signature is None:
            raise RuntimeError("当前场景缺少截图标定签名，已阻止输入")
        if self.baseline is None:
            raise RuntimeError("尚未建立截图标定，已阻止输入")
        if not signature.is_compatible_with(self.baseline):
            raise RuntimeError("当前场景来自已失效的截图标定，已阻止输入")
        return signature


__all__ = ["CaptureSignature", "CaptureStabilityGate"]
