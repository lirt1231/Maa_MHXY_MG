"""Platform-neutral 帮派签到 and dividend workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

try:
    from .automation_actions import ActionMetrics, EvidenceClickAction
    from .automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from .bangpai_qiandao_recognition import classify_clan_sign_in_scene
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import ActionMetrics, EvidenceClickAction
    from automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from bangpai_qiandao_recognition import classify_clan_sign_in_scene


CLAN_SIGN_IN_TEMPLATES = {
    "expand_menu_live": {
        "templates": ["zonghe/jiahao_live_923.png"],
        "threshold": 0.80,
    },
    "activity_toolbar": [
        "zonghe/huodong1.png",
        "zonghe/huodong2.png",
        "zonghe/huodong_live_923.png",
    ],
    "clan_header": [
        "qiandao/bangpai_jiemian_panduan.png",
        "qiandao/bangpai_jiemian_panduan2.png",
    ],
    "clan_welfare": ["qiandao/bangpaifuli.png"],
    "welfare_header": ["qiandao/fuli.png"],
}


@dataclass(frozen=True)
class ClanSignInWorkflow:
    max_steps: int = 16
    classifier: Callable[..., Mapping[str, Any]] = field(
        default=classify_clan_sign_in_scene,
        repr=False,
        compare=False,
    )

    def __post_init__(self):
        if self.max_steps <= 0:
            raise ValueError("帮派签到最大识别步骤必须是正整数")

    def execute(self, context: WorkflowContext, *, inspect: bool = False) -> WorkflowOutcome:
        phase = "enter"
        unknown_polls = 0
        idle_polls = 0
        metrics = ActionMetrics()

        def click_action(status, name, reason, *, wait_seconds=0.8, repeat_limit=None, repeat_error=None):
            return EvidenceClickAction(
                name=name,
                statuses=(status,),
                reason=reason,
                wait_seconds=wait_seconds,
                repeat_limit=repeat_limit or self.max_steps,
                repeat_error=repeat_error,
                signature_factory=lambda action_context: action_context.decision_status,
            )

        actions = {
            "expand_menu": click_action(
                "expand_menu",
                "expand_main_menu",
                "展开主界面菜单",
                repeat_limit=2,
                repeat_error="主界面菜单连续两次未能展开，已安全停止",
            ),
            "close_known_welfare_overlay": click_action(
                "close_known_welfare_overlay",
                "close_known_welfare_overlay",
                "关闭上一任务遗留的福利页面",
                wait_seconds=1.5,
                repeat_limit=1,
                repeat_error="福利页面关闭后仍未变化，已安全停止",
            ),
            "open_clan": click_action("open_clan", "open_clan", "打开帮派"),
            "open_welfare": click_action("open_welfare", "open_clan_welfare", "打开帮派福利"),
            "sign_in": click_action(
                "sign_in",
                "clan_sign_in",
                "执行帮派签到",
                repeat_limit=1,
                repeat_error="帮派签到按钮点击后仍未变化，已安全停止",
            ),
            "claim_dividend": click_action(
                "claim_dividend",
                "claim_clan_dividend",
                "领取帮派分红",
                repeat_limit=1,
                repeat_error="帮派分红按钮点击后仍未变化，已安全停止",
            ),
            "confirm_dividend": click_action(
                "confirm_dividend",
                "confirm_clan_dividend",
                "确认领取帮派分红",
                repeat_limit=1,
                repeat_error="帮派分红确认后页面仍未变化，已安全停止",
            ),
        }

        for step in range(1, self.max_steps + 1):
            scene = context.runtime.read_scene(CLAN_SIGN_IN_TEMPLATES)
            decision = self.classifier(scene.texts, scene.templates, phase, scene.image_size)
            status = decision["status"]
            context.emit("scene", step=step, phase=phase, status=status)
            if inspect:
                return WorkflowInspection.from_scene(
                    scene,
                    decision,
                    context.inspection_evidence(scene, decision),
                )

            unknown_polls = unknown_polls + 1 if status == "unknown" else 0
            idle_polls = idle_polls + 1 if status == "clan_idle" else 0
            if status in {"completed", "already_completed"}:
                return WorkflowResult(
                    status,
                    {"actions": metrics.actions, "clicks": metrics.clicks},
                )
            if status == "no_clan":
                raise RuntimeError("当前角色未加入帮派，无法执行帮派签到")
            if status == "sign_in_confirmed":
                phase = "check_dividend"
                context.waiter.wait(0.8)
                continue

            action = actions.get(status)
            if action is not None:
                metrics.record(
                    action.execute(context.action_context(scene, decision, "帮派签到"))
                )
                phase = {
                    "expand_menu": "menu",
                    "close_known_welfare_overlay": "enter",
                    "open_clan": "clan",
                    "open_welfare": "welfare",
                    "sign_in": "verify_sign_in",
                    "claim_dividend": "dividend_confirm",
                    "confirm_dividend": "verify_dividend",
                }[status]
                continue
            if status == "clan_idle" and idle_polls >= 4:
                raise RuntimeError("帮派页面未显示可识别的福利入口，已安全停止")
            if status == "unknown" and unknown_polls >= 3:
                raise RuntimeError("连续三次无法识别帮派签到页面，已停止以避免无证据点击")
            context.waiter.wait(0.8)

        raise RuntimeError("帮派签到超过最大识别步骤，已安全停止")


__all__ = ["CLAN_SIGN_IN_TEMPLATES", "ClanSignInWorkflow"]
