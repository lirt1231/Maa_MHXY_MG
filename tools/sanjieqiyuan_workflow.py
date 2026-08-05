"""Platform-neutral 三界奇缘 answering workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

try:
    from .automation_actions import (
        ActionBudget,
        ActionMetrics,
        EvidenceClickAction,
    )
    from .automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from .quiz_actions import AnswerOptionClickAction
    from .quiz_workflows import QuizObservationSource
    from .daily_activity_actions import DailyActivityActions
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_actions import ActionBudget, ActionMetrics, EvidenceClickAction
    from automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from quiz_actions import AnswerOptionClickAction
    from quiz_workflows import QuizObservationSource
    from daily_activity_actions import DailyActivityActions


CompletionVerifier = Callable[[], str | None]


class SanjieNavigationSource(Protocol):
    def observe(self, daily_tab_confirmed: bool): ...


def _navigation_signature(decision, image_size):
    point = decision.get("click")
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        return (decision["status"],)
    width, height = image_size
    return (
        decision["status"],
        round(point[0] / width, 2),
        round(point[1] / height, 2),
    )


@dataclass(frozen=True)
class SanjieNavigationWorkflow:
    source: SanjieNavigationSource
    max_inputs: int = 12
    max_repeated_actions: int = 2

    def execute(self, context: WorkflowContext) -> WorkflowResult:
        budget = ActionBudget(
            self.max_inputs,
            "活动导航已达到输入上限，已停止以避免死循环",
        )
        daily_actions = DailyActivityActions("三界奇缘", budget=budget)
        special_actions = {
            status: EvidenceClickAction(
                name=status,
                statuses=(status,),
                reason=reason,
                wait_seconds=1.2,
                budget=budget,
                repeat_limit=self.max_repeated_actions,
                repeat_error=f"检测到重复导航动作 {status}，已停止以避免死循环",
            )
            for status, reason in {
                "open_battle_menu": "打开战斗快捷菜单",
                "click_activity_side_menu": "从战斗快捷菜单打开活动",
                "dismiss_known_popup": "关闭已识别弹窗",
                "close_bag_overlay": "关闭包裹遮挡",
            }.items()
        }
        metrics = ActionMetrics()
        action_counts = {}
        battle_return_attempts = 0

        while True:
            observation = self.source.observe(daily_actions.daily_tab_confirmed)
            decision = dict(observation.decision)
            status = decision.get("status", "unknown")
            context.emit("navigation_scene", status=status, decision=decision)
            if status == "answering":
                return WorkflowResult(
                    "answering",
                    {
                        "actions": metrics.actions,
                        "clicks": metrics.clicks,
                        "drags": metrics.drags,
                    },
                )
            if status in {"completed", "already_completed"}:
                return WorkflowResult(
                    status,
                    {
                        "actions": metrics.actions,
                        "clicks": metrics.clicks,
                        "drags": metrics.drags,
                    },
                )
            if status == "open_battle_menu":
                if battle_return_attempts == 0:
                    battle_return_attempts = 1
                elif battle_return_attempts == 1:
                    width, height = observation.scene.image_size
                    decision = {
                        "status": "click_activity_side_menu",
                        "click": [round(width * 0.038), round(height * 0.161)],
                    }
                    status = decision["status"]
                    battle_return_attempts = 2
                else:
                    raise RuntimeError("战斗快捷菜单打开后仍无法进入活动界面")

            normalized_status = {
                "click_activity": "open_activity",
                "click_activity_toolbar": "open_activity",
                "click_activity_template": "open_activity",
                "click_daily": "open_daily",
                "click_join": "join_task",
            }.get(status, status)
            if status == "click_daily" and daily_actions.daily_tab_confirmed:
                normalized_status = "scroll_daily"
            if "click" in decision:
                signature = _navigation_signature(
                    decision,
                    observation.scene.image_size,
                )
                action_counts[signature] = action_counts.get(signature, 0) + 1
                if action_counts[signature] > self.max_repeated_actions:
                    raise RuntimeError(
                        f"检测到重复导航动作 {status}，已停止以避免死循环"
                    )

            normalized = {"status": normalized_status}
            if "click" in decision:
                x, y = decision["click"]
                normalized["box"] = [x - 1, y - 1, 2, 2]
            if "reason" in decision:
                normalized["reason"] = decision["reason"]
            action_context = context.action_context(
                observation.scene,
                normalized,
                "三界奇缘",
            )
            action = special_actions.get(normalized_status)
            result = (
                action.execute(action_context)
                if action is not None
                else daily_actions.execute(action_context)
            )
            if result is None:
                raise RuntimeError(
                    f"当前画面没有足够证据执行安全点击：{status}"
                )
            metrics.record(result)
            if normalized_status == "scroll_daily":
                daily_actions.daily_tab_confirmed = True
            context.emit(
                "navigation_action",
                status=status,
                normalizedStatus=normalized_status,
                source=decision.get("click"),
                actions=metrics.actions,
                clicks=metrics.clicks,
                drags=metrics.drags,
            )


def advance_question_count(
    current_question: str | None,
    next_question: str,
    completed_questions: int,
) -> tuple[str, int]:
    """Count completed questions, not individual choices in multi-select questions."""
    if current_question and next_question != current_question:
        completed_questions += 1
    return next_question, completed_questions


@dataclass(frozen=True)
class SanjieqiyuanWorkflow:
    source: QuizObservationSource
    completion_verifier: CompletionVerifier
    max_questions: int = 10
    answer_wait_seconds: float = 1.2
    initial_wait_seconds: float = 1.5
    max_transition_polls: int = 6
    max_answer_not_found_polls: int = 3

    def __post_init__(self):
        if self.max_questions <= 0:
            raise ValueError("三界奇缘最大答题数必须是正整数")
        if self.max_answer_not_found_polls <= 0:
            raise ValueError("三界奇缘答案识别重试上限必须是正整数")

    def execute(
        self,
        context: WorkflowContext,
        *,
        preview: bool = False,
    ) -> WorkflowOutcome:
        metrics = ActionMetrics()
        max_clicks = max(20, self.max_questions * 4)
        answer_action = AnswerOptionClickAction(
            "三界奇缘",
            max_clicks=max_clicks,
            wait_seconds=self.answer_wait_seconds,
        )
        current_question_key: str | None = None
        selected_question_key: str | None = None
        selected_answers: set[str] = set()
        completed_questions = 0
        unchanged_polls = 0
        answer_not_found_key: str | None = None
        answer_not_found_polls = 0
        first_observation = True

        while completed_questions < self.max_questions and metrics.clicks < max_clicks:
            if first_observation and self.initial_wait_seconds:
                context.waiter.wait(self.initial_wait_seconds)
            first_observation = False
            observation = self.source.observe(
                selected_question_key,
                frozenset(selected_answers),
            )
            decision = dict(observation.decision)
            status = decision.get("status", "unknown")
            question = str(decision.get("question", ""))
            question_key = str(decision.get("question_key") or question)
            context.emit(
                "quiz_observation",
                status=status,
                question=question,
                questions=completed_questions,
                clicks=metrics.clicks,
            )

            if status == "finished":
                questions = completed_questions + bool(current_question_key)
                completion_status = self.completion_verifier()
                if completion_status is None:
                    raise RuntimeError(
                        "题目区域已消失，但未识别到三界奇缘完成状态，已安全停止"
                    )
                return WorkflowResult(
                    completion_status,
                    {"questions": questions, "clicks": metrics.clicks},
                )
            if status == "awaiting_transition":
                unchanged_polls += 1
                if unchanged_polls >= self.max_transition_polls:
                    raise RuntimeError("本题答案已全部选择但题目持续未变化，已安全停止")
                context.waiter.wait(0.5)
                continue

            unchanged_polls = 0
            if status == "ready":
                answer_not_found_key = None
                answer_not_found_polls = 0
                decision["question_key"] = question_key
                if preview:
                    return WorkflowInspection.from_scene(observation.scene, decision)
                if question_key != selected_question_key:
                    selected_question_key = question_key
                    selected_answers = set()
                if current_question_key is None:
                    question_number = decision.get("question_number")
                    if (
                        isinstance(question_number, int)
                        and not isinstance(question_number, bool)
                        and 1 <= question_number <= self.max_questions
                    ):
                        completed_questions = max(
                            completed_questions,
                            question_number - 1,
                        )
                current_question_key, completed_questions = advance_question_count(
                    current_question_key,
                    question_key,
                    completed_questions,
                )
                if completed_questions >= self.max_questions:
                    break
                metrics.record(
                    answer_action.execute(
                        context.action_context(
                            observation.scene,
                            decision,
                            "三界奇缘",
                        )
                    )
                )
                selected_answers.add(str(decision["answer"]))
                continue
            if status == "low_confidence":
                raise RuntimeError(
                    f"题库匹配度不足，停止答题：{question}（{decision.get('confidence', 0)}%）"
                )
            if status == "answer_not_found":
                if question_key != answer_not_found_key:
                    answer_not_found_key = question_key
                    answer_not_found_polls = 0
                answer_not_found_polls += 1
                if answer_not_found_polls < self.max_answer_not_found_polls:
                    context.emit(
                        "answer_recognition_retry",
                        question=question,
                        questionKey=question_key,
                        attempt=answer_not_found_polls,
                        limit=self.max_answer_not_found_polls,
                    )
                    context.waiter.wait(0.4)
                    continue
                raise RuntimeError(
                    f"题库答案未在当前选项中找到：{question}，候选：{decision.get('candidates', [])}"
                )
            raise RuntimeError(f"三界奇缘进入未知识别状态：{status}")

        if metrics.clicks >= max_clicks:
            raise RuntimeError(
                f"已执行 {metrics.clicks} 次选择但活动仍未完成，已停止以避免异常循环"
            )
        raise RuntimeError(
            f"已完成固定 {self.max_questions} 题输入，但未识别到明确完成状态，已保留现场"
        )


__all__ = [
    "SanjieNavigationSource",
    "SanjieNavigationWorkflow",
    "SanjieqiyuanWorkflow",
    "advance_question_count",
]
