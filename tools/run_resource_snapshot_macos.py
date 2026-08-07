#!/usr/bin/env python3
"""Capture one resource snapshot for the dashboard task orchestrator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.metrics import RESOURCE_CAPTURE_FIELDS, capture_visible_metrics
from tools.resource_snapshot_workflow import ResourceSnapshotWorkflow
from tools.resource_snapshots import ResourceSnapshot


PREFIX = "resource_snapshot_json="


def build_snapshot(phase: str, *, captured_fields: frozenset[str] = RESOURCE_CAPTURE_FIELDS) -> ResourceSnapshot:
    return ResourceSnapshotWorkflow(capture_visible_metrics).execute(
        phase, captured_fields=captured_fields
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    parser.add_argument(
        "--fields",
        default=",".join(sorted(RESOURCE_CAPTURE_FIELDS)),
        help="Comma-separated resource fields to collect",
    )
    args = parser.parse_args()
    captured_fields = frozenset(field.strip() for field in args.fields.split(",") if field.strip())
    if not captured_fields or captured_fields - RESOURCE_CAPTURE_FIELDS:
        parser.error("--fields 包含不支持的资源字段")
    snapshot = build_snapshot(args.phase, captured_fields=captured_fields)
    print(PREFIX + json.dumps(snapshot.to_payload(), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
