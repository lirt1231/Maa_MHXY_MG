"""Platform-neutral answer matching for 三界奇缘 OCR results."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence


_CHAR_NORMALIZATION = str.maketrans(
    {
        "屍": "尸",
        "殭": "僵",
        "戶": "户",
        # PaddleOCR consistently reads the first character of 啸天虎 as 噬
        # on the macOS answer card. This normalization is still followed by
        # the unique-candidate check below, so it cannot select an unrelated
        # option on its own.
        "噬": "啸",
    }
)


@dataclass(frozen=True)
class AnswerTextMatch:
    """A unique, reliable mapping from OCR text to a question-bank answer."""

    candidate: str
    recognized_text: str
    box: tuple[int, int, int, int]
    edit_distance: int


def normalize_answer_text(text: str) -> str:
    """Normalize harmless OCR variants before comparing answer text."""
    normalized = unicodedata.normalize("NFKC", text or "").translate(
        _CHAR_NORMALIZATION
    )
    return re.sub(r"[^\w\u3400-\u9fff]+", "", normalized).replace("_", "")


def _edit_distance_at_most_one(left: str, right: str) -> int | None:
    """Return an edit distance of zero or one, without general fuzzy matching."""
    if left == right:
        return 0
    if abs(len(left) - len(right)) > 1:
        return None
    if len(left) == len(right):
        return 1 if sum(a != b for a, b in zip(left, right)) == 1 else None

    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    shorter_index = 0
    longer_index = 0
    skipped = False
    while shorter_index < len(shorter) and longer_index < len(longer):
        if shorter[shorter_index] == longer[longer_index]:
            shorter_index += 1
            longer_index += 1
            continue
        if skipped:
            return None
        skipped = True
        longer_index += 1
    return 1


def _box_in_roi(box: Sequence[int], roi: Sequence[int]) -> bool:
    if len(box) != 4 or len(roi) != 4:
        return False
    x, y, width, height = box
    roi_x, roi_y, roi_width, roi_height = roi
    center_x = x + width / 2
    center_y = y + height / 2
    return (
        width > 0
        and height > 0
        and roi_x <= center_x <= roi_x + roi_width
        and roi_y <= center_y <= roi_y + roi_height
    )


def find_unique_answer_match(
    ocr_results: Iterable[object],
    candidates: Sequence[str],
    answer_roi: Sequence[int],
) -> AnswerTextMatch | None:
    """Find one exact or one-character OCR match, rejecting any ambiguity.

    One-character substitution recovery requires four visible characters. A
    four-or-more-character answer may also recover from one omitted OCR
    character when at least three characters remain. Exact normalized matches
    remain valid at any length, and all recovered matches must still be unique.
    """
    possible_matches: list[AnswerTextMatch] = []
    for result in ocr_results:
        recognized_text = str(getattr(result, "text", "") or "").strip()
        box = tuple(getattr(result, "box", ()) or ())
        if not recognized_text or not _box_in_roi(box, answer_roi):
            continue
        normalized_result = normalize_answer_text(recognized_text)
        if not normalized_result:
            continue
        for candidate in candidates:
            normalized_candidate = normalize_answer_text(candidate)
            distance = _edit_distance_at_most_one(
                normalized_result, normalized_candidate
            )
            if distance is None:
                continue
            if distance == 1:
                shorter_length = min(
                    len(normalized_result), len(normalized_candidate)
                )
                same_length = len(normalized_result) == len(normalized_candidate)
                minimum_length = 4 if same_length else 3
                if (
                    shorter_length < minimum_length
                    or len(normalized_candidate) < 4
                ):
                    continue
            possible_matches.append(
                AnswerTextMatch(
                    candidate=candidate,
                    recognized_text=recognized_text,
                    box=box,
                    edit_distance=distance,
                )
            )

    if not possible_matches:
        return None
    best_distance = min(match.edit_distance for match in possible_matches)
    best_matches = [
        match for match in possible_matches if match.edit_distance == best_distance
    ]
    return best_matches[0] if len(best_matches) == 1 else None
