"""What every change from outside a run refreshes (a launcher link, a queue build, `resume_builder refresh`, an
import): applications that should exist (external jobs whose resume is READY → "Ready to apply"), dashboard/sync.js,
the applications Excel file on the Desktop, and dashboard/live.js. Each step is independent and never raises: the
change itself is already saved in SQLite.
"""
from __future__ import annotations

import logging

from . import paths
from .storage import db

log = logging.getLogger(__name__)


def after_change(conn=None, reason: str = "") -> list[str]:
    own = conn is None
    conn = conn or db.connect(paths.DB)
    lines: list[str] = []
    try:
        steps = []
        try:
            from .tracker import ready_apps
            steps.append(("applications", lambda: ready_apps.ensure(conn)))
        except ImportError:
            pass
        steps.append(("dashboard sync.js", lambda: _sync(conn)))
        try:
            from .ops import applications_excel
            steps.append(("applications Excel", lambda: applications_excel.update(conn)))
        except ImportError:
            pass
        steps.append(("Desktop resume copies", _desktop_catch_up))
        from . import live
        steps.append(("live.js", lambda: live.write(conn)))
        for what, fn in steps:
            try:
                out = fn()
                if isinstance(out, str) and out:
                    lines.append(out)
            except Exception as e:  # noqa: BLE001
                log.warning("after change: %s not updated", what, extra={"reason": reason, "error": str(e)[:200]})
                lines.append(f"Warning: {what} not updated: {e}")
    finally:
        if own:
            conn.close()
    return lines


def _sync(conn) -> None:
    from .tracker import statuses
    statuses.write_sync_js(paths.SYNC_JS, statuses.sync_payload(conn))
    db.set_meta(conn, "sync_at", db.utcnow())
    conn.commit()


def _desktop_catch_up() -> str:
    """Copies a background job had to skip (Desktop blocked for it): made now, if this process may write there."""
    from .ops.desktop_access import pending_marker, writable
    m = pending_marker()
    if not m.exists():
        return ""
    from resume_builder import desktop
    enabled, folder = desktop.settings_of()
    if not enabled or not writable(folder)[0]:
        return ""
    code, lines = desktop.sync_all()
    if code == 0:
        m.unlink(missing_ok=True)
    return lines[-1] if lines else ""
