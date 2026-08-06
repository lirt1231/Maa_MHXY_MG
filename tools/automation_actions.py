"""Platform-neutral contracts and primitives for evidence-backed UI actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

try:
    from .automation_geometry import relative_point, valid_box
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_geometry import relative_point, valid_box


class ActionScene(Protocol):
    image_size: Sequence[int]
    texts: Sequence[Mapping[str, Any]]
    templates: Mapping[str, Any]


class ActionInput(Protocol):
    def click(self, point, scene: ActionScene, reason: str): ...

    def click_box(self, box, scene: ActionScene, reason: str): ...

    def drag(self, start, end, scene: ActionScene, reason: str): ...

    def input_text(self, text: str, scene: ActionScene, reason: str): ...


class ActionWaiter(Protocol):
    def wait(self, seconds: float) -> None: ...


class ActionEventSink(Protocol):
    def __call__(self, event: str, **payload: Any) -> None: ...


ReasonFactory = Callable[["ActionContext"], str]


@dataclass(frozen=True)
class ActionContext:
    """Fresh evidence and abstract I/O dependencies for one atomic action."""

    input: ActionInput
    waiter: ActionWaiter
    scene: ActionScene
    decision: Mapping[str, Any]
    task_label: str
    event_sink: ActionEventSink | None = None

    @property
    def decision_status(self) -> str:
        status = self.decision.get("status")
        return status if isinstance(status, str) else ""

    def emit(self, event: str, **payload: Any) -> None:
        if self.event_sink is not None:
            self.event_sink(event, **payload)


@dataclass(frozen=True)
class ActionResult:
    """Auditable result returned after one atomic action finishes its input."""

    name: str
    decision_status: str
    actions: int
    clicks: int = 0
    drags: int = 0
    wait_seconds: float = 0.0


@dataclass
class ActionMetrics:
    actions: int = 0
    clicks: int = 0
    drags: int = 0

    def record(self, result: ActionResult) -> ActionResult:
        self.actions += result.actions
        self.clicks += result.clicks
        self.drags += result.drags
        return result


class ActionBudget:
    """Shared input budget across a related set of atomic actions."""

    def __init__(self, limit: int, exhausted_message: str):
        if limit <= 0:
            raise ValueError("动作输入上限必须是正整数")
        self.limit = limit
        self.exhausted_message = exhausted_message
        self.used = 0

    def consume(self) -> None:
        if self.used >= self.limit:
            raise RuntimeError(self.exhausted_message)
        self.used += 1


class AtomicAction:
    """Base contract for a semantic input guarded by fresh scene evidence."""

    name = "atomic_action"
    statuses: frozenset[str] = frozenset()

    def accepts(self, context: ActionContext) -> bool:
        return context.decision_status in self.statuses

    def execute(self, context: ActionContext) -> ActionResult:
        raise NotImplementedError


class TextInputAction(AtomicAction):
    """Submit text through a platform adapter after fresh focus evidence."""

    def __init__(
        self,
        *,
        name: str,
        statuses: Sequence[str],
        text: str,
        reason: str,
        wait_seconds: float = 0.0,
    ):
        if not name or not statuses or not reason or wait_seconds < 0:
            raise ValueError("文本输入动作参数无效")
        self.name = name
        self.statuses = frozenset(statuses)
        self.text = text
        self.reason = reason
        self.wait_seconds = wait_seconds

    def execute(self, context: ActionContext) -> ActionResult:
        if not self.accepts(context):
            raise RuntimeError(
                f"动作 {self.name} 不接受状态 {context.decision_status or 'missing'}"
            )
        context.input.input_text(self.text, context.scene, self.reason)
        if self.wait_seconds:
            context.waiter.wait(self.wait_seconds)
        result = ActionResult(
            name=self.name,
            decision_status=context.decision_status,
            actions=1,
            wait_seconds=self.wait_seconds,
        )
        context.emit(
            "action_completed",
            action=self.name,
            task=context.task_label,
            decision=context.decision_status,
            clicks=0,
            drags=0,
            textInputs=1,
        )
        return result


class EvidenceClickAction(AtomicAction):
    """Click a recognized box with shared-budget and repetition protection."""

    def __init__(
        self,
        *,
        name: str,
        statuses: Sequence[str],
        reason: str | ReasonFactory,
        wait_seconds: float,
        budget: ActionBudget | None = None,
        repeat_limit: int = 2,
        repeat_error: str | None = None,
        signature_factory: Callable[[ActionContext], Any] | None = None,
    ):
        if not name or not statuses:
            raise ValueError("点击动作需要名称和至少一个识别状态")
        if wait_seconds < 0 or repeat_limit <= 0:
            raise ValueError("点击动作等待时间和重复上限无效")
        self.name = name
        self.statuses = frozenset(statuses)
        self.reason = reason
        self.wait_seconds = wait_seconds
        self.budget = budget
        self.repeat_limit = repeat_limit
        self.repeat_error = repeat_error
        self.signature_factory = signature_factory
        self._last_signature: Any = None
        self._consecutive_repeats = 0

    def reset_repetition(self) -> None:
        self._last_signature = None
        self._consecutive_repeats = 0

    def _signature(self, context: ActionContext, box: list[float]) -> Any:
        if self.signature_factory is not None:
            return self.signature_factory(context)
        return context.decision_status, tuple(box)

    def _reason(self, context: ActionContext) -> str:
        return self.reason(context) if callable(self.reason) else self.reason

    def execute(self, context: ActionContext) -> ActionResult:
        if not self.accepts(context):
            raise RuntimeError(
                f"动作 {self.name} 不接受状态 {context.decision_status or 'missing'}"
            )
        box = valid_box({"box": context.decision.get("box")})
        if box is None:
            raise RuntimeError(f"动作 {self.name} 缺少经过识别的有效点击区域")
        signature = self._signature(context, box)
        repeated = self._consecutive_repeats + 1 if signature == self._last_signature else 1
        if repeated > self.repeat_limit:
            message = self.repeat_error or (
                f"{context.task_label}连续重复执行 {context.decision_status}，已安全停止"
            )
            raise RuntimeError(message)
        if self.budget is not None:
            self.budget.consume()
        self._last_signature = signature
        self._consecutive_repeats = repeated

        context.input.click_box(box, context.scene, self._reason(context))
        if self.wait_seconds:
            context.waiter.wait(self.wait_seconds)
        result = ActionResult(
            name=self.name,
            decision_status=context.decision_status,
            actions=1,
            clicks=1,
            wait_seconds=self.wait_seconds,
        )
        context.emit(
            "action_completed",
            action=self.name,
            task=context.task_label,
            decision=context.decision_status,
            clicks=result.clicks,
            drags=result.drags,
        )
        return result


class EvidenceDoubleClickAction(EvidenceClickAction):
    """Double-click one recognized box as a single bounded semantic action."""

    def __init__(self, *, click_interval_seconds: float = 0.12, **kwargs):
        if click_interval_seconds < 0:
            raise ValueError("双击间隔不能为负数")
        super().__init__(**kwargs)
        self.click_interval_seconds = click_interval_seconds

    def execute(self, context: ActionContext) -> ActionResult:
        if not self.accepts(context):
            raise RuntimeError(
                f"动作 {self.name} 不接受状态 {context.decision_status or 'missing'}"
            )
        box = valid_box({"box": context.decision.get("box")})
        if box is None:
            raise RuntimeError(f"动作 {self.name} 缺少经过识别的有效点击区域")
        signature = self._signature(context, box)
        repeated = self._consecutive_repeats + 1 if signature == self._last_signature else 1
        if repeated > self.repeat_limit:
            message = self.repeat_error or (
                f"{context.task_label}连续重复执行 {context.decision_status}，已安全停止"
            )
            raise RuntimeError(message)
        if self.budget is not None:
            self.budget.consume()
        self._last_signature = signature
        self._consecutive_repeats = repeated

        reason = self._reason(context)
        context.input.click_box(box, context.scene, reason)
        if self.click_interval_seconds:
            context.waiter.wait(self.click_interval_seconds)
        context.input.click_box(box, context.scene, reason)
        if self.wait_seconds:
            context.waiter.wait(self.wait_seconds)
        result = ActionResult(
            name=self.name,
            decision_status=context.decision_status,
            actions=1,
            clicks=2,
            wait_seconds=self.click_interval_seconds + self.wait_seconds,
        )
        context.emit(
            "action_completed",
            action=self.name,
            task=context.task_label,
            decision=context.decision_status,
            clicks=result.clicks,
            drags=0,
        )
        return result


class RelativeClickAction(AtomicAction):
    """Click a stable scene-relative point after recognition proves the state."""

    def __init__(
        self,
        *,
        name: str,
        statuses: Sequence[str],
        point_ratio: Sequence[float],
        reason: str | ReasonFactory,
        wait_seconds: float,
        budget: ActionBudget | None = None,
        repeat_limit: int = 2,
        repeat_error: str | None = None,
    ):
        if not name or not statuses:
            raise ValueError("相对点击动作需要名称和至少一个识别状态")
        if wait_seconds < 0 or repeat_limit <= 0:
            raise ValueError("相对点击动作等待时间和重复上限无效")
        # Validate ratios without binding them to a platform or reference size.
        relative_point(point_ratio, (1, 1))
        self.name = name
        self.statuses = frozenset(statuses)
        self.point_ratio = tuple(point_ratio)
        self.reason = reason
        self.wait_seconds = wait_seconds
        self.budget = budget
        self.repeat_limit = repeat_limit
        self.repeat_error = repeat_error
        self._last_signature: Any = None
        self._consecutive_repeats = 0

    def reset_repetition(self) -> None:
        self._last_signature = None
        self._consecutive_repeats = 0

    def execute(self, context: ActionContext) -> ActionResult:
        if not self.accepts(context):
            raise RuntimeError(
                f"动作 {self.name} 不接受状态 {context.decision_status or 'missing'}"
            )
        point = relative_point(self.point_ratio, context.scene.image_size)
        reason = self.reason(context) if callable(self.reason) else self.reason
        signature = context.decision_status, point
        repeated = self._consecutive_repeats + 1 if signature == self._last_signature else 1
        if repeated > self.repeat_limit:
            message = self.repeat_error or (
                f"{context.task_label}连续重复执行 {context.decision_status}，已安全停止"
            )
            raise RuntimeError(message)
        if self.budget is not None:
            self.budget.consume()
        self._last_signature = signature
        self._consecutive_repeats = repeated
        context.input.click(point, context.scene, reason)
        if self.wait_seconds:
            context.waiter.wait(self.wait_seconds)
        result = ActionResult(
            name=self.name,
            decision_status=context.decision_status,
            actions=1,
            clicks=1,
            wait_seconds=self.wait_seconds,
        )
        context.emit(
            "action_completed",
            action=self.name,
            task=context.task_label,
            decision=context.decision_status,
            clicks=result.clicks,
            drags=result.drags,
        )
        return result


class RelativeDragAction(AtomicAction):
    """Drag between stable scene-relative points after page recognition."""

    def __init__(
        self,
        *,
        name: str,
        statuses: Sequence[str],
        start_ratio: Sequence[float],
        end_ratio: Sequence[float],
        reason: str | ReasonFactory,
        wait_seconds: float,
        repeat_limit: int = 2,
        repeat_error: str | None = None,
    ):
        if not name or not statuses:
            raise ValueError("相对拖动动作需要名称和至少一个识别状态")
        if wait_seconds < 0 or repeat_limit <= 0:
            raise ValueError("相对拖动动作等待时间和重复上限无效")
        relative_point(start_ratio, (1, 1))
        relative_point(end_ratio, (1, 1))
        self.name = name
        self.statuses = frozenset(statuses)
        self.start_ratio = tuple(start_ratio)
        self.end_ratio = tuple(end_ratio)
        self.reason = reason
        self.wait_seconds = wait_seconds
        self.repeat_limit = repeat_limit
        self.repeat_error = repeat_error
        self._last_signature: Any = None
        self._consecutive_repeats = 0

    def reset_repetition(self) -> None:
        self._last_signature = None
        self._consecutive_repeats = 0

    def execute(self, context: ActionContext) -> ActionResult:
        if not self.accepts(context):
            raise RuntimeError(
                f"动作 {self.name} 不接受状态 {context.decision_status or 'missing'}"
            )
        start = relative_point(self.start_ratio, context.scene.image_size)
        end = relative_point(self.end_ratio, context.scene.image_size)
        signature = context.decision_status, start, end
        repeated = self._consecutive_repeats + 1 if signature == self._last_signature else 1
        if repeated > self.repeat_limit:
            message = self.repeat_error or (
                f"{context.task_label}连续重复执行 {context.decision_status}，已安全停止"
            )
            raise RuntimeError(message)
        self._last_signature = signature
        self._consecutive_repeats = repeated

        reason = self.reason(context) if callable(self.reason) else self.reason
        context.input.drag(start, end, context.scene, reason)
        if self.wait_seconds:
            context.waiter.wait(self.wait_seconds)
        result = ActionResult(
            name=self.name,
            decision_status=context.decision_status,
            actions=1,
            drags=1,
            wait_seconds=self.wait_seconds,
        )
        context.emit(
            "action_completed",
            action=self.name,
            task=context.task_label,
            decision=context.decision_status,
            clicks=0,
            drags=1,
        )
        return result


class EvidenceThenRelativeClickAction(AtomicAction):
    """Open a transient control and select its stable scene-relative item."""

    def __init__(
        self,
        *,
        name: str,
        statuses: Sequence[str],
        point_ratio: Sequence[float],
        first_reason: str,
        second_reason: str,
        between_seconds: float,
        wait_seconds: float,
        repeat_limit: int = 2,
        repeat_error: str | None = None,
    ):
        if not name or not statuses or not first_reason or not second_reason:
            raise ValueError("连续点击动作参数无效")
        if between_seconds < 0 or wait_seconds < 0 or repeat_limit <= 0:
            raise ValueError("连续点击动作等待时间和重复上限无效")
        relative_point(point_ratio, (1, 1))
        self.name = name
        self.statuses = frozenset(statuses)
        self.point_ratio = tuple(point_ratio)
        self.first_reason = first_reason
        self.second_reason = second_reason
        self.between_seconds = between_seconds
        self.wait_seconds = wait_seconds
        self.repeat_limit = repeat_limit
        self.repeat_error = repeat_error
        self._last_signature: Any = None
        self._consecutive_repeats = 0

    def execute(self, context: ActionContext) -> ActionResult:
        if not self.accepts(context):
            raise RuntimeError(
                f"动作 {self.name} 不接受状态 {context.decision_status or 'missing'}"
            )
        evidence_box = valid_box({"box": context.decision.get("box")})
        if evidence_box is None:
            raise RuntimeError(f"动作 {self.name} 缺少经过识别的有效点击区域")
        follow_up_point = relative_point(self.point_ratio, context.scene.image_size)
        signature = context.decision_status, tuple(evidence_box), follow_up_point
        repeated = self._consecutive_repeats + 1 if signature == self._last_signature else 1
        if repeated > self.repeat_limit:
            message = self.repeat_error or (
                f"{context.task_label}连续重复执行 {context.decision_status}，已安全停止"
            )
            raise RuntimeError(message)
        self._last_signature = signature
        self._consecutive_repeats = repeated

        context.input.click_box(
            evidence_box,
            context.scene,
            self.first_reason,
        )
        if self.between_seconds:
            context.waiter.wait(self.between_seconds)
        context.input.click(
            follow_up_point,
            context.scene,
            self.second_reason,
        )
        if self.wait_seconds:
            context.waiter.wait(self.wait_seconds)
        result = ActionResult(
            name=self.name,
            decision_status=context.decision_status,
            actions=1,
            clicks=2,
            wait_seconds=self.between_seconds + self.wait_seconds,
        )
        context.emit(
            "action_completed",
            action=self.name,
            task=context.task_label,
            decision=context.decision_status,
            clicks=result.clicks,
            drags=0,
        )
        return result


class ContinueSidebarTaskAction(EvidenceClickAction):
    def __init__(
        self,
        task_label: str,
        *,
        wait_seconds: float,
        max_clicks: int = 12,
    ):
        super().__init__(
            name="continue_sidebar_task",
            statuses=("continue_task",),
            reason=f"继续{task_label}",
            wait_seconds=wait_seconds,
            budget=ActionBudget(
                max_clicks,
                f"{task_label}栏点击达到上限，已停止以避免循环",
            ),
            repeat_limit=max_clicks,
        )


__all__ = [
    "ActionBudget",
    "ActionContext",
    "ActionEventSink",
    "ActionInput",
    "ActionMetrics",
    "ActionResult",
    "ActionScene",
    "ActionWaiter",
    "AtomicAction",
    "ContinueSidebarTaskAction",
    "EvidenceDoubleClickAction",
    "EvidenceThenRelativeClickAction",
    "EvidenceClickAction",
    "RelativeClickAction",
    "RelativeDragAction",
    "TextInputAction",
]
