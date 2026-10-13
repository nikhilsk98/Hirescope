"""Resume Builder ↔ HireScope: resume statuses and versions in SQLite, and dashboard/resumes.js for the page.

Statuses (per job): queued → generating → validating → ready → reviewed, or failed. They sit beside HireScope's own
job status (shortlisted / package exported / applied …): when a resume becomes ready the dashboard moves a job that
is not yet further along to "package exported" (in the browser, where your latest status lives).

dashboard/resumes.js (window.RESUMES) is written after every Resume Builder command and every HireScope run, with
relative links (../data/resumes/…) so the page works from file:// with no server. A page refresh shows the new state.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from jobsearch import paths as hs_paths
from jobsearch.storage import db

from . import config as rc
from . import versions

log = logging.getLogger("resume_builder")
STATUSES = ("queued", "generating", "validating", "ready", "reviewed", "failed")
IN_FLIGHT_STALE_S = 2 * 3600


class BridgeError(Exception):
    pass


def _conn(db_path: Path | None = None):
    return db.connect(db_path or hs_paths.DB)


def set_status(job_pk: int, status: str, message: str | None = None, db_path: Path | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown resume status {status}")
    conn = _conn(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO resume_status (job_pk, status, message, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(job_pk) DO UPDATE SET status=excluded.status, message=excluded.message, "
                "updated_at=excluded.updated_at", (int(job_pk), status, (message or "")[:500], db.utcnow()))
    finally:
        conn.close()


def _job(conn, pk: int) -> dict:
    r = conn.execute("SELECT id, company, title, job_id, source FROM jobs WHERE id=?", (pk,)).fetchone()
    if not r:
        raise BridgeError(f"job {pk} is not in the HireScope database")
    return dict(r)


def sync_job(conn, pk: int) -> dict | None:
    """Mirror data/resumes/<job>/v*/manifest.json into resume_builds + resume_status for one job."""
    from .pipeline import job_dir
    j = _job(conn, pk)
    jd = job_dir(j["company"], pk)
    vs = versions.version_dirs(jd) if jd.exists() else []
    cur = versions.current(jd) if jd.exists() else None
    latest = None
    for v in vs:
        try:
            m = versions.read_manifest(v)
        except (OSError, ValueError):
            continue
        latest = m
        f = m.get("files", {})
        summary = {"truth_unsupported": (m.get("truth") or {}).get("unsupported"), "coverage_pct": m.get("coverage_pct"),
                   "pages": (m.get("validation") or {}).get("pages"), "font": m.get("font_used"),
                   "reasons": (m.get("failure_reasons") or [])[:6], "profile": m.get("profile")}
        conn.execute(
            "INSERT INTO resume_builds (job_pk, version, status, pdf_path, tex_path, manifest_path, manifest_sha, "
            "created_at, reviewed_at, summary) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_pk, version) DO UPDATE SET "
            "status=excluded.status, reviewed_at=excluded.reviewed_at, summary=excluded.summary, "
            "manifest_sha=excluded.manifest_sha",
            (pk, m["version"], m.get("status"), str(v / f["pdf"]) if f.get("pdf") else None, str(v / f.get("tex", "main.tex")),
             str(v / "manifest.json"), rc.sha256_file(v / "manifest.json"), m.get("generated_at") or db.utcnow(),
             m.get("reviewed_at"), json.dumps(summary)))
    row = conn.execute("SELECT status, updated_at FROM resume_status WHERE job_pk=?", (pk,)).fetchone()
    # a status set after the newest version (a run in progress, or a build that stopped before saving a version)
    # wins over the manifests; an in-progress one older than 2 h is treated as abandoned
    stamp, built = (row["updated_at"] or "") if row else "", (latest or {}).get("generated_at", "")
    newer = bool(row and row["status"] in ("queued", "generating", "validating", "failed")
                 and (stamp > built or (stamp == built and row["status"] == "failed")))
    if newer and row["status"] != "failed":
        from datetime import datetime, timezone
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["updated_at"])).total_seconds()
        newer = age < IN_FLIGHT_STALE_S
    if latest:
        if newer:
            status, msg = row["status"], None
        elif latest.get("status") == "failed":
            status = "failed"
            msg = "; ".join((latest.get("failure_reasons") or [])[:3])
            if cur:
                msg = f"v{latest['version']} failed (v{cur['version']} is still current): {msg}"
        else:
            status = (cur or {}).get("status") or latest.get("status")
            msg = None
        conn.execute(
            "INSERT INTO resume_status (job_pk, status, current_version, latest_version, message, job_dir, updated_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(job_pk) DO UPDATE SET status=excluded.status, "
            "current_version=excluded.current_version, latest_version=excluded.latest_version, "
            "message=COALESCE(excluded.message, resume_status.message), job_dir=excluded.job_dir, "
            "updated_at=excluded.updated_at",
            (pk, status, (cur or {}).get("version"), latest["version"], (msg or "")[:500] or None, str(jd), db.utcnow()))
    conn.commit()
    return latest


def _rel(path: Path) -> str:
    """Relative link from dashboard/ to a resume file (works on file://)."""
    return Path(os.path.relpath(Path(path).absolute(), hs_paths.DASHBOARD.absolute())).as_posix()


def dashboard_payload(conn) -> dict:
    out = {}
    for r in conn.execute("SELECT s.*, j.company, j.source, j.job_id, j.title FROM resume_status s "
                          "JOIN jobs j ON j.id = s.job_pk"):
        r = dict(r)
        entry = {"status": r["status"], "message": r["message"], "updated_at": r["updated_at"],
                 "key": f"{r['company']}::{r['source']}::{r['job_id']}",
                 "command": f'claude "/resume-builder {r["job_pk"]}"', "current": None, "latest": None, "versions": []}
        for b in conn.execute("SELECT * FROM resume_builds WHERE job_pk=? ORDER BY version", (r["job_pk"],)):
            b = dict(b)
            vdir = Path(b["manifest_path"]).parent
            summ = json.loads(b["summary"] or "{}")
            v = {"version": b["version"], "status": b["status"], "created_at": b["created_at"],
                 "reviewed_at": b["reviewed_at"], "pdf": _rel(b["pdf_path"]) if b["pdf_path"] else None,
                 "pdf_name": Path(b["pdf_path"]).name if b["pdf_path"] else None,
                 "tex": _rel(vdir / "main.tex"), "content_tex": _rel(vdir / "content.tex"),
                 "preview": _rel(vdir / "pages" / "page-1.png") if (vdir / "pages" / "page-1.png").exists() else None,
                 "report": _rel(vdir / "reports" / "validation.md"), "reports_dir": _rel(vdir / "reports"),
                 "manifest_sha": b["manifest_sha"], **summ}
            entry["versions"].append(v)
            if b["version"] == r["current_version"]:
                entry["current"] = v
            if b["version"] == r["latest_version"]:
                entry["latest"] = v
        out[str(r["job_pk"])] = entry
    return out


def write_dashboard_data(conn=None, path: Path | None = None) -> Path:
    own = conn is None
    conn = conn or _conn()
    try:
        payload = {"generated_at": db.utcnow(), "jobs": dashboard_payload(conn)}
    finally:
        if own:
            conn.close()
    path = path or (hs_paths.DASHBOARD / "resumes.js")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".js.tmp")
    tmp.write_text("// Generated by the Resume Builder from SQLite — resume statuses and versions. Do not edit.\n"
                   f"window.RESUMES = {json.dumps(payload, ensure_ascii=False)};\n", encoding="utf-8")
    tmp.replace(path)
    return path


def update_job(pk: int, status: str | None = None, message: str | None = None, db_path: Path | None = None) -> dict:
    """After a Resume Builder command: SQLite status + versions for the job, application links, resumes.js."""
    try:
        if status:
            set_status(pk, status, message, db_path)
        conn = _conn(db_path)
        try:
            sync_job(conn, int(pk))
            link_applications(conn)
            p = write_dashboard_data(conn)
        finally:
            conn.close()
        return {"ok": True, "dashboard": str(p)}
    except Exception as e:          # noqa: BLE001 — reported to the user, never hides the saved resume
        log.warning("HireScope update failed", extra={"job": pk, "error": str(e)[:200]})
        raise BridgeError(f"the resume is saved, but updating HireScope failed: {e}. Run "
                          "`python -m resume_builder refresh` to retry") from e


def refresh_all(db_path: Path | None = None) -> int:
    """Re-sync every job that has a resume folder (also run at the end of each HireScope run)."""
    conn = _conn(db_path)
    try:
        root = rc.resumes_dir()
        n = 0
        if root.exists():
            for d in root.iterdir():
                if d.is_dir() and "_" in d.name and d.name.rsplit("_", 1)[1].isdigit():
                    pk = int(d.name.rsplit("_", 1)[1])
                    if conn.execute("SELECT 1 FROM jobs WHERE id=?", (pk,)).fetchone():
                        sync_job(conn, pk)
                        n += 1
        link_applications(conn)
        write_dashboard_data(conn)
        return n
    finally:
        conn.close()


# --- applications --------------------------------------------------------------------------------------------
def resume_ref(conn, pk: int, at: str | None = None) -> dict | None:
    """The resume version that was current for a job at a moment (default now): ready/reviewed builds only."""
    q = "SELECT * FROM resume_builds WHERE job_pk=? AND status IN ('ready','reviewed')"
    args = [pk]
    if at:
        q += " AND created_at <= ?"
        args.append(at + ("T23:59:59+00:00" if len(at) == 10 else ""))
    cur = conn.execute("SELECT current_version FROM resume_status WHERE job_pk=?", (pk,)).fetchone()
    rows = [dict(r) for r in conn.execute(q + " ORDER BY version", args)]
    if not rows:
        return None
    pick = next((r for r in rows if cur and r["version"] == cur["current_version"]), None) if not at else None
    pick = pick or rows[-1]
    return {"job_pk": pk, "version": pick["version"], "pdf": pick["pdf_path"], "manifest_sha": pick["manifest_sha"],
            "linked": "auto" if at else "current"}


def link_applications(conn) -> int:
    """Applications with a HireScope job and no resume recorded get the version that was current on the date you
    applied (the dashboard records it exactly at Mark Applied; this covers older or imported applications)."""
    n = 0
    for a in conn.execute("SELECT id, job_pk, applied_date FROM applications WHERE job_pk IS NOT NULL AND "
                          "(resume IS NULL OR resume = '') AND deleted = 0").fetchall():
        ref = resume_ref(conn, a["job_pk"], a["applied_date"])
        if ref:
            conn.execute("UPDATE applications SET resume=? WHERE id=?", (json.dumps(ref), a["id"]))
            n += 1
    conn.commit()
    return n


def apply_reviews(conn, reviews: dict) -> tuple[int, list[str]]:
    """'Mark reviewed' clicks from the dashboard export: {job_pk: {version, reviewed_at}}."""
    from .pipeline import job_dir
    n, errs = 0, []
    for k, v in (reviews or {}).items():
        try:
            pk, ver = int(k), int((v or {}).get("version"))
            j = _job(conn, pk)
            jd = job_dir(j["company"], pk)
            m = versions.read_manifest(jd / f"v{ver}")
            if m.get("status") == "reviewed":
                continue
            with versions.JobLock(jd):
                versions.mark_reviewed(jd, ver, str((v or {}).get("reviewed_at") or "")[:40] or None)
            sync_job(conn, pk)
            n += 1
        except (ValueError, TypeError, OSError, BridgeError, versions.LockedError) as e:
            errs.append(f"resume review {k}: {e}")
    return n, errs
