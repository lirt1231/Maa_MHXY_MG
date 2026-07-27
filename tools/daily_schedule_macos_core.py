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
ROW_STATE_RE = re.compile(
    r"已完成|全部完成|未开启|未解锁|等级不足|不可参与|已锁定|暂未开放|"
    r"明日开启|前往|参加|去完成|可参与|进行中|未完成|领取任务|"
    r"可领取|领取奖励|已领取|领取完毕|奖励已领|^领取$"
)

# The activity panel is authored at several resolutions. Coordinates are kept
# normalized so recognition remains read-only and independent of window scale.
PAGE_TITLE_REGION = (0.35, 0.0, 0.75, 0.18)
DAILY_TAB_REGION = (0.04, 0.08, 0.36, 0.34)
TASK_LIST_REGION = (0.20, 0.13, 0.96, 0.84)
CHAT_PREFIX_RE = re.compile(r"^(世界|当前|帮派|队伍|系统|传闻|综合)[\[【:：]")
IGNORED_NAMES = {
    "日程",
    "日程中心",
    "日常活动",
    "活动",
    "任务",
    "今日任务",
    "活跃度",
    "今日活跃",
    "活跃",
    "次数",
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


def _box_center_in_region(box, image_size, region) -> bool:
    if box is None or image_size is None or len(image_size) != 2:
        return False
    image_width, image_height = image_size
    if image_width <= 0 or image_height <= 0:
        return False
    x, y, width, height = box
    center_x = (x + width / 2) / image_width
    center_y = (y + height / 2) / image_height
    min_x, min_y, max_x, max_y = region
    return min_x <= center_x <= max_x and min_y <= center_y <= max_y


def _find_text_in_region(items, pattern, image_size, region) -> dict | None:
    regex = re.compile(pattern)
    for item in items:
        if not regex.fullmatch(normalize_text(item.get("text", ""))):
            continue
        if _box_center_in_region(_valid_box(item), image_size, region):
            return item
    return None


def daily_schedule_page_evidence(
    items: list[dict],
    image_size,
    templates: dict | None = None,
) -> dict | None:
    """Return explicit activity-page evidence or ``None`` for every other scene."""
    templates = templates or {}
    template_box = templates.get("activity_header")
    template_title = _box_center_in_region(
        template_box, image_size, PAGE_TITLE_REGION
    )
    ocr_title = _find_text_in_region(
        items, r"活动", image_size, PAGE_TITLE_REGION
    )
    daily_tab = _find_text_in_region(
        items, r"日常活动", image_size, DAILY_TAB_REGION
    )
    if not (template_title or ocr_title) or daily_tab is None:
        return None
    return {
        "page_title": "template" if template_title else "ocr",
        "daily_tab_detected": True,
        "task_roi": list(TASK_LIST_REGION),
    }


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


def _normalized_center(item, image_size) -> tuple[float, float] | None:
    box = _valid_box(item)
    if box is None or image_size is None or len(image_size) != 2:
        return None
    image_width, image_height = image_size
    if image_width <= 0 or image_height <= 0:
        return None
    x, y, width, height = box
    return (x + width / 2) / image_width, (y + height / 2) / image_height


def _is_task_name_anchor(item, image_size) -> bool:
    text = normalize_text(item.get("text", ""))
    return bool(
        text
        and text not in IGNORED_NAMES
        and len(text) <= 24
        and not CHAT_PREFIX_RE.search(text)
        and not PROGRESS_RE.search(text)
        and not ROW_STATE_RE.fullmatch(text)
        and _box_center_in_region(_valid_box(item), image_size, TASK_LIST_REGION)
    )


def _card_signals(anchor, items, image_size) -> list[dict]:
    anchor_center = _normalized_center(anchor, image_size)
    if anchor_center is None:
        return []
    anchor_x, anchor_y = anchor_center
    signals = []
    for item in items:
        if item is anchor:
            continue
        text = normalize_text(item.get("text", ""))
        center = _normalized_center(item, image_size)
        if center is None:
            continue
        center_x, center_y = center
        if not (
            anchor_x - 0.03 <= center_x <= anchor_x + 0.30
            and anchor_y - 0.035 <= center_y <= anchor_y + 0.14
        ):
            continue
        progress_column = PROGRESS_RE.search(text) and center_x <= anchor_x + 0.19
        state_column = ROW_STATE_RE.fullmatch(text) and center_x >= anchor_x + 0.08
        if progress_column or state_column:
            signals.append(item)
    return signals


def parse_daily_schedule(items: list[dict], image_size=None, templates=None) -> list[dict]:
    if daily_schedule_page_evidence(items, image_size, templates) is None:
        return []
    roi_items = [
        item
        for item in items
        if _box_center_in_region(_valid_box(item), image_size, TASK_LIST_REGION)
    ]
    tasks = []
    seen = set()
    anchors = [
        item for item in roi_items if _is_task_name_anchor(item, image_size)
    ]
    anchors.sort(key=lambda item: (_valid_box(item)[1], _valid_box(item)[0]))
    for anchor in anchors:
        signals = _card_signals(anchor, roi_items, image_size)
        if not signals:
            continue
        # A card must have an independently recognized progress or state
        # column. One OCR sentence containing chat and a task keyword cannot
        # become a task by itself.
        task = parse_daily_task_row([anchor, *signals])
        if task is None or task["name"] in seen:
            continue
        seen.add(task["name"])
        tasks.append(task)
    return tasks


__all__ = [
    "daily_schedule_page_evidence",
    "group_ocr_rows",
    "normalize_text",
    "parse_daily_schedule",
    "parse_daily_task_row",
]
