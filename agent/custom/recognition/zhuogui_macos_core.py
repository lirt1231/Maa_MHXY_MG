"""Pure scene classification and safety helpers for macOS 抓鬼 automation."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Iterable


SCHOOLS = (
    "大唐官府",
    "方寸山",
    "狮驼岭",
    "普陀山",
    "阴曹地府",
    "龙宫",
    "魔王寨",
    "化生寺",
    "月宫",
    "女儿村",
    "小雷音",
    "花果山",
    "须弥海",
    "盘丝洞",
)


def normalize_text(value: str) -> str:
    value = (value or "").replace(" ", "")
    return value.replace("（", "(").replace("）", ")").replace("：", ":")


def center(item: dict) -> tuple[int, int]:
    x, y, width, height = item["box"]
    return x + width // 2, y + height // 2


def relative_point(point: tuple[float, float], image_size: tuple[int, int]) -> tuple[int, int]:
    width, height = image_size
    return round(point[0] * width), round(point[1] * height)


def matching(items: Iterable[dict], pattern: str) -> list[dict]:
    regex = re.compile(pattern)
    return [item for item in items if regex.search(normalize_text(item.get("text", "")))]


def first_click(items: Iterable[dict], pattern: str) -> tuple[int, int] | None:
    matches = matching(items, pattern)
    return center(matches[0]) if matches else None


def selected_level(
    items: Iterable[dict], image_size: tuple[int, int], column_x: float
) -> int | None:
    """Read the centered value of one level picker column."""
    width, height = image_size
    candidates = []
    for item in items:
        text = normalize_text(item.get("text", ""))
        match = re.fullmatch(r"(\d{1,3})(?:级)?", text)
        if not match:
            continue
        x, y = center(item)
        if abs(x / width - column_x) <= 0.045 and abs(y / height - 0.395) <= 0.035:
            candidates.append((abs(y / height - 0.395), int(match.group(1))))
    return min(candidates)[1] if candidates else None


def fifth_member_present(items: Iterable[dict], image_size: tuple[int, int]) -> bool:
    width, height = image_size
    materialized = list(items)
    if width / height < 1.5:
        assist_count = len(matching(materialized, r"^助战$"))
        level_count = len(matching(materialized, r"^\d+级$"))
        return assist_count == 0 and level_count >= 5
    for item in materialized:
        text = normalize_text(item.get("text", ""))
        if any(school in text for school in SCHOOLS) and center(item)[1] >= height * 0.68:
            return True
    return False


def classify_scene(
    items: list[dict],
    image_size: tuple[int, int],
    templates: dict[str, tuple[int, int, int, int] | None] | None = None,
) -> dict:
    """Classify only states supported by fresh OCR/template evidence."""
    templates = templates or {}
    texts = [normalize_text(item.get("text", "")) for item in items]
    joined = "|".join(texts)

    if re.search(r"双倍点数不足.*领取100点双倍", joined):
        return {
            "status": "double_points_prompt",
            "confirm": first_click(items, r"^确定$"),
        }

    if any(
        re.search(
            r"今日(?:的)?捉鬼(?:任务)?(?:已完成|次数已达上限)"
            r"|捉鬼(?:任务)?(?:今日已完成|次数已达上限)",
            text,
        )
        for text in texts
    ):
        return {"status": "already_completed"}

    if re.search(r"捉完1轮鬼.*继续捉鬼", joined):
        return {
            "status": "round_complete",
            "confirm": first_click(items, r"^确定$"),
            "cancel": first_click(items, r"^取消(?:\(\d+\))?$"),
        }

    if re.search(r"不足五人|队伍人数不足", joined):
        return {
            "status": "not_enough_members",
            "cancel": first_click(items, r"^取消(?:\(\d+\))?$"),
        }

    if re.search(r"邀请成员", joined):
        return {
            "status": "invite_members_overlay",
            "close": templates.get("generic_close")
            or (*relative_point((0.80, 0.17), image_size), 40, 40),
        }

    if templates.get("welfare"):
        return {
            "status": "welfare_overlay",
            "close": relative_point((0.89, 0.08), image_size),
        }

    battle_cancel = [
        item
        for item in matching(items, r"^取消$")
        if center(item)[0] > image_size[0] * 0.88
        and center(item)[1] > image_size[1] * 0.9
    ]
    if (
        templates.get("battle")
        or re.search(r"^取消自动$|^自动$", joined)
        or battle_cancel
    ):
        return {"status": "battle"}

    task_hits = matching(items, r"捉鬼\(?\d+/10\)?|捉拿.*鬼|千年厉鬼")
    if task_hits:
        task_text = normalize_text(task_hits[0].get("text", ""))
        progress_match = re.search(r"捉鬼[（(]?(\d+)/10", task_text)
        return {
            "status": "ghost_task",
            "click": center(task_hits[0]),
            "progress": int(progress_match.group(1)) if progress_match else None,
        }

    if templates.get("auto_pathing") or re.search(r"自动寻路", joined):
        return {"status": "auto_pathing"}

    if re.search(r"调整目标", joined):
        daily = first_click(items, r"^日常任务$")
        ghost_items = matching(items, r"^捉鬼任务$")
        selected_ghost = any(center(item)[1] < image_size[1] * 0.36 for item in ghost_items)
        selectable_ghosts = [
            item for item in ghost_items if center(item)[1] >= image_size[1] * 0.36
        ]
        ghost = center(selectable_ghosts[0]) if selectable_ghosts else None
        level_20 = first_click(items, r"^20$")
        level_115 = first_click(items, r"^115(?:级)?$")
        return {
            "status": "target_dialog",
            "daily": daily,
            "ghost": ghost,
            "target_is_ghost": selected_ghost,
            "level_20": level_20,
            "level_115": level_115,
            "current_min_level": selected_level(items, image_size, 0.623),
            "current_max_level": selected_level(items, image_size, 0.731),
            "range_90_115": first_click(items, r"^90-115$"),
            "confirm": first_click(items, r"^确定$"),
        }

    if re.search(r"请组建或加入队", joined):
        return {
            "status": "team_quick_panel",
            "create": first_click(items, r"^创建(?:队伍)?$"),
            "find": first_click(items, r"^找队$")
        }

    if re.search(r"暂离队伍|离开队伍", joined) and re.search(r"邀请入队", joined):
        return {
            "status": "team_created_panel",
            "team": first_click(items, r"^队伍$"),
        }

    team_visible = bool(
        templates.get("team_header")
        or matching(
            items,
            r"^(?:创建队伍|退出队伍|自动匹配|取消匹配|匹配中|一键喊话)$",
        )
    )
    if team_visible:
        offline = matching(items, r"离线|暂离")
        remove = first_click(items, r"请离队伍")
        return {
            "status": "team_page",
            "create": first_click(items, r"创建队伍"),
            "exit": first_click(items, r"退出队伍"),
            "auto_match": first_click(items, r"^自动匹配$"),
            "matching": bool(matching(items, r"取消匹配|匹配中")),
            "shout": first_click(items, r"一键喊话|键喊话"),
            "current_channel": first_click(items, r"当前频道|^当前$"),
            "offline": center(offline[0]) if offline else None,
            "remove": remove,
            "full": fifth_member_present(items, image_size),
            "target_icon": templates.get("target_icon")
            or (*relative_point((0.602, 0.205), image_size), 40, 40),
            "close": templates.get("team_close")
            or (*relative_point((0.921, 0.146), image_size), 40, 40),
            "target_is_ghost": bool(matching(items, r"捉鬼任务")),
        }

    if re.search(r"请离队伍", joined):
        return {"status": "remove_member_menu", "click": first_click(items, r"请离队伍")}

    if re.search(r"捉鬼任务", joined) and re.search(r"钟馗|钟道", joined):
        return {"status": "zhongkui_dialog", "click": first_click(items, r"捉鬼任务")}

    zhongkui = first_click(items, r"钟馗|钟道")
    if zhongkui:
        return {"status": "map_zhongkui", "click": zhongkui}

    changan = first_click(items, r"^长安城$")
    if changan:
        if re.search(r"世界地图|地图", joined):
            return {"status": "world_map_changan", "click": changan}

    current_channel = first_click(items, r"当前频道|^当前$")
    if current_channel:
        return {"status": "channel_picker", "click": current_channel}

    team_button = first_click(items, r"^队伍$")
    if team_button:
        return {
            "status": "main_screen",
            "team": team_button,
            "location": "长安城" if changan else None,
        }

    if templates.get("main_ready") or templates.get("small_map"):
        return {
            "status": "main_screen",
            "team": None,
            "location": "长安城" if changan else None,
        }

    return {"status": "unknown"}


def round_completion_action(completed_rounds: int, target_rounds: int) -> str:
    if target_rounds <= 0:
        raise ValueError("target_rounds must be positive")
    new_count = completed_rounds + 1
    return "finish" if new_count >= target_rounds else "continue"


def proposed_action(decision: dict) -> str:
    """Describe one safe next action without performing it."""
    status = decision.get("status", "unknown")
    if status in {"battle", "auto_pathing"}:
        return "wait"
    if status == "unknown":
        return "stop"
    return {
        "main_screen": "open_team",
        "team_quick_panel": "create_team",
        "team_created_panel": "open_full_team_page",
        "team_page": "evaluate_team",
        "target_dialog": "configure_ghost_target",
        "channel_picker": "choose_current_channel",
        "map_zhongkui": "click_zhongkui",
        "world_map_changan": "travel_to_changan",
        "zhongkui_dialog": "accept_ghost_task",
        "ghost_task": "click_ghost_task",
        "round_complete": "apply_exact_round_decision",
        "double_points_prompt": "claim_100_double_points",
        "not_enough_members": "return_to_recruitment",
        "invite_members_overlay": "close_invite_overlay",
        "welfare_overlay": "close_known_welfare_overlay",
        "remove_member_menu": "remove_one_abnormal_member",
        "already_completed": "stop",
    }.get(status, "stop")


@dataclass
class RuntimeBudget:
    max_shouts: int = 3
    shout_interval: float = 45.0
    recruitment_timeout: float = 180.0
    max_rebuilds: int = 3
    battle_timeout: float = 180.0
    round_timeout: float = 1200.0
    started_at: float = field(default_factory=time.monotonic)
    recruitment_started_at: float | None = None
    round_started_at: float | None = None
    battle_started_at: float | None = None
    last_shout_at: float | None = None
    shouts: int = 0
    rebuilds: int = 0

    def can_shout(self, now: float) -> bool:
        if self.shouts >= self.max_shouts:
            return False
        return self.last_shout_at is None or now - self.last_shout_at >= self.shout_interval

    def record_shout(self, now: float) -> None:
        self.shouts += 1
        self.last_shout_at = now

    def recruitment_expired(self, now: float) -> bool:
        return (
            self.recruitment_started_at is not None
            and now - self.recruitment_started_at > self.recruitment_timeout
        )

    def can_rebuild(self) -> bool:
        return self.rebuilds < self.max_rebuilds

    def record_rebuild(self) -> None:
        if not self.can_rebuild():
            raise RuntimeError("team rebuild budget exhausted")
        self.rebuilds += 1

    def battle_expired(self, now: float) -> bool:
        return (
            self.battle_started_at is not None
            and now - self.battle_started_at > self.battle_timeout
        )

    def round_expired(self, now: float) -> bool:
        return (
            self.round_started_at is not None
            and now - self.round_started_at > self.round_timeout
        )


__all__ = [
    "RuntimeBudget",
    "SCHOOLS",
    "center",
    "classify_scene",
    "fifth_member_present",
    "first_click",
    "matching",
    "normalize_text",
    "relative_point",
    "round_completion_action",
    "proposed_action",
]
