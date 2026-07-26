from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OCR_SIZE = (1020, 718)


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


def parse_backpack_gold(results: list[dict]) -> int | None:
    """The yellow-gold balance is the left-most currency value at the bag footer."""
    candidates = []
    for item in results:
        x, y, _, _ = item["box"]
        text = re.sub(r"\s+", "", item["text"])
        if y < 565 or x > 330 or not re.fullmatch(r"\d[\d,，.]*", text):
            continue
        value = parse_number(text)
        if value is not None:
            candidates.append((x, value))
    return min(candidates)[1] if candidates else None


def parse_profile_metrics(results: list[dict]) -> dict:
    level = None
    experience = None
    required = None
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
                            break
        x, y, _, _ = item["box"]
        fraction = re.fullmatch(r"(\d[\d,，.]*)/(\d[\d,，.]*)", text)
        if fraction and y >= 500 and x >= 450:
            numerator = parse_number(fraction.group(1))
            denominator = parse_number(fraction.group(2))
            if numerator is not None and denominator is not None and numerator >= 10000:
                experience, required = numerator, denominator
    return {"level": level, "experience": experience, "experienceRequired": required}


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


def _click_text(native_input, results: list[dict], text: str, *, min_y: int = 0) -> bool:
    matches = [item for item in results if text in re.sub(r"\s+", "", item["text"]) and item["box"][1] >= min_y]
    if not matches:
        return False
    item = max(matches, key=lambda value: value["box"][0])
    x, y, width, height = item["box"]
    native_input.click(x + width / 2, y + height / 2, OCR_SIZE)
    return True


def _close_panel(native_input) -> None:
    # Standard bag/profile panels share this close-button position at the normalized macOS layout.
    native_input.click(890, 112, OCR_SIZE)
    time.sleep(1)


def _wait_for(tasker, window_id: int, predicate, timeout: float = 8) -> tuple[np.ndarray, list[dict]]:
    deadline = time.monotonic() + timeout
    last_image, last_results = None, []
    while time.monotonic() < deadline:
        last_image = _window_capture(window_id)
        last_results = _recognize(tasker, last_image)
        if predicate(last_results):
            return last_image, last_results
        time.sleep(0.6)
    raise RuntimeError("游戏页面切换超时，请确认窗口完整可见且当前不在战斗中")


def _ensure_main(tasker, native_input, window_id: int) -> list[dict]:
    for _ in range(4):
        _, results = _wait_for(tasker, window_id, lambda value: bool(value), timeout=4)
        if _is_main(results):
            return results
        if _has(results, "人物属性") or _has(results, "人物评分", "包裹"):
            _close_panel(native_input)
            continue
        raise RuntimeError("当前页面无法安全返回主界面，请先结束战斗或关闭特殊活动页面")
    raise RuntimeError("自动返回游戏主界面失败")


def capture_visible_metrics() -> dict:
    """Navigate through bag/profile pages, OCR both metrics, and restore the main page."""
    from maa.controller import MacOSController, MaaMacOSInputMethodEnum, MaaMacOSScreencapMethodEnum
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from tools.run_sanjieqiyuan_macos import NativeMacOSInput

    Toolkit.init_option(str(ROOT))
    windows = [
        item for item in Toolkit.find_desktop_windows()
        if item.class_name == "com.netease.my" and item.window_name == "梦幻西游"
    ]
    if not windows:
        raise RuntimeError("未找到已打开的梦幻西游窗口")
    window = windows[0]
    controller = MacOSController(
        window.hwnd,
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

    native_input = NativeMacOSInput()
    main_results = _ensure_main(tasker, native_input, window.hwnd)
    if not _click_text(native_input, main_results, "包裹", min_y=500):
        # Bottom-right bag label may be obscured by nearby players; its normalized
        # main-screen position remains stable in the fixed macOS game layout.
        native_input.click(985, 630, OCR_SIZE)
    _, bag_results = _wait_for(tasker, window.hwnd, lambda value: _has(value, "人物评分", "包裹"))
    gold = parse_backpack_gold(bag_results)
    _close_panel(native_input)
    if gold is None:
        raise RuntimeError("背包已自动打开，但未能识别金币数值")

    _wait_for(tasker, window.hwnd, _is_main)
    native_input.click(978, 55, OCR_SIZE)
    _, profile_results = _wait_for(tasker, window.hwnd, lambda value: _has(value, "人物属性"))
    profile = parse_profile_metrics(profile_results)
    _close_panel(native_input)
    if profile["experience"] is None:
        raise RuntimeError("人物属性已自动打开，但未能识别经验数值")

    raw_text = "背包：" + " | ".join(item["text"] for item in bag_results)
    raw_text += "\n人物属性：" + " | ".join(item["text"] for item in profile_results)
    return {"gold": gold, **profile, "rawText": raw_text, "status": "success"}
