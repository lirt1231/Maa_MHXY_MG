from __future__ import annotations

import json
import re


SCENE_MESSAGES = {
    "main_screen": "已找到游戏主界面，正在进入任务",
    "open_activity": "正在打开活动页面",
    "click_activity": "正在打开活动页面",
    "click_activity_toolbar": "正在打开活动页面",
    "click_activity_template": "正在打开活动页面",
    "click_activity_side_menu": "正在打开活动页面",
    "open_daily": "正在打开日常活动列表",
    "click_daily": "正在打开日常活动列表",
    "scroll_daily": "正在活动列表中查找任务",
    "join_task": "已找到任务入口，正在参加任务",
    "continue_task": "正在继续当前任务",
    "accept_dialogue": "正在领取宝图任务",
    "accept_task_dialogue": "正在领取师门任务",
    "start_task": "已领取师门任务，正在正式开始",
    "progress_task": "正在推进当前师门任务",
    "battle": "战斗进行中，正在等待结束",
    "auto_pathing": "角色正在自动寻路",
    "delivery_active": "普通运镖进行中",
    "select_normal": "正在选择普通镖银",
    "confirm_normal": "正在确认普通运镖",
    "open_welfare": "正在打开福利页面",
    "open_scratch": "正在打开每日刮刮乐",
    "scratch_ready": "刮奖页面已就绪，正在完成刮奖",
    "confirm_reward": "正在确认签到奖励",
    "welfare_idle": "正在检查今日签到状态",
    "expand_menu": "正在展开游戏菜单",
    "open_clan": "正在打开帮派页面",
    "sign_in": "正在完成帮派签到",
    "sign_in_confirmed": "帮派签到成功，正在检查分红",
    "claim_dividend": "发现可领取分红，正在领取",
    "confirm_dividend": "正在确认领取帮派分红",
    "clan_idle": "正在检查帮派签到与分红状态",
    "team_quick_panel": "正在打开队伍页面",
    "team_created_panel": "队伍已创建，正在检查队伍设置",
    "team_page": "正在检查队伍成员与设置",
    "target_dialog": "正在设置捉鬼队伍目标",
    "formation_menu": "正在选择队伍阵法",
    "channel_picker": "正在选择招募频道",
    "not_enough_members": "队伍人数不足，正在继续招募",
    "invite_members_overlay": "正在关闭邀请窗口",
    "remove_member_menu": "正在调整异常队员",
    "map_zhongkui": "正在前往钟馗",
    "world_map_changan": "正在前往长安城",
    "zhongkui_dialog": "正在领取捉鬼任务",
    "ghost_task": "正在继续当前捉鬼任务",
    "double_points_prompt": "正在确认领取双倍点数",
    "start_challenge": "正在进入秘境挑战",
    "select_stage": "正在选择当前可挑战关卡",
    "enter_battle": "正在进入秘境战斗",
    "enter_dungeon": "正在进入当前秘境",
    "open_skills": "正在打开技能页面",
    "open_auxiliary": "正在切换到辅助技能",
    "open_archaeology": "正在进入工坊考古",
    "open_production": "正在切换到技能生产",
    "switch_tier": "正在切换到二阶考古",
    "open_tool_selection": "正在打开考古工具选择",
    "open_shovel_shop": "正在打开洛阳铲购买页",
    "buy_shovel": "正在购买一个洛阳铲",
    "close_shop": "洛阳铲已购买，正在关闭杂货铺",
    "select_shovel": "正在选择洛阳铲",
    "use_shovel": "正在使用洛阳铲",
    "start_archaeology": "洛阳铲已就绪，正在开始考古",
    "dig": "正在进行考古挖掘",
    "close_temporary_bag": "正在关闭临时道具栏",
    "wait_for_dig": "正在等待下一次挖掘",
}

WAIT_MESSAGES = {
    "battle": "战斗进行中，正在等待结束",
    "battle_ocr": "战斗进行中，正在等待结束",
    "auto_pathing": "角色正在自动寻路",
    "auto_pathing_ocr": "角色正在自动寻路",
    "stage_completed": "本关已完成，正在等待下一步",
    "game_auto_asset_action": "游戏正在自动处理当前任务",
    "game_auto_continue": "游戏正在继续当前任务",
}

ENTRY_MESSAGES = {
    "answering": "已进入三界奇缘答题页面",
    "manual_answer_screen": "已确认从当前答题页面开始",
    "completed": "三界奇缘今日已完成",
    "already_completed": "三界奇缘今日已完成",
}

INTERNAL_PREFIXES = (
    "result_json=",
    "preflight_json=",
    "action_json=",
    "safe_stop_reason=",
    "read_only_error=",
    "stop_ack=",
    "recognize_only_complete=",
)


def _scene_message(status: object, reason: object = None) -> str | None:
    if status == "wait":
        return WAIT_MESSAGES.get(str(reason))
    if status in {"completed", "already_completed", "unknown"}:
        return None
    return SCENE_MESSAGES.get(str(status))


def _structured_message(task_id: str, data: dict) -> str | None:
    event = data.get("event")
    if event == "target_bound":
        return "已连接到游戏窗口"
    if event == "scene":
        return _scene_message(data.get("status"), data.get("reason"))
    if event in {"click", "drag"}:
        reason = str(data.get("reason", "")).strip()
        if not reason:
            return None
        internal_action = reason.rsplit("：", 1)[-1]
        translated = _scene_message(internal_action)
        if translated:
            return translated
        return reason if reason.startswith(("正在", "已")) else f"正在{reason}"
    if event == "round_complete":
        round_number = data.get("round")
        if isinstance(round_number, int) and round_number > 0:
            return f"第 {round_number} 轮捉鬼已完成"
        return "本轮捉鬼已完成"
    if event:
        return None

    # 科举 runner 输出的是不带 event 字段的结构化识别结果。
    status = data.get("status")
    if task_id in {"keju", "keju_huishi"}:
        if status == "ready":
            step = data.get("step")
            return (
                f"已识别第 {step} 题，正在选择答案"
                if isinstance(step, int)
                else "已识别题目，正在选择答案"
            )
        if status == "waiting":
            return "正在等待正式题目出现"
    return None


def user_progress(task_id: str, line: str) -> dict | None:
    """Translate runner output into a user-facing live progress event.

    Unknown and protocol-only lines are intentionally omitted. The original
    output remains available to the runner for result parsing and diagnostics.
    """

    text = line.strip()
    if not text or text.startswith(INTERNAL_PREFIXES):
        return None

    if text.startswith("entry_status="):
        message = ENTRY_MESSAGES.get(text.partition("=")[2])
    elif text.startswith("scene="):
        try:
            scene = json.loads(text[len("scene="):])
        except json.JSONDecodeError:
            message = None
        else:
            message = _scene_message(scene.get("status"), scene.get("reason"))
    elif text.startswith("navigation_scroll"):
        message = "正在活动列表中查找任务"
    elif text.startswith("navigation_click"):
        message = "正在进入活动"
    elif text.startswith("screencap_fallback="):
        message = "正在重新获取游戏画面"
    elif text.startswith("same_question_waiting=") or text.startswith(
        "awaiting_question_transition"
    ):
        message = "本题答案已提交，正在等待下一题"
    elif text.startswith("waiting_for_formal_question="):
        message = "正在等待正式题目出现"
    elif text.startswith("native_click") and task_id in {
        "sanjieqiyuan", "keju", "keju_huishi",
    }:
        message = "本题答案已提交，正在等待下一题"
    else:
        sanjie = re.match(r"click=\d+ completed=(\d+) status=([a-z_]+)", text)
        if task_id == "sanjieqiyuan" and sanjie and sanjie.group(2) == "ready":
            message = f"正在回答第 {int(sanjie.group(1)) + 1} 题"
        else:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                message = None
            else:
                message = (
                    _structured_message(task_id, data)
                    if isinstance(data, dict)
                    else None
                )

    if not message:
        return None
    return {
        "level": "info",
        "message": message,
        "payload": {"taskId": task_id, "stage": message},
    }
