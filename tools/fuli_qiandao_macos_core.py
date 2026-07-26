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


def classify_welfare_scene(texts: list[dict], templates: dict, phase: str) -> dict:
    completion = find_text(texts, r"今日已签到|签到成功|明日再来")
    if completion and phase in {"welfare", "scratch", "verify"}:
        return {
            "status": "already_completed" if phase == "welfare" else "completed"
        }

    confirmation = find_text(texts, r"^(确定|确认|领取)$")
    reward_context = find_text(texts, r"签到奖励|恭喜获得|获得奖励")
    if confirmation and reward_context and phase in {"scratch", "verify"}:
        return {"status": "confirm_reward", "box": confirmation["box"]}

    scratch = templates.get("scratch")
    if scratch:
        return {"status": "scratch_ready", "box": scratch}

    open_scratch = find_text(texts, r"打开刮刮乐")
    if open_scratch:
        return {"status": "open_scratch", "box": open_scratch["box"]}

    welfare_page = bool(templates.get("welfare_header")) or find_text(
        texts, r"累计签到|每日签到|签到奖励"
    )
    if welfare_page:
        return {"status": "welfare_idle"}

    welfare = find_text(texts, r"^福利$")
    if welfare:
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
