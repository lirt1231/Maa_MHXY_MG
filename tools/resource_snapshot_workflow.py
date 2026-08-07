"""Task-independent resource snapshot workflow boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from tools.resource_snapshots import ResourceFieldEvidence, ResourceSnapshot, ResourceValues


class ResourceSnapshotWorkflow:
    """Capture declared resource fields without exposing game-window details to Runner."""

    def __init__(self, capture: Callable[..., dict], *, target_id: str = "local-primary"):
        self.capture = capture
        self.target_id = target_id

    def execute(self, phase: str, *, captured_fields: frozenset[str]) -> ResourceSnapshot:
        captured_at = datetime.now(timezone.utc).isoformat()
        try:
            captured = self.capture(captured_fields=captured_fields)
        except Exception as exc:
            return ResourceSnapshot(target_id=self.target_id, phase=phase, captured_at=captured_at, values=ResourceValues(), error=str(exc), captured_fields=captured_fields)
        evidence = {
            name: ResourceFieldEvidence(status=item["status"], source_page=item["sourcePage"], raw_text=item.get("rawText"), confidence=item.get("confidence"), box=None if item.get("box") is None else tuple(item["box"]), frame_id=item.get("frameId"))
            for name, item in captured.get("resourceEvidence", {}).items()
        }
        return ResourceSnapshot(target_id=self.target_id, phase=phase, captured_at=captured_at, values=ResourceValues(level=captured.get("level"), experience=captured.get("experience"), experience_required=captured.get("experienceRequired"), gold=captured.get("gold"), silver=captured.get("silver")), evidence=evidence, error=captured.get("error"), captured_fields=captured_fields)


__all__ = ["ResourceSnapshotWorkflow"]
