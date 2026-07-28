from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

from .game_data_contract import (
    GAME_DATA_CATEGORIES,
    GAME_DATA_SCHEMA_VERSION,
    GAME_DATA_STATUSES,
    LOCAL_PRIMARY_TARGET_ID,
    snapshot_is_stale,
    validate_category_data,
    validate_evidence,
    validate_game_date,
    validate_target_id,
)

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
                    result_json TEXT,
                    benefit_status TEXT NOT NULL DEFAULT 'not_captured'
                );
                DROP INDEX IF EXISTS idx_task_runs_started_task;
                CREATE INDEX IF NOT EXISTS idx_task_runs_completed_task
                ON task_runs(COALESCE(finished_at, started_at), task_id);
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
                CREATE TABLE IF NOT EXISTS game_data_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    game_date TEXT,
                    schema_version INTEGER NOT NULL DEFAULT 2,
                    target_id TEXT NOT NULL DEFAULT 'local-primary',
                    category TEXT NOT NULL,
                    source_page TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT
                );
                """
            )
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(task_runs)")
            }
            if "result_json" not in columns:
                db.execute("ALTER TABLE task_runs ADD COLUMN result_json TEXT")
            game_data_columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(game_data_snapshots)")
            }
            if "game_date" not in game_data_columns:
                db.execute("ALTER TABLE game_data_snapshots ADD COLUMN game_date TEXT")
                db.execute(
                    "UPDATE game_data_snapshots SET game_date=substr(captured_at, 1, 10)"
                )
            if "schema_version" not in game_data_columns:
                db.execute(
                    "ALTER TABLE game_data_snapshots ADD COLUMN schema_version "
                    "INTEGER NOT NULL DEFAULT 1"
                )
            if "target_id" not in game_data_columns:
                db.execute(
                    "ALTER TABLE game_data_snapshots ADD COLUMN target_id "
                    "TEXT NOT NULL DEFAULT 'local-primary'"
                )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_game_data_target_category_captured "
                "ON game_data_snapshots(target_id, category, captured_at DESC, id DESC)"
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

    def update_run(
        self,
        run_id: str,
        status: str,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as db:
            row = db.execute("SELECT started_at FROM task_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return
            started = datetime.fromisoformat(row["started_at"])
            duration = max(0, int((datetime.now(UTC) - started).total_seconds() * 1000))
            db.execute(
                "UPDATE task_runs SET status=?, finished_at=?, duration_ms=?, error=?, result_json=? WHERE id=?",
                (
                    status,
                    now,
                    duration,
                    error,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    run_id,
                ),
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

    def today_task_completions(
        self,
        local_date: date | None = None,
        local_timezone: tzinfo | None = None,
    ) -> dict[str, dict[str, str | None]]:
        if local_date is None:
            local_date = (
                datetime.now(local_timezone).date()
                if local_timezone is not None
                else datetime.now().astimezone().date()
            )
        next_date = local_date + timedelta(days=1)
        if local_timezone is None:
            # Naive datetimes are interpreted using the machine timezone, including DST.
            start_utc = datetime.combine(local_date, time.min).astimezone(UTC)
            end_utc = datetime.combine(next_date, time.min).astimezone(UTC)
        else:
            start_utc = datetime.combine(
                local_date, time.min, tzinfo=local_timezone
            ).astimezone(UTC)
            end_utc = datetime.combine(
                next_date, time.min, tzinfo=local_timezone
            ).astimezone(UTC)

        with self.connect() as db:
            rows = db.execute(
                """SELECT task_id, started_at, finished_at, result_json,
                COALESCE(finished_at, started_at) completed_at
                FROM task_runs
                WHERE status='succeeded'
                AND COALESCE(finished_at, started_at)>=?
                AND COALESCE(finished_at, started_at)<?
                ORDER BY completed_at DESC""",
                (start_utc.isoformat(), end_utc.isoformat()),
            ).fetchall()

        completions: dict[str, dict[str, str | None]] = {}
        for row in rows:
            if row["task_id"] in completions or not row["result_json"]:
                continue
            try:
                result = json.loads(row["result_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            result_status = result.get("status") if isinstance(result, dict) else None
            if result_status not in {"completed", "already_completed"}:
                continue
            completions[row["task_id"]] = {
                "status": "completed",
                "completedAt": row["completed_at"],
                "resultStatus": result_status,
            }
        return completions

    def events_after(self, event_id: int, limit: int = 300) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM run_events WHERE id>? ORDER BY id LIMIT ?",
                (event_id, limit),
            ).fetchall()
        return [self._event_row(row) for row in rows]

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

    def add_game_data_snapshot(self, values: dict[str, Any]) -> dict:
        captured_at = values.get("capturedAt", utc_now())
        schema_version = values.get("schemaVersion", GAME_DATA_SCHEMA_VERSION)
        target_id = values.get("targetId", LOCAL_PRIMARY_TARGET_ID)
        category = values.get("category")
        source_page = values.get("sourcePage")
        status = values.get("status")
        data = values.get("data", {})
        evidence = values.get("evidence", {})
        error = values.get("error")

        if not isinstance(captured_at, str) or not captured_at.strip():
            raise ValueError("游戏数据采集时间无效")
        try:
            captured_datetime = datetime.fromisoformat(captured_at)
        except ValueError as exc:
            raise ValueError("游戏数据采集时间无效") from exc
        game_date = values.get(
            "gameDate", captured_datetime.astimezone().date().isoformat()
        )
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != GAME_DATA_SCHEMA_VERSION
        ):
            raise ValueError("游戏数据契约版本无效")
        target_id = validate_target_id(target_id)
        game_date = validate_game_date(game_date)
        if category not in GAME_DATA_CATEGORIES:
            raise ValueError("不支持的游戏数据分类")
        if not isinstance(source_page, str) or not source_page.strip():
            raise ValueError("游戏数据来源页面不能为空")
        if status not in GAME_DATA_STATUSES:
            raise ValueError("游戏数据状态必须是 success、partial 或 failed")
        if not isinstance(data, dict):
            raise ValueError("游戏数据内容必须是对象")
        if not isinstance(evidence, dict):
            raise ValueError("游戏数据证据必须是对象")
        if error is not None and (not isinstance(error, str) or not error.strip()):
            raise ValueError("游戏数据错误信息无效")
        validate_category_data(category, status, data)
        validate_evidence(evidence)

        payload = {
            "capturedAt": captured_at,
            "gameDate": game_date,
            "schemaVersion": schema_version,
            "targetId": target_id,
            "category": category,
            "sourcePage": source_page.strip(),
            "status": status,
            "data": data,
            "evidence": evidence,
            "error": error,
        }
        try:
            data_json = json.dumps(data, ensure_ascii=False, allow_nan=False)
            evidence_json = json.dumps(
                evidence, ensure_ascii=False, allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("游戏数据快照包含无法序列化的内容") from exc
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO game_data_snapshots(
                captured_at, game_date, schema_version, target_id, category,
                source_page, status, data_json, evidence_json, error
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    captured_at,
                    game_date,
                    schema_version,
                    target_id,
                    payload["category"],
                    payload["sourcePage"],
                    status,
                    data_json,
                    evidence_json,
                    error,
                ),
            )
            payload["id"] = cursor.lastrowid
        payload["stale"] = snapshot_is_stale(payload)
        return payload

    def game_data_snapshots(
        self,
        category: str | None = None,
        limit: int = 50,
        target_id: str = LOCAL_PRIMARY_TARGET_ID,
        statuses: set[str] | None = None,
    ) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("游戏数据查询数量必须是正整数")
        if category is not None and (
            not isinstance(category, str) or not category.strip()
        ):
            raise ValueError("游戏数据分类不能为空")
        target_id = validate_target_id(target_id)
        if statuses is not None and (not statuses or not statuses <= GAME_DATA_STATUSES):
            raise ValueError("游戏数据查询状态无效")
        query = "SELECT * FROM game_data_snapshots WHERE target_id=?"
        parameters: list[Any] = [target_id]
        if category is not None:
            query += " AND category=?"
            parameters.append(category.strip())
        if statuses is not None:
            placeholders = ",".join("?" for _ in statuses)
            query += f" AND status IN ({placeholders})"
            parameters.extend(sorted(statuses))
        query += " ORDER BY captured_at DESC, id DESC LIMIT ?"
        parameters.append(limit)
        with self.connect() as db:
            rows = db.execute(query, parameters).fetchall()
        return [self._game_data_row(row) for row in rows]

    def latest_game_data(
        self,
        category: str | None = None,
        target_id: str = LOCAL_PRIMARY_TARGET_ID,
        usable_only: bool = False,
    ) -> dict | None:
        snapshots = self.game_data_snapshots(
            category=category,
            limit=1,
            target_id=target_id,
            statuses={"success"} if usable_only else None,
        )
        return snapshots[0] if snapshots else None

    def latest_game_data_by_category(
        self,
        target_id: str = LOCAL_PRIMARY_TARGET_ID,
        usable_only: bool = False,
    ) -> dict[str, dict]:
        target_id = validate_target_id(target_id)
        status_clause = "AND status='success'" if usable_only else ""
        with self.connect() as db:
            rows = db.execute(
                f"""SELECT * FROM (
                SELECT game_data_snapshots.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY category
                           ORDER BY captured_at DESC, id DESC
                       ) AS category_rank
                FROM game_data_snapshots
                WHERE target_id=? {status_clause}
                ) WHERE category_rank=1
                ORDER BY category""",
                (target_id,),
            ).fetchall()
        return {
            row["category"]: self._game_data_row(row)
            for row in rows
        }

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
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM metric_snapshots WHERE captured_at>=? ORDER BY captured_at", (cutoff,)
            ).fetchall()
        snapshots = [self._metric_row(row) for row in rows]
        points = [{"capturedAt": item["capturedAt"], "value": item[metric], "status": item["status"]} for item in snapshots]
        valid = [point["value"] for point in points if point["value"] is not None and point["status"] == "success"]
        successful_captures = sum(item["status"] == "success" for item in snapshots)
        failed_captures = len(snapshots) - successful_captures
        latest_change = valid[-1] - valid[-2] if len(valid) >= 2 else None
        return {
            "metric": metric,
            "rangeDays": days,
            "current": valid[-1] if valid else None,
            "change": valid[-1] - valid[0] if len(valid) >= 2 else None,
            "stats": {
                "attempts": len(snapshots),
                "successfulCaptures": successful_captures,
                "failedCaptures": failed_captures,
                "correctedCaptures": sum(item["corrected"] for item in snapshots),
                "captureSuccessRate": round(successful_captures * 100 / len(snapshots), 1) if snapshots else 0,
                "validValues": len(valid),
                "minimum": min(valid) if valid else None,
                "maximum": max(valid) if valid else None,
                "average": round(sum(valid) / len(valid)) if valid else None,
                "latestChange": latest_change,
                "averageChangePerCapture": round((valid[-1] - valid[0]) / (len(valid) - 1)) if len(valid) >= 2 else None,
                "daysWithData": len({
                    datetime.fromisoformat(point["capturedAt"]).astimezone().date()
                    for point in points
                    if point["value"] is not None and point["status"] == "success"
                }),
            },
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
            rows = db.execute(
                "SELECT id, started_at FROM task_runs WHERE status IN ('preparing','running','stopping')"
            ).fetchall()
            finished = datetime.fromisoformat(now)
            for row in rows:
                started = datetime.fromisoformat(row["started_at"])
                duration = max(0, int((finished - started).total_seconds() * 1000))
                error = "看板进程意外退出，任务状态已恢复"
                db.execute(
                    "UPDATE task_runs SET status='failed', finished_at=?, duration_ms=?, error=? WHERE id=?",
                    (now, duration, error, row["id"]),
                )
                db.execute(
                    """INSERT INTO run_events(run_id, created_at, level, event_type, message, payload_json)
                    VALUES(?,?,?,?,?,?)""",
                    (row["id"], now, "error", "status", error, '{"status":"failed","recovered":true}'),
                )
        return len(rows)

    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "taskId": row["task_id"], "taskName": row["task_name"],
            "status": row["status"], "options": json.loads(row["options_json"]),
            "startedAt": row["started_at"], "finishedAt": row["finished_at"],
            "durationMs": row["duration_ms"], "error": row["error"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "benefitStatus": row["benefit_status"],
        }

    @staticmethod
    def _metric_row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "capturedAt": row["captured_at"], "gold": row["gold"],
            "level": row["level"], "experience": row["experience"],
            "experienceRequired": row["experience_required"], "rawText": row["raw_text"],
            "status": row["status"], "error": row["error"], "corrected": bool(row["corrected"]),
        }

    @staticmethod
    def _game_data_row(row: sqlite3.Row) -> dict:
        payload = {
            "id": row["id"],
            "capturedAt": row["captured_at"],
            "gameDate": row["game_date"],
            "schemaVersion": row["schema_version"],
            "targetId": row["target_id"],
            "category": row["category"],
            "sourcePage": row["source_page"],
            "status": row["status"],
            "data": json.loads(row["data_json"]),
            "evidence": json.loads(row["evidence_json"]),
            "error": row["error"],
        }
        payload["stale"] = snapshot_is_stale(payload)
        return payload

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "runId": row["run_id"], "createdAt": row["created_at"],
            "level": row["level"], "type": row["event_type"], "message": row["message"],
            "payload": json.loads(row["payload_json"]),
        }
