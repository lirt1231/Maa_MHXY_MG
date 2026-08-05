#!/usr/bin/env python3
"""Capture one resource snapshot for the dashboard task orchestrator."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.metrics import capture_visible_metrics
from tools.resource_snapshots import (
    ResourceFieldEvidence,
    ResourceSnapshot,
    ResourceValues,
)


PREFIX = "resource_snapshot_json="


def build_snapshot(phase: str) -> ResourceSnapshot:
    captured_at = datetime.now(timezone.utc).isoformat()
    try:
        captured = capture_visible_metrics()
    except Exception as exc:
        return ResourceSnapshot(
            target_id="local-primary",
            phase=phase,
            captured_at=captured_at,
            values=ResourceValues(),
            error=str(exc),
        )

    evidence = {
        name: ResourceFieldEvidence(
            status=item["status"],
            source_page=item["sourcePage"],
            raw_text=item.get("rawText"),
            confidence=item.get("confidence"),
            box=None if item.get("box") is None else tuple(item["box"]),
            frame_id=item.get("frameId"),
        )
        for name, item in captured.get("resourceEvidence", {}).items()
    }
    return ResourceSnapshot(
        target_id="local-primary",
        phase=phase,
        captured_at=captured_at,
        values=ResourceValues(
            level=captured.get("level"),
            experience=captured.get("experience"),
            experience_required=captured.get("experienceRequired"),
            gold=captured.get("gold"),
            silver=captured.get("silver"),
        ),
        evidence=evidence,
        error=captured.get("error"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    args = parser.parse_args()
    snapshot = build_snapshot(args.phase)
    print(PREFIX + json.dumps(snapshot.to_payload(), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
