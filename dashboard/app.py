from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .database import Database, utc_now
from .events import EventBroker
from .game_data import collect_daily_task_data
from .game_data_contract import LOCAL_PRIMARY_TARGET_ID
from .metrics import capture_visible_metrics
from .registry import task_list
from .runner import RunnerManager
from .system import inspect_macos, launch_game, open_system_settings


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = Path(os.environ.get("MAA_DASHBOARD_DB", ROOT / "config/dashboard.sqlite3"))
FRONTEND_DIST = ROOT / "web/dist"


class RunRequest(BaseModel):
    taskId: str
    options: dict[str, Any] = Field(default_factory=dict)
    checklistConfirmed: bool = False


class InspectionRequest(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)


class TaskTodayStatusUpdate(BaseModel):
    status: Literal["completed", "pending"]


class MetricCorrection(BaseModel):
    gold: int | None = Field(default=None, ge=0)
    level: int | None = Field(default=None, ge=1)
    experience: int | None = Field(default=None, ge=0)
    experienceRequired: int | None = Field(default=None, ge=0)


def create_app(
    database_path: Path | None = None, *, resource_tracking: bool = False
) -> FastAPI:
    database = Database(database_path or DATA_PATH)
    broker = EventBroker()
    runner = RunnerManager(database, broker, resource_tracking=resource_tracking)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        broker.bind_loop(asyncio.get_running_loop())
        database.recover_incomplete_runs()
        database.prune_events(30)
        yield
        current = runner.current
        if current and current["status"] not in {"succeeded", "failed", "cancelled"}:
            runner.stop(current["id"])

    app = FastAPI(title="Maa MHXY macOS Dashboard", version="0.1.0", lifespan=lifespan)
    app.state.database = database
    app.state.broker = broker
    app.state.runner = runner

    def publish_game_data(snapshot: dict, *, level: str, message: str) -> None:
        broker.publish({
            "runId": "system",
            "createdAt": utc_now(),
            "level": level,
            "type": "game_data",
            "message": message,
            "payload": snapshot,
        })
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/system")
    def system_status():
        system = inspect_macos()
        current = runner.current
        system["run"] = current
        system["status"] = current["status"] if current and current["status"] not in {"succeeded", "failed", "cancelled"} else "idle"
        system["metricCollectedToday"] = database.metric_collected_today()
        return system

    @app.post("/api/game/start", status_code=202)
    async def start_game():
        if runner.inspecting:
            raise HTTPException(status_code=409, detail="画面检测期间不能重新启动游戏")
        current = runner.current
        if current and current["status"] not in {"succeeded", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="任务运行期间不能重新启动游戏")
        try:
            state = await asyncio.to_thread(launch_game)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        broker.publish({
            "runId": "system", "createdAt": utc_now(), "level": "info",
            "type": "system", "message": "梦幻西游已启动", "payload": state,
        })
        return state

    @app.post("/api/system/settings/{section}", status_code=202)
    async def open_settings(section: str):
        try:
            return await asyncio.to_thread(open_system_settings, section)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/tasks")
    def tasks():
        completions = database.today_task_completions()
        items = task_list()
        for task in items:
            task["todayStatus"] = completions.get(task["id"], {
                "status": "pending",
                "completedAt": None,
                "resultStatus": None,
            })
        return {"items": items}

    @app.patch("/api/tasks/{task_id}/today-status")
    def update_task_today_status(task_id: str, request: TaskTodayStatusUpdate):
        if task_id not in {task["id"] for task in task_list()}:
            raise HTTPException(status_code=404, detail="不支持的任务")
        today_status = database.set_task_completion_override(
            task_id, request.status
        )
        return {"taskId": task_id, "todayStatus": today_status}

    @app.post("/api/tasks/{task_id}/inspect")
    async def inspect_task(task_id: str, request: InspectionRequest):
        try:
            return await asyncio.to_thread(runner.inspect, task_id, request.options)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except RuntimeError as exc:
            message = str(exc)
            status_code = 409 if "期间不能" in message or "正在进行" in message else 502
            raise HTTPException(status_code=status_code, detail=message) from exc

    @app.post("/api/runs", status_code=202)
    def start_run(request: RunRequest):
        try:
            return runner.start(request.taskId, request.options, request.checklistConfirmed)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/stop", status_code=202)
    def stop_run(run_id: str):
        try:
            return runner.stop(run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs")
    def runs(limit: int = Query(20, ge=1, le=200)):
        return {"items": database.recent_runs(limit), "summary": database.summary()}

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str, limit: int = Query(200, ge=1, le=300)):
        return {"items": database.events_for_run(run_id, limit)}

    @app.get("/api/metrics")
    def metrics(
        range: str = Query("7d", pattern=r"^\d+d$"),
        metric: Literal["gold", "experience"] = "gold",
    ):
        days = min(365, max(1, int(range[:-1])))
        return database.metrics(days, metric)

    @app.get("/api/game-data")
    def game_data(
        category: str | None = Query(
            default=None,
            min_length=1,
            max_length=80,
            pattern=r"^[a-z][a-z0-9_]*$",
        ),
        limit: int = Query(50, ge=1, le=200),
        targetId: str = Query(
            LOCAL_PRIMARY_TARGET_ID,
            min_length=1,
            max_length=64,
            pattern=r"^[a-z][a-z0-9-]*$",
        ),
    ):
        items = database.game_data_snapshots(
            category=category, limit=limit, target_id=targetId
        )
        latest_attempt = (
            {category: database.latest_game_data(category, target_id=targetId)}
            if category is not None
            else database.latest_game_data_by_category(target_id=targetId)
        )
        latest_usable = (
            {
                category: database.latest_game_data(
                    category, target_id=targetId, usable_only=True
                )
            }
            if category is not None
            else database.latest_game_data_by_category(
                target_id=targetId, usable_only=True
            )
        )
        return {
            "targetId": targetId,
            "items": items,
            "latestByCategory": latest_attempt,
            "latestAttemptByCategory": latest_attempt,
            "latestUsableByCategory": latest_usable,
        }

    @app.post("/api/game-data/daily-tasks/capture", status_code=202)
    async def capture_daily_tasks():
        try:
            runner.begin_read_only()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            payload = await asyncio.to_thread(collect_daily_task_data)
        except TimeoutError as exc:
            error = str(exc)
            snapshot = database.add_game_data_snapshot({
                "targetId": LOCAL_PRIMARY_TARGET_ID,
                "category": "daily_tasks",
                "sourcePage": "daily_schedule_capture",
                "status": "failed",
                "data": {"tasks": []},
                "evidence": {},
                "error": error,
            })
            publish_game_data(snapshot, level="error", message=error)
            raise HTTPException(status_code=504, detail=error) from exc
        except RuntimeError as exc:
            error = str(exc)
            snapshot = database.add_game_data_snapshot({
                "targetId": LOCAL_PRIMARY_TARGET_ID,
                "category": "daily_tasks",
                "sourcePage": "daily_schedule_capture",
                "status": "failed",
                "data": {"tasks": []},
                "evidence": {},
                "error": error,
            })
            publish_game_data(snapshot, level="error", message=error)
            raise HTTPException(status_code=502, detail=error) from exc
        finally:
            runner.end_read_only()

        snapshot = database.add_game_data_snapshot({
            "targetId": LOCAL_PRIMARY_TARGET_ID,
            "category": payload["category"],
            "sourcePage": payload["source_page"],
            "status": payload["status"],
            "data": payload["data"],
            "evidence": payload["evidence"],
        })
        publish_game_data(snapshot, level="info", message="今日任务数据采集完成")
        return snapshot

    @app.post("/api/metrics/capture", status_code=202)
    async def capture_metrics():
        if runner.inspecting:
            raise HTTPException(status_code=409, detail="画面检测期间不能采集指标")
        current = runner.current
        if current and current["status"] not in {"succeeded", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="任务运行期间不能采集指标")
        try:
            runner.begin_read_only()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            try:
                values = await asyncio.to_thread(capture_visible_metrics)
            except Exception as exc:
                values = {"status": "failed", "error": str(exc), "rawText": None}
        finally:
            runner.end_read_only()
        snapshot = database.add_metric(values)
        resource_fields = ("gold", "level", "experience", "experienceRequired")
        resource_data = {field: snapshot.get(field) for field in resource_fields}
        captured_fields = [
            field for field, value in resource_data.items() if value is not None
        ]
        game_data_status = (
            "success"
            if snapshot["status"] == "success"
            else "partial"
            if captured_fields
            else "failed"
        )
        database.add_game_data_snapshot({
            "capturedAt": snapshot["capturedAt"],
            "targetId": LOCAL_PRIMARY_TARGET_ID,
            "category": "character_resources",
            "sourcePage": "backpack_profile",
            "status": game_data_status,
            "data": resource_data,
            "evidence": {
                "metricSnapshotId": snapshot["id"],
                "capturedFields": captured_fields,
                "rawOcrStored": False,
                "fields": {
                    field: {
                        "status": "captured" if value is not None else "missing",
                        "confidence": None,
                        "sourcePage": (
                            "backpack" if field == "gold" else "character_profile"
                        ),
                    }
                    for field, value in resource_data.items()
                },
            },
            "error": snapshot.get("error"),
        })
        broker.publish({
            "runId": "system", "createdAt": utc_now(), "level": "info" if snapshot["status"] == "success" else "error",
            "type": "metric", "message": "指标采集完成" if snapshot["status"] == "success" else snapshot["error"],
            "payload": snapshot,
        })
        return snapshot

    @app.patch("/api/metrics/{metric_id}")
    def correct_metric(metric_id: int, request: MetricCorrection):
        changes = request.model_dump(exclude_unset=True)
        if not changes or not any(value is not None for value in changes.values()):
            raise HTTPException(status_code=422, detail="至少填写一个可确认的指标数值")
        snapshot = database.update_metric(metric_id, changes)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="指标记录不存在")
        return snapshot

    @app.get("/api/events")
    async def events(last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0)):
        async def stream():
            yield ": connected\n\n"
            subscription = broker.subscribe().__aiter__()
            next_event = asyncio.create_task(subscription.__anext__())
            await asyncio.sleep(0)
            last_sent_id = last_event_id or 0
            try:
                if last_event_id is not None:
                    for event in database.events_after(last_event_id):
                        last_sent_id = max(last_sent_id, event["id"])
                        yield _sse_event(event)
                while True:
                    done, _ = await asyncio.wait({next_event}, timeout=15)
                    if not done:
                        yield ": keepalive\n\n"
                        continue
                    event = next_event.result()
                    next_event = asyncio.create_task(subscription.__anext__())
                    event_id = event.get("id")
                    if event_id is not None and event_id <= last_sent_id:
                        continue
                    if event_id is not None:
                        last_sent_id = event_id
                    yield _sse_event(event)
            finally:
                next_event.cancel()
                with suppress(asyncio.CancelledError):
                    await next_event
                await subscription.aclose()

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    if FRONTEND_DIST.exists():
        assets = FRONTEND_DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}")
        def frontend(path: str):
            target = FRONTEND_DIST / path
            if path and target.is_file() and FRONTEND_DIST in target.resolve().parents:
                return FileResponse(target)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


def _sse_event(event: dict) -> str:
    event_id = f"id: {event['id']}\n" if event.get("id") is not None else ""
    return f"{event_id}event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


app = create_app(resource_tracking=True)
