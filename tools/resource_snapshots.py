"""Platform-neutral resource snapshots and per-task net-change calculation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence


RESOURCE_FIELDS = frozenset(
    {"level", "experience", "experience_required", "gold", "silver"}
)
YIELD_FIELDS = frozenset({"experience", "gold", "silver"})
SNAPSHOT_PHASES = frozenset({"before", "after", "reward", "standalone"})
SNAPSHOT_STATUSES = frozenset({"success", "partial", "failed"})
FIELD_STATUSES = frozenset({"captured", "missing", "invalid", "corrected"})
ATTRIBUTION_STATUSES = frozenset(
    {
        "reliable",
        "interrupted",
        "cross_level_unknown",
        "partial",
        "unavailable",
        "not_run",
    }
)
TASK_STATUSES = frozenset(
    {
        "completed",
        "already_completed",
        "limit_reached",
        "safe_stopped",
        "cancelled",
        "failed",
    }
)
YIELD_SOURCES = frozenset({"resource_delta", "settlement_reward"})
TARGET_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def _optional_nonnegative_int(value: object, label: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValueError(f"{label}必须是非负整数或空值")


def _parse_timestamp(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}不能为空")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label}不是有效时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label}必须包含时区")
    return parsed


@dataclass(frozen=True)
class ResourceValues:
    level: int | None = None
    experience: int | None = None
    experience_required: int | None = None
    gold: int | None = None
    silver: int | None = None

    def __post_init__(self) -> None:
        for name, value in self.as_dict().items():
            _optional_nonnegative_int(value, name)

    def as_dict(self) -> dict[str, int | None]:
        return {
            "level": self.level,
            "experience": self.experience,
            "experience_required": self.experience_required,
            "gold": self.gold,
            "silver": self.silver,
        }


@dataclass(frozen=True)
class ResourceFieldEvidence:
    status: str
    source_page: str
    raw_text: str | None = None
    confidence: float | None = None
    box: tuple[float, float, float, float] | None = None
    frame_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in FIELD_STATUSES:
            raise ValueError("资源字段证据状态无效")
        if not isinstance(self.source_page, str) or not self.source_page.strip():
            raise ValueError("资源字段证据来源页面不能为空")
        if self.raw_text is not None and not isinstance(self.raw_text, str):
            raise ValueError("资源字段 OCR 原文无效")
        if self.confidence is not None and (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("资源字段证据置信度无效")
        if self.box is not None and (
            not isinstance(self.box, (list, tuple))
            or len(self.box) != 4
            or not all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(item)
                for item in self.box
            )
            or self.box[2] <= 0
            or self.box[3] <= 0
        ):
            raise ValueError("资源字段证据坐标无效")
        if self.frame_id is not None and (
            not isinstance(self.frame_id, str) or not self.frame_id.strip()
        ):
            raise ValueError("资源字段证据帧标识无效")

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sourcePage": self.source_page,
            "rawText": self.raw_text,
            "confidence": self.confidence,
            "box": None if self.box is None else list(self.box),
            "frameId": self.frame_id,
        }


@dataclass(frozen=True)
class ResourceSnapshot:
    target_id: str
    phase: str
    captured_at: str
    values: ResourceValues
    evidence: Mapping[str, ResourceFieldEvidence] = field(default_factory=dict)
    status: str | None = None
    character_id: str | None = None
    snapshot_id: int | None = None
    error: str | None = None
    captured_fields: frozenset[str] = RESOURCE_FIELDS

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or not TARGET_ID_RE.fullmatch(
            self.target_id
        ):
            raise ValueError("资源快照目标标识无效")
        if self.phase not in SNAPSHOT_PHASES:
            raise ValueError("资源快照阶段无效")
        _parse_timestamp(self.captured_at, "资源快照采集时间")
        if not isinstance(self.values, ResourceValues):
            raise ValueError("资源快照数值契约无效")
        if self.character_id is not None and (
            not isinstance(self.character_id, str) or not self.character_id.strip()
        ):
            raise ValueError("资源快照角色标识无效")
        if self.snapshot_id is not None and (
            isinstance(self.snapshot_id, bool)
            or not isinstance(self.snapshot_id, int)
            or self.snapshot_id <= 0
        ):
            raise ValueError("资源快照编号无效")
        if self.error is not None and not isinstance(self.error, str):
            raise ValueError("资源快照错误信息无效")
        if not isinstance(self.captured_fields, frozenset) or not self.captured_fields or (
            self.captured_fields - RESOURCE_FIELDS
        ):
            raise ValueError("资源快照采集范围无效")

        evidence = dict(self.evidence)
        if set(evidence) - RESOURCE_FIELDS or not all(
            isinstance(item, ResourceFieldEvidence) for item in evidence.values()
        ):
            raise ValueError("资源快照字段证据无效")
        values = self.values.as_dict()
        for name, item in evidence.items():
            if item.status in {"captured", "corrected"} and values[name] is None:
                raise ValueError("已采集的资源字段不能缺少数值")
            if item.status in {"missing", "invalid"} and values[name] is not None:
                raise ValueError("缺失或无效的资源字段不能携带数值")
        object.__setattr__(self, "evidence", evidence)

        present = sum(values[name] is not None for name in self.captured_fields)
        expected_status = (
            "success" if present == len(self.captured_fields) else "partial" if present else "failed"
        )
        if self.status is not None and self.status not in SNAPSHOT_STATUSES:
            raise ValueError("资源快照状态无效")
        if self.status is not None and self.status != expected_status:
            raise ValueError("资源快照状态与字段完整度不一致")
        object.__setattr__(self, "status", expected_status)

    @property
    def captured_datetime(self) -> datetime:
        return _parse_timestamp(self.captured_at, "资源快照采集时间")

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.snapshot_id,
            "targetId": self.target_id,
            "characterId": self.character_id,
            "phase": self.phase,
            "capturedAt": self.captured_at,
            "status": self.status,
            "values": {
                "level": self.values.level,
                "experience": self.values.experience,
                "experienceRequired": self.values.experience_required,
                "gold": self.values.gold,
                "silver": self.values.silver,
            },
            "evidence": {
                name: item.to_payload() for name, item in self.evidence.items()
            },
            "error": self.error,
            "capturedFields": sorted(self.captured_fields),
        }


@dataclass(frozen=True)
class ResourceYield:
    task_status: str
    attribution_status: str
    experience_delta: int | None = None
    gold_delta: int | None = None
    silver_delta: int | None = None
    reasons: tuple[str, ...] = ()
    before_snapshot_id: int | None = None
    after_snapshot_id: int | None = None
    tracked_fields: frozenset[str] = YIELD_FIELDS
    measurement_source: str = "resource_delta"

    def __post_init__(self) -> None:
        if self.task_status not in TASK_STATUSES:
            raise ValueError("任务资源收益的任务状态无效")
        if self.attribution_status not in ATTRIBUTION_STATUSES:
            raise ValueError("任务资源收益归因状态无效")
        if self.measurement_source not in YIELD_SOURCES:
            raise ValueError("任务资源收益来源无效")
        if not isinstance(self.tracked_fields, frozenset) or not self.tracked_fields or (
            self.tracked_fields - YIELD_FIELDS
        ):
            raise ValueError("任务资源收益统计范围无效")
        for name, value in {
            "experience_delta": self.experience_delta,
            "gold_delta": self.gold_delta,
            "silver_delta": self.silver_delta,
        }.items():
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise ValueError(f"{name} 必须是整数或空值")
        for label, value in {
            "before_snapshot_id": self.before_snapshot_id,
            "after_snapshot_id": self.after_snapshot_id,
        }.items():
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{label} 必须是正整数或空值")
        if not isinstance(self.reasons, tuple) or any(
            not isinstance(reason, str) or not reason.strip() for reason in self.reasons
        ):
            raise ValueError("任务资源收益原因无效")
        deltas = {
            "experience": self.experience_delta,
            "gold": self.gold_delta,
            "silver": self.silver_delta,
        }
        if self.attribution_status == "reliable" and any(
            deltas[name] is None for name in self.tracked_fields
        ):
            raise ValueError("可靠的任务资源收益必须包含统计范围内的全部差值")
        if self.attribution_status in {"unavailable", "not_run"} and any(
            value is not None for value in deltas.values()
        ):
            raise ValueError("未采集或未执行的任务不能携带资源差值")
        if self.measurement_source == "settlement_reward":
            if self.before_snapshot_id is not None or self.after_snapshot_id is None:
                raise ValueError("结算奖励必须只关联结算快照")
            if self.tracked_fields != frozenset({"gold"}):
                raise ValueError("结算奖励只能记录金币")
            if self.experience_delta is not None or self.silver_delta is not None:
                raise ValueError("结算奖励不能记录经验或银币")
            if self.attribution_status == "reliable" and self.gold_delta is None:
                raise ValueError("可靠的结算奖励必须包含金币")

    def to_payload(self) -> dict[str, Any]:
        return {
            "taskStatus": self.task_status,
            "attributionStatus": self.attribution_status,
            "beforeSnapshotId": self.before_snapshot_id,
            "afterSnapshotId": self.after_snapshot_id,
            "experienceDelta": self.experience_delta,
            "goldDelta": self.gold_delta,
            "silverDelta": self.silver_delta,
            "reasons": list(self.reasons),
            "trackedFields": sorted(self.tracked_fields),
            "measurementSource": self.measurement_source,
        }


def _difference(before: int | None, after: int | None) -> int | None:
    if before is None or after is None:
        return None
    return after - before


def _experience_difference(
    before: ResourceValues,
    after: ResourceValues,
    level_requirements: Mapping[int, int],
) -> tuple[int | None, str | None]:
    if (
        before.level is None
        or after.level is None
        or before.experience is None
        or after.experience is None
    ):
        return None, "experience_fields_missing"
    if after.level < before.level:
        return None, "level_regressed"
    if after.level == before.level:
        return after.experience - before.experience, None
    if before.experience_required is None:
        return None, "level_requirement_missing"

    spent = before.experience_required
    for level in range(before.level + 1, after.level):
        required = level_requirements.get(level)
        if (
            required is None
            or isinstance(required, bool)
            or not isinstance(required, int)
            or required < 0
        ):
            return None, f"level_requirement_missing:{level}"
        spent += required
    delta = after.experience + spent - before.experience
    if delta < 0:
        return None, "cross_level_delta_negative"
    return delta, None


def calculate_resource_yield(
    before: ResourceSnapshot,
    after: ResourceSnapshot | None,
    *,
    task_status: str,
    level_requirements: Mapping[int, int] | None = None,
    interference_reasons: Sequence[str] = (),
    tracked_fields: frozenset[str] = YIELD_FIELDS,
) -> ResourceYield:
    """Calculate auditable net changes without claiming gross task rewards."""
    if not isinstance(before, ResourceSnapshot):
        raise ValueError("任务收益缺少有效的执行前快照")
    if task_status not in TASK_STATUSES:
        raise ValueError("任务资源收益的任务状态无效")
    if level_requirements is not None and not isinstance(level_requirements, Mapping):
        raise ValueError("等级经验需求表无效")
    if isinstance(interference_reasons, (str, bytes)) or not isinstance(
        interference_reasons, Sequence
    ):
        raise ValueError("任务资源收益干扰原因无效")
    if not isinstance(tracked_fields, frozenset) or not tracked_fields or (
        tracked_fields - YIELD_FIELDS
    ):
        raise ValueError("任务资源收益统计范围无效")
    if task_status == "already_completed":
        return ResourceYield(
            task_status=task_status,
            attribution_status="not_run",
            reasons=("task_not_run",),
            before_snapshot_id=before.snapshot_id,
            tracked_fields=tracked_fields,
        )
    if after is not None and not isinstance(after, ResourceSnapshot):
        raise ValueError("任务收益的执行后快照无效")
    if after is None or before.status == "failed" or after.status == "failed":
        return ResourceYield(
            task_status=task_status,
            attribution_status="unavailable",
            reasons=("resource_snapshot_unavailable",),
            before_snapshot_id=before.snapshot_id,
            after_snapshot_id=None if after is None else after.snapshot_id,
            tracked_fields=tracked_fields,
        )
    if before.captured_datetime > after.captured_datetime:
        raise ValueError("任务收益快照时间顺序无效")

    identity_reasons = []
    if before.target_id != after.target_id:
        identity_reasons.append("target_changed")
    if (
        before.character_id is not None
        and after.character_id is not None
        and before.character_id != after.character_id
    ):
        identity_reasons.append("character_changed")
    if identity_reasons:
        return ResourceYield(
            task_status=task_status,
            attribution_status="interrupted",
            reasons=tuple(identity_reasons),
            before_snapshot_id=before.snapshot_id,
            after_snapshot_id=after.snapshot_id,
            tracked_fields=tracked_fields,
        )

    experience_delta, experience_reason = (None, None)
    if "experience" in tracked_fields:
        experience_delta, experience_reason = _experience_difference(
            before.values,
            after.values,
            level_requirements or {},
        )
    gold_delta = (
        _difference(before.values.gold, after.values.gold)
        if "gold" in tracked_fields else None
    )
    silver_delta = (
        _difference(before.values.silver, after.values.silver)
        if "silver" in tracked_fields else None
    )
    if any(not isinstance(reason, str) for reason in interference_reasons):
        raise ValueError("任务资源收益干扰原因无效")
    interferences = [reason.strip() for reason in interference_reasons if reason.strip()]
    reasons = list(interferences)
    if experience_reason is not None:
        reasons.append(experience_reason)

    if (
        task_status not in {"completed", "limit_reached"}
        or interferences
        or experience_reason == "level_regressed"
    ):
        attribution_status = "interrupted"
    elif experience_reason and (
        experience_reason.startswith("level_requirement_missing")
        or experience_reason == "cross_level_delta_negative"
    ):
        attribution_status = "cross_level_unknown"
    elif (
        before.status != "success"
        or after.status != "success"
        or any({
            "experience": experience_delta,
            "gold": gold_delta,
            "silver": silver_delta,
        }[name] is None for name in tracked_fields)
    ):
        attribution_status = "partial"
    else:
        attribution_status = "reliable"

    return ResourceYield(
        task_status=task_status,
        attribution_status=attribution_status,
        experience_delta=experience_delta,
        gold_delta=gold_delta,
        silver_delta=silver_delta,
        reasons=tuple(reasons),
        before_snapshot_id=before.snapshot_id,
        after_snapshot_id=after.snapshot_id,
        tracked_fields=tracked_fields,
    )


__all__ = [
    "ATTRIBUTION_STATUSES",
    "FIELD_STATUSES",
    "RESOURCE_FIELDS",
    "YIELD_FIELDS",
    "YIELD_SOURCES",
    "ResourceFieldEvidence",
    "ResourceSnapshot",
    "ResourceValues",
    "ResourceYield",
    "calculate_resource_yield",
]
