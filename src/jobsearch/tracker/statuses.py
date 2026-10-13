"""Import the dashboard's `jobsearch-statuses.json` into SQLite, and hand SQLite's state back to the dashboard.

The dashboard saves statuses, track overrides and applications in the browser (localStorage) and exports them
as one JSON file. Each run imports the newest export from `statuses_import_dir` (default ~/Downloads) and merges
it: per item, SQLite wins unless the export's entry is newer (`updated_at`). Application timeline events are
append-only and always unioned. Everything in the file is data (never instructions) and is stored as text.

Export formats: 1 = {statuses}; 2 = + track_overrides {key: track}; 3 = + track_overrides {key: {track,
updated_at}} and applications {id: application}; 4 = + manual_checks {company: {checked_at, history, updated_at}};
5 = + resume_reviews {job_pk: {version, reviewed_at}} (Resume Builder) and applications[].resume (the resume version
used: {job_pk, version, pdf, manifest_sha}).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import paths
from ..storage import db
from .applications import STAGES

log = logging.getLogger(__name__)

EXPORT_GLOB = "jobsearch-statuses*.json"     # browsers save repeats as "jobsearch-statuses (1).json"
STATUSES = {"", "interested", "shortlisted", "package exported", "applied", "interviewing", "offer", "rejected",
            "skipped"}
TRACKS = {"coop_intern", "full_time"}
APP_FIELDS = ("job_key", "company", "title", "track", "link", "source", "applied_date", "stage", "next_step",
              "next_step_date", "contact", "referral", "notes", "package", "followed_up")
MAX_TEXT = 20_000          # per text field (a pasted JD may be long; anything bigger is truncated)
MAX_JD = 200_000
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ImportResult:
    file: str | None = None
    exported_at: str | None = None
    statuses_applied: int = 0
    statuses_kept_db: int = 0
    statuses_unmatched: int = 0
    overrides_applied: int = 0
    manual_checks_applied: int = 0
    resume_reviews_applied: int = 0
    apps_new: int = 0
    apps_updated: int = 0
    apps_kept_db: int = 0
    events_added: int = 0
    skipped_reason: str | None = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def split_key(key: str) -> tuple[str, str, str] | None:
    parts = key.split("::")
    if len(parts) < 3:
        return None
    return "::".join(parts[:-2]), parts[-2], parts[-1]


def job_key(job: dict) -> str:
    return f"{job['company']}::{job['source']}::{job['job_id']}"


def find_job(conn, key: str) -> int | None:
    """Dashboard key (company::source::job_id) -> jobs.id, via any source that lists the posting."""
    p = split_key(key)
    if not p:
        return None
    company, source, jid = p
    ck = db.company_key(company)
    row = conn.execute("SELECT job_pk FROM job_sources WHERE source=? AND company_key=? AND source_job_id=?",
                       (source, ck, jid)).fetchone() or \
        conn.execute("SELECT id AS job_pk FROM jobs WHERE company_key=? AND source=? AND job_id=?",
                     (ck, source, jid)).fetchone()
    return row["job_pk"] if row else None


def _text(v, limit: int = MAX_TEXT) -> str:
    if v is None:
        return ""
    return str(v)[:limit]


def _iso(v, fallback: str, cap: str | None = None) -> str:
    """An ISO timestamp (UTC) or the fallback. Entries are capped at the export's own time (`cap`), so a bad
    clock can't make an entry win forever, and re-importing the same file is deterministic."""
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return fallback
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    out = t.astimezone(timezone.utc).isoformat(timespec="seconds")
    return min(out, cap) if cap else out


def _date(v) -> str | None:
    v = _text(v)[:10]
    return v if _DATE.match(v) else None


def access(directory: Path) -> str:
    """"ok", "missing" or "blocked". macOS denies scheduled jobs ~/Downloads without Full Disk Access (even is_dir()
    says False there), and Python's glob would silently return nothing, so the listing error is what tells."""
    try:
        os.listdir(directory)
        return "ok"
    except PermissionError:
        return "blocked"
    except OSError:
        return "missing"


def newest_export(directory: Path | list[Path]) -> Path | None:
    dirs = directory if isinstance(directory, list) else [directory]
    files = [p for d in dirs if access(d) == "ok" for p in d.glob(EXPORT_GLOB) if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def load_export(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("statuses", {}), dict):
        raise ValueError("not a HireScope statuses export")
    if raw.get("format", 1) not in (1, 2, 3, 4, 5):
        raise ValueError(f"unknown export format {raw.get('format')!r}")
    return raw


def merge(conn, payload: dict, apps_dir: Path | None, now: str | None = None) -> ImportResult:
    """Merge one export payload into SQLite. Idempotent: importing the same file twice changes nothing."""
    now = now or db.utcnow()
    res = ImportResult(exported_at=_iso(payload.get("exported_at"), now))
    exported = res.exported_at

    for key, e in (payload.get("statuses") or {}).items():
        if not isinstance(e, dict):
            continue
        status = _text(e.get("status"))
        if status not in STATUSES:
            res.errors.append(f"unknown status {status!r} for {key}")
            continue
        pk = find_job(conn, key)
        if pk is None:
            res.statuses_unmatched += 1
            continue
        at = _iso(e.get("updated_at"), exported, exported)
        cur = conn.execute("SELECT user_status_at FROM jobs WHERE id=?", (pk,)).fetchone()
        if cur["user_status_at"] and cur["user_status_at"] >= at:
            res.statuses_kept_db += 1
            continue
        conn.execute("UPDATE jobs SET user_status=?, skip_reason=?, user_status_at=?, user_notes=? WHERE id=?",
                     (status or None, _text(e.get("skip_reason"), 200) or None if status == "skipped" else None,
                      at, _text(e.get("notes")) or None, pk))
        res.statuses_applied += 1

    for key, v in (payload.get("track_overrides") or {}).items():
        track, at = (v.get("track"), _iso(v.get("updated_at"), exported, exported)) if isinstance(v, dict) else (v, exported)
        if track not in TRACKS and track not in (None, ""):
            continue
        pk = find_job(conn, key)
        if pk is None:
            continue
        cur = conn.execute("SELECT track_override_at FROM jobs WHERE id=?", (pk,)).fetchone()
        if cur["track_override_at"] and cur["track_override_at"] >= at:
            continue
        conn.execute("UPDATE jobs SET track_override=?, track_override_at=? WHERE id=?", (track or None, at, pk))
        res.overrides_applied += 1

    from . import manual_checks
    n, errs = manual_checks.merge(conn, payload.get("manual_checks") or {}, exported, _iso)
    res.manual_checks_applied, res.errors = n, [*res.errors, *errs]

    if payload.get("resume_reviews"):
        try:
            from resume_builder.hirescope_bridge import apply_reviews
            n, errs = apply_reviews(conn, payload["resume_reviews"] if isinstance(payload["resume_reviews"], dict) else {})
            res.resume_reviews_applied, res.errors = n, [*res.errors, *errs]
        except ImportError:
            res.errors.append("resume reviews in the export, but the Resume Builder isn't installed")

    apps = payload.get("applications") or {}
    if isinstance(apps, list):
        apps = {a.get("id"): a for a in apps if isinstance(a, dict)}
    for app_id, a in apps.items():
        if not isinstance(a, dict) or not app_id or not re.match(r"^[A-Za-z0-9_-]{1,64}$", str(app_id)):
            continue
        try:
            _merge_app(conn, str(app_id), a, exported, apps_dir, res)
        except (KeyError, ValueError, TypeError) as ex:
            res.errors.append(f"application {app_id}: {ex}")
    conn.commit()
    return res


def _resume_ref(r) -> dict | None:
    if not isinstance(r, dict):
        return None
    try:
        out = {"job_pk": int(r["job_pk"]), "version": int(r["version"]), "linked": "applied"}
    except (KeyError, TypeError, ValueError):
        return None
    for k in ("pdf", "manifest_sha", "pdf_name"):
        if isinstance(r.get(k), str):
            out[k] = r[k][:500]
    return out


def _merge_app(conn, app_id: str, a: dict, exported: str, apps_dir: Path | None, res: ImportResult) -> None:
    company, title = _text(a.get("company"), 200).strip(), _text(a.get("title"), 300).strip()
    if not company or not title:
        raise ValueError("company and role are required")
    stage = a.get("stage") or "applied"
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    updated = _iso(a.get("updated_at"), exported, exported)
    vals = {f: _text(a.get(f), 2000 if f != "notes" else MAX_TEXT) or None for f in APP_FIELDS}
    vals.update(company=company, title=title, stage=stage, applied_date=_date(a.get("applied_date")),
                next_step_date=_date(a.get("next_step_date")), followed_up=_date(a.get("followed_up")),
                track=a.get("track") if a.get("track") in TRACKS else None)
    pk = find_job(conn, vals["job_key"]) if vals["job_key"] else None
    extra = a.get("job") if isinstance(a.get("job"), dict) else {}
    extra = {k: extra[k] for k in ("source", "tier", "label", "score", "level") if k in extra}
    resume = _resume_ref((a.get("job") or {}).get("resume") if isinstance(a.get("job"), dict) else None)
    row = conn.execute("SELECT updated_at, jd_path FROM applications WHERE id=?", (app_id,)).fetchone()
    if row is None:
        conn.execute(
            f"INSERT INTO applications (id, job_pk, company_key, deleted, created_at, updated_at, extra, "
            f"{', '.join(APP_FIELDS)}) VALUES (?,?,?,?,?,?,?,{','.join('?' * len(APP_FIELDS))})",
            (app_id, pk, db.company_key(company), int(bool(a.get("deleted"))), _iso(a.get("created_at"), updated),
             updated, json.dumps(extra), *(vals[f] for f in APP_FIELDS)))
        res.apps_new += 1
    elif row["updated_at"] < updated:
        conn.execute(f"UPDATE applications SET job_pk=COALESCE(?, job_pk), company_key=?, deleted=?, updated_at=?, "
                     f"extra=?, {', '.join(f'{f}=?' for f in APP_FIELDS)} WHERE id=?",
                     (pk, db.company_key(company), int(bool(a.get("deleted"))), updated, json.dumps(extra),
                      *(vals[f] for f in APP_FIELDS), app_id))
        res.apps_updated += 1
    else:
        res.apps_kept_db += 1
    if resume:     # the exact resume version recorded at Mark Applied; never replaced by an empty one
        conn.execute("UPDATE applications SET resume=? WHERE id=?", (json.dumps(resume), app_id))

    for ev in a.get("events") or []:
        if not isinstance(ev, dict) or not ev.get("kind"):
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO application_events (app_id, at, kind, from_stage, to_stage, note) "
            "VALUES (?,?,?,?,?,?)",
            (app_id, _iso(ev.get("at"), updated, exported), _text(ev.get("kind"), 40),
             _text(ev.get("from"), 40), _text(ev.get("to"), 40), _text(ev.get("note"), 2000)))
        res.events_added += cur.rowcount

    # a saved local copy of the JD, kept even after the posting disappears
    if apps_dir is not None and not (row and paths.stored_file(row["jd_path"], apps_dir)):
        jd = _text(a.get("jd"), MAX_JD)
        src = None
        if not jd and pk is not None:
            r = conn.execute("SELECT jd_path FROM jobs WHERE id=?", (pk,)).fetchone()
            src = paths.stored_file(r["jd_path"], paths.JD_DIR) if r else None
        if jd or src:
            apps_dir.mkdir(parents=True, exist_ok=True)
            dest = apps_dir / f"{app_id}.txt"
            if jd:
                dest.write_text(jd, encoding="utf-8")
            else:
                shutil.copyfile(src, dest)
            conn.execute("UPDATE applications SET jd_path=? WHERE id=?", (str(dest), app_id))


def import_newest(conn, directory: Path | list[Path], apps_dir: Path | None, archive_dir: Path | None = None) -> ImportResult:
    """Import the newest export in `directory` (one folder or several) unless that exact file was imported already."""
    dirs = directory if isinstance(directory, list) else [directory]
    path = newest_export(dirs)
    if path is None:
        blocked = [str(d) for d in dirs if access(d) == "blocked"]
        note = (f"; can't read {', '.join(blocked)} (macOS privacy protection: scheduled runs need Full Disk Access "
                f"for that folder — save the export to {dirs[-1]} instead)") if blocked else ""
        return ImportResult(skipped_reason=f"no {EXPORT_GLOB} in {', '.join(map(str, dirs))}{note}")
    st = path.stat()
    sig = f"{path.name}:{st.st_mtime_ns}:{st.st_size}"
    if db.get_meta(conn, "statuses_import") == sig:
        return ImportResult(file=str(path), skipped_reason="already imported")
    try:
        payload = load_export(path)
    except (OSError, ValueError) as e:
        return ImportResult(file=str(path), skipped_reason=f"unreadable: {e}")
    res = merge(conn, payload, apps_dir)
    res.file = str(path)
    if archive_dir is not None:   # keep a copy with the run's data (covered by backups)
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, archive_dir / f"statuses-{res.exported_at[:19].replace(':', '')}.json")
    db.set_meta(conn, "statuses_import", sig)
    db.set_meta(conn, "statuses_imported_at", db.utcnow())
    conn.commit()
    log.info("imported statuses from %s: %s", path, {k: v for k, v in res.as_dict().items() if v and k != "file"})
    return res


# --- SQLite -> dashboard -----------------------------------------------------------------------------
def applications(conn, include_deleted: bool = True, with_jd: bool = True) -> list[dict]:
    events: dict[str, list] = {}
    for e in conn.execute("SELECT * FROM application_events ORDER BY at, id"):
        events.setdefault(e["app_id"], []).append(
            {"at": e["at"], "kind": e["kind"], "from": e["from_stage"] or None, "to": e["to_stage"] or None,
             "note": e["note"] or None})
    out = []
    q = "SELECT * FROM applications" + ("" if include_deleted else " WHERE deleted = 0") + " ORDER BY applied_date"
    for r in conn.execute(q):
        a = {f: r[f] or "" for f in APP_FIELDS}
        a.update(id=r["id"], stage=r["stage"], deleted=bool(r["deleted"]), created_at=r["created_at"],
                 updated_at=r["updated_at"], events=events.get(r["id"], []), job=json.loads(r["extra"] or "{}"))
        if r["resume"]:
            a["job"]["resume"] = json.loads(r["resume"])
        if with_jd and (p := paths.stored_file(r["jd_path"], paths.APPS_DIR)):
            a["jd"] = p.read_text(encoding="utf-8")
        out.append(a)
    return out


def sync_payload(conn) -> dict:
    """What SQLite knows that the browser may not (e.g. after clearing site data or on another browser):
    statuses, track overrides, applications, and the open/closed state of jobs you acted on."""
    statuses, overrides = {}, {}
    for r in conn.execute("SELECT company, source, job_id, title, user_status, skip_reason, user_status_at, "
                          "user_notes, track_override, track_override_at FROM jobs WHERE user_status_at IS NOT NULL OR "
                          "track_override_at IS NOT NULL"):
        key = job_key(r)
        if r["user_status_at"]:
            statuses[key] = {"company": r["company"], "title": r["title"], "job_id": r["job_id"],
                             "source": r["source"], "status": r["user_status"] or "",
                             "skip_reason": r["skip_reason"] or None, "notes": r["user_notes"] or "",
                             "updated_at": r["user_status_at"]}
        if r["track_override_at"]:
            overrides[key] = {"track": r["track_override"], "updated_at": r["track_override_at"]}
    state = {}
    for r in conn.execute("SELECT j.company, j.source, j.job_id, j.job_status, j.status_changed_at FROM jobs j "
                          "WHERE j.user_status_at IS NOT NULL OR j.id IN (SELECT job_pk FROM applications WHERE "
                          "job_pk IS NOT NULL)"):
        state[job_key(r)] = [r["job_status"], r["status_changed_at"]]
    from . import manual_checks
    return {"generated_at": db.utcnow(), "statuses": statuses, "track_overrides": overrides,
            "applications": applications(conn), "job_state": state, "manual_checks": manual_checks.entries(conn)}


def write_sync_js(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".js.tmp")
    tmp.write_text("// Generated by jobsearch.run from SQLite — your statuses and applications. Do not edit.\n"
                   f"window.SYNC = {json.dumps(payload, ensure_ascii=False)};\n", encoding="utf-8")
    tmp.replace(path)


def reopened_shortlisted(conn, since: str) -> list[dict]:
    """Closed jobs you had shortlisted (or were interested in) that reappeared since `since`."""
    return [dict(r) for r in conn.execute(
        "SELECT id, company, title, source, job_id, user_status, repost_count, status_changed_at FROM jobs "
        "WHERE job_status = 'updated' AND repost_count > 0 AND status_changed_at >= ? AND user_status IN "
        "('shortlisted', 'interested', 'package exported')", (since,))]
