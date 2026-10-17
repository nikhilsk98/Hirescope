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
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import external, paths
from .storage import db

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


def request(file_arg: str) -> Path:
    """The launcher's half: remember which file to import (the worker does the rest)."""
    named_path(file_arg)
    d = requests_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}-{os.getpid()}.json"
    p.write_text(json.dumps({"file": file_arg, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}),
                 encoding="utf-8")
    return p


def pending_requests() -> list[Path]:
    d = requests_dir()
    return sorted(d.glob("*.json")) if d.is_dir() else []


def process_requests(conn, cfg=None) -> list[dict]:
    """The worker's half: import each requested file and queue its resume; the outcome goes to live.js (the Resume
    Builder tab shows it). A file that is refused or can't be imported is reported and dropped."""
    import sqlite3
    out = []
    for r in pending_requests():
        try:
            req = json.loads(r.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            r.unlink(missing_ok=True)
            continue
        if cfg is None:
            from .config import load_config
            cfg = load_config(paths.PREFERENCES)
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            res = import_and_queue(conn, cfg, req["file"])
            msg, ok = (f"{res['note']}; {res['message']}" if res["note"] else res["message"]), True
        except sqlite3.OperationalError:
            break                                         # the database is busy: try again on the next round
        except (FileRefused, ValueError, external.ExternalError) as e:
            msg, ok = f"{Path(str(req.get('file'))).name}: {e}", False
        db.set_meta(conn, "launcher_last", json.dumps({"at": at, "action": "external", "ok": ok, "message": msg}))
        conn.commit()
        r.unlink(missing_ok=True)
        out.append({"file": req.get("file"), "ok": ok, "message": msg})
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


def import_file(conn, cfg, p: Path) -> dict:
    """{job_pk, status: new | duplicate | updated | seen, message}. Raises ValueError for a file that can't be imported."""
    payload = _payload(p)
    if payload is not None and payload.get("url") and not str(payload.get("jd_text") or "").strip() \
            and not payload.get("file"):
        from .adapters.base import HttpClient
        from .add_url import add_job
        client = HttpClient()
        try:
            s = lambda k: (str(payload.get(k) or "").strip() or None)  # noqa: E731
            pk, status, how = add_job(conn, cfg, s("url"), client, company=s("company"), title=s("role"),
                                      location=s("location"), track=external.norm_track(s("track")))
        finally:
            client.close()
        conn.commit()
        return {"job_pk": pk, "status": status, "message": f"added by URL ({how}): job {pk}"}
    if payload is not None:
        ext, force = external.from_json(payload, origin="resume_builder" if payload.get("origin") == "resume_builder"
                                        else "dashboard")
    else:
        ext, force = external.from_file(p, origin="drop"), False
    out = external.import_external(conn, cfg, ext, force=force)
    if out.status == "error" or not out.job_pk:
        raise ValueError(out.message or "not imported")
    return {"job_pk": out.job_pk, "status": out.status, "message": out.message}


def import_and_queue(conn, cfg, file_arg: str, wait: float = 20.0) -> dict:
    """The launcher's / external_resume.sh's work: find the file (or the newest pending one), import it, queue its
    resume, start the worker, ask the worker to republish the dashboard data. Returns what happened."""
    from resume_builder import queue
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
    db.set_meta(conn, "publish_pending", db.utcnow())
    conn.commit()
    external._archive(p, True, paths.EXTERNAL_INBOX)
    q = queue.add(conn, [res["job_pk"]], origin="resume_builder")[0]
    queue.kick()
    j = conn.execute("SELECT company, title FROM jobs WHERE id=?", (res["job_pk"],)).fetchone()
    name = f"{j['company']} — {j['title']}" if j else f"job {res['job_pk']}"
    dup = res["status"] == "duplicate"
    msg = (f"{'matched existing' if dup else 'imported'} {name} (job {res['job_pk']}); resume "
           + ("queued" if q["status"] == "queued" else q["message"].split(": ", 1)[-1]))
    return {"job_pk": res["job_pk"], "imported": res["status"], "queue": q["status"], "message": msg, "note": note,
            "file": str(p)}


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
