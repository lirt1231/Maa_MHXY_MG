"""Platform-neutral bounded workflow for 秘境降妖."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

try:
    from .automation_actions import ActionBudget, ActionMetrics
    from .automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from .daily_activity_actions import DailyActivityActions
    from .daily_workflow_common import DAILY_TASK_TEMPLATES
    from .game_atomic_actions import (
        ContinueTrackedTaskAction,
        EnterDungeonStageAction,
        StartDungeonChallengeAction,
    )
    from .mijingxiangyao_recognition import classify_mijingxiangyao_scene
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import ActionBudget, ActionMetrics
    from automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from daily_activity_actions import DailyActivityActions
    from daily_workflow_common import DAILY_TASK_TEMPLATES
    from game_atomic_actions import (
        ContinueTrackedTaskAction,
        EnterDungeonStageAction,
        StartDungeonChallengeAction,
    )
    from mijingxiangyao_recognition import classify_mijingxiangyao_scene


MIJINGXIANGYAO_TEMPLATES = {
    **DAILY_TASK_TEMPLATES,
    "mode_page": ["mijing/mijing_moshi.png"],
    "failure": [
        "mijing/mijing_shibai.png",
        "mijing/mijing_shibai2.png",
        "mijing/mijing_riyue_shibai.png",
    ],
}

CLICK_STATUSES = frozenset(
    {
        "open_activity",
        "open_daily",
        "join_task",
        "continue_task",
        "enter_dungeon",
        "start_challenge",
        "select_stage",
        "enter_battle",
    }
)


@dataclass(frozen=True)
class MijingxiangyaoWorkflow:
    max_battles: int | None = None
    max_steps: int = 600
    max_navigation_inputs: int = 16
    max_wait_polls: int = 180
    max_transition_unknown_polls: int = 24
    continue_task_delay_seconds: float = 8.0
    continue_task_repeat_limit: int = 8
    classifier: Callable[..., Mapping[str, Any]] = field(
        default=classify_mijingxiangyao_scene,
        repr=False,
        compare=False,
    )

    def __post_init__(self):
        positive_limits = (
            self.max_steps,
            self.max_navigation_inputs,
            self.max_wait_polls,
            self.max_transition_unknown_polls,
            self.continue_task_repeat_limit,
        )
        if any(value <= 0 for value in positive_limits):
            raise ValueError("秘境降妖 Workflow 上限必须是正数")
        if self.max_battles is not None and self.max_battles <= 0:
            raise ValueError("秘境降妖战斗安全上限必须是正数")
        if self.continue_task_delay_seconds < 0:
            raise ValueError("秘境降妖继续任务等待时间不能为负数")

    def execute(self, context: WorkflowContext, *, inspect: bool = False) -> WorkflowOutcome:
        phase = "navigate"
        metrics = ActionMetrics()
        battles = 0
        unknown_polls = 0
        wait_polls = 0
        recent_states: list[tuple[int, str, str]] = []
        battle_counted = False
        previous_status: str | None = None

        navigation_budget = ActionBudget(
            self.max_navigation_inputs,
            "秘境降妖活动导航达到输入上限，已安全停止",
        )
        daily_actions = DailyActivityActions(
            "秘境降妖",
            budget=navigation_budget,
        )
        battle_state = {"count": 0}

        def battle_signature(action_context):
            return (
                battle_state["count"],
                action_context.decision_status,
                tuple(action_context.decision["box"]),
            )

        task_actions = {
            "continue_task": ContinueTrackedTaskAction(
                "秘境降妖",
                wait_seconds=self.continue_task_delay_seconds,
                budget=navigation_budget,
                repeat_limit=self.continue_task_repeat_limit,
                signature_factory=battle_signature,
            ),
            "enter_dungeon": EnterDungeonStageAction(
                status="enter_dungeon",
                reason="进入秘境降妖",
                wait_seconds=2.0,
                budget=navigation_budget,
                signature_factory=battle_signature,
            ),
            "start_challenge": StartDungeonChallengeAction(
                signature_factory=battle_signature,
            ),
            "select_stage": EnterDungeonStageAction(
                status="select_stage",
                reason="选择秘境关卡",
                wait_seconds=1.0,
                signature_factory=battle_signature,
            ),
            "enter_battle": EnterDungeonStageAction(
                status="enter_battle",
                reason="进入秘境战斗",
                wait_seconds=1.0,
                signature_factory=battle_signature,
            ),
        }

        def result(status: str, **payload: Any) -> WorkflowResult:
            return WorkflowResult(
                status,
                {
                    "battles": battles,
                    "max_battles": self.max_battles or 0,
                    "actions": metrics.actions,
                    "clicks": metrics.clicks,
                    **payload,
                },
            )

        for step in range(1, self.max_steps + 1):
            scene = context.runtime.read_scene(MIJINGXIANGYAO_TEMPLATES)
            decision = self.classifier(
                scene.texts,
                scene.templates,
                scene.image_size,
                phase,
                daily_tab_confirmed=daily_actions.daily_tab_confirmed,
            )
            status = decision["status"]
            prior_status = previous_status
            previous_status = status

            if status in CLICK_STATUSES:
                recent_states.append((battles, phase, status))
                recent_states = recent_states[-12:]
                if len(recent_states) == 12 and recent_states[:6] == recent_states[6:]:
                    raise RuntimeError("秘境降妖重复相同的六步操作序列，已停止以避免死循环")
            elif status == "wait":
                recent_states.clear()

            context.emit(
                "scene",
                step=step,
                phase=phase,
                status=status,
                battles=battles,
                reason=decision.get("reason"),
            )
            if inspect:
                return WorkflowInspection.from_scene(
                    scene,
                    decision,
                    context.inspection_evidence(scene, decision),
                )

            if status in {"completed", "already_completed"}:
                return result(status)
            if status == "unsupported_asset_branch":
                raise RuntimeError(
                    f"秘境降妖进入资产或购买分支，首版不会自动处理：{decision['reason']}"
                )
            if status == "challenge_failed":
                raise RuntimeError(f"秘境降妖挑战失败，已保留现场：{decision['reason']}")
            if status == "mode_selection_required":
                raise RuntimeError(decision["reason"])

            action_context = context.action_context(scene, decision, "秘境降妖")
            navigation_result = daily_actions.execute(action_context)
            if navigation_result is not None:
                metrics.record(navigation_result)
                if status == "join_task":
                    phase = "await_lobby"
                unknown_polls = 0
                wait_polls = 0
                continue

            action = task_actions.get(status)
            if action is not None:
                if (
                    self.max_battles is not None
                    and status in {"start_challenge", "enter_battle"}
                    and battles >= self.max_battles
                ):
                    raise RuntimeError(
                        f"秘境降妖已达到 {self.max_battles} 场内部战斗安全上限，"
                        "但未识别到明确完成状态，已保留现场"
                    )
                if status == "start_challenge":
                    battle_counted = False
                elif status == "enter_battle":
                    if prior_status != "enter_battle":
                        battle_counted = False
                    if not battle_counted:
                        battles += 1
                        battle_state["count"] = battles
                        battle_counted = True

                metrics.record(action.execute(action_context))
                daily_actions.reset_page_state()
                phase = {
                    "join_task": "await_lobby",
                    "continue_task": "await_lobby",
                    "enter_dungeon": "await_challenge",
                    "start_challenge": "stage_select",
                    "select_stage": "stage_selected",
                    "enter_battle": "battle",
                }.get(status, phase)
                unknown_polls = 0
                wait_polls = 0
                continue

            if status == "wait":
                if decision.get("reason") in {"battle", "battle_ocr"} and not battle_counted:
                    battles += 1
                    battle_state["count"] = battles
                    battle_counted = True
                wait_polls += 1
                if wait_polls > self.max_wait_polls:
                    raise RuntimeError("秘境降妖等待画面超过上限，已停止以避免无限挂机")
                unknown_polls = 0
                context.waiter.wait(2.0)
                continue

            wait_polls = 0
            unknown_polls += 1
            if unknown_polls >= self.max_transition_unknown_polls:
                raise RuntimeError("连续多次无法识别秘境降妖页面，已停止以避免无证据点击")
            context.waiter.wait(1.0)

        raise RuntimeError("秘境降妖超过最大识别步骤，已安全停止")


__all__ = [
    "CLICK_STATUSES",
    "MIJINGXIANGYAO_TEMPLATES",
    "MijingxiangyaoWorkflow",
]
