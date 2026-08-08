from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime


COMMON_CHECKLIST = [
    "游戏已经登录，窗口完整可见且没有被其他窗口遮挡",
    "已为运行终端授予“屏幕与系统音频录制”和“辅助功能”权限",
    "运行期间可以让自动化激活并操作游戏窗口",
]

TASK_CATEGORIES = {
    "daily": {"id": "daily", "label": "日常任务", "order": 10},
    "weekly": {"id": "weekly", "label": "周常任务", "order": 20},
    "repeatable": {"id": "repeatable", "label": "常驻任务", "order": 30},
}


TASKS = {
    "sanjieqiyuan": {
        "id": "sanjieqiyuan",
        "name": "三界奇缘",
        "eyebrow": "每日答题",
        "summary": "使用本地题库识别题目并完成三界奇缘答题。",
        "description": "从游戏主界面启动后，自动点击“活动”、进入三界奇缘任务界面并完成答题。题目未变化或无法可靠识别时会停止，避免重复点击。",
        "status": "experimental",
        "category": "daily",
        "taskOrder": 30,
        "risk": {"level": "low", "label": "低风险", "summary": "仅答题，不消耗角色资产"},
        "steps": [
            "确认三界奇缘当前可参与。",
            "停留在游戏主界面，自动化会点击“活动”并进入三界奇缘。",
            "启动固定 10 题答题，运行期间不要移动或遮挡窗口。",
        ],
        "checklist": COMMON_CHECKLIST
        + ["三界奇缘当前可参与，且当前画面是无遮挡的游戏主界面"],
        "warnings": ["识别不到题目或题目画面没有变化时会安全停止。"],
        "parameters": [],
    },
    "keju": {
        "id": "keju",
        "name": "科举乡试",
        "eyebrow": "工作日 17:00",
        "summary": "本地题库 Top 3 配合模型复核，低置信度时保留现场。",
        "description": "科举乡试仅在周一至周五 17:00 后开放。请从游戏主界面启动，系统先记录任务前资源；随后手动进入正式答题页，系统才会开始答题。",
        "status": "experimental",
        "category": "daily",
        "taskOrder": 40,
        "schedule": {
            "weekdays": [0, 1, 2, 3, 4],
            "startTime": "17:00",
            "label": "周一至周五 17:00 后开放",
            "unavailableReason": "科举乡试仅在周一至周五 17:00 后开放，请在开放时间再运行。",
        },
        "risk": {"level": "low", "label": "低风险", "summary": "仅答题，不消耗角色资产"},
        "steps": [
            "周一至周五 17:00 后，保持无遮挡的游戏主界面并启动任务。",
            "等待任务前资源记录完成后，手动进入科举乡试正式答题页面。",
            "系统确认第 1 题与四个选项后使用本地题库和模型答题。",
        ],
        "checklist": COMMON_CHECKLIST
        + [
            "当前是周一至周五 17:00 后",
            "当前是无遮挡的游戏主界面，便于记录任务前资源",
        ],
        "warnings": [
            "非周一至周五 17:00 后不能检测或启动科举乡试。",
            "模型失败且本地题库置信度不足时会停止并保留当前页面。",
            "API Key 不应填写在页面或日志中。",
        ],
        "parameters": [],
    },
    "keju_huishi": {
        "id": "keju_huishi",
        "name": "科举会试",
        "eyebrow": "每周六会试",
        "summary": "严格确认会试页面后，使用本地题库与模型复核完成答题。",
        "description": "科举会试仅在每周六开放。进入会试页面后启动，系统确认第 1 题后开始答题；答完仅从结算页记录本次金币奖励。",
        "status": "experimental",
        "category": "weekly",
        "taskOrder": 10,
        "schedule": {
            "weekdays": [5],
            "label": "仅每周六开放",
            "unavailableReason": "科举会试仅在每周六开放，请在周六再运行。",
        },
        "risk": {"level": "low", "label": "低风险", "summary": "仅答题，不消耗角色资产"},
        "steps": [
            "仅在周六进入科举会试页面后启动任务。",
            "系统确认第 1 题与四个选项后使用本地题库和模型答题。",
            "完成答题后，系统只读识别结算页中的本次金币奖励。",
        ],
        "checklist": COMMON_CHECKLIST
        + [
            "今天是周六",
            "当前处于科举会试活动中，或即将进入正式第 1 题",
        ],
        "warnings": [
            "非周六不能检测或启动科举会试。",
            "无法确认当前是会试页面时会停止，不会按乡试坐标尝试点击。",
            "模型失败且本地题库置信度不足时会停止并保留当前页面。",
            "结算页未同时确认会试身份和金币数时，不会猜测或写入奖励。",
        ],
        "parameters": [
            {
                "key": "waitForRoundOne",
                "label": "等待正式第 1 题",
                "type": "boolean",
                "default": True,
            },
        ],
    },
    "zhuogui": {
        "id": "zhuogui",
        "name": "捉鬼任务",
        "eyebrow": "组队循环",
        "summary": "自动组队、匹配并按设定轮数完成捉鬼。",
        "description": "支持从主界面开始，也兼容已经领取捉鬼任务的状态。首版只支持有限正整数轮数。",
        "status": "experimental",
        "category": "repeatable",
        "taskOrder": 10,
        "risk": {"level": "medium", "label": "中风险", "summary": "包含组队、战斗和双倍点数确认"},
        "steps": [
            "确认角色可以创建或加入队伍，背包与弹窗不会遮挡主界面。",
            "填写实际希望完成的轮数，并选择队伍等级范围和阵法。",
            "启动后允许自动化进行匹配、队伍重组和双倍领取确认。",
        ],
        "checklist": COMMON_CHECKLIST
        + ["角色可以正常组队，并接受自动匹配及必要的队伍重组"],
        "warnings": [
            "同一轮重复出现双倍领取提示时会停止，避免重复消耗。",
            "队伍重组超过安全上限后会停止并保留现场。",
        ],
        "parameters": [
            {
                "key": "rounds",
                "label": "实际执行轮数",
                "type": "integer",
                "default": 2,
                "minimum": 1,
                "maximum": 100,
            },
            {
                "key": "levelRange",
                "label": "队伍等级范围",
                "type": "select",
                "default": "90-115",
                "choices": [
                    {"value": "90-115", "label": "90-115（默认）"},
                    {"value": "20-115", "label": "20-115"},
                ],
            },
            {
                "key": "formation",
                "label": "队伍阵法",
                "type": "select",
                "default": "current",
                "choices": [
                    {"value": "current", "label": "保持当前阵法（默认）"},
                    {"value": "天覆阵", "label": "天覆阵"},
                    {"value": "地载阵", "label": "地载阵"},
                    {"value": "风扬阵", "label": "风扬阵"},
                    {"value": "云垂阵", "label": "云垂阵"},
                    {"value": "龙飞阵", "label": "龙飞阵"},
                    {"value": "虎翼阵", "label": "虎翼阵"},
                    {"value": "鸟翔阵", "label": "鸟翔阵"},
                    {"value": "蛇蟠阵", "label": "蛇蟠阵"},
                    {"value": "鹰啸阵", "label": "鹰啸阵"},
                    {"value": "雷绝阵", "label": "雷绝阵"},
                ],
            },
        ],
    },
    "fuli_qiandao": {
        "id": "fuli_qiandao",
        "name": "福利签到",
        "eyebrow": "每日领取",
        "summary": "识别福利入口并完成每日刮刮乐签到。",
        "description": "从主界面进入福利页，只在识别到“打开刮刮乐”和刮奖区域后执行。首版不领取累计 2/5/7 次奖励。",
        "status": "experimental",
        "category": "daily",
        "taskOrder": 10,
        "risk": {"level": "low", "label": "低风险", "summary": "仅领取每日签到奖励，不购买或消耗资产"},
        "steps": [
            "保持游戏主界面完整可见，关闭遮挡福利入口的弹窗。",
            "启动后会进入福利页并完成今日刮刮乐。",
            "已签到、页面不明确或识别连续失败时会停止，不会尝试累计奖励。",
        ],
        "checklist": COMMON_CHECKLIST
        + ["允许自动化领取今日签到奖励，且福利入口当前可见"],
        "warnings": [
            "首版仅完成每日刮刮乐，不领取累计签到奖励。",
            "未识别到刮奖区域时不会执行拖动。",
        ],
        "parameters": [],
    },
    "bangpai_qiandao": {
        "id": "bangpai_qiandao",
        "name": "帮派签到",
        "eyebrow": "帮派日常",
        "summary": "完成每日帮派签到，并领取当前可领的帮派分红。",
        "description": "从主界面进入帮派福利，只在独立识别签到、分红和确认按钮后执行。",
        "status": "experimental",
        "category": "daily",
        "taskOrder": 20,
        "risk": {"level": "low", "label": "低风险", "summary": "仅签到并领取可用分红，不购买或消耗资产"},
        "steps": [
            "保持游戏主界面完整可见，关闭遮挡右下菜单的弹窗。",
            "启动后会完成帮派签到，并领取当前可用的帮派分红。",
            "页面不明确或任一按钮点击后未变化时会停止。",
        ],
        "checklist": COMMON_CHECKLIST
        + ["角色已加入帮派，且允许自动化完成签到和领取可用分红"],
        "warnings": [
            "只处理帮派福利页右下方的“领取分红”和随后出现的确认按钮。",
            "签到、分红和确认按钮各最多点击一次，页面未变化时会保留现场。",
        ],
        "parameters": [],
    },
    "baotu": {
        "id": "baotu",
        "name": "宝图任务",
        "eyebrow": "每日获得",
        "summary": "领取宝图任务后由游戏自动完成，控制台只读监控到明确完成。",
        "description": "从主界面进入日常活动并领取宝图任务；游戏接管后控制台不再点击，只持续观察到明确完成证据，再返回主界面。只获得宝图，不自动挖宝。",
        "status": "experimental",
        "category": "daily",
        "taskOrder": 50,
        "risk": {"level": "low", "label": "低风险", "summary": "只获得宝图，不使用背包宝图或购买资产"},
        "steps": [
            "保持游戏主界面完整可见，关闭遮挡活动入口的弹窗。",
            "启动后会在日常活动列表中查找宝图任务并领取。",
            "任务接取后由游戏自动寻路和战斗，控制台只读监控到明确完成。",
        ],
        "checklist": COMMON_CHECKLIST
        + ["宝图任务当前可参与，且允许自动化领取并完成今日宝图任务"],
        "warnings": [
            "本功能只获得宝图，不会打开背包或挖掘宝图。",
            "进入自动执行后不会点击任务栏；长时间没有明确完成证据时会保留现场。",
        ],
        "parameters": [],
    },
    "yunbiao": {
        "id": "yunbiao",
        "name": "普通运镖",
        "eyebrow": "单人日常",
        "summary": "固定完成 3 次普通运镖，只选择“押送普通镖银”。",
        "description": "从主界面进入日常活动，只在严格识别普通镖选项及随后确认按钮时点击。运镖途中、战斗和自动寻路期间仅等待。",
        "status": "experimental",
        "category": "daily",
        "taskOrder": 70,
        "risk": {"level": "medium", "label": "中风险", "summary": "普通运镖可能消耗活力，但不会选择其他镖种"},
        "steps": [
            "保持游戏主界面完整可见，并确认角色具备普通运镖所需条件。",
            "启动后固定执行每日 3 次普通运镖。",
            "启动后仅接受普通镖，看到运镖进行中证据后等待每轮结束。",
        ],
        "checklist": COMMON_CHECKLIST
        + ["允许自动化消耗普通运镖所需活力，并确认只执行普通镖"],
        "warnings": [
            "活力不足、银币不足或只识别到其他镖种时会安全停止。",
            "每轮普通镖选项只点击一次，任务栏最多点击 12 次。",
        ],
        "parameters": [],
    },
    "shimen": {
        "id": "shimen",
        "name": "师门任务",
        "eyebrow": "自动领取",
        "summary": "领取并启动师门任务，随后只读监控到游戏明确完成。",
        "description": "从主界面、日常活动页或已有师门任务状态开始，完成必要的领取和首次启动操作；游戏接管后不再点击，持续观察到明确完成证据，再返回主界面。",
        "status": "experimental",
        "category": "daily",
        "taskOrder": 60,
        "risk": {
            "level": "medium",
            "label": "中风险",
            "summary": "游戏可能按师门流程自动购买或上交物品，控制台不会点击资产控件",
        },
        "steps": [
            "保持游戏主界面完整可见，关闭无关弹窗。",
            "启动后会进入师门任务、完成领取，并点击右侧师门任务正式开始。",
            "游戏接管后控制台只读监控寻路、战斗和交付，直到明确完成。",
        ],
        "checklist": COMMON_CHECKLIST
        + ["接受领取并启动师门任务，以及游戏按师门流程自动购买或上交物品"],
        "warnings": [
            "首次启动后不会继续点击任务栏、“去完成”、购买、上交或最终考核控件；这些步骤完全由游戏处理。",
            "没有明确完成证据时不会提前返回主界面或统计任务后资源。",
            "领取阶段连续重复同一按钮或超过点击上限时会安全停止。",
        ],
        "parameters": [],
    },
    "mijingxiangyao": {
        "id": "mijingxiangyao",
        "name": "秘境降妖",
        "eyebrow": "单人挑战",
        "summary": "沿用游戏当前已选择的秘境模式推进战斗，资产操作和付费恢复一律保留现场。",
        "description": "从主界面、日常活动页或已有秘境挑战状态开始，沿用游戏当前已选择的秘境模式，不自动切换海底秘境或日之井。战斗期间仅等待，不会购买药品、购买或更换助战，也不会在挑战失败后付费恢复。",
        "status": "experimental",
        "category": "daily",
        "taskOrder": 80,
        "risk": {
            "level": "medium",
            "label": "中风险",
            "summary": "包含连续自动战斗；药品、助战购买及付费恢复默认禁止",
        },
        "steps": [
            "保持游戏主界面完整可见，关闭无关弹窗，并确认秘境降妖当前可参与。",
            "启动后沿用当前秘境模式，持续推进到明确完成状态。",
            "战斗期间自动等待；遇到药品、助战购买、更换助战或付费恢复页面时安全停止。",
        ],
        "checklist": COMMON_CHECKLIST
        + [
            "确认游戏已选择所需秘境模式，并接受在当前模式中连续自动战斗",
            "理解战斗失败或资产操作需要手动处理，自动化不会切换秘境模式",
            "确认自动化不得购买药品、购买或更换助战，也不得执行任何付费恢复",
        ],
        "warnings": [
            "自动化沿用游戏当前已选择的秘境模式，不会主动切换模式；模式选择页需要手动处理。",
            "自动化不购买或使用药品，不购买或更换助战，也不会在失败后执行付费恢复。",
            "安全步数超限、重复页面或证据不足时会安全停止。",
        ],
        "parameters": [],
    },
    "fun_news": {
        "id": "fun_news",
        "name": "趣闻鉴赏",
        "eyebrow": "每日互动",
        "summary": "在趣闻动态中点赞 5 条，每次滚动加载下一条后再继续。",
        "description": "从主界面进入日常活动的趣闻鉴赏。仅在心形点赞控件被模板确认后点赞一次；每次点赞后会向下滑动加载新的动态，累计 5 次后必须识别到完成状态才会返回主界面。",
        "status": "experimental",
        "category": "daily",
        "taskOrder": 90,
        "risk": {"level": "low", "label": "低风险", "summary": "仅对趣闻动态点赞，不涉及购买或角色资产"},
        "steps": [
            "保持游戏主界面完整可见，关闭遮挡活动入口的弹窗。",
            "启动后会进入日常活动的趣闻鉴赏并点击参加。",
            "自动点赞 5 条动态；每次点赞后会滚动加载下一条，运行期间不要移动或操作游戏窗口。",
        ],
        "checklist": COMMON_CHECKLIST
        + ["趣闻鉴赏当前可参与，且允许自动化完成 5 次动态点赞"],
        "warnings": [
            "只会点击模板确认的心形点赞控件；同一条动态重复出现时会停止。",
            "点赞 5 次后仍未识别到完成状态时会保留现场，不会继续输入。",
        ],
        "parameters": [],
    },
    "kaogu": {
        "id": "kaogu",
        "name": "工坊考古",
        "eyebrow": "常驻生产",
        "summary": "自动完成 10 次考古，并将非真品古董兑换为银币。",
        "description": "推荐先在游戏的工坊考古生产页手动选好工具，再从当前页面启动；完成 10 次挖掘后会进入包裹，前往古董商并自动出售非真品古董，以便统计本次获得的银币。真品始终保留。",
        "guideImage": {
            "src": "/task-guides/kaogu-selected-tool-start.jpg",
            "alt": "工坊考古生产页，工具已选好且考古按钮可见",
            "caption": "从这个页面启动：先手动选好工具，确认工具数量足够，并保持“考古”按钮可见。",
        },
        "status": "experimental",
        "category": "repeatable",
        "taskOrder": 20,
        "risk": {
            "level": "medium",
            "label": "中风险",
            "summary": "会消耗考古活力并出售非真品古董；真品始终保留",
        },
        "steps": [
            "推荐在游戏中进入工坊考古生产页，手动选好本次使用的工具。",
            "保持已选工具及“考古”按钮可见，再选择“使用游戏中已选工具”。",
            "完成 10 次挖掘后自动进入包裹，前往古董商并出售所有非真品古董。",
            "遇到真品出售确认时只会取消，随后返回游戏主界面并统计银币变化。",
        ],
        "checklist": COMMON_CHECKLIST
        + [
            "已按所选工具方式准备页面：手动模式已选好工具；自动模式允许购买一个洛阳铲",
            "角色具备 10 次考古所需活力，且接受自动化连续完成 10 次挖掘",
            "包裹内的非真品古董将被自动出售，真品会保留",
        ],
        "warnings": [
            "使用游戏中已选工具时，脚本不会打开工具选择或购买页面；起始画面不符合提示时会停止。",
            "洛阳铲购买硬限制为一次；无法同时识别商品与购买按钮时不会购买。",
            "一旦发起购买，本轮购买保护会跨进程保留；任务中断后不会再次购买，考古和售卖全部完成后才自动释放。",
            "挖掘硬限制为 10 次；页面连续不明确或按钮长时间未出现时会安全停止。",
            "只识别并出售非真品古董；真品确认框只会点击取消，绝不会确认出售。",
            "售卖页面、按钮或古董模板不明确时会停止并保留现场，不会猜测点击。",
        ],
        "parameters": [
            {
                "key": "toolMode",
                "label": "工具方式",
                "type": "select",
                "default": "selected",
                "choices": [
                    {
                        "value": "selected",
                        "label": "使用游戏中已选工具（推荐）",
                    },
                    {
                        "value": "luoyang_shovel",
                        "label": "自动选择洛阳铲（必要时购买）",
                    },
                ],
            }
        ],
    },
}


def task_availability(task_id: str, today: date | datetime | None = None) -> dict:
    if task_id not in TASKS:
        raise ValueError("不支持的任务")
    schedule = TASKS[task_id].get("schedule")
    if schedule is None:
        return {"available": True, "label": None, "reason": None}

    local_datetime = None
    if today is None:
        local_datetime = datetime.now().astimezone()
        local_date = local_datetime.date()
    elif isinstance(today, datetime):
        local_datetime = today.astimezone() if today.tzinfo else today
        local_date = local_datetime.date()
    else:
        # A date-only value asks whether the task opens on that calendar day;
        # runtime start/inspection always call this with the current datetime.
        local_date = today
    available = local_date.weekday() in schedule["weekdays"]
    start_time = schedule.get("startTime")
    if available and start_time and local_datetime is not None:
        hour, minute = (int(part) for part in start_time.split(":", 1))
        available = (local_datetime.hour, local_datetime.minute) >= (hour, minute)
    return {
        "available": available,
        "label": schedule["label"],
        "reason": None if available else schedule["unavailableReason"],
    }


def ensure_task_available(task_id: str, today: date | datetime | None = None) -> None:
    availability = task_availability(task_id, today)
    if not availability["available"]:
        raise ValueError(availability["reason"])


def task_list(today: date | datetime | None = None) -> list[dict]:
    items = []
    for task in TASKS.values():
        item = deepcopy(task)
        category_id = item["category"]
        item["category"] = deepcopy(TASK_CATEGORIES[category_id])
        item["availability"] = task_availability(task["id"], today)
        items.append(item)
    items.sort(
        key=lambda item: (
            item["category"]["order"],
            item["taskOrder"],
            item["name"],
        )
    )
    return items


def validate_options(task_id: str, supplied: dict | None) -> dict:
    if task_id not in TASKS:
        raise ValueError("不支持的任务")
    supplied = supplied or {}
    parameters = {item["key"]: item for item in TASKS[task_id]["parameters"]}
    unknown = set(supplied) - set(parameters)
    if unknown:
        raise ValueError(f"未知参数：{', '.join(sorted(unknown))}")

    result = {}
    for key, parameter in parameters.items():
        value = supplied.get(key, parameter.get("default"))
        kind = parameter["type"]
        if kind == "integer":
            if isinstance(value, bool):
                raise ValueError(f"{parameter['label']}必须是整数")
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{parameter['label']}必须是整数") from exc
            if value < parameter["minimum"] or value > parameter["maximum"]:
                raise ValueError(
                    f"{parameter['label']}必须在 {parameter['minimum']}–{parameter['maximum']} 之间"
                )
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{parameter['label']}必须是布尔值")
        elif kind == "select":
            allowed = {choice["value"] for choice in parameter["choices"]}
            if value not in allowed:
                raise ValueError(f"{parameter['label']}选项无效")
        result[key] = value
    return result
