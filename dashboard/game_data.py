from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GAME_DATA_PREFIX = "game_data_json="
COLLECTION_TIMEOUT_SECONDS = 15.0
DAILY_TASK_STATUSES = {"completed", "available", "locked", "unknown"}
REWARD_STATUSES = {"claimable", "claimed", "unknown"}


def collect_daily_task_data(timeout: float = COLLECTION_TIMEOUT_SECONDS) -> dict:
    command = [sys.executable, str(ROOT / "tools/run_daily_schedule_macos.py")]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        raise TimeoutError("日程数据采集超过 15 秒，已停止采集") from exc
    if process.returncode != 0:
        raise RuntimeError("日程数据采集失败，请确认日程页面完整可见")
    return parse_daily_task_data(output)


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def parse_daily_task_data(output: str) -> dict:
    protocol_lines = [
        line[len(GAME_DATA_PREFIX):]
        for line in output.splitlines()
        if line.startswith(GAME_DATA_PREFIX)
    ]
    if len(protocol_lines) != 1:
        raise RuntimeError("日程采集没有返回唯一的结构化结果")
    try:
        payload = json.loads(protocol_lines[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError("日程采集返回了无效的结构化结果") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("日程采集返回了无效的结构化结果")
    if payload.get("category") != "daily_tasks":
        raise RuntimeError("日程采集返回了错误的数据类别")
    if payload.get("source_page") != "daily_schedule":
        raise RuntimeError("日程采集返回了错误的来源页面")
    if payload.get("status") not in {"success", "partial"}:
        raise RuntimeError("日程采集返回了无效的采集状态")

    data = payload.get("data")
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        raise RuntimeError("日程采集结果缺少任务列表")
    for task in tasks:
        _validate_daily_task(task)

    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        raise RuntimeError("日程采集结果缺少识别证据")
    image_size = evidence.get("image_size")
    if not (
        isinstance(image_size, list)
        and len(image_size) == 2
        and all(isinstance(value, int) and value > 0 for value in image_size)
    ):
        raise RuntimeError("日程采集结果包含无效的截图尺寸")
    ocr_count = evidence.get("ocr_item_count")
    if isinstance(ocr_count, bool) or not isinstance(ocr_count, int) or ocr_count < 0:
        raise RuntimeError("日程采集结果包含无效的 OCR 计数")
    return payload


def _validate_daily_task(task: object) -> None:
    if not isinstance(task, dict):
        raise RuntimeError("日程采集结果包含无效的任务")
    if set(task) - {
        "taskId", "name", "completed", "target", "status", "rewardStatus"
    }:
        raise RuntimeError("日程采集任务包含未声明字段")
    name = task.get("name")
    if not isinstance(name, str) or not name.strip():
        raise RuntimeError("日程采集任务缺少名称")
    task_id = task.get("taskId")
    if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
        raise RuntimeError("日程采集任务包含无效标识")
    for field in ("completed", "target"):
        value = task.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise RuntimeError("日程采集任务包含无效进度")
    completed, target = task.get("completed"), task.get("target")
    if completed is not None and target is not None and completed > target:
        raise RuntimeError("日程采集任务的完成次数超过目标")
    if task.get("status") not in DAILY_TASK_STATUSES:
        raise RuntimeError("日程采集任务包含无效状态")
    if task.get("rewardStatus") not in REWARD_STATUSES:
        raise RuntimeError("日程采集任务包含无效奖励状态")
