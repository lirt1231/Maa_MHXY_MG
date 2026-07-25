#!/usr/bin/env python3
"""Run a bounded 抓鬼 workflow against the open macOS game client."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agent" / "custom" / "recognition"))

from maa.controller import (  # noqa: E402
    MacOSController,
    MaaMacOSInputMethodEnum,
    MaaMacOSScreencapMethodEnum,
)
from maa.pipeline import JOCR, JTemplateMatch  # noqa: E402
from maa.resource import Resource  # noqa: E402
from maa.tasker import Tasker  # noqa: E402
from maa.toolkit import Toolkit  # noqa: E402

from zhuogui_macos_core import (  # noqa: E402
    RuntimeBudget,
    classify_scene,
    proposed_action,
    relative_point,
    round_completion_action,
)
from run_sanjieqiyuan_macos import NativeMacOSInput  # noqa: E402


@dataclass
class WorkflowState:
    phase: str = "ensure_team"
    completed_rounds: int = 0
    target_daily_clicked: bool = False
    target_ghost_clicked: bool = False
    target_high_range_clicked: bool = False
    target_level_swipes: int = 0
    target_configured: bool = False
    double_claims_this_round: int = 0
    unknown_count: int = 0
    round_started_at: float | None = None
    last_task_click_at: float | None = None
    navigate_not_before: float | None = None


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--inspect-scene", action="store_true")
    parser.add_argument("--recognize-only", action="store_true")
    parser.add_argument("--max-steps", type=int, default=600)
    parser.add_argument("--poll-interval", type=float, default=1.2)
    parser.add_argument("--recruit-timeout", type=float, default=180.0)
    parser.add_argument("--max-rebuilds", type=int, default=3)
    args = parser.parse_args()
    if args.rounds <= 0:
        parser.error("--rounds 必须是正整数；第一版不支持无限抓鬼")
    if args.max_steps <= 0:
        parser.error("--max-steps 必须是正整数")
    return args


def recognition_detail(tasker, recognition_type, recognition_param, image):
    task = tasker.post_recognition(recognition_type, recognition_param, image).wait()
    if not task.succeeded:
        raise RuntimeError(f"{recognition_type} 识别任务失败")
    detail = task.get()
    if detail is None or not detail.nodes:
        return None
    return detail.nodes[-1].recognition


def template_box(tasker, image, templates, threshold=0.75, roi=None):
    height, width = image.shape[:2]
    search_roi = roi or (0, 0, width, height)
    detail = recognition_detail(
        tasker,
        "TemplateMatch",
        JTemplateMatch(template=templates, roi=search_roi, threshold=[threshold]),
        image,
    )
    if detail is None or not detail.hit:
        return None
    return tuple(detail.box)


def read_scene(tasker, controller):
    image = None
    for attempt in range(1, 4):
        screenshot = controller.post_screencap().wait()
        if screenshot.succeeded:
            image = screenshot.get()
            if image is not None:
                break
        log_event("screencap_retry", attempt=attempt)
        time.sleep(0.5)
    if image is None:
        raise RuntimeError("连续三次截图失败；停止以避免使用旧画面")
    height, width = image.shape[:2]
    ocr = recognition_detail(
        tasker,
        "OCR",
        JOCR(expected=[".*"], roi=(0, 0, width, height), threshold=0.2),
        image,
    )
    items = []
    if ocr is not None:
        items = [
            {"text": result.text, "box": list(result.box)}
            for result in ocr.filtered_results
        ]

    template_boxes = {
        "battle": template_box(
            tasker,
            image,
            ["zhandou/zidong.png", "zhandou/quxiao.png"],
        ),
        "auto_pathing": template_box(
            tasker,
            image,
            [
                "zonghe/zidongxunlu1.png",
                "zonghe/zidongxunlu2.png",
                "zonghe/zidongxunlu3.png",
                "zonghe/zidongxunlu4.png",
            ],
            0.75,
        ),
        "welfare": template_box(
            tasker,
            image,
            ["qiandao/fuli.png"],
            0.8,
            (round(width * 0.30), 0, round(width * 0.48), round(height * 0.18)),
        ),
        "team_header": template_box(
            tasker,
            image,
            ["duiwu/duiwu-zudui.png", "duiwu/duiwu-duiwu.png"],
            0.8,
        ),
        "target_icon": template_box(tasker, image, ["duiwu/duiwu-mubiao.png"], 0.8),
        "team_close": template_box(tasker, image, ["duiwu/duiwu-guanbi.png"], 0.8),
        "main_ready": template_box(
            tasker,
            image,
            ["zonghe/chenghao.png", "zonghe/jiahao.png"],
            0.75,
        ),
        "small_map": template_box(
            tasker,
            image,
            ["zonghe/xiaoditu_taiyang.png", "zonghe/xiaoditu_yueliang.png"],
            0.75,
        ),
        "generic_close": template_box(
            tasker,
            image,
            ["zonghe/guanbi3.png", "zonghe/guanbi4.png", "zonghe/guanbi2.png"],
            0.75,
        ),
    }
    scene = {
        "image_size": [width, height],
        "texts": items,
        "templates": template_boxes,
    }
    scene["decision"] = classify_scene(items, (width, height), template_boxes)
    return scene


def box_center(box):
    x, y, width, height = box
    return x + width // 2, y + height // 2


def log_event(event, **payload):
    print(
        json.dumps(
            {"event": event, "time": round(time.time(), 3), **payload},
            ensure_ascii=False,
        ),
        flush=True,
    )


def scene_summary(scene):
    return {
        "image_size": scene["image_size"],
        "decision": scene["decision"],
        "evidence": {
            "ocr_item_count": len(scene["texts"]),
            "matched_templates": sorted(
                name for name, box in scene["templates"].items() if box is not None
            ),
        },
    }


def main() -> int:
    args = parse_args()
    os.environ["MAA_DIRECT_RESOURCE"] = "1"
    Toolkit.init_option(str(ROOT))
    windows = [
        window
        for window in Toolkit.find_desktop_windows()
        if window.class_name == "com.netease.my" and window.window_name == "梦幻西游"
    ]
    if not windows:
        raise RuntimeError("未找到已打开的梦幻西游窗口")

    controller = MacOSController(
        windows[0].hwnd,
        MaaMacOSScreencapMethodEnum.ScreenCaptureKit,
        MaaMacOSInputMethodEnum.GlobalEvent,
    )
    if not controller.post_connection().wait().succeeded:
        raise RuntimeError("连接梦幻西游窗口失败")

    resource = Resource()
    for bundle in (ROOT / "assets/resource/base", ROOT / "assets/resource/mac"):
        if not resource.post_bundle(bundle).wait().succeeded:
            raise RuntimeError(f"加载资源失败：{bundle}")
    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise RuntimeError("初始化 Maa Tasker 失败")

    native_input = NativeMacOSInput()
    first_scene = read_scene(tasker, controller)
    if args.inspect_scene:
        print(json.dumps(scene_summary(first_scene), ensure_ascii=False), flush=True)
        return 0
    if args.recognize_only:
        print(
            json.dumps(
                {
                    "image_size": first_scene["image_size"],
                    "decision": first_scene["decision"],
                    "proposed_action": proposed_action(first_scene["decision"]),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0

    state = WorkflowState()
    budget = RuntimeBudget(
        recruitment_timeout=args.recruit_timeout,
        max_rebuilds=args.max_rebuilds,
    )

    def click(point, scene, reason):
        if point is None:
            raise RuntimeError(f"{reason} 缺少经过识别的点击位置")
        result = controller.post_click(*point).wait()
        if not result.succeeded:
            raise RuntimeError(f"Maa 点击失败：{reason}，位置：{point}")
        log_event("click", reason=reason, source=point, phase=state.phase)

    def click_box(box, scene, reason):
        click(box_center(box) if box else None, scene, reason)

    for step in range(1, args.max_steps + 1):
        now = time.monotonic()
        scene = read_scene(tasker, controller)
        decision = scene["decision"]
        status = decision["status"]
        log_event(
            "scene",
            step=step,
            phase=state.phase,
            status=status,
            completed_rounds=state.completed_rounds,
            ghost_progress=decision.get("progress"),
        )
        if status != "unknown":
            state.unknown_count = 0

        if status == "double_points_prompt":
            if state.double_claims_this_round >= 1:
                raise RuntimeError("本轮重复出现双倍领取弹窗，停止避免重复消耗")
            click(decision.get("confirm"), scene, "领取100点双倍")
            state.double_claims_this_round += 1
            time.sleep(args.poll_interval)
            continue

        if status == "not_enough_members":
            click(decision.get("cancel"), scene, "取消人数不足弹窗")
            state.phase = "recruit"
            if budget.recruitment_started_at is None:
                budget.recruitment_started_at = now
            time.sleep(args.poll_interval)
            continue

        if status == "invite_members_overlay":
            click_box(decision.get("close"), scene, "关闭邀请成员窗口")
            time.sleep(args.poll_interval)
            continue

        if status == "welfare_overlay":
            click(decision.get("close"), scene, "关闭已知福利弹窗")
            time.sleep(args.poll_interval)
            continue

        if status == "round_complete":
            action = round_completion_action(state.completed_rounds, args.rounds)
            state.completed_rounds += 1
            log_event("round_complete", round=state.completed_rounds, action=action)
            if action == "finish":
                click(decision.get("cancel"), scene, "达到目标轮数，取消继续")
                log_event("completed", rounds=state.completed_rounds)
                return 0
            click(decision.get("confirm"), scene, "继续下一轮")
            state.phase = "recruit"
            state.double_claims_this_round = 0
            state.round_started_at = None
            budget.recruitment_started_at = now
            time.sleep(args.poll_interval)
            continue

        if status == "battle":
            if budget.battle_started_at is None:
                budget.battle_started_at = now
            elif budget.battle_expired(now):
                raise TimeoutError("单场战斗超过180秒，已停止")
            time.sleep(args.poll_interval)
            continue
        if status != "unknown":
            budget.battle_started_at = None

        budget.round_started_at = state.round_started_at
        if budget.round_expired(now):
            raise TimeoutError("单轮抓鬼超过20分钟，已停止")

        if status in ("ghost_task", "auto_pathing"):
            state.phase = "run_round"
            if state.round_started_at is None:
                state.round_started_at = now
            if status == "ghost_task":
                if (
                    state.last_task_click_at is None
                    or now - state.last_task_click_at >= 8.0
                ):
                    click(decision.get("click"), scene, "点击抓鬼任务栏")
                    state.last_task_click_at = now
            time.sleep(args.poll_interval)
            continue

        if status == "zhongkui_dialog":
            click(decision.get("click"), scene, "接取捉鬼任务")
            state.phase = "run_round"
            state.round_started_at = now
            time.sleep(args.poll_interval)
            continue

        if status in ("map_zhongkui", "world_map_changan"):
            click(decision.get("click"), scene, status)
            state.phase = "navigate_zhongkui"
            time.sleep(2.0)
            continue

        if status == "target_dialog":
            if decision.get("target_is_ghost"):
                state.target_daily_clicked = True
                state.target_ghost_clicked = True
            if not state.target_ghost_clicked and decision.get("ghost"):
                click(decision["ghost"], scene, "选择捉鬼任务")
                state.target_ghost_clicked = True
            elif not state.target_daily_clicked and decision.get("daily"):
                click(decision["daily"], scene, "展开日常任务")
                state.target_daily_clicked = True
            elif (
                not state.target_high_range_clicked
                and decision.get("range_90_115")
                and decision.get("current_max_level") != 115
            ):
                click(decision["range_90_115"], scene, "选择90-115预设区间")
                state.target_high_range_clicked = True
            elif (
                decision.get("current_min_level") == 20
                and decision.get("current_max_level") == 115
                and decision.get("confirm")
            ):
                click(decision.get("confirm"), scene, "确认20-115抓鬼目标")
                state.target_configured = True
                state.phase = "recruit"
                budget.recruitment_started_at = now
            elif state.target_level_swipes < 20:
                width, height = scene["image_size"]
                current_min = decision.get("current_min_level")
                if current_min is None:
                    raise RuntimeError("未能读取最低等级，停止避免盲目拖动")
                if current_min < 20:
                    raise RuntimeError(f"最低等级已越过20（当前{current_min}），停止")
                drag_end_y = 0.75 if current_min > 35 else 0.45
                native_input.drag(
                    relative_point((0.623, 0.395), (width, height)),
                    relative_point((0.623, drag_end_y), (width, height)),
                    (width, height),
                )
                state.target_level_swipes += 1
                log_event(
                    "drag",
                    reason="调整最低等级至20",
                    current_min=current_min,
                    count=state.target_level_swipes,
                )
            else:
                raise RuntimeError("调整20-115等级范围超过20次，停止")
            time.sleep(args.poll_interval)
            continue

        if status in ("team_page", "remove_member_menu"):
            state.phase = "recruit"
            if decision.get("target_is_ghost"):
                state.target_configured = True
            if budget.recruitment_started_at is None:
                budget.recruitment_started_at = now
            if budget.recruitment_expired(now):
                raise TimeoutError("招募超过180秒，已停止并保留当前队伍")
            if status == "remove_member_menu":
                if not budget.can_rebuild():
                    raise RuntimeError("队伍重组已达最大次数，停止并保留当前队伍")
                click(decision.get("click"), scene, "请离一个异常成员")
                budget.record_rebuild()
            elif decision.get("remove"):
                if not budget.can_rebuild():
                    raise RuntimeError("队伍重组已达最大次数，停止并保留当前队伍")
                click(decision["remove"], scene, "请离一个异常成员")
                budget.record_rebuild()
            elif decision.get("offline"):
                click(decision["offline"], scene, "选择离线或暂离成员")
            elif decision.get("current_channel"):
                click(decision["current_channel"], scene, "选择当前频道")
            elif decision.get("create"):
                click(decision["create"], scene, "创建队伍")
            elif not state.target_configured and decision.get("target_icon"):
                click_box(decision["target_icon"], scene, "打开调整目标")
            elif decision.get("full"):
                click_box(decision.get("close"), scene, "满员后关闭队伍界面")
                state.phase = "navigate_zhongkui"
                state.navigate_not_before = now + 8.0
                budget.recruitment_started_at = None
            elif decision.get("auto_match"):
                click(decision["auto_match"], scene, "开启自动匹配")
            elif decision.get("shout") and budget.can_shout(now):
                click(decision["shout"], scene, "一键喊话")
                budget.record_shout(now)
            else:
                time.sleep(args.poll_interval)
                continue
            time.sleep(args.poll_interval)
            continue

        if status == "team_quick_panel":
            click(decision.get("create"), scene, "从快捷面板创建队伍")
            state.phase = "ensure_team"
            time.sleep(args.poll_interval)
            continue

        if status == "team_created_panel":
            click(decision.get("team"), scene, "从队长面板进入完整队伍页")
            time.sleep(args.poll_interval)
            continue

        if status == "channel_picker":
            click(decision.get("click"), scene, "选择当前频道")
            time.sleep(args.poll_interval)
            continue

        if status == "main_screen":
            if state.phase in ("ensure_team", "recruit"):
                click(decision.get("team"), scene, "打开队伍界面")
            elif state.phase == "navigate_zhongkui":
                if state.navigate_not_before and now < state.navigate_not_before:
                    time.sleep(args.poll_interval)
                    continue
                small_map = scene["templates"].get("small_map")
                if small_map:
                    click_box(small_map, scene, "打开小地图寻找钟馗")
                else:
                    raise RuntimeError("主界面未识别到小地图入口")
            elif state.phase == "run_round":
                # 自动寻路结束到战斗加载之间会短暂回到主界面。
                time.sleep(args.poll_interval)
                continue
            else:
                raise RuntimeError(f"主界面与当前阶段不一致：{state.phase}")
            time.sleep(args.poll_interval)
            continue

        state.unknown_count += 1
        if state.unknown_count >= 3:
            raise RuntimeError(
                "连续三次无法识别当前画面，停止："
                + json.dumps(scene_summary(scene), ensure_ascii=False)
            )
        time.sleep(args.poll_interval)

    raise RuntimeError(f"达到最大状态步骤：{args.max_steps}")


if __name__ == "__main__":
    raise SystemExit(main())
