from __future__ import annotations

from copy import deepcopy


COMMON_CHECKLIST = [
    "游戏已经登录，窗口完整可见且没有被其他窗口遮挡",
    "已为运行终端授予“屏幕与系统音频录制”和“辅助功能”权限",
    "运行期间可以让自动化激活并操作游戏窗口",
]


TASKS = {
    "sanjieqiyuan": {
        "id": "sanjieqiyuan",
        "name": "三界奇缘",
        "eyebrow": "每日答题",
        "summary": "使用本地题库识别题目并完成三界奇缘答题。",
        "description": "可从可识别的主界面自动进入活动，也可以在手动打开答题页后直接接管。题目未变化或无法可靠识别时会停止，避免重复点击。",
        "status": "experimental",
        "steps": [
            "确认三界奇缘当前可参与。",
            "自动导航模式请停留在主界面；手动模式请先打开答题页。",
            "选择最多答题数并启动，运行期间不要移动或遮挡窗口。",
        ],
        "checklist": COMMON_CHECKLIST
        + ["三界奇缘当前可参与，且已按所选模式准备好游戏页面"],
        "warnings": ["识别不到题目或题目画面没有变化时会安全停止。"],
        "parameters": [
            {
                "key": "maxSteps",
                "label": "最多答题数",
                "type": "integer",
                "default": 10,
                "minimum": 1,
                "maximum": 20,
            },
            {
                "key": "navigation",
                "label": "进入方式",
                "type": "select",
                "default": "auto",
                "choices": [
                    {"value": "auto", "label": "从主界面自动进入"},
                    {"value": "manual", "label": "已手动打开答题页"},
                ],
            },
        ],
    },
    "keju": {
        "id": "keju",
        "name": "科举乡试",
        "eyebrow": "乡试 / 会试",
        "summary": "本地题库 Top 3 配合模型复核，低置信度时保留现场。",
        "description": "启动前必须手动打开正式答题页。未配置模型时仅使用高置信度本地题库兜底，不能可靠判断时停止而不是盲选。",
        "status": "experimental",
        "steps": [
            "在游戏中手动进入乡试或会试的正式答题页面。",
            "选择答题来源；兼容 API 的密钥应存放在 macOS Keychain。",
            "确认题目和 A/B/C/D 选项清晰可见后启动。",
        ],
        "checklist": COMMON_CHECKLIST
        + ["已经进入乡试或会试的正式答题页面，题目与四个选项可见"],
        "warnings": [
            "模型失败且本地题库置信度不足时会停止并保留当前页面。",
            "API Key 不应填写在页面或日志中。",
        ],
        "parameters": [
            {
                "key": "maxSteps",
                "label": "最多答题数",
                "type": "integer",
                "default": 20,
                "minimum": 1,
                "maximum": 30,
            },
            {
                "key": "answerSource",
                "label": "答题来源",
                "type": "select",
                "default": "configured",
                "choices": [
                    {"value": "configured", "label": "本地题库 + 已配置兼容模型"},
                    {"value": "codex", "label": "本地题库 + Codex 复核"},
                ],
            },
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
        "steps": [
            "确认角色可以创建或加入队伍，背包与弹窗不会遮挡主界面。",
            "填写实际希望完成的轮数；页面数值不会沿用旧流程的加一规则。",
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
            }
        ],
    },
}


def task_list() -> list[dict]:
    return [deepcopy(task) for task in TASKS.values()]


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
