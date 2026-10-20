"""Resume Builder tab → one saved external job → imported and its resume queued, in one click.

The dashboard saves the job you entered (pasted JD, a JD file, or a URL only) as hirescope-external-job-*.json in
~/Downloads and opens hirescope://external?file=<that file>; the launcher calls `import_and_queue` here. The same works
from Terminal: ./scripts/external_resume.sh "<file>" (a name in ~/Downloads or inputs/external_jobs/, or a path in
one of them). If the file isn't there, the newest pending external job is used instead, and the output says so.

The launcher never opens a file in ~/Downloads itself: macOS asks once per app before an app may read that folder,
and the launcher app has no window to show the question in, so it would hang. It checks the name only (`named_path`) and
leaves a request (data.nosync/state/external_requests/); the background worker, whose Python already may read
~/Downloads, resolves the path for real (`allowed_path`: symlinks resolved, folder checked) and imports it.

Import = the usual external-job import (dedupe: same URL, same JD, or same company and role with a similar JD; a
match uses the existing job's ID instead of adding a copy). A URL with no JD goes through Add job by URL. Then the job's
resume is queued (skipped when it already has a current READY resume) and the worker is started; the dashboard data
(data.js) is rewritten by the worker so the new job shows up there too.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import external, paths
from .storage import db

log = logging.getLogger(__name__)
JSON_PREFIX = "hirescope-external-job-"
FILE_TYPES = (".json", ".txt", ".md", ".pdf", ".docx")
_NAME = re.compile(r"^[A-Za-z0-9 ._()\[\]+,-]{1,200}$")


class FileRefused(ValueError):
    """A file outside the allowed folders, or not an external-job file."""


def downloads_dir() -> Path:
    return Path(os.environ.get("JOBSEARCH_DOWNLOADS_DIR") or "~/Downloads").expanduser()


def allowed_dirs() -> list[Path]:
    return [downloads_dir(), paths.EXTERNAL_INBOX]


def allowed_path(v: str) -> Path:
    """The file a link or argument names, only directly inside ~/Downloads or inputs/external_jobs/ (symlinks are
    resolved first, so one can't point elsewhere). Existence is not checked here."""
    v = str(v or "").strip()
    if not v:
        raise FileRefused("no file given")
    roots = [d.expanduser().resolve() for d in allowed_dirs()]
    if "/" in v:
        p = Path(os.path.expanduser(v))
        if not p.is_absolute():
            raise FileRefused("give a file name or an absolute path")
        candidates = [p]
    else:
        candidates = [d / v for d in allowed_dirs()]
        candidates = [c for c in candidates if c.exists()] or candidates[:1]
    p = candidates[0]
    if not _NAME.match(p.name):
        raise FileRefused("unexpected characters in the file name")
    if p.suffix.lower() not in FILE_TYPES:
        raise FileRefused(f"only {', '.join(FILE_TYPES)} files")
    real = p.resolve(strict=False)
    if real.parent not in roots:
        raise FileRefused("files only from ~/Downloads or inputs/external_jobs/")
    return real


def named_path(v: str) -> Path:
    """The same rules as allowed_path from the text alone, touching nothing in ~/Downloads (the launcher's check)."""
    v = str(v or "").strip()
    if not v:
        raise FileRefused("no file given")
    if "/" in v:
        p = Path(os.path.expanduser(v))
        if not p.is_absolute():
            raise FileRefused("give a file name or an absolute path")
        p = Path(os.path.normpath(p))
        if str(p.parent) not in {os.path.normpath(os.path.expanduser(str(d))) for d in allowed_dirs()}:
            raise FileRefused("files only from ~/Downloads or inputs/external_jobs/")
    else:
        inbox = paths.EXTERNAL_INBOX / v                  # the drop folder is the repo's own: safe to look at
        p = inbox if inbox.exists() else downloads_dir() / v
    if not _NAME.match(p.name):
        raise FileRefused("unexpected characters in the file name")
    if p.suffix.lower() not in FILE_TYPES:
        raise FileRefused(f"only {', '.join(FILE_TYPES)} files")
    return p


def requests_dir() -> Path:
    return paths.STATE_DIR / "external_requests"


def request(file_arg: str | None = None, payload: dict | None = None) -> Path:
    """The launcher's half: remember which file to import — or the job itself, sent inside the link — (the worker does
    the rest)."""
    if payload is None:
        named_path(file_arg)
    d = requests_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}-{os.getpid()}.json"
    p.write_text(json.dumps({"file": file_arg, "payload": payload,
                             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}), encoding="utf-8")
    return p


OUTCOMES_KEY = "external_outcomes"     # what happened to each Resume Builder submission (live.js → the tab)
OUTCOME_DAYS = 7


def queue_resume(conn, pk: int, origin: str = "external") -> dict:
    """Queue the job's resume through the one resume queue (skipped when it already has a current READY resume, not
    queued twice) and start the worker. Every way an external job comes in ends here: same queue, same build."""
    from resume_builder import queue
    q = queue.add(conn, [pk], origin=origin)[0]
    queue.kick()
    return q


def _queued_text(q: dict) -> str:
    return "queued" if q["status"] == "queued" else q["message"].split(": ", 1)[-1]


def _finish(conn, res: dict, note: str = "") -> dict:
    """An imported (or linked) job: dashboard republish pending, its resume queued; the answer for live.js."""
    db.set_meta(conn, "publish_pending", db.utcnow())
    conn.commit()
    q = queue_resume(conn, res["job_pk"], origin="resume_builder")
    j = conn.execute("SELECT company, title FROM jobs WHERE id=?", (res["job_pk"],)).fetchone()
    name = f"{j['company']} — {j['title']}" if j else f"job {res['job_pk']}"
    dup = res["status"] == "duplicate"
    msg = (f"{'linked to existing job' if dup else 'imported'} {name} (job {res['job_pk']}"
           + (f"; {res['why']}" if dup and res.get("why") else "") + f"); resume {_queued_text(q)}")
    return {"job_pk": res["job_pk"], "imported": res["status"], "queue": q["status"], "message": msg, "note": note,
            "company": j["company"] if j else "", "title": j["title"] if j else ""}


def import_payload_and_queue(conn, cfg, payload: dict) -> dict:
    """A pasted JD (or a URL) sent inside the link: import it (usual dedupe) and queue its resume. No file anywhere."""
    return _finish(conn, import_file(conn, cfg, None, payload))


def request_id(req: dict) -> str:
    """The id the dashboard knows a submission by: its `rid`, else its created_at (older pages), else the file name."""
    pl = req.get("payload") or {}
    return str(pl.get("rid") or pl.get("created_at") or req.get("rid") or req.get("file") or req.get("at") or "")[:80]


def record_outcome(conn, rid: str, **o) -> dict:
    """Remember what happened to one submission (the last week's, newest last) for the Resume Builder tab."""
    try:
        cur = json.loads(db.get_meta(conn, OUTCOMES_KEY) or "[]")
    except ValueError:
        cur = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=OUTCOME_DAYS)).isoformat(timespec="seconds")
    rec = {"rid": rid, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **o}
    keep = [x for x in cur if isinstance(x, dict) and x.get("rid") != rid and str(x.get("at", "")) >= cutoff]
    db.set_meta(conn, OUTCOMES_KEY, json.dumps([*keep, rec][-200:]))
    return rec


def outcomes(conn) -> list[dict]:
    try:
        return [x for x in json.loads(db.get_meta(conn, OUTCOMES_KEY) or "[]") if isinstance(x, dict)]
    except ValueError:
        return []


def pending_requests(include_blocked: bool = True) -> list[Path]:
    d = requests_dir()
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if include_blocked:
        return files
    out = []
    for f in files:                       # waiting for macOS permission: doesn't keep the worker running
        try:
            if not json.loads(f.read_text(encoding="utf-8")).get("blocked_reported"):
                out.append(f)
        except (OSError, ValueError):
            pass
    return out


def process_requests(conn, cfg=None) -> list[dict]:
    """The worker's half: import each requested file and queue its resume; the outcome goes to live.js (the Resume
    Builder tab shows it). A file that is refused or can't be imported is reported and dropped."""
    import sqlite3
    from .ops.desktop_access import readable
    out = []
    for r in pending_requests():
        try:
            req = json.loads(r.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            r.unlink(missing_ok=True)
            continue
        target = named_path(str(req.get("file") or "x.json"))
        if not req.get("payload") and target.parent == downloads_dir():
            ok, why = readable(downloads_dir(), target.name)     # never hang on macOS's Downloads prompt
            if not ok:
                if not req.get("blocked_reported"):
                    cmd = f'./scripts/external_resume.sh "{target.name}"'
                    db.set_meta(conn, "launcher_last", json.dumps({
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "action": "external", "ok": False,
                        "message": f"{why} It is imported as soon as that's allowed; or now in Terminal: {cmd}"}))
                    conn.commit()
                    r.write_text(json.dumps({**req, "blocked_reported": True}), encoding="utf-8")
                out.append({"file": req.get("file"), "ok": False, "blocked": True, "message": why})
                continue
        if cfg is None:
            from .config import load_config
            cfg = load_config(paths.PREFERENCES)
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rid, pl = request_id(req), req.get("payload") or {}
        sent = {"company": str(pl.get("company") or "").strip(), "role": str(pl.get("role") or "").strip(),
                "url": str(pl.get("url") or "").strip()}
        try:
            res = (import_payload_and_queue(conn, cfg, req["payload"]) if req.get("payload")
                   else import_and_queue(conn, cfg, req["file"]))
            msg, ok = (f"{res['note']}; {res['message']}" if res["note"] else res["message"]), True
            record_outcome(conn, rid, **sent, status="linked" if res["imported"] == "duplicate" else "imported",
                           job_pk=res["job_pk"], job_company=res.get("company"), job_title=res.get("title"),
                           queue=res["queue"], message=msg)
        except sqlite3.OperationalError:
            break                                         # the database is busy: try again on the next round
        except Exception as e:  # noqa: BLE001 — any failure is reported on the tab (never "Importing…" forever)
            from .add_url import UnreadableURL
            what = Path(str(req.get("file") or "")).name or sent["url"] or "the pasted job"
            reason = e.reason if isinstance(e, UnreadableURL) else str(e) if isinstance(
                e, (FileRefused, ValueError, external.ExternalError)) else f"{type(e).__name__}: {str(e)[:200]}"
            if not isinstance(e, (FileRefused, ValueError, external.ExternalError)):
                log.warning("external job not imported: %s", e, exc_info=True)
            msg, ok = f"{what}: import failed: {reason}", False
            record_outcome(conn, rid, **sent, status="failed", reason=reason, message=msg,
                           paste=bool(sent["url"]) and not str(pl.get("jd_text") or "").strip())
        db.set_meta(conn, "launcher_last", json.dumps({"at": at, "action": "external", "ok": ok, "message": msg,
                                                       "rid": rid}))
        conn.commit()
        r.unlink(missing_ok=True)
        out.append({"file": req.get("file"), "rid": rid, "ok": ok, "message": msg})
        try:
            from . import hooks
            hooks.after_change(conn, reason="external job")
        except Exception:  # noqa: BLE001
            pass
    return out


def wait_for(p: Path, seconds: float) -> bool:
    """The browser may still be writing the download: wait for the file (and for its size to settle)."""
    end = time.time() + seconds
    last = -1
    while time.time() < end:
        if p.exists() and not p.with_name(p.name + ".crdownload").exists():
            size = p.stat().st_size
            if size == last and size > 0:
                return True
            last = size
        time.sleep(0.4)
    return p.exists() and p.stat().st_size > 0


def newest_pending(conn) -> Path | None:
    """The newest external-job file the dashboard saved that hasn't been imported yet."""
    seen = set(external._seen(conn))
    files = []
    for d in allowed_dirs():
        if d.is_dir():
            for f in d.glob(f"{JSON_PREFIX}*.json"):
                try:
                    if f.is_file() and external._sig(f) not in seen:
                        files.append(f)
                except OSError:
                    continue
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def _payload(p: Path) -> dict | None:
    if p.suffix.lower() != ".json":
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("kind") != "hirescope_external_job":
        raise FileRefused("not a HireScope external-job file")
    return data


def import_file(conn, cfg, p: Path | None, payload: dict | None = None) -> dict:
    """{job_pk, status: new | duplicate | updated | seen, message}. Raises ValueError for a file that can't be imported.
    `payload`: the job itself (a pasted JD sent inside the link), no file involved."""
    if payload is None:
        payload = _payload(p)
    elif not isinstance(payload, dict) or payload.get("kind") != "hirescope_external_job":
        raise FileRefused("not a HireScope external job")
    if payload is not None and payload.get("url") and not str(payload.get("jd_text") or "").strip() \
            and not payload.get("file"):
        return import_url(conn, cfg, payload)
    if payload is not None:
        ext, force = external.from_json(payload, origin="resume_builder" if payload.get("origin") == "resume_builder"
                                        else "dashboard")
    else:
        ext, force = external.from_file(p, origin="drop"), False
    out = external.import_external(conn, cfg, ext, force=force)
    if out.status == "error" or not out.job_pk:
        raise ValueError(out.message or "not imported")
    return {"job_pk": out.job_pk, "status": out.status, "message": out.message}


def import_url(conn, cfg, payload: dict) -> dict:
    """A URL with no JD. A job HireScope already has (same posting — checked from the URL first, then from what the
    page says: its canonical URL, posting number, or company + role + JD) is linked, not imported again. Otherwise the
    page is read (ATS API, else schema.org JSON-LD) and added. Raises add_url.UnreadableURL (with the reason) when the
    page can't be read — blocked, JavaScript-only, gone — so the tab can offer "Paste JD instead"."""
    from .adapters.base import HttpClient
    from .add_url import UnreadableURL, add_job, resolve
    s = lambda k: (str(payload.get(k) or "").strip() or None)  # noqa: E731
    url, company = s("url"), s("company")
    if not re.match(r"^https?://", url or "", re.I):
        raise external.ExternalError("the job URL must start with http:// or https://")
    if not payload.get("force") and (d := external.find_by_url(conn, url, company)):
        return {"job_pk": d["job_pk"], "status": "duplicate", "message": d["message"], "why": d["message"].rsplit("(", 1)[-1].rstrip(")")}
    client = HttpClient()
    try:
        raw, how = resolved = resolve(url, client, company)
        if raw is None:
            raise UnreadableURL(url, how)
        if not payload.get("force"):
            d = external.find_by_url(conn, raw.apply_url or url, company or raw.company, extra_urls=(url,))
            if d is None and len(raw.description or "") >= 200:
                d = external.find_duplicate(conn, external.ExternalJob(
                    company=company or raw.company, role=s("role") or raw.title, jd_text=raw.description), cfg.title_rules)
            if d:
                return {"job_pk": d["job_pk"], "status": "duplicate", "message": d["message"],
                        "why": d["message"].rsplit("(", 1)[-1].rstrip(")")}
        pk, status, how = add_job(conn, cfg, url, client, company=company, title=s("role"), location=s("location"),
                                  track=external.norm_track(s("track")), resolved=resolved)
    finally:
        client.close()
    conn.commit()
    if status != "new":            # the upsert found it (same ATS posting from another source): a link, not a copy
        return {"job_pk": pk, "status": "duplicate", "message": f"same posting as job {pk}", "why": "same posting"}
    return {"job_pk": pk, "status": "new", "message": f"added by URL ({how}): job {pk}"}


def import_and_queue(conn, cfg, file_arg: str, wait: float = 20.0) -> dict:
    """The launcher's / external_resume.sh's work: find the file (or the newest pending one), import it, queue its
    resume, start the worker, ask the worker to republish the dashboard data. Returns what happened."""
    note = ""
    try:
        p = allowed_path(file_arg)
        found = wait_for(p, wait)
    except FileRefused:
        raise
    if not found:
        alt = newest_pending(conn)
        if alt is None:
            raise FileRefused(f"{Path(file_arg).name} isn't in ~/Downloads or inputs/external_jobs/ and no external job "
                              "is waiting there")
        note = f"{Path(file_arg).name} not found; used the newest pending external job instead: {alt.name}"
        p = alt
    sig = external._sig(p)
    res = import_file(conn, cfg, p)
    seen = external._seen(conn)
    if sig not in seen:
        db.set_meta(conn, "external_imported", json.dumps([*seen, sig][-1000:]))
    conn.commit()
    external._archive(p, True, paths.EXTERNAL_INBOX)
    return {**_finish(conn, res, note), "file": str(p)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="external_resume.sh", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="the saved external-job file (name in ~/Downloads or inputs/external_jobs/, or a path)")
    args = ap.parse_args(argv)
    from .config import load_config
    cfg = load_config(paths.PREFERENCES)
    conn = db.connect(paths.DB)
    try:
        out = import_and_queue(conn, cfg, args.file, wait=2.0)
        from . import hooks
        hooks.after_change(conn, reason="external resume")
    except (FileRefused, ValueError, external.ExternalError) as e:
        print(f"external_resume: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    if out["note"]:
        print(out["note"])
    print(out["message"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
