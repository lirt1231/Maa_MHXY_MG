"""Platform-neutral observation contracts for quiz workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

try:
    from .automation_actions import ActionScene
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_actions import ActionScene


@dataclass(frozen=True)
class QuizObservation:
    scene: ActionScene
    decision: Mapping[str, Any]


class QuizObservationSource(Protocol):
    def observe(
        self,
        selected_question_key: str | None = None,
        selected_answers: frozenset[str] = frozenset(),
    ) -> QuizObservation: ...


__all__ = ["QuizObservation", "QuizObservationSource"]
