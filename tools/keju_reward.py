"""Read-only recognition for the 科举会试 settlement reward."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_NUMBER_RE = re.compile(r"(?<!\d)(\d[\d,，.]*)(?!\d)")
_HUI_SHI_TITLE_RE = re.compile(r"(?:科举)?会试(?:成绩|完成|结束|奖励)?")
_GOLD_RE = re.compile(r"(?:获得|奖励|本次)?金币")
_CURRENT_REWARD_RE = re.compile(r"(?:本次|本场)(?:会)?(?:获得)?奖金")
_EXPERIENCE_RE = re.compile(r"(?:获得|奖励|本次)?(?:经验|经验值)")
_SILVER_RE = re.compile(r"(?:获得|奖励|本次)?银币")


def _normalize(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).translate(
        str.maketrans("０１２３４５６７８９", "0123456789")
    )


def _box(item: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    value = item.get("box")
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if not all(isinstance(part, (int, float)) for part in value):
        return None
    x, y, width, height = (float(part) for part in value)
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _number(value: str) -> int | None:
    match = _NUMBER_RE.search(value)
    if match is None:
        return None
    try:
        return int(re.sub(r"[,，.]", "", match.group(1)))
    except ValueError:
        return None


def _payload_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {"text": str(item.get("text", "")), "box": list(_box(item) or ())}


def parse_huishi_settlement_reward(
    texts: Sequence[Mapping[str, Any]], image_size: tuple[int, int]
) -> dict[str, Any]:
    """Read explicitly labelled rewards only from the current 会试 settlement.

    OCR may split ``金币 5000`` into adjacent boxes, so a numeric value is
    accepted either from the gold label itself or a nearby box.  A central
    会试 title is independently required to prevent the permanent wallet UI
    from being mistaken for a reward.
    """
    width, height = image_size
    if width <= 0 or height <= 0:
        return {"status": "unavailable", "reason": "invalid_image_size"}

    normalized: list[tuple[Mapping[str, Any], str, tuple[float, float, float, float]]] = []
    for item in texts:
        if not isinstance(item, Mapping):
            continue
        box = _box(item)
        if box is None:
            continue
        normalized.append((item, _normalize(item.get("text")), box))

    title = next(
        (
            item
            for item, text, box in normalized
            if _HUI_SHI_TITLE_RE.search(text)
            and width * 0.18 <= box[0] + box[2] / 2 <= width * 0.82
            and height * 0.06 <= box[1] + box[3] / 2 <= height * 0.72
        ),
        None,
    )
    if title is None:
        return {"status": "unavailable", "reason": "huishi_settlement_identity_missing"}

    def find_amount(patterns):
        labels = [entry for entry in normalized if any(pattern.search(entry[1]) for pattern in patterns)]
        for label, label_text, label_box in labels:
            label_center_y = label_box[1] + label_box[3] / 2
            if not height * 0.30 <= label_center_y <= height * 0.90:
                continue
            inline = _number(label_text)
            if inline is not None:
                return inline, label, label
            candidates = []
            label_center_x = label_box[0] + label_box[2] / 2
            for amount_item, amount_text, amount_box in normalized:
                if amount_item is not label and any(
                    pattern.search(amount_text)
                    for pattern in (_EXPERIENCE_RE, _SILVER_RE, _CURRENT_REWARD_RE, _GOLD_RE)
                ):
                    continue
                amount = _number(amount_text)
                if amount is None:
                    continue
                horizontal = abs(amount_box[0] + amount_box[2] / 2 - label_center_x)
                vertical = abs(amount_box[1] + amount_box[3] / 2 - label_center_y)
                if horizontal <= width * 0.28 and vertical <= height * 0.10:
                    candidates.append((vertical * 2 + horizontal, amount_item, amount))
            if candidates:
                _, amount_item, amount = min(candidates, key=lambda candidate: candidate[0])
                return amount, label, amount_item
        return None

    # The completion page may call gold "本次会获得奖金".  Unlike the two
    # required lower reward rows it remains optional for compatibility.
    found = {
        "experience": find_amount((_EXPERIENCE_RE,)),
        "silver": find_amount((_SILVER_RE,)),
        "gold": find_amount((_CURRENT_REWARD_RE, _GOLD_RE)),
    }
    if found["experience"] is None or found["silver"] is None:
        return {"status": "unavailable", "reason": "settlement_experience_or_silver_missing"}
    result = {
        "status": "confirmed",
        "experience": found["experience"][0],
        "silver": found["silver"][0],
        "evidence": {"sourcePage": "huishi_settlement", "title": _payload_item(title)},
    }
    for name, value in found.items():
        if value is None:
            continue
        amount, label, amount_item = value
        result["evidence"][f"{name}Label"] = _payload_item(label)
        result["evidence"][name] = _payload_item(amount_item)
        if name == "gold":
            result[name] = amount
    return result


__all__ = ["parse_huishi_settlement_reward"]
