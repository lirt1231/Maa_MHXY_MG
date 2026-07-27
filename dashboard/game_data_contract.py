from __future__ import annotations

import re
from datetime import datetime
from typing import Any


GAME_DATA_SCHEMA_VERSION = 2
LOCAL_PRIMARY_TARGET_ID = "local-primary"
TARGET_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
GAME_DATA_CATEGORIES = {"character_resources", "daily_tasks"}
GAME_DATA_STATUSES = {"success", "partial", "failed"}
DAILY_TASK_STATUSES = {"completed", "available", "locked", "unknown"}
REWARD_STATUSES = {"claimable", "claimed", "unknown"}
FIELD_STATUSES = {"captured", "missing", "invalid", "corrected"}
CATEGORY_MAX_AGE_SECONDS = {
    "character_resources": 24 * 60 * 60,
    "daily_tasks": 24 * 60 * 60,
}

CHARACTER_RESOURCE_FIELDS = {
    "characterName",
    "server",
    "school",
    "gold",
    "silver",
    "jade",
    "vitality",
    "score",
    "level",
    "experience",
    "experienceRequired",
}


def validate_target_id(value: object) -> str:
    if not isinstance(value, str) or not TARGET_ID_RE.fullmatch(value):
        raise ValueError("游戏数据目标标识无效")
    return value


def validate_game_date(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("游戏数据所属日期无效")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("游戏数据所属日期无效") from exc


def validate_category_data(category: str, status: str, data: dict[str, Any]) -> None:
    if category == "character_resources":
        _validate_character_resources(status, data)
        return
    if category == "daily_tasks":
        _validate_daily_tasks(data)
        return
    raise ValueError("不支持的游戏数据分类")


def validate_evidence(evidence: dict[str, Any]) -> None:
    fields = evidence.get("fields")
    if fields is None:
        return
    if not isinstance(fields, dict):
        raise ValueError("游戏数据字段证据必须是对象")
    for field, item in fields.items():
        if not isinstance(field, str) or not field or not isinstance(item, dict):
            raise ValueError("游戏数据字段证据无效")
        unknown = set(item) - {"status", "confidence", "rawText", "box", "sourcePage"}
        if unknown:
            raise ValueError("游戏数据字段证据包含未声明字段")
        if item.get("status") not in FIELD_STATUSES:
            raise ValueError("游戏数据字段证据状态无效")
        confidence = item.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError("游戏数据字段证据置信度无效")
        raw_text = item.get("rawText")
        if raw_text is not None and not isinstance(raw_text, str):
            raise ValueError("游戏数据字段证据原文无效")
        source_page = item.get("sourcePage")
        if source_page is not None and (
            not isinstance(source_page, str) or not source_page.strip()
        ):
            raise ValueError("游戏数据字段证据来源无效")
        box = item.get("box")
        if box is not None and not _valid_box(box):
            raise ValueError("游戏数据字段证据坐标无效")


def snapshot_is_stale(snapshot: dict[str, Any], now: datetime | None = None) -> bool:
    captured = datetime.fromisoformat(snapshot["capturedAt"])
    current = now or datetime.now(captured.tzinfo)
    if captured.tzinfo is None and current.tzinfo is not None:
        current = current.replace(tzinfo=None)
    age_seconds = max(0, (current - captured).total_seconds())
    if snapshot["category"] == "daily_tasks":
        return snapshot["gameDate"] != current.astimezone().date().isoformat()
    max_age_seconds = CATEGORY_MAX_AGE_SECONDS.get(snapshot["category"])
    return max_age_seconds is None or age_seconds > max_age_seconds


def _validate_character_resources(status: str, data: dict[str, Any]) -> None:
    if set(data) - CHARACTER_RESOURCE_FIELDS:
        raise ValueError("角色资源数据包含未声明字段")
    for field in {"characterName", "server", "school"}:
        value = data.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("角色身份数据无效")
    for field in CHARACTER_RESOURCE_FIELDS - {"characterName", "server", "school"}:
        value = data.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError("角色资源数值无效")
    if status == "success" and not any(value is not None for value in data.values()):
        raise ValueError("成功的角色资源快照不能全部为空")


def _validate_daily_tasks(data: dict[str, Any]) -> None:
    if set(data) != {"tasks"} or not isinstance(data["tasks"], list):
        raise ValueError("日程数据必须包含任务列表")
    seen_ids: set[str] = set()
    for task in data["tasks"]:
        if not isinstance(task, dict):
            raise ValueError("日程任务数据无效")
        if set(task) - {
            "taskId", "name", "completed", "target", "status", "rewardStatus"
        }:
            raise ValueError("日程任务包含未声明字段")
        task_id = task.get("taskId")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in seen_ids:
            raise ValueError("日程任务标识无效或重复")
        seen_ids.add(task_id)
        name = task.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("日程任务名称无效")
        for field in ("completed", "target"):
            value = task.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError("日程任务进度无效")
        completed, target = task.get("completed"), task.get("target")
        if completed is not None and target is not None and completed > target:
            raise ValueError("日程任务完成次数超过目标")
        if task.get("status") not in DAILY_TASK_STATUSES:
            raise ValueError("日程任务状态无效")
        if task.get("rewardStatus") not in REWARD_STATUSES:
            raise ValueError("日程任务奖励状态无效")


def _valid_box(box: object) -> bool:
    return (
        isinstance(box, list)
        and len(box) == 4
        and all(
            not isinstance(value, bool) and isinstance(value, (int, float))
            for value in box
        )
        and box[2] > 0
        and box[3] > 0
    )
