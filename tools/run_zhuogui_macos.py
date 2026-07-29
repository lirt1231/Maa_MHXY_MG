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
from macos_cancellation import CancellationGuard, CancellationRequested  # noqa: E402
from run_sanjieqiyuan_macos import NativeMacOSInput, WindowCapture  # noqa: E402


RESULT_PREFIX = "result_json="
RESULT_STATUSES = {
    "completed",
    "already_completed",
    "limit_reached",
    "safe_stopped",
    "cancelled",
}
MAX_TARGET_LEVEL_SWIPES = 32
LEVEL_RANGES = {
    "90-115": (90, 115),
    "20-115": (20, 115),
}


def level_drag_end(current_level: int, target_level: int) -> float:
    """Return a bounded picker drag endpoint for either adjustment direction."""
    difference = current_level - target_level
    if difference == 0:
        raise ValueError("current level already matches target")
    if difference > 0:
        return 0.75 if difference > 15 else 0.45
    return 0.10 if difference < -15 else 0.34


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
    steps: int = 0
    last_ghost_progress: int | None = None


def build_result(
    status: str,
    state: WorkflowState,
    target_rounds: int,
    *,
    reason: str | None = None,
    max_steps: int | None = None,
) -> dict:
    """Build the stable, non-sensitive terminal result for one task run."""
    if status not in RESULT_STATUSES:
        raise ValueError(f"unsupported result status: {status}")
    if (
        isinstance(target_rounds, bool)
        or not isinstance(target_rounds, int)
        or target_rounds <= 0
    ):
        raise ValueError("target_rounds must be positive")
    if status in {"safe_stopped", "cancelled"} and not str(reason or "").strip():
        raise ValueError(f"{status} requires a reason")

    result = {
        "task_id": "zhuogui",
        "status": status,
        "rounds": state.completed_rounds,
        "target_rounds": target_rounds,
        "steps": state.steps,
    }
    if state.last_ghost_progress is not None:
        result["ghost_progress"] = state.last_ghost_progress
    if max_steps is not None:
        result["max_steps"] = max_steps
    if reason:
        result["reason"] = str(reason)
    return result


def emit_result(result: dict) -> None:
    """Emit the sole terminal protocol line consumed by the dashboard."""
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False), flush=True)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument(
        "--level-range",
        choices=tuple(LEVEL_RANGES),
        default="90-115",
    )
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


def recognition_detail(tasker, recognition_type, recognition_param, image, guard):
    task = tasker.post_recognition(recognition_type, recognition_param, image).wait()
    guard.checkpoint()
    if not task.succeeded:
        raise RuntimeError(f"{recognition_type} 识别任务失败")
    detail = task.get()
    if detail is None or not detail.nodes:
        return None
    return detail.nodes[-1].recognition


def template_box(tasker, image, templates, guard, threshold=0.75, roi=None):
    height, width = image.shape[:2]
    search_roi = roi or (0, 0, width, height)
    detail = recognition_detail(
        tasker,
        "TemplateMatch",
        JTemplateMatch(template=templates, roi=search_roi, threshold=[threshold]),
        image,
        guard,
    )
    if detail is None or not detail.hit:
        return None
    return tuple(detail.box)


def read_scene(tasker, capture, guard):
    image = capture.capture(guard)
    height, width = image.shape[:2]
    ocr = recognition_detail(
        tasker,
        "OCR",
        JOCR(expected=[".*"], roi=(0, 0, width, height), threshold=0.2),
        image,
        guard,
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
            guard,
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
            guard,
            0.75,
        ),
        "welfare": template_box(
            tasker,
            image,
            ["qiandao/fuli.png"],
            guard,
            0.8,
            (round(width * 0.30), 0, round(width * 0.48), round(height * 0.18)),
        ),
        "team_header": template_box(
            tasker,
            image,
            ["duiwu/duiwu-zudui.png", "duiwu/duiwu-duiwu.png"],
            guard,
            0.8,
        ),
        "target_icon": template_box(
            tasker, image, ["duiwu/duiwu-mubiao.png"], guard, 0.8
        ),
        "team_close": template_box(
            tasker, image, ["duiwu/duiwu-guanbi.png"], guard, 0.8
        ),
        "main_ready": template_box(
            tasker,
            image,
            ["zonghe/chenghao.png", "zonghe/jiahao.png"],
            guard,
            0.75,
        ),
        "small_map": template_box(
            tasker,
            image,
            ["zonghe/xiaoditu_taiyang.png", "zonghe/xiaoditu_yueliang.png"],
            guard,
            0.75,
        ),
        "generic_close": template_box(
            tasker,
            image,
            ["zonghe/guanbi3.png", "zonghe/guanbi4.png", "zonghe/guanbi2.png"],
            guard,
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
        "proposed_action": proposed_action(scene["decision"]),
        "evidence": {
            "ocr_item_count": len(scene["texts"]),
            "matched_templates": sorted(
                name for name, box in scene["templates"].items() if box is not None
            ),
        },
    }


def run_workflow(
    args,
    guard: CancellationGuard,
    state: WorkflowState | None = None,
) -> dict | None:
    state = state or WorkflowState()
    guard.checkpoint()
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
    connection = controller.post_connection().wait()
    guard.checkpoint()
    if not connection.succeeded:
        raise RuntimeError("连接梦幻西游窗口失败")

    resource = Resource()
    for bundle in (ROOT / "assets/resource/base", ROOT / "assets/resource/mac"):
        guard.checkpoint()
        loading = resource.post_bundle(bundle).wait()
        guard.checkpoint()
        if not loading.succeeded:
            raise RuntimeError(f"加载资源失败：{bundle}")
    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise RuntimeError("初始化 Maa Tasker 失败")
    guard.bind_tasker(tasker)
    guard.checkpoint()

    window_id = windows[0].hwnd
    native_input = NativeMacOSInput(window_id)
    capture = WindowCapture(controller, window_id)
    first_scene = read_scene(tasker, capture, guard)
    if args.inspect_scene:
        print(
            "preflight_json="
            + json.dumps(scene_summary(first_scene), ensure_ascii=False),
            flush=True,
        )
        return None
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
        return None

    budget = RuntimeBudget(
        recruitment_timeout=args.recruit_timeout,
        max_rebuilds=args.max_rebuilds,
    )
    target_min_level, target_max_level = LEVEL_RANGES[args.level_range]

    def click(point, scene, reason):
        if point is None:
            raise RuntimeError(f"{reason} 缺少经过识别的点击位置")
        screen_point = guard.perform_input(
            native_input.click,
            *point,
            scene["image_size"],
        )
        log_event(
            "click",
            reason=reason,
            source=point,
            screen=screen_point,
            phase=state.phase,
        )

    def click_box(box, scene, reason):
        click(box_center(box) if box else None, scene, reason)

    for step in range(1, args.max_steps + 1):
        state.steps = step
        guard.checkpoint()
        now = time.monotonic()
        scene = read_scene(tasker, capture, guard)
        decision = scene["decision"]
        status = decision["status"]
        progress = decision.get("progress")
        if isinstance(progress, int) and not isinstance(progress, bool) and progress >= 0:
            state.last_ghost_progress = progress
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

        if status == "already_completed":
            return build_result("already_completed", state, args.rounds)

        if status == "double_points_prompt":
            if state.double_claims_this_round >= 1:
                raise RuntimeError("本轮重复出现双倍领取弹窗，停止避免重复消耗")
            click(decision.get("confirm"), scene, "领取100点双倍")
            state.double_claims_this_round += 1
            guard.wait(args.poll_interval)
            continue

        if status == "not_enough_members":
            click(decision.get("cancel"), scene, "取消人数不足弹窗")
            state.phase = "recruit"
            if budget.recruitment_started_at is None:
                budget.recruitment_started_at = now
            guard.wait(args.poll_interval)
            continue

        if status == "invite_members_overlay":
            click_box(decision.get("close"), scene, "关闭邀请成员窗口")
            guard.wait(args.poll_interval)
            continue

        if status == "welfare_overlay":
            click(decision.get("close"), scene, "关闭已知福利弹窗")
            guard.wait(args.poll_interval)
            continue

        if status == "round_complete":
            action = round_completion_action(state.completed_rounds, args.rounds)
            state.completed_rounds += 1
            log_event("round_complete", round=state.completed_rounds, action=action)
            if action == "finish":
                click(decision.get("cancel"), scene, "达到目标轮数，取消继续")
                log_event("completed", rounds=state.completed_rounds)
                return build_result("completed", state, args.rounds)
            click(decision.get("confirm"), scene, "继续下一轮")
            state.phase = "recruit"
            state.double_claims_this_round = 0
            state.round_started_at = None
            budget.recruitment_started_at = now
            guard.wait(args.poll_interval)
            continue

        if status == "battle":
            if budget.battle_started_at is None:
                budget.battle_started_at = now
            elif budget.battle_expired(now):
                raise TimeoutError("单场战斗超过180秒，已停止")
            guard.wait(args.poll_interval)
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
            guard.wait(args.poll_interval)
            continue

        if status == "zhongkui_dialog":
            click(decision.get("click"), scene, "接取捉鬼任务")
            state.phase = "run_round"
            state.round_started_at = now
            guard.wait(args.poll_interval)
            continue

        if status in ("map_zhongkui", "world_map_changan"):
            click(decision.get("click"), scene, status)
            state.phase = "navigate_zhongkui"
            guard.wait(2.0)
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
                and (
                    decision.get("current_max_level") != target_max_level
                    or (
                        decision.get("current_min_level") is not None
                        and decision.get("current_min_level") < target_min_level
                    )
                )
            ):
                click(decision["range_90_115"], scene, "选择90-115预设区间")
                state.target_high_range_clicked = True
            elif (
                decision.get("current_min_level") == target_min_level
                and decision.get("current_max_level") == target_max_level
                and decision.get("confirm")
            ):
                click(
                    decision.get("confirm"),
                    scene,
                    f"确认{args.level_range}抓鬼目标",
                )
                state.target_configured = True
                state.phase = "recruit"
                budget.recruitment_started_at = now
            elif state.target_level_swipes < MAX_TARGET_LEVEL_SWIPES:
                width, height = scene["image_size"]
                current_min = decision.get("current_min_level")
                if current_min is None:
                    raise RuntimeError("未能读取最低等级，停止避免盲目拖动")
                drag_end_y = level_drag_end(current_min, target_min_level)
                guard.perform_input(
                    native_input.drag,
                    relative_point((0.623, 0.395), (width, height)),
                    relative_point((0.623, drag_end_y), (width, height)),
                    (width, height),
                )
                guard.checkpoint()
                state.target_level_swipes += 1
                log_event(
                    "drag",
                    reason=f"调整最低等级至{target_min_level}",
                    current_min=current_min,
                    count=state.target_level_swipes,
                )
            else:
                raise RuntimeError(
                    f"调整{args.level_range}等级范围超过"
                    f"{MAX_TARGET_LEVEL_SWIPES}次，停止"
                )
            guard.wait(args.poll_interval)
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
                guard.wait(args.poll_interval)
                continue
            guard.wait(args.poll_interval)
            continue

        if status == "team_quick_panel":
            click(decision.get("create"), scene, "从快捷面板创建队伍")
            state.phase = "ensure_team"
            guard.wait(args.poll_interval)
            continue

        if status == "team_created_panel":
            click(decision.get("team"), scene, "从队长面板进入完整队伍页")
            guard.wait(args.poll_interval)
            continue

        if status == "channel_picker":
            click(decision.get("click"), scene, "选择当前频道")
            guard.wait(args.poll_interval)
            continue

        if status == "main_screen":
            if state.phase in ("ensure_team", "recruit"):
                click(decision.get("team"), scene, "打开队伍界面")
            elif state.phase == "navigate_zhongkui":
                if state.navigate_not_before and now < state.navigate_not_before:
                    guard.wait(args.poll_interval)
                    continue
                small_map = scene["templates"].get("small_map")
                if small_map:
                    click_box(small_map, scene, "打开小地图寻找钟馗")
                else:
                    raise RuntimeError("主界面未识别到小地图入口")
            elif state.phase == "run_round":
                # 自动寻路结束到战斗加载之间会短暂回到主界面。
                guard.wait(args.poll_interval)
                continue
            else:
                raise RuntimeError(f"主界面与当前阶段不一致：{state.phase}")
            guard.wait(args.poll_interval)
            continue

        state.unknown_count += 1
        if state.unknown_count >= 3:
            raise RuntimeError("连续三次无法识别当前画面，已停止")
        guard.wait(args.poll_interval)

    raise RuntimeError(
        f"达到最大状态步骤 {args.max_steps}，"
        f"仅完成 {state.completed_rounds}/{args.rounds} 轮，已安全停止"
    )


def main() -> int:
    guard = CancellationGuard()
    guard.install_signal_handlers()
    args = parse_args()
    state = WorkflowState()
    try:
        result = run_workflow(args, guard, state)
        if args.inspect_scene or args.recognize_only:
            return 0
        if result is None:
            raise RuntimeError("捉鬼任务没有返回结构化最终结果")
        emit_result(result)
        return 0
    except CancellationRequested as exc:
        reason = str(exc) or "收到停止请求"
        try:
            guard.wait_for_tasker_stop()
        except Exception as stop_exc:
            reason = f"{reason}；停止确认失败：{stop_exc}"
        if not (args.inspect_scene or args.recognize_only):
            emit_result(
                build_result(
                    "cancelled",
                    state,
                    args.rounds,
                    reason=reason,
                )
            )
        log_event("stop_ack", status="cancelled", reason=reason)
        return 0
    except Exception as exc:
        if not (args.inspect_scene or args.recognize_only):
            emit_result(
                build_result(
                    "safe_stopped",
                    state,
                    args.rounds,
                    reason=str(exc) or "捉鬼任务异常停止",
                    max_steps=args.max_steps,
                )
            )
        print(f"safe_stop_reason={exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
