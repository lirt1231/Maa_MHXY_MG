from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image

from tools.fixed_game_controls import (
    CLOSE_STANDARD_PANEL,
    OPEN_BACKPACK,
    OPEN_CHARACTER_PROFILE,
    REFERENCE_SIZE,
)


ROOT = Path(__file__).resolve().parents[1]
OCR_SIZE = REFERENCE_SIZE
BACKPACK_ENTRY_POINT = OPEN_BACKPACK.point
PROFILE_ENTRY_POINT = OPEN_CHARACTER_PROFILE.point
RESOURCE_CAPTURE_FIELDS = frozenset(
    {"level", "experience", "experience_required", "gold", "silver"}
)


def parse_number(value: str) -> int | None:
    normalized = str(value or "").translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    match = re.search(r"-?\d[\d,，.]*", normalized)
    if not match:
        return None
    digits = re.sub(r"[,，.]", "", match.group(0))
    try:
        return int(digits)
    except ValueError:
        return None


def parse_visible_metrics(texts: Iterable[str]) -> dict:
    """Compatibility parser for labelled OCR text."""
    items = [re.sub(r"\s+", "", str(text)) for text in texts if str(text).strip()]
    result = {"gold": None, "level": None, "experience": None, "experienceRequired": None}
    for index, text in enumerate(items):
        if result["gold"] is None and any(key in text for key in ("金币", "金市")):
            result["gold"] = parse_number(text)
            if result["gold"] is None and index + 1 < len(items):
                result["gold"] = parse_number(items[index + 1])
        if result["level"] is None and ("等级" in text or re.fullmatch(r"\d+级", text)):
            result["level"] = parse_number(text)
        if "经验" in text and result["experience"] is None:
            match = re.search(r"(\d[\d,，.]*)\s*/\s*(\d[\d,，.]*)", text)
            if match:
                result["experience"] = parse_number(match.group(1))
                result["experienceRequired"] = parse_number(match.group(2))
            else:
                result["experience"] = parse_number(text)
    return result


def parse_backpack_resources(results: list[dict]) -> dict:
    """Read the two fixed currency slots in the normalized bag footer."""
    candidates: list[tuple[float, dict, int]] = []
    for item in results:
        x, y, width, _ = item["box"]
        text = re.sub(r"\s+", "", item["text"])
        center_x = x + width / 2
        if not 565 <= y <= 640 or not re.fullmatch(r"\d[\d,，.]*", text):
            continue
        value = parse_number(text)
        if value is not None:
            candidates.append((center_x, item, value))

    def in_slot(left: float, right: float, target: float):
        matches = [item for item in candidates if left <= item[0] < right]
        return min(matches, key=lambda item: abs(item[0] - target)) if matches else None

    gold = in_slot(120, 300, 210)
    silver = in_slot(300, 520, 395)
    return {
        "gold": None if gold is None else gold[2],
        "silver": None if silver is None else silver[2],
        "goldItem": None if gold is None else gold[1],
        "silverItem": None if silver is None else silver[1],
    }


def parse_backpack_gold(results: list[dict]) -> int | None:
    return parse_backpack_resources(results)["gold"]


def parse_profile_resources(results: list[dict]) -> dict:
    level = None
    experience = None
    required = None
    level_item = None
    experience_item = None
    for item in results:
        text = re.sub(r"\s+", "", item["text"])
        if "级" in text:
            match = re.search(r"(\d+)级", text)
            if match:
                digits = match.group(1)
                for width in (3, 2, 1):
                    if len(digits) >= width:
                        candidate = int(digits[-width:])
                        if 20 <= candidate <= 200:
                            level = candidate
                            level_item = item
                            break
        x, y, _, _ = item["box"]
        fraction = re.fullmatch(r"(\d[\d,，.]*)/(\d[\d,，.]*)", text)
        if fraction and y >= 500 and x >= 450:
            numerator = parse_number(fraction.group(1))
            denominator = parse_number(fraction.group(2))
            if numerator is not None and denominator is not None and numerator >= 10000:
                experience, required = numerator, denominator
                experience_item = item
    return {
        "level": level,
        "experience": experience,
        "experienceRequired": required,
        "levelItem": level_item,
        "experienceItem": experience_item,
    }


def parse_profile_metrics(results: list[dict]) -> dict:
    parsed = parse_profile_resources(results)
    return {
        "level": parsed["level"],
        "experience": parsed["experience"],
        "experienceRequired": parsed["experienceRequired"],
    }


def _window_capture(window_id: int) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".png") as output:
        completed = subprocess.run(
            ["/usr/sbin/screencapture", "-x", "-l", str(window_id), output.name],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if completed.returncode != 0:
            raise RuntimeError("游戏窗口截图失败")
        image = Image.open(output.name).convert("RGB")
        raw = np.asarray(image)
        visible = raw.max(axis=2) > 20
        ys, xs = np.where(visible)
        if not len(xs):
            raise RuntimeError("游戏窗口截图为空")
        # screencapture includes the transparent window border and native title bar.
        pixel_scale = 2 if image.width >= 1600 else 1
        left, right = int(xs.min()), int(xs.max()) + 1
        top = min(int(ys.max()), int(ys.min()) + 24 * pixel_scale)
        bottom = int(ys.max()) + 1
        content = image.crop((left, top, right, bottom)).resize(OCR_SIZE)
        return np.asarray(content)[:, :, ::-1].copy()


def _recognize(tasker, image: np.ndarray) -> list[dict]:
    from maa.pipeline import JOCR

    height, width = image.shape[:2]
    detail = tasker.post_recognition(
        "OCR", JOCR(expected=[".*"], roi=(0, 0, width, height), threshold=0.2), image
    ).wait().get()
    if detail is None or not detail.nodes or detail.nodes[-1].recognition is None:
        return []
    return [
        {"text": item.text, "box": list(item.box)}
        for item in detail.nodes[-1].recognition.filtered_results
    ]


def _has(results: list[dict], *needles: str) -> bool:
    texts = [re.sub(r"\s+", "", item["text"]) for item in results]
    return all(any(needle in text for text in texts) for needle in needles)


def _is_main(results: list[dict]) -> bool:
    return _has(results, "任务") and any(
        _has(results, marker) for marker in ("活动", "指引", "商城")
    )


def _is_backpack(results: list[dict]) -> bool:
    title = any(
        "包裹" in re.sub(r"\s+", "", item["text"])
        and 330 <= item["box"][0] <= 720
        and item["box"][1] <= 170
        for item in results
    )
    anchors = sum(
        any(marker in re.sub(r"\s+", "", item["text"]) for item in results)
        for marker in ("物品", "任务", "整理", "礼包", "仓库")
    )
    currencies = parse_backpack_resources(results)
    return bool(
        title
        and anchors >= 2
        and currencies["gold"] is not None
        and currencies["silver"] is not None
    )


def _is_profile(results: list[dict]) -> bool:
    normalized = [re.sub(r"\s+", "", item["text"]) for item in results]
    title = any("人物属性" in text for text in normalized)
    anchors = sum(
        any(marker in text for text in normalized)
        for marker in ("气血", "魔法", "物理", "法术", "速度", "评分")
    )
    return title and anchors >= 2


def _close_panel(native_input) -> None:
    # Standard bag/profile panels share this close-button position at the normalized macOS layout.
    native_input.click(*CLOSE_STANDARD_PANEL.point, OCR_SIZE)
    time.sleep(1)


def _wait_for(
    tasker,
    window_id: int,
    predicate,
    timeout: float = 8,
    *,
    step: str = "游戏页面切换",
) -> tuple[np.ndarray, list[dict]]:
    deadline = time.monotonic() + timeout
    last_image, last_results = None, []
    while time.monotonic() < deadline:
        last_image = _window_capture(window_id)
        last_results = _recognize(tasker, last_image)
        if predicate(last_results):
            return last_image, last_results
        time.sleep(0.6)
    raise RuntimeError(f"{step}超时，请确认窗口完整可见且当前不在战斗中")


def _wait_for_stable_values(
    tasker,
    window_id: int,
    parser,
    value_keys: tuple[str, ...],
    initial_results: list[dict],
    timeout: float = 6,
) -> tuple[np.ndarray, list[dict], dict]:
    previous = tuple(parser(initial_results).get(key) for key in value_keys)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        image = _window_capture(window_id)
        results = _recognize(tasker, image)
        parsed = parser(results)
        current = tuple(parsed.get(key) for key in value_keys)
        if current == previous:
            return image, results, parsed
        previous = current
    raise RuntimeError("资源数字在限定时间内没有稳定，请等待奖励动画结束后重试")


def _field_evidence(item: dict | None, source_page: str, frame_id: str) -> dict:
    return {
        "status": "captured" if item is not None else "missing",
        "sourcePage": source_page,
        "rawText": None if item is None else item["text"],
        "confidence": None,
        "box": None if item is None else list(item["box"]),
        "frameId": frame_id,
    }


def _ensure_main(tasker, native_input, window_id: int) -> list[dict]:
    for _ in range(4):
        _, results = _wait_for(
            tasker,
            window_id,
            lambda value: bool(value),
            timeout=4,
            step="等待游戏画面",
        )
        if _is_main(results):
            return results
        if _is_profile(results) or _is_backpack(results):
            _close_panel(native_input)
            continue
        raise RuntimeError("当前页面无法安全返回主界面，请先结束战斗或关闭特殊活动页面")
    raise RuntimeError("自动返回游戏主界面失败")


def _restore_known_resource_panel(tasker, native_input, window_id: int) -> bool:
    """Close only a positively identified bag/profile panel after capture errors."""
    try:
        image = _window_capture(window_id)
        results = _recognize(tasker, image)
        if _is_main(results):
            return True
        if not (_is_backpack(results) or _is_profile(results)):
            return False
        _close_panel(native_input)
        _wait_for(
            tasker,
            window_id,
            _is_main,
            timeout=6,
            step="等待关闭资源面板后主界面恢复",
        )
        return True
    except Exception:
        return False


def _resolve_game_window(toolkit, native_input_class):
    windows = [
        item for item in toolkit.find_desktop_windows()
        if item.class_name == "com.netease.my" and item.window_name == "梦幻西游"
    ]
    if len(windows) > 1:
        raise RuntimeError("检测到多个梦幻西游窗口，无法保证采集数据属于同一角色")
    window_id = windows[0].hwnd if windows else 0
    native_input = native_input_class(window_id) if window_id else native_input_class()
    if not window_id:
        window_id = native_input.find_window_id()
    if not window_id:
        raise RuntimeError("未找到已打开且可见的梦幻西游窗口")
    native_input.bind_window(window_id)
    return window_id, native_input


def capture_visible_metrics(
    *, captured_fields: frozenset[str] = RESOURCE_CAPTURE_FIELDS,
) -> dict:
    """Capture stable currencies and experience, then restore the main page."""
    if not isinstance(captured_fields, frozenset) or not captured_fields or (
        captured_fields - RESOURCE_CAPTURE_FIELDS
    ):
        raise ValueError("资源采集范围无效")
    from maa.controller import MacOSController, MaaMacOSInputMethodEnum, MaaMacOSScreencapMethodEnum
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from tools.macos_window_adapter import NativeMacOSInput

    Toolkit.init_option(str(ROOT))
    window_id, native_input = _resolve_game_window(Toolkit, NativeMacOSInput)
    controller = MacOSController(
        window_id,
        MaaMacOSScreencapMethodEnum.ScreenCaptureKit,
        MaaMacOSInputMethodEnum.GlobalEvent,
    )
    if not controller.post_connection().wait().succeeded:
        raise RuntimeError("连接梦幻西游窗口失败")
    resource = Resource()
    for bundle in (ROOT / "assets/resource/base", ROOT / "assets/resource/mac"):
        if not resource.post_bundle(bundle).wait().succeeded:
            raise RuntimeError(f"加载资源失败：{bundle.name}")
    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise RuntimeError("初始化 Maa Tasker 失败")

    try:
        main_results = _ensure_main(tasker, native_input, window_id)
        currencies = {"gold": None, "silver": None, "goldItem": None, "silverItem": None}
        bag_results, bag_image = [], None
        if captured_fields & {"gold", "silver"}:
            # The backpack icon is fixed in the verified main canvas.  Its
            # location is mapped from OCR_SIZE by NativeMacOSInput, so OCR is
            # reserved for the changing currency values rather than click
            # placement.
            native_input.click(*BACKPACK_ENTRY_POINT, OCR_SIZE)
            _, bag_results = _wait_for(
                tasker,
                window_id,
                _is_backpack,
                step="等待包裹页面打开",
            )
            try:
                bag_image, bag_results, currencies = _wait_for_stable_values(
                    tasker,
                    window_id,
                    parse_backpack_resources,
                    tuple(field for field in ("gold", "silver") if field in captured_fields),
                    bag_results,
                )
            finally:
                _close_panel(native_input)
            _wait_for(
                tasker,
                window_id,
                _is_main,
                step="等待关闭包裹后主界面恢复",
            )

        profile = {"level": None, "experience": None, "experienceRequired": None, "levelItem": None, "experienceItem": None}
        profile_results, profile_image = [], None
        if captured_fields & {"level", "experience", "experience_required"}:
            native_input.click(*PROFILE_ENTRY_POINT, OCR_SIZE)
            _, profile_results = _wait_for(
                tasker,
                window_id,
                _is_profile,
                step="等待人物属性页面打开",
            )
            try:
                profile_image, profile_results, profile = _wait_for_stable_values(
                    tasker,
                    window_id,
                    parse_profile_resources,
                    tuple(
                        field
                        for field in ("level", "experience", "experienceRequired")
                        if field.replace("Required", "_required") in captured_fields
                    ),
                    profile_results,
                )
            finally:
                _close_panel(native_input)
            _wait_for(
                tasker,
                window_id,
                _is_main,
                step="等待关闭人物属性后主界面恢复",
            )
    finally:
        _restore_known_resource_panel(tasker, native_input, window_id)

    raw_text = "背包：" + " | ".join(item["text"] for item in bag_results)
    raw_text += "\n人物属性：" + " | ".join(item["text"] for item in profile_results)
    bag_frame_id = None if bag_image is None else hashlib.sha256(bag_image.tobytes()).hexdigest()
    profile_frame_id = None if profile_image is None else hashlib.sha256(profile_image.tobytes()).hexdigest()
    values = {
        "gold": currencies["gold"] if "gold" in captured_fields else None,
        "silver": currencies["silver"] if "silver" in captured_fields else None,
        "level": profile["level"] if "level" in captured_fields else None,
        "experience": profile["experience"] if "experience" in captured_fields else None,
        "experienceRequired": profile["experienceRequired"] if "experience_required" in captured_fields else None,
    }
    external_names = {
        "level": "level", "experience": "experience", "experience_required": "experienceRequired",
        "gold": "gold", "silver": "silver",
    }
    missing = [name for name, key in external_names.items() if name in captured_fields and values[key] is None]
    return {
        **values,
        "rawText": raw_text,
        "error": f"未能识别字段：{', '.join(missing)}" if missing else None,
        "resourceEvidence": {
            **({"gold": _field_evidence(currencies["goldItem"], "backpack", bag_frame_id)} if "gold" in captured_fields else {}),
            **({"silver": _field_evidence(currencies["silverItem"], "backpack", bag_frame_id)} if "silver" in captured_fields else {}),
            **({"level": _field_evidence(profile["levelItem"], "character_profile", profile_frame_id)} if "level" in captured_fields else {}),
            **({"experience": _field_evidence(profile["experienceItem"], "character_profile", profile_frame_id)} if "experience" in captured_fields else {}),
            **({"experience_required": _field_evidence(profile["experienceItem"], "character_profile", profile_frame_id)} if "experience_required" in captured_fields else {}),
        },
    }
