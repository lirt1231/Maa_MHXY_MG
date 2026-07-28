"""Shared bounded scrolling for tasks entered from the daily activity list."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

try:
    from .daily_activity_navigation_core import valid_box
    from .daily_schedule_macos_core import normalize_text
except ImportError:  # Direct execution through tools/run_*.py.
    from daily_activity_navigation_core import valid_box
    from daily_schedule_macos_core import normalize_text


Direction = Literal["down", "up"]
TASK_LIST_REGION = (0.20, 0.13, 0.96, 0.84)


class DailyActivityScrollExhausted(RuntimeError):
    """Raised after both list directions have been searched without progress."""


@dataclass(frozen=True)
class DailyActivityScrollStep:
    direction: Direction
    start: tuple[int, int]
    end: tuple[int, int]
    index: int
    reversed: bool
    reason: str


def activity_view_signature(texts, image_size):
    """Describe visible list text and positions while ignoring outside-page noise."""
    if not image_size or len(image_size) != 2:
        return None
    width, height = image_size
    if width <= 0 or height <= 0:
        return None
    min_x, min_y, max_x, max_y = TASK_LIST_REGION
    items = []
    for item in texts or []:
        box = valid_box(item)
        text = normalize_text(item.get("text", ""))
        if box is None or not text:
            continue
        center_x = box[0] + box[2] / 2
        center_y = box[1] + box[3] / 2
        normalized_x = center_x / width
        normalized_y = center_y / height
        if not (min_x <= normalized_x <= max_x and min_y <= normalized_y <= max_y):
            continue
        items.append((text, round(normalized_x, 2), round(normalized_y, 2)))
    return tuple(sorted(items, key=lambda value: (value[2], value[1], value[0]))) or None


class DailyActivityScrollAction:
    """Plan small bidirectional drags and stop when the list search is exhausted."""

    def __init__(
        self,
        *,
        max_down_steps: int = 7,
        max_up_steps: int = 14,
        step_ratio: float = 0.11,
    ):
        if max_down_steps <= 0 or max_up_steps <= 0:
            raise ValueError("活动列表双向滚动步数必须是正整数")
        if not 0.05 <= step_ratio <= 0.18:
            raise ValueError("活动列表单次滚动幅度必须在窗口高度的 5%–18% 之间")
        self.max_steps = {"down": max_down_steps, "up": max_up_steps}
        self.step_ratio = step_ratio
        self.reset()

    def reset(self) -> None:
        self.direction: Direction = "down"
        self.direction_steps = 0
        self.total_steps = 0
        self.reversed = False
        self._seen = {"down": set(), "up": set()}

    def _reverse_or_stop(self, reason: str) -> str:
        if self.direction == "up":
            raise DailyActivityScrollExhausted(
                "日常活动列表已向下和向上双向搜索完毕，仍未找到目标任务"
            )
        self.direction = "up"
        self.direction_steps = 0
        self.reversed = True
        return reason

    def plan(self, texts, image_size) -> DailyActivityScrollStep:
        width, height = image_size
        if width <= 0 or height <= 0:
            raise ValueError("活动列表滚动需要有效的画面尺寸")

        signature = activity_view_signature(texts, image_size)
        reverse_reason = ""
        if signature is not None and signature in self._seen[self.direction]:
            reverse_reason = self._reverse_or_stop("列表画面未继续移动")
        elif self.direction_steps >= self.max_steps[self.direction]:
            reverse_reason = self._reverse_or_stop("已达到当前方向的小步搜索上限")

        if signature is not None and signature in self._seen[self.direction]:
            raise DailyActivityScrollExhausted(
                "日常活动列表已向下和向上双向搜索完毕，仍未找到目标任务"
            )
        if signature is not None:
            self._seen[self.direction].add(signature)

        x = round(width * 0.62)
        distance = max(24, round(height * self.step_ratio))
        if self.direction == "down":
            start = (x, round(height * 0.60))
            end = (x, max(0, start[1] - distance))
        else:
            start = (x, round(height * 0.40))
            end = (x, min(height - 1, start[1] + distance))

        self.direction_steps += 1
        self.total_steps += 1
        return DailyActivityScrollStep(
            direction=self.direction,
            start=start,
            end=end,
            index=self.total_steps,
            reversed=bool(reverse_reason),
            reason=reverse_reason or "继续小步搜索",
        )

    def perform(self, runtime, scene, reason: str) -> DailyActivityScrollStep:
        step = self.plan(scene.texts, scene.image_size)
        direction_label = "向下" if step.direction == "down" else "向上"
        runtime.drag(
            step.start,
            step.end,
            scene,
            f"{reason}（{direction_label}小步搜索）",
        )
        return step


__all__ = [
    "DailyActivityScrollAction",
    "DailyActivityScrollExhausted",
    "DailyActivityScrollStep",
    "activity_view_signature",
]
