"""Bounded, evidence-backed recovery from known task pages to the main screen."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

try:
    from .automation_geometry import in_region, valid_box
    from .automation_actions import ActionContext
    from .main_screen_actions import CloseKnownOverlayAction, MainScreenConfirmation
except ImportError:  # Direct execution through tools/run_return_to_main_macos.py.
    from automation_geometry import in_region, valid_box
    from automation_actions import ActionContext
    from main_screen_actions import CloseKnownOverlayAction, MainScreenConfirmation


RETURN_TO_MAIN_TEMPLATES = {
    "menu_plus": ["zonghe/jiahao.png", "zonghe/jiahao_live_923.png"],
    "activity_toolbar": [
        "zonghe/huodong1.png",
        "zonghe/huodong2.png",
        "zonghe/huodong_live_923.png",
    ],
    "fun_news_feed_close": {
        "templates": ["qiandao/liaotian_guanbi.png"],
        "threshold": 0.90,
        "roi_ratio": (0.55, 0.35, 0.12, 0.22),
    },
    "battle": ["zhandou/quxiao.png"],
    "close_welfare": ["qiandao/fuli_guanbi.png"],
    "close_shimen": {
        "templates": ["shimen/shimen_panel_close_923.png"],
        "threshold": 0.80,
    },
    "close_generic": [
        "zonghe/guanbi1.png", "zonghe/guanbi2.png", "zonghe/guanbi3.png",
        "zonghe/guanbi4.png", "zonghe/guanbi5.png", "zonghe/guanbi6.png",
        "zonghe/guanbi7.png", "duiwu/duiwu-guanbi.png",
        "jineng/zahuopu_guanbi.png", "qiandao/liaotian_guanbi.png",
        "zonghe/amazing_discount_close.png",
    ],
}

TASK_PAGE_PATTERNS = {
    "sanjieqiyuan": (("quiz", r"三界奇缘|答题|第\s*\d+\s*题"), ("activity", r"活动|日常活动")),
    "keju": (("quiz", r"科举|乡试|第\s*\d+\s*题"), ("activity", r"活动|日常活动")),
    "keju_huishi": (("quiz", r"科举|会试|第\s*\d+\s*题"), ("activity", r"活动|日常活动")),
    "zhuogui": (("team", r"队伍|创建队伍|申请列表|捉鬼"), ("activity", r"活动|日常活动")),
    "fuli_qiandao": (
        ("scratch", r"每日刮刮乐|梦幻刮刮乐|今日奖品|当前累计"),
        ("welfare", r"福利|签到"),
    ),
    "bangpai_qiandao": (("clan_welfare", r"帮派福利|帮派签到|帮派分红"), ("clan", r"帮派"), ("welfare", r"福利")),
    "baotu": (("activity", r"活动|日常活动|宝图任务"), ("team", r"队伍")),
    "fun_news": (("activity", r"活动|日常活动|趣(?:闻|味)鉴赏"),),
    "yunbiao": (("activity", r"活动|日常活动|运镖|押送普通镖银"), ("team", r"队伍")),
    "shimen": (("activity", r"活动|日常活动|师门任务"), ("team", r"队伍")),
    "mijingxiangyao": (("dungeon", r"秘境降妖|当前秘境|关卡"), ("activity", r"活动|日常活动"), ("team", r"队伍")),
    "kaogu": (("archaeology", r"工坊考古|技能生产|洛阳铲"), ("skills", r"辅助技能|人物技能|帮派技能|修炼技能")),
}

BLOCKING_PATTERN = re.compile(r"购买|充值|挑战失败|复活|药品")
PATHING_PATTERN = re.compile(r"自动寻路中?|寻路中|距离运镖完成")
TRANSIENT_COMPLETION_PATTERN = re.compile(r"任务完成|你从.*(?:搜出|获得).*(?:藏宝|宝图)")
OVERLAY_PATTERN = re.compile(
    r"福利|帮派|活动|日常活动|队伍|申请列表|三界奇缘|科举|乡试|会试|"
    r"运镖|宝图|师门|秘境|关卡|辅助技能|技能生产|工坊考古|洛阳铲"
)

# These controls belong to a stable, fixed game-canvas layout.  The boxes are
# ratios so MacOS task runtime maps them through the current window calibration
# before input.  Visual matching may establish page identity, but never moves
# the click target for these controls.
_TEMPORARY_BAG_CLOSE_BOX = (0.655, 0.205, 0.045, 0.070)
_SANJIE_COMPLETION_CLOSE_BOX = (0.86, 0.20, 0.06, 0.10)


def _has_overlay_identity(scene) -> bool:
    """Ignore task-tracker/sidebar labels while rejecting an open center panel."""
    return any(
        OVERLAY_PATTERN.search(str(item.get("text", "")))
        and valid_box(item)
        and in_region(
            item, scene.image_size, min_x=0.18, max_x=0.78, min_y=0.13, max_y=0.35
        )
        for item in _texts(scene)
    )


def _texts(scene) -> list[Mapping[str, Any]]:
    return [item for item in scene.texts if isinstance(item, Mapping)]


def _joined(scene) -> str:
    return " ".join(str(item.get("text", "")) for item in _texts(scene))


def _find_region(scene, pattern: str, **region) -> bool:
    regex = re.compile(pattern)
    return any(
        regex.search(str(item.get("text", "")))
        and valid_box(item)
        and in_region(item, scene.image_size, **region)
        for item in _texts(scene)
    )


def _find_item(scene, pattern: str, **region) -> Mapping[str, Any] | None:
    regex = re.compile(pattern)
    return next(
        (
            item
            for item in _texts(scene)
            if regex.search(str(item.get("text", "")))
            and valid_box(item)
            and in_region(item, scene.image_size, **region)
        ),
        None,
    )


def _relative_box(image_size, left, top, width, height) -> list[int]:
    image_width, image_height = image_size
    return [
        round(image_width * left),
        round(image_height * top),
        max(1, round(image_width * width)),
        max(1, round(image_height * height)),
    ]


def _fixed_temporary_bag(scene) -> bool:
    """Recognize the stable temporary-bag layout without reading its title.

    The temporary bag displayed after a completed Shimen task preserves the
    main canvas' bottom-right menu toggle and has its close badge in a fixed
    upper-center region.  The generic close template is identity evidence
    only; input always targets the calibrated fixed close box below.
    """
    close_box = valid_box({"box": scene.templates.get("close_generic")})
    return bool(
        scene.templates.get("menu_plus")
        and close_box is not None
        and in_region(
            {"box": close_box},
            scene.image_size,
            min_x=0.62,
            max_x=0.73,
            min_y=0.16,
            max_y=0.30,
        )
    )


def _expanded_chat_panel(scene) -> bool:
    channel_pattern = re.compile(r"^(系统|世界|当前|门派|帮派|队伍|空间|同城|组合)$")
    channels = {
        str(item.get("text", ""))
        for item in _texts(scene)
        if channel_pattern.fullmatch(str(item.get("text", "")))
        and valid_box(item)
        and in_region(item, scene.image_size, max_x=0.12, min_y=0.10, max_y=0.72)
    }
    has_input = _find_region(
        scene, r"点击这里输入", min_x=0.08, max_x=0.32, max_y=0.16
    )
    has_send = _find_region(
        scene, r"^发送$", min_x=0.30, max_x=0.48, max_y=0.16
    )
    return len(channels) >= 5 and has_input and has_send


def _team_quick_panel_evidence(scene) -> tuple[bool, bool]:
    """Return (panel_present, actionable_identity) for the expanded sidebar."""
    has_team_tab = _find_region(scene, r"^队伍$", min_x=0.90, max_y=0.35)
    has_prompt = _find_region(
        scene,
        r"请组建或加入队伍",
        min_x=0.78,
        min_y=0.20,
        max_y=0.62,
    )
    has_create = _find_region(
        scene, r"^创建$", min_x=0.78, min_y=0.35, max_y=0.75
    )
    has_find_team = _find_region(
        scene, r"^找队$", min_x=0.90, min_y=0.35, max_y=0.75
    )
    present = bool(has_team_tab and has_prompt)
    return present, bool(present and has_create and has_find_team)


def _full_team_panel(scene) -> bool:
    """Require independent header, body, command and side-tab evidence."""
    has_header = _find_region(
        scene, r"^组队$", max_x=0.18, min_y=0.08, max_y=0.30
    )
    has_prompt = _find_region(
        scene,
        r"你尚未创建队伍.*便捷组队.*快速匹配",
        min_x=0.12,
        max_x=0.78,
        min_y=0.10,
        max_y=0.32,
    )
    has_left_command = _find_region(
        scene, r"^(?:创建队伍|战斗指挥)$", max_x=0.42, min_y=0.68
    )
    has_right_command = _find_region(
        scene, r"^(?:我的助战|便捷组队)$", min_x=0.55, min_y=0.68
    )
    has_side_tabs = _find_region(
        scene, r"^队伍$", min_x=0.90, min_y=0.12, max_y=0.48
    ) and _find_region(
        scene, r"^申请$", min_x=0.90, min_y=0.22, max_y=0.62
    )
    return bool(
        has_header
        and has_prompt
        and has_left_command
        and has_right_command
        and has_side_tabs
    )


def _full_team_panel_present(scene) -> bool:
    """Block broad task-page fallbacks when a team page is only partly read."""
    has_header = _find_region(
        scene, r"^组队$", max_x=0.18, min_y=0.08, max_y=0.30
    )
    has_side_tabs = _find_region(
        scene, r"^队伍$", min_x=0.90, min_y=0.12, max_y=0.48
    ) and _find_region(
        scene, r"^申请$", min_x=0.90, min_y=0.22, max_y=0.62
    )
    return bool(has_header and has_side_tabs)


def _collapsed_main_screen(scene) -> bool:
    """Recognize the main UI when its task/team ribbon is not OCR-readable."""
    has_location = any(
        re.fullmatch(r"[\u4e00-\u9fff]{2,8}", str(item.get("text", "")))
        and valid_box(item)
        and in_region(item, scene.image_size, min_x=0.05, max_x=0.22, max_y=0.12)
        for item in _texts(scene)
    )
    has_left_menu = _find_region(
        scene, r"^(?:福利|商城)$", max_x=0.18, min_y=0.08, max_y=0.42
    )
    toolbar_labels = {
        label
        for item in _texts(scene)
        if valid_box(item)
        and in_region(item, scene.image_size, min_x=0.15, max_x=0.62, max_y=0.14)
        for label in ("活动", "排行", "挂机", "社群")
        if label in str(item.get("text", ""))
    }
    has_backpack = _find_region(scene, r"^包[裹里]$", min_x=0.88, min_y=0.76)
    return bool(has_location and has_left_menu and len(toolbar_labels) >= 2 and has_backpack)


def _shimen_quick_reward_on_main_screen(scene, anchors: list[str]) -> bool:
    """Recognize the non-modal Shimen reward card that remains on the main UI.

    The completed task can leave a consumable card at the lower right.  Its
    close glyph resembles a generic dialog close, but the surrounding main
    screen stays fully usable.  Treat it as main-screen evidence only when all
    independent main-screen anchors are present; a reward popup with fewer
    anchors must still take its explicit close path.
    """
    if not {
        "task_team_tabs",
        "left_menu",
        "activity_toolbar",
        "menu_plus",
    }.issubset(anchors):
        return False
    has_reward = _find_region(
        scene, r"^(?:绿芦羹|红罗羹)$", min_x=0.72, min_y=0.55, max_y=0.92
    )
    has_use = _find_region(scene, r"^使\s*用$", min_x=0.72, min_y=0.65, max_y=0.98)
    close_box = valid_box({"box": scene.templates.get("close_generic")})
    return bool(
        has_reward
        and has_use
        and close_box is not None
        and in_region(
            {"box": close_box}, scene.image_size, min_x=0.84, min_y=0.50, max_y=0.78
        )
    )


def _fun_news_feed_identity(scene) -> bool:
    channels = {
        str(item.get("text", ""))
        for item in _texts(scene)
        if valid_box(item)
        and in_region(item, scene.image_size, max_x=0.11, min_y=0.10, max_y=0.68)
        and str(item.get("text", ""))
        in {"系统", "世界", "当前", "门派", "帮派", "队伍", "空间", "同城", "组合"}
    }
    top_text = " ".join(
        str(item.get("text", ""))
        for item in _texts(scene)
        if valid_box(item)
        and in_region(item, scene.image_size, min_x=0.08, max_x=0.48, min_y=0.05, max_y=0.18)
    )
    return (
        len(channels) >= 5
        and all(label in top_text for label in ("关注", "图文", "视频", "论坛"))
        and _find_region(scene, r"打开空间", max_x=0.14, min_y=0.05, max_y=0.20)
        and _find_region(scene, r"\+?关注|十关注", min_x=0.32, max_x=0.50, min_y=0.10, max_y=0.94)
    )


def classify_return_scene(task_id: str, scene) -> dict[str, Any]:
    """Classify only states where either confirmation or one known close is safe."""
    joined = _joined(scene)
    if scene.templates.get("battle") or _find_region(
        scene, r"^(?:取消|取消自动)$|战斗中", min_x=0.80, min_y=0.72
    ):
        return {"status": "blocked", "reason": "battle"}
    if _find_region(
        scene,
        PATHING_PATTERN.pattern,
        min_x=0.18,
        max_x=0.90,
        min_y=0.08,
        max_y=0.92,
    ):
        return {"status": "blocked", "reason": "auto_pathing"}
    if _find_region(
        scene,
        BLOCKING_PATTERN.pattern,
        min_x=0.25,
        max_x=0.90,
        min_y=0.08,
        max_y=0.92,
    ):
        return {"status": "blocked", "reason": "unsafe_page"}

    anchors = []
    has_task_tab = _find_region(scene, r"^任务$", min_x=0.70, max_y=0.35)
    has_team_tab = _find_region(scene, r"^队伍$", min_x=0.82, max_y=0.35)
    if has_task_tab and has_team_tab:
        anchors.append("task_team_tabs")
    if _find_region(scene, r"^(福利|商城|节日活动)$", max_x=0.18, min_y=0.10, max_y=0.40):
        anchors.append("left_menu")
    if _find_region(scene, r"^(当前|个人|世界|帮派|队伍)$", max_x=0.18, min_y=0.75):
        anchors.append("chat_tab")
    if scene.templates.get("activity_toolbar") or _find_region(
        scene, r"^活动$", min_x=0.18, max_x=0.46, max_y=0.18
    ):
        anchors.append("activity_toolbar")
    if scene.templates.get("menu_plus"):
        anchors.append("menu_plus")

    team_quick_present, team_quick_actionable = _team_quick_panel_evidence(scene)
    if team_quick_actionable:
        return {
            "status": "close_known_page",
            "scene": "team_quick_panel",
            "action_id": "close:team_quick_panel",
            # This fixed toggle is the spiral control immediately left of the
            # task/team tabs. The four quick-panel anchors above are required
            # before the scaled window-relative region becomes actionable.
            "box": _relative_box(scene.image_size, 0.755, 0.12, 0.055, 0.075),
        }
    if team_quick_present:
        return {
            "status": "unknown",
            "reason": "team_quick_panel_identity_incomplete",
        }

    team_close = valid_box({"box": scene.templates.get("close_generic")})
    if (
        _full_team_panel(scene)
        and team_close is not None
        and in_region(
            {"box": team_close},
            scene.image_size,
            min_x=0.88,
            max_y=0.25,
        )
    ):
        return {
            "status": "close_known_page",
            "scene": "team_panel",
            "action_id": "close:team_panel",
            "box": team_close,
        }
    if _full_team_panel_present(scene):
        return {
            "status": "unknown",
            "reason": "team_panel_identity_incomplete",
        }

    if _collapsed_main_screen(scene) and not _has_overlay_identity(scene):
        return {"status": "main_screen", "anchors": ["collapsed_main_screen"]}

    quick_reward_on_main_screen = (
        task_id == "shimen" and _shimen_quick_reward_on_main_screen(scene, anchors)
    )
    main_screen_evidence = (
        len(anchors) >= 3
        and not scene.templates.get("close_welfare")
        and (
            not scene.templates.get("close_generic")
            or quick_reward_on_main_screen
        )
        and not _has_overlay_identity(scene)
    )
    if main_screen_evidence and _find_region(
        scene,
        TRANSIENT_COMPLETION_PATTERN.pattern,
        min_x=0.25,
        max_x=0.80,
        min_y=0.08,
        max_y=0.68,
    ):
        return {"status": "wait", "reason": "transient_completion"}
    if main_screen_evidence:
        return {
            "status": "main_screen",
            "anchors": anchors,
            "quickReward": quick_reward_on_main_screen,
        }

    if task_id == "shimen" and _fixed_temporary_bag(scene):
        return {
            "status": "close_known_page",
            "scene": "temporary_bag",
            "action_id": "close:temporary_bag",
            # Do not OCR-match the title or click an inferred X box.  This
            # stable control is fixed relative to the game content rectangle.
            "box": _relative_box(scene.image_size, *_TEMPORARY_BAG_CLOSE_BOX),
        }

    if _expanded_chat_panel(scene):
        return {
            "status": "close_known_page",
            "scene": "chat_panel",
            "action_id": "close:chat_panel",
            # The collapse control has a fixed position within the game canvas.
            # The surrounding chat controls are required before this scaled box
            # is considered actionable.
            "box": _relative_box(scene.image_size, 0.465, 0.43, 0.04, 0.12),
        }

    sanjie_title = _find_region(
        scene, r"三界奇缘", max_x=0.20, min_y=0.25, max_y=0.75
    )
    sanjie_reward = _find_region(
        scene, r"已获得奖励", max_x=0.40, min_y=0.45, max_y=0.80
    )
    sanjie_completion = (
        task_id == "sanjieqiyuan"
        and (sanjie_title or sanjie_reward)
        and _find_region(
            scene, r"^10/10$", max_x=0.40, min_y=0.15, max_y=0.35
        )
        and _find_region(
            scene,
            r"完成所有题目",
            min_x=0.35,
            max_x=0.85,
            min_y=0.45,
            max_y=0.72,
        )
    )
    if sanjie_completion:
        return {
            "status": "close_known_page",
            "scene": "sanjie_completion",
            "action_id": "close:sanjie_completion",
            # The settlement panel has a fixed red X.  Its identity requires
            # independent progress, reward and completion-text evidence above.
            "box": _relative_box(scene.image_size, *_SANJIE_COMPLETION_CLOSE_BOX),
        }

    if task_id == "shimen" and _find_region(
        scene, r"^师门任务完成$", min_x=0.25, max_x=0.75, min_y=0.15, max_y=0.60
    ):
        confirm = _find_item(
            scene, r"^确定$", min_x=0.30, max_x=0.70, min_y=0.55, max_y=0.85
        )
        if confirm is not None:
            return {
                "status": "close_known_page",
                "scene": "shimen_completion",
                "action_id": "confirm:shimen_completion",
                "box": valid_box(confirm),
            }

    fun_news_close = valid_box({"box": scene.templates.get("fun_news_feed_close")})
    if task_id == "fun_news" and _fun_news_feed_identity(scene):
        return {
            "status": "close_known_page",
            "scene": "fun_news_feed",
            "action_id": "close:fun_news_feed",
            # The space panel's return arrow is fixed within the game canvas.
            # Its page identity above is required before this scaled control is used.
            "box": _relative_box(scene.image_size, 0.445, 0.41, 0.04, 0.14),
        }

    shimen_title = _find_region(
        scene, r"^师门(?:任务)?$", max_x=0.35, min_y=0.15, max_y=0.50
    ) and (
        _find_region(scene, r"^任务$", max_x=0.35, min_y=0.15, max_y=0.50)
        or _find_region(scene, r"^师门任务$", max_x=0.35, min_y=0.15, max_y=0.50)
    )
    shimen_panel_anchors = (
        _find_region(
            scene,
            r"^完成五种不同的任(?:务)?$",
            max_x=0.40,
            min_y=0.25,
            max_y=0.60,
        )
        and _find_region(
            scene,
            r"^自动选择每日任务$",
            min_x=0.30,
            max_x=0.65,
            min_y=0.55,
            max_y=0.85,
        )
        and _find_region(
            scene,
            r"^自动完成任务$",
            min_x=0.55,
            max_x=0.85,
            min_y=0.55,
            max_y=0.85,
        )
    )
    shimen_close = valid_box({"box": scene.templates.get("close_shimen")})
    if (
        task_id == "shimen"
        and (shimen_title or shimen_panel_anchors)
        and shimen_close is not None
        and in_region({"box": shimen_close}, scene.image_size, min_x=0.75, max_y=0.40)
    ):
        return {
            "status": "close_known_page",
            "scene": "shimen_panel",
            "action_id": "close:shimen_panel",
            "box": shimen_close,
        }

    shimen_reward_item = _find_region(
        scene, r"^(?:绿芦羹|红罗羹)$", min_x=0.72, min_y=0.55, max_y=0.92
    ) and _find_region(
        scene, r"^使\s*用$", min_x=0.72, min_y=0.65, max_y=0.98
    )
    reward_close = valid_box({"box": scene.templates.get("close_generic")})
    if (
        task_id == "shimen"
        and shimen_reward_item
        and reward_close is not None
        and in_region(
            {"box": reward_close},
            scene.image_size,
            min_x=0.75,
            min_y=0.45,
            max_y=0.85,
        )
    ):
        return {
            "status": "close_known_page",
            "scene": "shimen_reward_item",
            "action_id": "close:shimen_reward_item",
            "box": reward_close,
        }

    for scene_id, pattern in TASK_PAGE_PATTERNS.get(task_id, ()):
        if re.search(pattern, joined):
            close_template = (
                "close_welfare" if scene_id == "welfare" else "close_generic"
            )
            close_box = valid_box({"box": scene.templates.get(close_template)})
            if close_box is None or not in_region(
                {"box": close_box}, scene.image_size, min_x=0.75, max_y=0.35
            ):
                return {"status": "unknown", "reason": "no_known_close"}
            return {
                "status": "close_known_page",
                "scene": scene_id,
                "action_id": f"close:{scene_id}",
                "box": close_box,
            }
    if scene.templates.get("close_generic") or scene.templates.get("close_welfare"):
        return {"status": "unknown", "reason": "page_not_allowed_for_task"}
    return {"status": "unknown", "reason": "no_known_close"}


@dataclass(frozen=True)
class ReturnToMainResult:
    status: str
    outcome: str | None = None
    scene: str | None = None
    reason: str | None = None
    actions: int = 0
    clicks: int = 0
    evidence: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "status": self.status,
            "outcome": self.outcome,
            "scene": self.scene,
            "reason": self.reason,
            "actions": self.actions,
            "clicks": self.clicks,
        }
        if self.status == "confirmed":
            payload["confirmedAt"] = datetime.now(timezone.utc).isoformat()
        if self.evidence is not None:
            payload["evidence"] = self.evidence
        return payload


@dataclass
class ReturnToMainWorkflow:
    task_id: str
    max_observations: int = 12
    max_clicks: int = 4
    wait_seconds: float = 0.8
    classifier: Callable[[str, Any], Mapping[str, Any]] = field(
        default=classify_return_scene, repr=False, compare=False
    )

    def execute(self, runtime, guard) -> ReturnToMainResult:
        used_actions: set[str] = set()
        main_confirmation = MainScreenConfirmation()
        close_overlay = CloseKnownOverlayAction()
        observations = 0
        clicks = 0
        for _ in range(self.max_observations):
            guard.checkpoint()
            scene = runtime.read_scene(RETURN_TO_MAIN_TEMPLATES)
            observations += 1
            decision = dict(self.classifier(self.task_id, scene))
            status = decision.get("status")
            if status == "main_screen":
                confirmation_status, main_confirmation, evidence = main_confirmation.observe(
                    scene, decision
                )
                if confirmation_status == "pending":
                    guard.wait(0.25)
                    continue
                if confirmation_status == "failed":
                    return ReturnToMainResult(
                        "failed", reason=str(evidence), actions=observations, clicks=clicks
                    )
                assert isinstance(evidence, dict)
                return ReturnToMainResult(
                    "confirmed",
                    outcome="already_main" if clicks == 0 else "recovered",
                    scene="main_screen",
                    actions=observations,
                    clicks=clicks,
                    evidence={
                        "targetId": runtime.target_id,
                        **evidence,
                    },
                )
            main_confirmation = MainScreenConfirmation()
            if status == "wait":
                guard.wait(self.wait_seconds)
                continue
            if status == "close_known_page":
                action_id = decision.get("action_id")
                if not isinstance(action_id, str) or action_id in used_actions:
                    return ReturnToMainResult("failed", reason="同一关闭动作不得重复", actions=observations, clicks=clicks)
                if clicks >= self.max_clicks:
                    return ReturnToMainResult("failed", reason="返回主界面点击预算已耗尽", actions=observations, clicks=clicks)
                close_overlay.execute(
                    ActionContext(
                        input=runtime,
                        waiter=guard,
                        scene=scene,
                        decision=decision,
                        task_label=self.task_id,
                    )
                )
                used_actions.add(action_id)
                clicks += 1
                guard.wait(self.wait_seconds)
                continue
            reason = decision.get("reason") or "unknown_page"
            return ReturnToMainResult("failed", reason=f"恢复停止：{reason}", actions=observations, clicks=clicks)
        return ReturnToMainResult("failed", reason="返回主界面观察预算已耗尽", actions=observations, clicks=clicks)
