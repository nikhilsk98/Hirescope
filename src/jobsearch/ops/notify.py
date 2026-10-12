"""macOS notifications via `osascript` (Notification Center). No-op elsewhere or when turned off."""
from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)
ENABLED = True


def _q(s: str) -> str:
    """AppleScript string literal (job titles are data: quotes and backslashes are escaped)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def command(title: str, message: str, subtitle: str | None = None) -> list[str]:
    script = f"display notification {_q(message[:240])} with title {_q(title[:80])}"
    if subtitle:
        script += f" subtitle {_q(subtitle[:120])}"
    return ["osascript", "-e", script]


def notify(title: str, message: str, subtitle: str | None = None, runner=subprocess.run) -> bool:
    if not ENABLED or os.environ.get("HIRESCOPE_NO_NOTIFY") or sys.platform != "darwin":
        log.info("notification (not shown): %s — %s", title, message)
        return False
    try:
        runner(command(title, message, subtitle), check=False, timeout=10, capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("notification failed: %s", e)
        return False
