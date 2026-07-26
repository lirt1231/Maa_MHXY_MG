"""Pure OCR row parsing for the macOS daily schedule page."""

from __future__ import annotations

import re
import unicodedata


PROGRESS_RE = re.compile(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)")
COMPLETED_RE = re.compile(r"已完成|全部完成")
LOCKED_RE = re.compile(r"未开启|未解锁|等级不足|不可参与|已锁定|暂未开放|明日开启")
AVAILABLE_RE = re.compile(r"前往|参加|去完成|可参与|进行中|未完成|领取任务")
CLAIMED_RE = re.compile(r"已领取|领取完毕|奖励已领")
CLAIMABLE_RE = re.compile(r"可领取|领取奖励|^领取$")
TASK_HINT_RE = re.compile(
    r"任务|捉鬼|师门|宝图|运镖|封妖|秘境|副本|科举|三界|帮派|签到|鉴赏|竞技|活跃"
)
IGNORED_NAMES = {
    "日程",
    "日程中心",
    "日常活动",
    "活动",
    "任务",
    "今日任务",
    "活跃度",
    "今日活跃",
    "奖励",
}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def _valid_box(item: dict) -> tuple[float, float, float, float] | None:
    box = item.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if not all(isinstance(value, (int, float)) for value in box):
        return None
    x, y, width, height = box
    if width <= 0 or height <= 0:
        return None
    return float(x), float(y), float(width), float(height)


def group_ocr_rows(items: list[dict]) -> list[list[dict]]:
    """Group OCR items by overlapping text baselines, preserving left-to-right order."""
    positioned = []
    for item in items:
        box = _valid_box(item)
        text = normalize_text(item.get("text", ""))
        if box is None or not text:
            continue
        x, y, width, height = box
        positioned.append((y + height / 2, x, height, item))
    positioned.sort(key=lambda entry: (entry[0], entry[1]))

    rows: list[dict] = []
    for center_y, x, height, item in positioned:
        best = None
        best_distance = None
        for row in rows:
            distance = abs(center_y - row["center_y"])
            tolerance = max(6.0, min(height, row["height"]) * 0.55)
            if distance <= tolerance and (best_distance is None or distance < best_distance):
                best = row
                best_distance = distance
        if best is None:
            rows.append({"center_y": center_y, "height": height, "items": [(x, item)]})
            continue
        count = len(best["items"])
        best["center_y"] = (best["center_y"] * count + center_y) / (count + 1)
        best["height"] = (best["height"] * count + height) / (count + 1)
        best["items"].append((x, item))

    rows.sort(key=lambda row: row["center_y"])
    return [
        [item for _, item in sorted(row["items"], key=lambda entry: entry[0])]
        for row in rows
    ]


def _task_name(parts: list[str], has_progress: bool) -> str | None:
    for part in parts:
        candidate = PROGRESS_RE.sub("", part)
        candidate = re.sub(
            r"已完成|全部完成|未开启|未解锁|等级不足|不可参与|已锁定|暂未开放|"
            r"明日开启|前往|参加|去完成|可参与|进行中|未完成|领取任务|"
            r"可领取|领取奖励|已领取|领取完毕|奖励已领|^领取$",
            "",
            candidate,
        ).strip("-:：·|")
        if not candidate or candidate in IGNORED_NAMES:
            continue
        if len(candidate) > 24:
            continue
        if has_progress or TASK_HINT_RE.search(candidate):
            return candidate
    return None


def parse_daily_task_row(row: list[dict]) -> dict | None:
    parts = [normalize_text(item.get("text", "")) for item in row]
    parts = [part for part in parts if part]
    if not parts:
        return None
    text = "|".join(parts)
    progress = PROGRESS_RE.search(text)
    completed = int(progress.group(1)) if progress else None
    target = int(progress.group(2)) if progress else None
    valid_progress = bool(progress and target and completed is not None and completed <= target)
    name = _task_name(parts, progress is not None)
    if name is None:
        return None

    if COMPLETED_RE.search(text) or (valid_progress and completed == target):
        status = "completed"
    elif LOCKED_RE.search(text):
        status = "locked"
    elif AVAILABLE_RE.search(text) or (valid_progress and completed < target):
        status = "available"
    else:
        status = "unknown"

    if CLAIMED_RE.search(text):
        reward_status = "claimed"
    elif CLAIMABLE_RE.search(text):
        reward_status = "claimable"
    else:
        reward_status = "unknown"

    return {
        "name": name,
        "completed": completed,
        "target": target,
        "status": status,
        "reward_status": reward_status,
    }


def parse_daily_schedule(items: list[dict]) -> list[dict]:
    tasks = []
    seen = set()
    for row in group_ocr_rows(items):
        task = parse_daily_task_row(row)
        if task is None or task["name"] in seen:
            continue
        seen.add(task["name"])
        tasks.append(task)
    return tasks


__all__ = [
    "group_ocr_rows",
    "normalize_text",
    "parse_daily_schedule",
    "parse_daily_task_row",
]
