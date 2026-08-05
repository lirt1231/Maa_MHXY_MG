from __future__ import annotations

import sys
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GAME_BUNDLE_ID = "com.netease.my"
SYSTEM_SETTINGS_URLS = {
    "screen-capture": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
}


def _game_is_running() -> bool:
    """Detect the app process without requiring Screen Recording permission."""
    completed = subprocess.run(
        ["/usr/bin/lsappinfo", "find", f"bundleID={GAME_BUNDLE_ID}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=3,
    )
    return completed.returncode == 0 and bool(completed.stdout.strip())


def _screen_capture_is_available(
    permission_granted: bool,
    window_id: int,
    *,
    capture_probe=None,
) -> bool:
    if permission_granted:
        return True
    if not window_id:
        return False
    if capture_probe is None:
        from tools.macos_window_adapter import system_window_capture_frame

        capture_probe = system_window_capture_frame
    try:
        frame = capture_probe(window_id)
    except Exception:
        return False
    image = getattr(frame, "image", None)
    return image is not None and bool(getattr(image, "size", 0))


def inspect_macos() -> dict:
    result = {
        "platform": sys.platform,
        "gameRunning": False,
        "windowFound": False,
        "windowName": None,
        "screenCapturePermission": False,
        "accessibilityPermission": False,
        "available": False,
        "error": None,
    }
    if sys.platform != "darwin":
        result["error"] = "macOS 原生控制仅能在 macOS 上使用"
        return result
    try:
        result["gameRunning"] = _game_is_running()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        from maa.define import MaaMacOSPermissionEnum
        from maa.toolkit import Toolkit

        Toolkit.init_option(str(ROOT))
        windows = [
            item for item in Toolkit.find_desktop_windows()
            if item.class_name == "com.netease.my" and item.window_name == "梦幻西游"
        ]
        native_window_id = 0
        if not windows and result["gameRunning"]:
            from tools.run_sanjieqiyuan_macos import NativeMacOSInput

            native_window_id = NativeMacOSInput().find_window_id()
        result["windowFound"] = bool(windows or native_window_id)
        result["gameRunning"] = result["gameRunning"] or bool(windows)
        result["windowName"] = "梦幻西游" if result["windowFound"] else None
        reported_screen_capture_permission = Toolkit.macos_check_permission(
            MaaMacOSPermissionEnum.ScreenCapture
        )
        result["screenCapturePermission"] = _screen_capture_is_available(
            reported_screen_capture_permission,
            native_window_id or (windows[0].hwnd if windows else 0),
        )
        result["accessibilityPermission"] = Toolkit.macos_check_permission(
            MaaMacOSPermissionEnum.Accessibility
        )
        result["available"] = all(
            (result["windowFound"], result["screenCapturePermission"], result["accessibilityPermission"])
        )
        if result["gameRunning"] and not result["windowFound"]:
            result["error"] = "游戏已运行，但未找到可见窗口；请取消最小化并将游戏切换到前台"
    except Exception as exc:  # Maa native errors must become actionable UI state.
        result["error"] = str(exc)
    return result


def launch_game(timeout: float = 20) -> dict:
    if sys.platform != "darwin":
        raise RuntimeError("启动游戏仅支持 macOS")
    current = inspect_macos()
    completed = subprocess.run(
        ["/usr/bin/open", "-b", GAME_BUNDLE_ID], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "系统无法启动梦幻西游"
        raise RuntimeError(detail)
    deadline = time.monotonic() + timeout
    if current["gameRunning"]:
        time.sleep(0.5)
        return inspect_macos()
    while time.monotonic() < deadline:
        state = inspect_macos()
        if state["gameRunning"]:
            return state
        time.sleep(0.8)
    raise RuntimeError("游戏已经启动，但等待窗口出现超时；请检查登录界面")


def open_system_settings(section: str) -> dict:
    url = SYSTEM_SETTINGS_URLS.get(section)
    if not url:
        raise ValueError("不支持的系统设置页面")
    if sys.platform != "darwin":
        raise RuntimeError("打开系统设置仅支持 macOS")
    completed = subprocess.run(
        ["/usr/bin/open", url], capture_output=True, text=True, check=False, timeout=5
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "系统无法打开隐私与安全性设置"
        raise RuntimeError(detail)
    return {"opened": True, "section": section}
