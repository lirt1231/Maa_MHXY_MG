"""Durable single-purchase guard for an interruptible archaeology run."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class FilePurchaseGuard:
    """Persist a bounded archaeology shovel purchase count across retries."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def is_reserved(self) -> bool:
        return self.path.is_file()

    def purchase_count(self) -> int:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            count = payload.get("purchases", 1)
            return count if isinstance(count, int) and count >= 0 else 0
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return 0

    def can_reserve(self, max_purchases: int) -> bool:
        if max_purchases <= 0 or self.purchase_count() >= max_purchases:
            return False
        if not self.path.is_file():
            return True
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            owner = payload.get("pid")
            if owner == os.getpid():
                return True
            if not isinstance(owner, int) or owner <= 0:
                return False
            os.kill(owner, 0)
        except ProcessLookupError:
            return True
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
        return False

    def reserve(self, max_purchases: int = 1) -> None:
        if max_purchases <= 0:
            raise ValueError("洛阳铲购买上限必须为正整数")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        purchases = self.purchase_count()
        if not self.can_reserve(max_purchases):
            raise RuntimeError("本轮考古洛阳铲购买已达到安全上限，不再自动购买")
        payload = {
            "task_id": "kaogu",
            "reserved_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "purchases": purchases + 1,
        }
        try:
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            # The previous owner has exited and the currently confirmed empty
            # tool dialog requires one bounded replenishment purchase.
            if not self.can_reserve(max_purchases):
                raise RuntimeError("本轮考古洛阳铲购买已达到安全上限，不再自动购买")
            self.path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
            return
        with os.fdopen(descriptor, "wb") as marker:
            marker.write(json.dumps(payload, ensure_ascii=True).encode("utf-8"))
            marker.flush()
            os.fsync(marker.fileno())

    def reset_if_owner_exited(self, *, minimum_age_seconds: float = 1800.0) -> bool:
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
            reserved_at = datetime.fromisoformat(payload.get("reserved_at", ""))
            if reserved_at.tzinfo is None:
                return False
            age_seconds = (datetime.now(timezone.utc) - reserved_at).total_seconds()
            if age_seconds < minimum_age_seconds:
                return False
            os.kill(pid, 0)
        except FileNotFoundError:
            return False
        except ProcessLookupError:
            self.release()
            return True
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
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
