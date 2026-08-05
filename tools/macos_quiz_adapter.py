"""Thin macOS adapter for platform-neutral quiz workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class MacOSQuizScene:
    image_size: tuple[int, int]
    texts: list[Mapping[str, Any]] = field(default_factory=list)
    templates: Mapping[str, Any] = field(default_factory=dict)


class MacOSQuizInputAdapter:
    """Expose abstract point input while retaining the existing macOS driver."""

    def __init__(
        self,
        point_click: Callable[
            [tuple[float, float], tuple[int, int], str], Any
        ],
        point_drag: Callable[
            [tuple[float, float], tuple[float, float], tuple[int, int], str], Any
        ]
        | None = None,
        text_input: Callable[[str, tuple[int, int], str], Any] | None = None,
    ):
        self._point_click = point_click
        self._point_drag = point_drag
        self._text_input = text_input

    def read_scene(self, templates=None):
        raise RuntimeError("答题输入适配器不负责场景识别")

    def click(self, point, scene, reason: str):
        return self._point_click(tuple(point), tuple(scene.image_size), reason)

    def click_box(self, box, scene, reason: str):
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise RuntimeError(f"{reason}缺少有效识别区域")
        x, y, width, height = box
        return self.click((x + width / 2, y + height / 2), scene, reason)

    def drag(self, start, end, scene, reason: str):
        if self._point_drag is None:
            raise RuntimeError("当前 macOS 输入适配器不支持拖动动作")
        return self._point_drag(
            tuple(start),
            tuple(end),
            tuple(scene.image_size),
            reason,
        )

    def input_text(self, text: str, scene, reason: str):
        if self._text_input is None:
            raise RuntimeError("当前平台输入适配器不支持文本输入")
        return self._text_input(text, tuple(scene.image_size), reason)


__all__ = ["MacOSQuizInputAdapter", "MacOSQuizScene"]
