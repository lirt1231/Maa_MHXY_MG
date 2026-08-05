"""Platform-neutral geometry helpers for recognized UI evidence."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def relative_point(
    ratio: Sequence[float],
    image_size: Sequence[int],
) -> tuple[int, int]:
    if len(ratio) != 2 or len(image_size) != 2:
        raise ValueError("相对坐标和画面尺寸必须各包含两个值")
    ratio_x, ratio_y = ratio
    width, height = image_size
    if not all(isinstance(value, (int, float)) for value in (ratio_x, ratio_y)):
        raise ValueError("相对坐标必须是数值")
    if not all(isinstance(value, int) for value in (width, height)):
        raise ValueError("画面尺寸必须是整数")
    if not 0 <= ratio_x <= 1 or not 0 <= ratio_y <= 1:
        raise ValueError("相对坐标必须在 0-1 之间")
    if width <= 0 or height <= 0:
        raise ValueError("画面尺寸必须是正整数")
    return (
        min(width - 1, round(width * ratio_x)),
        min(height - 1, round(height * ratio_y)),
    )


def valid_box(item: Mapping[str, Any] | None) -> list[float] | None:
    box = item.get("box") if isinstance(item, Mapping) else None
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if not all(isinstance(value, (int, float)) for value in box):
        return None
    if box[2] <= 0 or box[3] <= 0:
        return None
    return list(box)


def center(item: Mapping[str, Any]) -> list[int]:
    box = valid_box(item)
    if box is None:
        raise ValueError("中心点计算需要有效的识别区域")
    x, y, width, height = box
    return [round(x + width / 2), round(y + height / 2)]


def in_region(
    item: Mapping[str, Any],
    image_size: Sequence[int] | None,
    *,
    min_x: float = 0,
    max_x: float = 1,
    min_y: float = 0,
    max_y: float = 1,
) -> bool:
    box = valid_box(item)
    if box is None or not image_size or len(image_size) != 2:
        return False
    image_width, image_height = image_size
    if image_width <= 0 or image_height <= 0:
        return False
    x, y = center(item)
    return (
        min_x <= x / image_width <= max_x
        and min_y <= y / image_height <= max_y
    )


__all__ = [
    "center",
    "in_region",
    "relative_point",
    "valid_box",
]
