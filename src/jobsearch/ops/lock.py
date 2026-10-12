"""One run at a time: an exclusive lock file holding the owner's pid and start time.

A lock whose process is gone, or that is older than `stale_hours` (a hung run), is stale and is replaced.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class Locked(RuntimeError):
    pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RunLock:
    def __init__(self, path: Path, stale_hours: float = 3.0, what: str = "run"):
        self.path, self.stale, self.what = Path(path), timedelta(hours=stale_hours), what
        self.held = False

    def info(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                cur = self.info() or {}
                started = cur.get("started")
                age = datetime.now(timezone.utc) - datetime.fromisoformat(started) if started else self.stale
                if cur.get("pid") and _alive(int(cur["pid"])) and age < self.stale:
                    raise Locked(f"another {cur.get('what', 'run')} is in progress (pid {cur['pid']}, started "
                                 f"{started})") from None
                log.warning("removing stale lock %s (%s)", self.path, cur or "unreadable")
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(), "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "what": self.what}, f)
            self.held = True
            return
        raise Locked(f"could not take {self.path}")

    def release(self) -> None:
        if self.held and (self.info() or {}).get("pid") == os.getpid():
            self.path.unlink(missing_ok=True)
        self.held = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
