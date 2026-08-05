"""Platform-neutral workflow contracts built on the atomic action interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Union

try:
    from .automation_actions import (
        ActionContext,
        ActionEventSink,
        ActionInput,
        ActionScene,
        ActionWaiter,
    )
except ImportError:  # Direct execution through a platform runner.
    from automation_actions import (
        ActionContext,
        ActionEventSink,
        ActionInput,
        ActionScene,
        ActionWaiter,
    )


class SceneSource(Protocol):
    def read_scene(self, templates: Mapping[str, list[str]] | None = None) -> ActionScene: ...


class WorkflowRuntime(ActionInput, SceneSource, Protocol):
    """Capabilities a platform adapter must provide to a workflow."""


InspectionEvidenceProvider = Callable[
    [ActionScene, Mapping[str, Any]],
    Mapping[str, Any],
]


@dataclass(frozen=True)
class WorkflowContext:
    runtime: WorkflowRuntime
    waiter: ActionWaiter
    event_sink: ActionEventSink | None = None
    inspection_evidence_provider: InspectionEvidenceProvider | None = None

    def emit(self, event: str, **payload: Any) -> None:
        if self.event_sink is not None:
            self.event_sink(event, **payload)

    def action_context(
        self,
        scene: ActionScene,
        decision: Mapping[str, Any],
        task_label: str,
    ) -> ActionContext:
        return ActionContext(
            self.runtime,
            self.waiter,
            scene,
            decision,
            task_label,
            self.event_sink,
        )

    def inspection_evidence(
        self,
        scene: ActionScene,
        decision: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if self.inspection_evidence_provider is None:
            return {}
        evidence = self.inspection_evidence_provider(scene, decision)
        if not isinstance(evidence, Mapping):
            raise RuntimeError("平台预检证据必须是映射")
        return evidence


@dataclass(frozen=True)
class WorkflowResult:
    status: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowInspection:
    image_size: Any
    decision: Mapping[str, Any]
    texts: Any
    templates: Mapping[str, Any]
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_scene(
        cls,
        scene: ActionScene,
        decision: Mapping[str, Any],
        evidence: Mapping[str, Any] | None = None,
    ):
        return cls(
            scene.image_size,
            decision,
            scene.texts,
            scene.templates,
            evidence or {},
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "image_size": self.image_size,
            "decision": self.decision,
            "texts": self.texts,
            "templates": self.templates,
            "evidence": self.evidence,
        }


WorkflowOutcome = Union[WorkflowResult, WorkflowInspection]


__all__ = [
    "SceneSource",
    "InspectionEvidenceProvider",
    "WorkflowContext",
    "WorkflowInspection",
    "WorkflowOutcome",
    "WorkflowResult",
    "WorkflowRuntime",
]
