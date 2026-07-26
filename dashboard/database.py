from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    task_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms INTEGER,
                    error TEXT,
                    benefit_status TEXT NOT NULL DEFAULT 'not_captured'
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(run_id) REFERENCES task_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, id);
                CREATE TABLE IF NOT EXISTS metric_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    gold INTEGER,
                    level INTEGER,
                    experience INTEGER,
                    experience_required INTEGER,
                    raw_text TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    corrected INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    def create_run(self, run: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO task_runs(id, task_id, task_name, status, options_json, started_at) VALUES(?,?,?,?,?,?)",
                (
                    run["id"], run["taskId"], run["taskName"], run["status"],
                    json.dumps(run["options"], ensure_ascii=False), run["startedAt"],
                ),
            )

    def update_run(self, run_id: str, status: str, error: str | None = None) -> None:
        now = utc_now()
        with self.connect() as db:
            row = db.execute("SELECT started_at FROM task_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return
            started = datetime.fromisoformat(row["started_at"])
            duration = max(0, int((datetime.now(UTC) - started).total_seconds() * 1000))
            db.execute(
                "UPDATE task_runs SET status=?, finished_at=?, duration_ms=?, error=? WHERE id=?",
                (status, now, duration, error, run_id),
            )

    def add_event(self, run_id: str, level: str, event_type: str, message: str, payload: dict | None = None) -> dict:
        event = {
            "runId": run_id,
            "createdAt": utc_now(),
            "level": level,
            "type": event_type,
            "message": message,
            "payload": payload or {},
        }
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO run_events(run_id, created_at, level, event_type, message, payload_json) VALUES(?,?,?,?,?,?)",
                (run_id, event["createdAt"], level, event_type, message, json.dumps(event["payload"], ensure_ascii=False)),
            )
            event["id"] = cursor.lastrowid
        return event

    def recent_runs(self, limit: int = 20) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM task_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._run_row(row) for row in rows]

    def summary(self) -> dict:
        with self.connect() as db:
            row = db.execute(
                """SELECT COUNT(*) total,
                SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) succeeded,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
                AVG(CASE WHEN status IN ('succeeded','failed','cancelled') THEN duration_ms END) average_ms
                FROM task_runs"""
            ).fetchone()
        total = row["total"] or 0
        succeeded = row["succeeded"] or 0
        return {
            "total": total,
            "succeeded": succeeded,
            "failed": row["failed"] or 0,
            "successRate": round(succeeded * 100 / total, 1) if total else 0,
            "averageDurationMs": round(row["average_ms"] or 0),
        }

    def add_metric(self, values: dict[str, Any]) -> dict:
        payload = {
            "capturedAt": values.get("capturedAt", utc_now()),
            "gold": values.get("gold"),
            "level": values.get("level"),
            "experience": values.get("experience"),
            "experienceRequired": values.get("experienceRequired"),
            "rawText": values.get("rawText"),
            "status": values.get("status", "failed"),
            "error": values.get("error"),
            "corrected": bool(values.get("corrected", False)),
        }
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO metric_snapshots(captured_at,gold,level,experience,experience_required,raw_text,status,error,corrected)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (payload["capturedAt"], payload["gold"], payload["level"], payload["experience"],
                 payload["experienceRequired"], payload["rawText"], payload["status"], payload["error"], int(payload["corrected"])),
            )
            payload["id"] = cursor.lastrowid
        return payload

    def update_metric(self, metric_id: int, changes: dict[str, Any]) -> dict | None:
        allowed = {"gold": "gold", "level": "level", "experience": "experience", "experienceRequired": "experience_required"}
        assignments, values = [], []
        for public, column in allowed.items():
            if public in changes:
                assignments.append(f"{column}=?")
                values.append(changes[public])
        if not assignments:
            return self.metric(metric_id)
        assignments.extend(["corrected=1", "status='success'", "error=NULL"])
        values.append(metric_id)
        with self.connect() as db:
            db.execute(f"UPDATE metric_snapshots SET {', '.join(assignments)} WHERE id=?", values)
        return self.metric(metric_id)

    def metric(self, metric_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM metric_snapshots WHERE id=?", (metric_id,)).fetchone()
        return self._metric_row(row) if row else None

    def metrics(self, days: int = 7, metric: str = "gold") -> dict:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        column = "gold" if metric == "gold" else "experience"
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM metric_snapshots WHERE captured_at>=? ORDER BY captured_at", (cutoff,)
            ).fetchall()
        snapshots = [self._metric_row(row) for row in rows]
        points = [{"capturedAt": item["capturedAt"], "value": item[metric], "status": item["status"]} for item in snapshots]
        valid = [point["value"] for point in points if point["value"] is not None and point["status"] == "success"]
        return {
            "metric": metric,
            "rangeDays": days,
            "current": valid[-1] if valid else None,
            "change": valid[-1] - valid[0] if len(valid) >= 2 else None,
            "points": points,
            "snapshots": snapshots,
        }

    def metric_collected_today(self) -> bool:
        local_today = datetime.now().astimezone().date()
        with self.connect() as db:
            rows = db.execute(
                "SELECT captured_at FROM metric_snapshots WHERE status='success' ORDER BY captured_at DESC LIMIT 20"
            ).fetchall()
        for row in rows:
            captured = datetime.fromisoformat(row["captured_at"])
            if captured.astimezone().date() == local_today:
                return True
        return False

    def prune_events(self, days: int = 30) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            db.execute("DELETE FROM run_events WHERE created_at<?", (cutoff,))

    def recover_incomplete_runs(self) -> int:
        """Mark runs abandoned by a previous dashboard process as failed."""
        now = utc_now()
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE task_runs
                SET status='failed', finished_at=?, error='看板进程意外退出，任务状态已恢复'
                WHERE status IN ('preparing','running','stopping')""",
                (now,),
            )
        return cursor.rowcount

    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "taskId": row["task_id"], "taskName": row["task_name"],
            "status": row["status"], "options": json.loads(row["options_json"]),
            "startedAt": row["started_at"], "finishedAt": row["finished_at"],
            "durationMs": row["duration_ms"], "error": row["error"], "benefitStatus": row["benefit_status"],
        }

    @staticmethod
    def _metric_row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "capturedAt": row["captured_at"], "gold": row["gold"],
            "level": row["level"], "experience": row["experience"],
            "experienceRequired": row["experience_required"], "rawText": row["raw_text"],
            "status": row["status"], "error": row["error"], "corrected": bool(row["corrected"]),
        }
