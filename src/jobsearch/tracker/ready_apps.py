"""External jobs whose tailored resume is READY → My Applications at "Ready to apply" (the first stage, before
Applied). Mark Applied then moves the application to Applied, dated that day, with the exact resume version.
preferences.yaml → applications.auto_mark_applied_external: true marks them Applied right away instead.

Only once per job: an application you removed (deleted) is never re-created. Runs after every change that can make a
resume READY (queue builds, `resume_builder refresh`, launcher links, each HireScope run).
"""
from __future__ import annotations

import json

from .. import paths
from ..storage import db
from . import statuses

EXTERNAL_SOURCES = ("external", "added_url")


def candidates(conn) -> list:
    return conn.execute(
        f"SELECT j.*, s.current_version AS cur FROM jobs j JOIN resume_status s ON s.job_pk = j.id "
        f"WHERE j.source IN ({','.join('?' * len(EXTERNAL_SOURCES))}) AND s.current_version IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM resume_builds b WHERE b.job_pk = j.id AND b.version = s.current_version "
        "AND b.status IN ('ready','reviewed')) "
        "AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_pk = j.id OR a.job_key = j.company || '::' || "
        "j.source || '::' || j.job_id)", EXTERNAL_SOURCES).fetchall()


def ensure(conn, auto_applied: bool | None = None, apps_dir=None) -> str:
    """Create the missing applications. Returns a one-line summary ("" when nothing was new)."""
    if auto_applied is None:
        from ..config import load_preferences
        try:
            auto_applied = load_preferences().applications.auto_mark_applied_external
        except (OSError, ValueError):
            auto_applied = False
    apps_dir = paths.APPS_DIR if apps_dir is None else apps_dir
    made = []
    for j in candidates(conn):
        if auto_applied:
            statuses.mark_applied(conn, j["id"], apps_dir, app_id=f"ext-{j['id']}", version=j["cur"])
        else:
            now = db.utcnow()
            app_id = statuses.insert_app(conn, j, f"ext-{j['id']}", "ready", now, None, apps_dir)
            try:   # the READY version it's ready with (Mark Applied records the one actually sent)
                from resume_builder.hirescope_bridge import version_ref
                ref = version_ref(conn, j["id"], j["cur"])
                if ref:
                    conn.execute("UPDATE applications SET resume=? WHERE id=?", (json.dumps(ref), app_id))
            except ImportError:
                pass
        made.append(f"{j['company']} — {j['title']}")
    conn.commit()
    if not made:
        return ""
    return f"{'Applied' if auto_applied else 'Ready to apply'}: {', '.join(made)}"
