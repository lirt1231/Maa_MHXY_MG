from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from types import FrameType
from typing import Any, TypeVar


T = TypeVar("T")


class CancellationRequested(RuntimeError):
    """Raised when the dashboard asks a native macOS runner to stop."""


class CancellationGuard:
    """Cooperative stop state shared by Maa tasks and native input calls."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._tasker: Any | None = None
        self._post_stop_sent = False
        self._stop_job: Any | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def bind_tasker(self, tasker: Any) -> None:
        self._tasker = tasker
        if self.cancelled:
            self._post_stop()

    def request_stop(self) -> None:
        self._cancelled.set()
        self._post_stop()

    def checkpoint(self) -> None:
        if self.cancelled:
            raise CancellationRequested("收到停止请求，已取消后续游戏操作")

    def perform_input(self, operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        self.checkpoint()
        return operation(*args, **kwargs)

    def wait(self, seconds: float) -> None:
        if self._cancelled.wait(max(0.0, seconds)):
            self.checkpoint()

    def wait_for_tasker_stop(self) -> None:
        """Wait for Maa to acknowledge post_stop before the process exits."""
        if self._stop_job is not None and hasattr(self._stop_job, "wait"):
            self._stop_job.wait()

    def _post_stop(self) -> None:
        if self._tasker is None or self._post_stop_sent:
            return
        self._post_stop_sent = True
        self._stop_job = self._tasker.post_stop()

    def _handle_signal(self, signum: int, _frame: FrameType | None) -> None:
        self.request_stop()
        raise CancellationRequested(f"收到系统停止信号 {signum}")
