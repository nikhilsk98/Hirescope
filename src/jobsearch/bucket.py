"""The Bucket: your apply-list (the dashboard's Bucket tab). Ticking the checkbox before a job title anywhere adds a
copy of the job; unticking removes it. Entries keep a snapshot of the job (title, company, track, score, deadline,
link), so a job stays in the Bucket after it closes (shown "Closed"). Marking a job Applied removes it.

Stored in the browser (localStorage, instant), sent to SQLite right away through the launcher
(hirescope://bucket?job=<id>&op=add|remove) and with Export statuses (format 6), and handed back to the page in
dashboard/sync.js. Per job, the newer change wins (`updated_at`); a removal is kept as a dated tombstone so an older
export can't bring the job back.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .storage import db

SNAP_FIELDS = ("company", "title", "source", "job_id", "location", "deadline", "level", "label")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def snapshot(conn, pk: int) -> dict | None:
    r = conn.execute("SELECT * FROM jobs WHERE id=?", (int(pk),)).fetchone()
    if not r:
        return None
    s = {k: r[k] for k in SNAP_FIELDS}
    s.update(id=r["id"], track=r["track_override"] or r["track"], score=None if r["score"] is None else round(r["score"]),
             apply_url=r["apply_url"] or r["source_url"], job_status=r["job_status"],
             key=f"{r['company']}::{r['source']}::{r['job_id']}")
    return s


def set_in(conn, pk: int, on: bool, at: str | None = None) -> bool:
    """Add (on) or remove a job. Returns whether anything changed. Unknown jobs are refused (ValueError)."""
    snap = snapshot(conn, pk)
    if snap is None:
        raise ValueError(f"no HireScope job {pk}")
    at = at or _now()
    cur = conn.execute("SELECT in_bucket, added_at FROM bucket WHERE job_pk=?", (int(pk),)).fetchone()
    if cur and bool(cur["in_bucket"]) == on:
        conn.execute("UPDATE bucket SET snapshot=? WHERE job_pk=?", (json.dumps(snap), int(pk)))
        conn.commit()
        return False
    added = at if on else (cur["added_at"] if cur else None)
    conn.execute("INSERT INTO bucket (job_pk, job_key, in_bucket, added_at, updated_at, snapshot) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(job_pk) DO UPDATE SET in_bucket=excluded.in_bucket, added_at=excluded.added_at, "
                 "updated_at=excluded.updated_at, snapshot=excluded.snapshot",
                 (int(pk), snap["key"], int(on), added, at, json.dumps(snap)))
    conn.commit()
    return True


def in_bucket(conn, pk: int) -> bool:
    r = conn.execute("SELECT in_bucket FROM bucket WHERE job_pk=?", (int(pk),)).fetchone()
    return bool(r and r["in_bucket"])


def entries(conn) -> dict[str, dict]:
    """Every entry (removed ones as tombstones) for sync.js: {job id: {in, added_at, updated_at, snap}}; the snapshot's
    job_status is refreshed from the job (a closed job shows "Closed")."""
    out = {}
    for r in conn.execute("SELECT b.*, j.job_status AS live_status FROM bucket b LEFT JOIN jobs j ON j.id = b.job_pk"):
        snap = json.loads(r["snapshot"] or "{}")
        if r["live_status"]:
            snap["job_status"] = r["live_status"]
        out[str(r["job_pk"])] = {"in": bool(r["in_bucket"]), "added_at": r["added_at"], "updated_at": r["updated_at"],
                                 "snap": snap}
    return out


_PK = re.compile(r"^\d{1,9}$")


def merge(conn, items: dict, exported: str, iso) -> tuple[int, list[str]]:
    """Merge the export's bucket: per job, the newer updated_at wins (capped at the export's time by `iso`)."""
    n, errs = 0, []
    if not isinstance(items, dict):
        return 0, ["bucket: not an object"]
    for k, v in items.items():
        if not _PK.match(str(k)) or not isinstance(v, dict):
            errs.append(f"bucket entry {str(k)[:20]}: malformed")
            continue
        at = iso(v.get("updated_at"), exported, exported)
        cur = conn.execute("SELECT updated_at FROM bucket WHERE job_pk=?", (int(k),)).fetchone()
        if cur and cur["updated_at"] >= at:
            continue
        try:
            if set_in(conn, int(k), bool(v.get("in")), at):
                n += 1
        except ValueError:
            continue                 # a job this database doesn't have (another machine's export): skipped
    return n, errs


def jobs_in(conn) -> list[int]:
    return [r[0] for r in conn.execute("SELECT job_pk FROM bucket WHERE in_bucket=1 ORDER BY added_at, job_pk")]


def touch(conn) -> str:
    db.set_meta(conn, "bucket_at", _now())
    conn.commit()
    return _now()
