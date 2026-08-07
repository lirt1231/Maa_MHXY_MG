"""Durable single-purchase guard for an interruptible archaeology run."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class FilePurchaseGuard:
    """Reserve one purchase atomically until the archaeology run completes."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def is_reserved(self) -> bool:
        return self.path.is_file()

    def reserve(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "task_id": "kaogu",
                "reserved_at": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid(),
            },
            ensure_ascii=True,
        ).encode("utf-8")
        try:
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError as exc:
            raise RuntimeError(
                "本轮考古已经购买或尝试购买过洛阳铲，不再自动购买"
            ) from exc
        with os.fdopen(descriptor, "wb") as marker:
            marker.write(payload)
            marker.flush()
            os.fsync(marker.fileno())

    def reset_if_owner_exited(self) -> bool:
        """Release a reservation left by an exited runner process.

        A marker only protects one live automation run. Retaining it forever
        turns an interrupted attempt into an unrecoverable global purchase
        ban, even after the player has explicitly started a new task.
        """
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = payload.get("pid")
            if not isinstance(pid, int) or pid <= 0:
                return False
            os.kill(pid, 0)
        except FileNotFoundError:
            return False
        except ProcessLookupError:
            self.release()
            return True
        except (OSError, json.JSONDecodeError):
            # An unreadable marker cannot prove that another live task owns
            # the purchase slot, so preserve it for a manual safety stop.
            return False
        return False

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


__all__ = ["FilePurchaseGuard"]
