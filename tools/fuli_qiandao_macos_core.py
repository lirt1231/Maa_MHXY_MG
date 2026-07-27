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
        return {"status": "open_welfare", "box": welfare["box"]}
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
