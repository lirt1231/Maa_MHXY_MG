"""Platform-neutral 科举 answering workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass

try:
    from .automation_actions import ActionMetrics
    from .automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from .quiz_actions import AnswerOptionClickAction
    from .quiz_workflows import QuizObservationSource
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_actions import ActionMetrics
    from automation_workflows import (
        WorkflowContext,
        WorkflowInspection,
        WorkflowOutcome,
        WorkflowResult,
    )
    from quiz_actions import AnswerOptionClickAction
    from quiz_workflows import QuizObservationSource


def stable_question_key(question: str) -> str:
    """Remove the live round/timer prefix so one question is clicked only once."""
    round_match = re.search(r"第\s*(\d+)\s*/\s*\d+\s*题", question or "")
    if round_match:
        return f"round:{round_match.group(1)}"
    text = re.sub(r"^第\s*\d+\s*/\s*\d+\s*题", "", question or "")
    text = re.sub(r"^剩余时间\s*\d*", "", text)
    return re.sub(r"\s+", "", text)


def parse_question_round(question: str) -> tuple[int, int] | None:
    match = re.search(r"第\s*(\d+)\s*/\s*(\d+)\s*题", question or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def should_end_loop(
    answered: int,
    max_steps: int,
    question_round: tuple[int, int] | None = None,
    *,
    question_number: int | None = None,
    final_question_number: int | None = None,
) -> bool:
    if answered >= max_steps:
        return True
    if question_round is not None:
        current, total = question_round
        if current >= total:
            return True
    return bool(
        question_number is not None
        and final_question_number is not None
        and question_number >= final_question_number
    )


@dataclass(frozen=True)
class KejuWorkflow:
    source: QuizObservationSource
    max_answers: int = 20
    final_question_number: int | None = None
    delay: float = 3.0
    max_waiting_polls: int = 180
    max_same_question_polls: int = 8
    max_recognition_failures: int = 4

    def __post_init__(self):
        if (
            self.max_answers <= 0
            or self.delay < 0
            or self.max_recognition_failures <= 0
            or (
                self.final_question_number is not None
                and self.final_question_number <= 0
            )
        ):
            raise ValueError("科举答题上限必须为正整数且等待时间不能为负数")

    def execute(
        self,
        context: WorkflowContext,
        *,
        preview: bool = False,
    ) -> WorkflowOutcome:
        metrics = ActionMetrics()
        answer_action = AnswerOptionClickAction(
            "科举",
            max_clicks=self.max_answers,
            wait_seconds=self.delay,
        )
        last_question: str | None = None
        last_question_number: int | None = None
        answered = 0
        waiting_polls = 0
        same_question_polls = 0
        recognition_failures = 0
        step = 0

        while not should_end_loop(answered, self.max_answers):
            step += 1
            observation = self.source.observe()
            decision = dict(observation.decision)
            status = decision.get("status", "unknown")
            context.emit("quiz_observation", step=step, **decision)

            if status == "ready":
                recognition_failures = 0
                question = str(decision.get("question", ""))
                question_key = str(
                    decision.get("question_key") or stable_question_key(question)
                )
                decision["question_key"] = question_key
                question_round = decision.get("question_round")
                if question_round is None:
                    question_round = parse_question_round(question)
                question_number = decision.get("question_number")
                if question_number is None and question_round:
                    question_number = question_round[0]
                same_round = (
                    question_number is not None
                    and last_question_number is not None
                    and question_number == last_question_number
                )
                if question_key == last_question or same_round:
                    same_question_polls += 1
                    if same_question_polls >= self.max_same_question_polls:
                        raise RuntimeError(
                            "同一道科举题连续未变化，已停止以避免重复识别或点击"
                        )
                    context.emit("same_question_waiting", polls=same_question_polls)
                    context.waiter.wait(min(self.delay, 1.0))
                    continue
                if preview:
                    return WorkflowInspection.from_scene(observation.scene, decision)

                same_question_polls = 0
                waiting_polls = 0
                metrics.record(
                    answer_action.execute(
                        context.action_context(observation.scene, decision, "科举")
                    )
                )
                last_question = question_key
                last_question_number = question_number
                answered += 1
                context.emit(
                    "quiz_answered",
                    answers=answered,
                    clicks=metrics.clicks,
                    questionRound=question_round,
                )
                if should_end_loop(
                    answered,
                    self.max_answers,
                    question_round,
                    question_number=question_number,
                    final_question_number=self.final_question_number,
                ):
                    if (
                        question_round
                        and question_round[0] >= question_round[1]
                    ) or (
                        question_number is not None
                        and self.final_question_number is not None
                        and question_number >= self.final_question_number
                    ):
                        return WorkflowResult(
                            "completed",
                            {
                                "answers": answered,
                                "clicks": metrics.clicks,
                                "limit": self.max_answers,
                            },
                        )
                    break
                continue

            if status == "waiting":
                recognition_failures = 0
                waiting_polls += 1
                if waiting_polls >= self.max_waiting_polls:
                    raise RuntimeError("科举页面长时间未出现可答题目，已安全停止")
                context.emit("waiting_for_formal_question", polls=waiting_polls)
                context.waiter.wait(min(self.delay, 1.0))
                continue
            if status in {"no_question", "unreliable"}:
                recognition_failures += 1
                if recognition_failures >= self.max_recognition_failures:
                    if status == "no_question":
                        raise RuntimeError(
                            "科举页面连续多帧未识别到题目或明确完成状态，已保留页面"
                        )
                    raise RuntimeError("当前题目连续多帧无法可靠决策，已保留页面")
                context.emit(
                    "recognition_retry",
                    polls=recognition_failures,
                    status=status,
                )
                context.waiter.wait(min(self.delay, 1.0))
                continue
            if status == "wrong_exam_type":
                raise RuntimeError(str(decision.get("reason") or "当前科举考试类型不匹配"))
            if status == "unverified_exam_type":
                raise RuntimeError(str(decision.get("reason") or "无法确认当前科举考试类型"))
            raise RuntimeError(f"未处理的科举状态：{status}")

        raise RuntimeError(
            f"已完成固定 {self.max_answers} 题输入，但未识别到明确完成状态，已保留现场"
        )


__all__ = [
    "KejuWorkflow",
    "parse_question_round",
    "should_end_loop",
    "stable_question_key",
]
