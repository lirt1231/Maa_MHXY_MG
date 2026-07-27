"""Pure scene classification and scratch gestures for 福利签到."""

from __future__ import annotations

import re


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def find_text(texts: list[dict], pattern: str):
    regex = re.compile(pattern)
    return next(
        (item for item in texts if regex.search(normalized_text(item["text"]))),
        None,
    )


def box_in_region(box, image_size, *, min_x=0.0, max_x=1.0, min_y=0.0, max_y=1.0):
    if not box:
        return False
    if image_size is None:
        return True
    x, y, width, height = box
    image_width, image_height = image_size
    if width <= 0 or height <= 0 or image_width <= 0 or image_height <= 0:
        return False
    center_x = (x + width / 2) / image_width
    center_y = (y + height / 2) / image_height
    return min_x <= center_x <= max_x and min_y <= center_y <= max_y


def welfare_entry_box(text_box, image_size):
    """Move the OCR label evidence into the interactive gift icon above it."""
    x, y, width, height = text_box
    icon_height = max(height * 2, round((image_size[1] if image_size else 718) * 0.055))
    return [x, max(0, y - icon_height), width, icon_height]


def scratch_box_from_ocr(texts: list[dict], image_size):
    """Derive the scratch panel only from a complete, spatially valid page context."""
    if image_size is None:
        return None

    title = find_text(texts, r"^梦幻刮刮乐$")
    prize = find_text(texts, r"^今日奖品$")
    prompt = find_text(texts, r"^滑动刮奖$")
    cumulative = find_text(texts, r"^当前累计[:：]?\d+/\d+次$")
    if not all((title, prize, prompt, cumulative)):
        return None

    expected_regions = (
        (title["box"], dict(min_x=0.35, max_x=0.7, min_y=0.08, max_y=0.3)),
        (prize["box"], dict(min_x=0.45, max_x=0.85, min_y=0.12, max_y=0.4)),
        (prompt["box"], dict(min_x=0.45, max_x=0.85, min_y=0.45, max_y=0.7)),
        (
            cumulative["box"],
            dict(min_x=0.35, max_x=0.7, min_y=0.72, max_y=0.95),
        ),
    )
    if any(
        not box_in_region(box, image_size, **region)
        for box, region in expected_regions
    ):
        return None

    image_width, image_height = image_size
    x, y, width, height = prompt["box"]
    horizontal_margin = round(image_width * 0.059)
    top_margin = round(image_height * 0.07)
    bottom_margin = round(image_height * 0.049)
    left = max(0, x - horizontal_margin)
    top = max(0, y - top_margin)
    right = min(image_width, x + width + horizontal_margin)
    bottom = min(image_height, y + height + bottom_margin)
    box = [left, top, right - left, bottom - top]
    if not box_in_region(
        box, image_size, min_x=0.45, max_x=0.85, min_y=0.42, max_y=0.7
    ):
        return None
    return box


def completed_scratch_page(texts: list[dict], image_size) -> bool:
    """Recognize the stable post-scratch page without touching cumulative rewards."""
    if image_size is None or find_text(texts, r"^滑动刮奖$"):
        return False
    title = find_text(texts, r"^梦幻刮刮乐$")
    prize = find_text(texts, r"^今日奖品$")
    hint = find_text(texts, r"^刮奖满一定次数可$")
    reward = find_text(texts, r"^获得累计奖励$")
    cumulative = find_text(texts, r"^当前累计[:：]?\d+/\d+次$")
    if not all((title, prize, hint, reward, cumulative)):
        return False
    return all(
        (
            box_in_region(
                title["box"],
                image_size,
                min_x=0.35,
                max_x=0.7,
                min_y=0.08,
                max_y=0.3,
            ),
            box_in_region(
                prize["box"],
                image_size,
                min_x=0.45,
                max_x=0.85,
                min_y=0.12,
                max_y=0.4,
            ),
            box_in_region(
                hint["box"],
                image_size,
                min_x=0.35,
                max_x=0.7,
                min_y=0.65,
                max_y=0.85,
            ),
            box_in_region(
                reward["box"],
                image_size,
                min_x=0.35,
                max_x=0.7,
                min_y=0.68,
                max_y=0.88,
            ),
            box_in_region(
                cumulative["box"],
                image_size,
                min_x=0.35,
                max_x=0.7,
                min_y=0.72,
                max_y=0.95,
            ),
        )
    )


def classify_welfare_scene(
    texts: list[dict], templates: dict, phase: str, image_size=None
) -> dict:
    welfare_page = bool(templates.get("welfare_header")) or find_text(
        texts, r"累计签到|每日签到|签到奖励"
    )
    completion = find_text(texts, r"今日已签到|签到成功|明日再来")
    if completion and (phase in {"welfare", "scratch", "verify"} or welfare_page):
        return {
            "status": (
                "completed" if phase in {"scratch", "verify"} else "already_completed"
            )
        }

    if completed_scratch_page(texts, image_size):
        return {
            "status": (
                "completed"
                if phase in {"scratch", "verify"}
                else "already_completed"
            )
        }

    confirmation = find_text(texts, r"^(确定|确认|领取)$")
    reward_context = find_text(texts, r"签到奖励|恭喜获得|获得奖励")
    if (
        confirmation
        and reward_context
        and phase in {"scratch", "verify"}
        and box_in_region(
            confirmation["box"], image_size, min_x=0.3, max_x=0.7, min_y=0.55
        )
    ):
        return {"status": "confirm_reward", "box": confirmation["box"]}

    scratch = templates.get("scratch")
    if (
        scratch
        and phase in {"enter", "scratch", "verify"}
        and box_in_region(
            scratch, image_size, min_x=0.2, max_x=0.85, min_y=0.15, max_y=0.8
        )
    ):
        return {"status": "scratch_ready", "box": scratch}

    scratch_ocr_box = scratch_box_from_ocr(texts, image_size)
    if scratch_ocr_box and phase in {"enter", "scratch", "verify"}:
        return {"status": "scratch_ready", "box": scratch_ocr_box}

    open_scratch = find_text(texts, r"打开刮刮乐")
    if (
        open_scratch
        and phase in {"enter", "welfare"}
        and box_in_region(
            open_scratch["box"], image_size, min_x=0.65, min_y=0.65
        )
    ):
        return {"status": "open_scratch", "box": open_scratch["box"]}

    if welfare_page:
        return {"status": "welfare_idle"}

    welfare = find_text(texts, r"^福利$")
    if (
        welfare
        and phase == "enter"
        and box_in_region(
            welfare["box"], image_size, max_x=0.18, min_y=0.08, max_y=0.4
        )
    ):
        return {
            "status": "open_welfare",
            "box": welfare_entry_box(welfare["box"], image_size),
        }
    return {"status": "unknown"}


def scratch_gestures(box) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    x, y, width, height = box
    left = round(x + width * 0.12)
    right = round(x + width * 0.88)
    rows = [round(y + height * fraction) for fraction in (0.22, 0.38, 0.54, 0.70, 0.84)]
    return [
        ((left, row), (right, row)) if index % 2 == 0 else ((right, row), (left, row))
        for index, row in enumerate(rows)
    ]
