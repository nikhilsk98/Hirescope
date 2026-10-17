"""Import the dashboard's `jobsearch-statuses.json` into SQLite, and hand SQLite's state back to the dashboard.

The dashboard saves statuses, track overrides and applications in the browser (localStorage) and exports them
as one JSON file. Each run imports the newest export from `statuses_import_dir` (default ~/Downloads) and merges
it: per item, SQLite wins unless the export's entry is newer (`updated_at`). Application timeline events are
append-only and always unioned. Everything in the file is data (never instructions) and is stored as text.

Export formats: 1 = {statuses}; 2 = + track_overrides {key: track}; 3 = + track_overrides {key: {track,
updated_at}} and applications {id: application}; 4 = + manual_checks {company: {checked_at, history, updated_at}};
5 = + resume_reviews {job_pk: {version, reviewed_at}} (Resume Builder) and applications[].resume (the resume version
used: {job_pk, version, pdf, pdf_sha}); 6 = + bucket {job_pk: {in, added_at, updated_at, snap}} (the Bucket tab) and the
"ready" stage (Ready to apply).
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
    bucket_applied: int = 0
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
    """"ok", "missing" or "blocked". macOS privacy protection decides ~/Downloads access per program: under launchd the
    venv's Python has been allowed (tested) while /bin/ls is not, and a Python upgrade can lose it (even is_dir()
    says False there), and Python's glob would silently return nothing, so the listing error is what tells."""
    try:
        os.listdir(directory)
    except PermissionError:
        return "blocked"
    except OSError:
        return "missing"
    # macOS may also *ask* (a prompt nobody can answer in a scheduled job), which blocks the first open() forever:
    # a probe in a child process with a timeout tells (it opens one file there)
    from ..ops.desktop_access import readable
    return "ok" if readable(Path(directory))[0] else "blocked"


def newest_export(directory: Path | list[Path]) -> Path | None:
    dirs = directory if isinstance(directory, list) else [directory]
    files = [p for d in dirs if access(d) == "ok" for p in d.glob(EXPORT_GLOB) if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def load_export(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("statuses", {}), dict):
        raise ValueError("not a HireScope statuses export")
    if raw.get("format", 1) not in (1, 2, 3, 4, 5, 6):
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

    if payload.get("bucket"):
        from .. import bucket
        n, errs = bucket.merge(conn, payload["bucket"], exported, _iso)
        res.bucket_applied, res.errors = n, [*res.errors, *errs]

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
    for k in ("pdf", "pdf_sha", "manifest_sha", "pdf_name"):
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
        note = (f"; can't read {', '.join(blocked)} (macOS privacy protection for this Python, e.g. after an upgrade)"
                f" — run ./scripts/import_pending.sh yourself, or save the export to {dirs[-1]}"
                ) if blocked else ""
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


# --- every job marked applied is in My Applications ---------------------------------------------------------
APPLIED_STATUSES = {"applied": "applied", "interviewing": "interviewing", "offer": "offer"}
ADDED_BY_YOU = ("external", "added_url")


def app_source(job_source: str | None) -> str:
    """The application's Source: jobs you added yourself (external / by URL) are "External"."""
    return "External" if job_source in ADDED_BY_YOU else "HireScope"


def _save_jd_copy(conn, app_id: str, job_pk: int, apps_dir: Path | None) -> None:
    if apps_dir is None:
        return
    r = conn.execute("SELECT jd_path FROM jobs WHERE id=?", (job_pk,)).fetchone()
    src = paths.stored_file(r["jd_path"], paths.JD_DIR) if r else None
    if src:
        apps_dir.mkdir(parents=True, exist_ok=True)
        dest = apps_dir / f"{app_id}.txt"
        shutil.copyfile(src, dest)
        conn.execute("UPDATE applications SET jd_path=? WHERE id=?", (str(dest), app_id))


def ensure_applications(conn, apps_dir: Path | None, now: str | None = None) -> list[str]:
    """Jobs you marked applied / interviewing / offer (on the dashboard, in an older export, or with
    `python -m jobsearch.apps mark-applied`) that have no application yet get one: applied date, company, role,
    source (HireScope / External), track, application link, a saved JD copy, and the resume version current when you
    applied (resume_builder's link_applications fills that in). Returns the new application ids."""
    now = now or db.utcnow()
    have_pk = {r[0] for r in conn.execute("SELECT job_pk FROM applications WHERE job_pk IS NOT NULL")}
    have_key = {r[0] for r in conn.execute("SELECT job_key FROM applications WHERE job_key IS NOT NULL")}
    out = []
    for j in conn.execute("SELECT * FROM jobs WHERE user_status IN ('applied', 'interviewing', 'offer')").fetchall():
        key = job_key(j)
        if j["id"] in have_pk or key in have_key:
            continue
        at = j["user_status_at"] or now
        out.append(insert_app(conn, j, f"job-{j['id']}", APPLIED_STATUSES[j["user_status"]], at, at[:10], apps_dir))
    conn.commit()
    return out


def insert_app(conn, j, app_id: str, stage: str, at: str, applied_date: str | None, apps_dir: Path | None) -> str:
    """One application for a HireScope job: company, role, track, link, source (HireScope / External), a saved JD
    copy, a "created" timeline event."""
    extra = json.loads(j["extra"] or "{}")
    track = j["track_override"] or j["track"]
    conn.execute(
        "INSERT OR IGNORE INTO applications (id, job_key, job_pk, company, company_key, title, track, link, source, "
        "applied_date, stage, deleted, created_at, updated_at, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
        (app_id, job_key(j), j["id"], j["company"], j["company_key"], j["title"], track if track in TRACKS else None,
         j["apply_url"] or j["source_url"] or None, app_source(j["source"]), applied_date, stage, at, at,
         json.dumps({"source": j["source"], "label": j["label"], "level": j["level"], "job_id": j["job_id"],
                     "score": None if j["score"] is None else round(j["score"]),
                     **({"external": True} if j["source"] in ADDED_BY_YOU else {}),
                     **({"origin": extra["external"].get("origin")} if isinstance(extra.get("external"), dict) else {})})))
    conn.execute("INSERT OR IGNORE INTO application_events (app_id, at, kind, from_stage, to_stage, note) "
                 "VALUES (?,?,?,?,?,?)", (app_id, at, "created", "", stage, ""))
    _save_jd_copy(conn, app_id, j["id"], apps_dir)
    return app_id


def mark_applied(conn, job_pk: int, apps_dir: Path | None, applied_date: str | None = None,
                 app_id: str | None = None, version: int | None = None) -> str:
    """Mark one job applied (the dashboard's Mark Applied through the launcher, or the command line): status
    "applied", its application (with the dashboard's own id when given, so the page and SQLite share one record) and
    the exact resume version you are sending (`version` when the page says which, else the current READY one), and the
    job leaves the Bucket. An application at "Ready to apply" moves to Applied. Returns the application id."""
    j = conn.execute("SELECT * FROM jobs WHERE id=?", (job_pk,)).fetchone()
    if j is None:
        raise ValueError(f"no HireScope job {job_pk}")
    now = db.utcnow()
    day = applied_date or now[:10]
    if not _DATE.match(day):
        raise ValueError(f"applied date must be YYYY-MM-DD (got {day!r})")
    from .. import bucket
    undo = {"prev_status": j["user_status"], "prev_status_at": j["user_status_at"],
            "bucket_removed": bucket.in_bucket(conn, job_pk)}
    if undo["bucket_removed"]:
        bucket.set_in(conn, job_pk, False, now)
    existing = conn.execute("SELECT id, stage FROM applications WHERE deleted=0 AND (job_pk=? OR job_key=?)",
                            (job_pk, job_key(j))).fetchone()
    if j["user_status"] not in APPLIED_STATUSES:
        conn.execute("UPDATE jobs SET user_status='applied', skip_reason=NULL, user_status_at=? WHERE id=?", (now, job_pk))
    if existing and existing["stage"] != "ready":
        conn.commit()
        return existing["id"]
    if existing:                                       # Ready to apply → Applied, dated today
        app_id = existing["id"]
        undo["prev_stage"] = "ready"
        conn.execute("UPDATE applications SET stage='applied', applied_date=?, updated_at=? WHERE id=?", (day, now, app_id))
        conn.execute("INSERT OR IGNORE INTO application_events (app_id, at, kind, from_stage, to_stage, note) "
                     "VALUES (?,?,?,?,?,?)", (app_id, now, "stage", "ready", "applied", ""))
    else:
        app_id = app_id or f"job-{job_pk}"
        if not re.match(r"^[A-Za-z0-9_-]{1,64}$", app_id):
            raise ValueError("bad application id")
        if conn.execute("SELECT 1 FROM applications WHERE id=?", (app_id,)).fetchone():   # undone, then re-applied
            conn.execute("UPDATE applications SET deleted=0, stage='applied', applied_date=?, updated_at=? WHERE id=?",
                         (day, now, app_id))
        else:
            insert_app(conn, j, app_id, "applied", now, day, apps_dir)
    try:   # the exact resume version you are sending
        from resume_builder.hirescope_bridge import resume_ref, version_ref
        ref = (version_ref(conn, job_pk, version) if version else None) or resume_ref(conn, job_pk)
        if ref:
            ref = {**ref, "linked": "applied", **({"pdf_name": Path(ref["pdf"]).name} if ref.get("pdf") else {})}
            conn.execute("UPDATE applications SET resume=? WHERE id=?", (json.dumps(ref), app_id))
    except ImportError:
        pass
    db.set_meta(conn, f"undo_applied:{app_id}", json.dumps(undo))
    conn.commit()
    return app_id


def undo_applied(conn, job_pk: int, app_id: str) -> bool:
    """The toast's Undo after Mark Applied: the application goes (or back to Ready to apply), the job's status and
    its Bucket entry come back as they were. Only for an application Mark Applied just made."""
    raw = db.get_meta(conn, f"undo_applied:{app_id}")
    a = conn.execute("SELECT * FROM applications WHERE id=? AND job_pk=?", (app_id, job_pk)).fetchone()
    if not raw or not a:
        return False
    undo, now = json.loads(raw), db.utcnow()
    if undo.get("prev_stage") == "ready":
        conn.execute("UPDATE applications SET stage='ready', applied_date=NULL, updated_at=? WHERE id=?", (now, app_id))
        conn.execute("INSERT OR IGNORE INTO application_events (app_id, at, kind, from_stage, to_stage, note) "
                     "VALUES (?,?,?,?,?,?)", (app_id, now, "stage", "applied", "ready", "undo"))
    else:
        conn.execute("UPDATE applications SET deleted=1, updated_at=? WHERE id=?", (now, app_id))
    conn.execute("UPDATE jobs SET user_status=?, user_status_at=? WHERE id=?",
                 (undo.get("prev_status"), now if undo.get("prev_status") is not None else now, job_pk))
    if undo.get("bucket_removed"):
        from .. import bucket
        bucket.set_in(conn, job_pk, True, now)
    db.set_meta(conn, f"undo_applied:{app_id}", "")
    conn.commit()
    return True


STAGE_TO_STATUS = {"applied": "applied", "oa": "applied", "interviewing": "interviewing", "offer": "offer",
                   "rejected": "rejected", "ghosted": "applied", "withdrawn": "applied"}   # as the dashboard maps them


def move_stage(conn, app_id: str, stage: str, at: str | None = None) -> bool:
    """An application's stage change from the dashboard (drag, [ / ], the drawer's stage bar): stage, timeline event,
    and the job's status (as the dashboard keeps them in step). Returns whether anything changed."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    a = conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    if a is None:
        raise ValueError(f"no application {app_id} in SQLite yet (Export statuses once)")
    if a["stage"] == stage and not a["deleted"]:
        return False
    at = at or db.utcnow()
    applied = a["applied_date"] or (at[:10] if a["stage"] == "ready" and stage != "ready" else None)
    conn.execute("UPDATE applications SET stage=?, deleted=0, applied_date=?, updated_at=? WHERE id=?",
                 (stage, applied, at, app_id))
    conn.execute("INSERT OR IGNORE INTO application_events (app_id, at, kind, from_stage, to_stage, note) "
                 "VALUES (?,?,?,?,?,?)", (app_id, at, "stage", a["stage"], stage, ""))
    if a["job_pk"] and stage in STAGE_TO_STATUS:
        conn.execute("UPDATE jobs SET user_status=?, user_status_at=? WHERE id=?", (STAGE_TO_STATUS[stage], at,
                                                                                    a["job_pk"]))
    conn.commit()
    return True


def app_for_job(conn, job_pk: int):
    j = conn.execute("SELECT company, source, job_id FROM jobs WHERE id=?", (job_pk,)).fetchone()
    if not j:
        return None
    return conn.execute("SELECT * FROM applications WHERE deleted=0 AND (job_pk=? OR job_key=?) ORDER BY created_at "
                        "LIMIT 1", (job_pk, job_key(j))).fetchone()


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
    from .. import bucket
    return {"generated_at": db.utcnow(), "statuses": statuses, "track_overrides": overrides,
            "applications": applications(conn), "job_state": state, "manual_checks": manual_checks.entries(conn),
            "bucket": bucket.entries(conn)}


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
