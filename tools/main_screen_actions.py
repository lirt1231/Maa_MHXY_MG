"""Shared read-only confirmation and close actions for the game main screen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

try:
    from .automation_actions import ActionContext, EvidenceClickAction
except ImportError:  # Direct execution through tools/run_*.py.
    from automation_actions import ActionContext, EvidenceClickAction


@dataclass(frozen=True)
class MainScreenConfirmation:
    """Require two calibrated, distinct frames before accepting the main screen."""

    first_frame_id: str | None = None
    first_signature: Any = None
    first_anchors: tuple[str, ...] = ()

    def observe(self, scene, decision: Mapping[str, Any]) -> tuple[str, "MainScreenConfirmation", dict[str, Any] | str | None]:
        """Return ``pending``, ``confirmed`` or ``failed`` without producing input."""
        frame = scene.frame
        if frame is None:
            return "failed", self, "主界面证据缺少 frameId"
        anchors = tuple(sorted({str(item) for item in decision.get("anchors", [])}))
        if self.first_frame_id is None:
            return (
                "pending",
                MainScreenConfirmation(
                    first_frame_id=frame.frame_id,
                    first_signature=scene.capture_signature,
                    first_anchors=anchors,
                ),
                None,
            )
        if frame.frame_id == self.first_frame_id:
            return "failed", self, "主界面确认重复使用旧帧"
        if (
            self.first_signature is None
            or scene.capture_signature is None
            or not self.first_signature.is_compatible_with(scene.capture_signature)
        ):
            return "failed", self, "两次主界面截图标定不一致"
        shared_anchors = sorted(set(self.first_anchors) & set(anchors))
        if len(shared_anchors) < 3:
            return "failed", self, "两帧主界面锚点不一致"
        return (
            "confirmed",
            self,
            {
                "firstFrameId": self.first_frame_id,
                "lastFrameId": frame.frame_id,
                "anchors": shared_anchors,
                "imageSize": list(scene.image_size),
                "captureMethod": frame.capture_method,
            },
        )


class CloseKnownOverlayAction(EvidenceClickAction):
    """Close a classifier-approved panel or overlay exactly at its proven target."""

    def __init__(self):
        super().__init__(
            name="close_known_overlay",
            statuses=("close_known_page",),
            reason=lambda context: (
                f"关闭已确认的{context.decision.get('scene', '任务')}页面"
            ),
            wait_seconds=0.0,
            repeat_limit=1,
            repeat_error="同一关闭动作不得重复",
            signature_factory=lambda context: context.decision.get("action_id"),
        )


def close_known_overlay(context: ActionContext):
    """Execute the shared close action without exposing a generic click to callers."""
    return CloseKnownOverlayAction().execute(context)


__all__ = [
    "CloseKnownOverlayAction",
    "MainScreenConfirmation",
    "close_known_overlay",
]
