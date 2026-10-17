"""hirescope:// links from the dashboard → actions, run silently in the background (no Terminal window), except
`interactive`, which opens Terminal for a live /resume-builder session.

    hirescope://queue?job=<id>[,<id>…][&regenerate=1]   queue resume builds (the background worker runs them)
    hirescope://queue?list=bucket[&regenerate=1]        every Bucket job without a current READY resume (all of them
                                                        with regenerate)
    hirescope://external?file=<saved file>              Resume Builder tab: import that external job (dedupe: an
                                                        existing job keeps its ID) and queue its resume
    hirescope://cancel?job=<id>                         cancel a queued or running build
    hirescope://bucket?job=<id>[,<id>…]&op=add|remove   the Bucket (your apply-list)
    hirescope://status?job=<id>&stage=<stage>[&app=<application id>&date=YYYY-MM-DD&v=<resume version>]
    hirescope://status?app=<application id>&stage=<stage>
                                                        Mark Applied (removes the job from the Bucket, records the
                                                        resume version) and application stage changes; stage=undo
                                                        reverts the Mark Applied that created `app`
    hirescope://interactive?job=<id>                    Terminal: cd <repo> && claude "/resume-builder <id>"

The page can't run programs: ~/Applications/HireScope Launcher.app (scripts/install_launcher.sh) receives the link
and runs `scripts/launcher.sh "<link>"`, which runs this module with the link as one argument. Every part of the link
is validated here before anything happens: known actions and parameter names only, numeric job IDs of jobs that
exist, known values for every option, file names only inside ~/Downloads or inputs/external_jobs/. Nothing from the
link is ever passed to a shell. Anything else is rejected with a short error (exit 2), which the launcher app shows
as a notification. Every link handled is logged to data.nosync/logs/launcher.log.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from . import paths
from .storage import db

log = logging.getLogger("jobsearch.launcher")
MAX_URL = 8000
MAX_JOBS = 100
_DIGITS = re.compile(r"^\d{1,9}$")
_JOBS = re.compile(r"^\d{1,9}(?:,\d{1,9}){0,%d}$" % (MAX_JOBS - 1))
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_APP = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STAGES = ("ready", "applied", "oa", "interviewing", "offer", "rejected", "ghosted", "withdrawn", "undo")


class LinkError(ValueError):
    """A link that is not exactly one of the known forms: rejected, nothing done."""


def _one_or_many(v: str, many: bool) -> list[int]:
    if not (_JOBS if many else _DIGITS).match(v):
        raise LinkError("job must be a numeric HireScope job ID" + (" (or a comma-separated list)" if many else ""))
    return [int(x) for x in v.split(",")]


def _choice(*allowed: str):
    def check(v: str) -> str:
        if v not in allowed:
            raise LinkError(f"unknown value {v[:30]!r} (expected {' / '.join(allowed)})")
        return v
    return check


def _app(v: str) -> str:
    if not _APP.match(v):
        raise LinkError("app must be an application id (letters, digits, - and _)")
    return v


def _date(v: str) -> str:
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise LinkError("date must be YYYY-MM-DD") from None
    return v


def _file(v: str) -> str:
    from .external_resume import FileRefused, allowed_path
    try:
        allowed_path(v)
    except FileRefused as e:
        raise LinkError(str(e)) from None
    return v


def _version(v: str) -> int:
    if not re.match(r"^\d{1,4}$", v):
        raise LinkError("v must be a resume version number")
    return int(v)


# action → {parameter: validator}; required parameters are checked per action in validate()
ACTIONS: dict[str, dict] = {
    "queue": {"job": lambda v: _one_or_many(v, True), "list": _choice("bucket"), "regenerate": _choice("0", "1")},
    "cancel": {"job": lambda v: _one_or_many(v, False)},
    "interactive": {"job": lambda v: _one_or_many(v, False)},
    "bucket": {"job": lambda v: _one_or_many(v, True), "op": _choice("add", "remove")},
    "external": {"file": _file},
    "status": {"job": lambda v: _one_or_many(v, False), "app": _app, "stage": _choice(*STAGES), "date": _date,
               "v": _version},
}


def parse(url: str) -> tuple[str, dict]:
    """(action, validated parameters) or LinkError."""
    if not isinstance(url, str) or not url:
        raise LinkError("no link given")
    if len(url) > MAX_URL:
        raise LinkError("link too long")
    if _CTRL.search(url):
        raise LinkError("link contains control characters")
    u = urlsplit(url)
    if u.scheme.lower() != "hirescope":
        raise LinkError("not a hirescope:// link")
    action = u.netloc.lower()                      # hirescope://<action>?…, nothing else
    if u.path not in ("", "/"):
        raise LinkError("unexpected path in the link")
    if u.fragment:
        raise LinkError("unexpected #fragment in the link")
    if action not in ACTIONS:
        raise LinkError(f"unknown action {action[:20]!r}")
    try:
        pairs = parse_qsl(u.query, keep_blank_values=True, strict_parsing=bool(u.query), max_num_fields=12)
    except ValueError as e:
        raise LinkError(f"malformed parameters ({e})") from None
    spec, out = ACTIONS[action], {}
    for k, v in pairs:
        if k not in spec:
            raise LinkError(f"unknown parameter {k[:20]!r} for {action}")
        if k in out:
            raise LinkError(f"parameter {k!r} given twice")
        if _CTRL.search(v) and k != "text":
            raise LinkError(f"parameter {k!r} contains control characters")
        out[k] = spec[k](v)
    return action, validate(action, out)


def validate(action: str, p: dict) -> dict:
    need = {"cancel": ["job"], "interactive": ["job"], "bucket": ["job", "op"], "status": ["stage"],
            "external": ["file"]}.get(action, [])
    for k in need:
        if k not in p:
            raise LinkError(f"{action} needs {k}")
    if action == "queue" and ("job" in p) == ("list" in p):
        raise LinkError("queue needs either job=<id> or list=bucket")
    if action == "status":
        if "job" not in p and "app" not in p:
            raise LinkError("status needs job=<id> or app=<application id>")
        if p["stage"] == "undo" and not ("job" in p and "app" in p):
            raise LinkError("stage=undo needs job and app")
        if ("date" in p or "v" in p) and p["stage"] != "applied":
            raise LinkError("date / v go with stage=applied only")
    return p


# --- actions ---------------------------------------------------------------------------------------------------------
def _known_jobs(conn, pks: list[int]) -> list[int]:
    missing = [pk for pk in pks if not conn.execute("SELECT 1 FROM jobs WHERE id=?", (pk,)).fetchone()]
    if missing:
        raise LinkError(f"no HireScope job {', '.join(map(str, missing[:5]))}")
    return pks


def do_queue(conn, p: dict) -> str:
    from resume_builder import queue
    regen = p.get("regenerate") == "1"
    if "list" in p:
        pks = queue.bucket_jobs(conn, regen)
        if not pks:
            return "Bucket: every job already has a current READY resume" if not regen else "The Bucket is empty"
        batch = f"bucket-{queue.now()}"
    else:
        pks, batch = _known_jobs(conn, p["job"]), None
    res = queue.add(conn, pks, regen, origin="bucket" if "list" in p else "dashboard",
                    batch=batch or (f"dashboard-{queue.now()}" if len(pks) > 1 else None))
    queue.kick()
    n = sum(1 for r in res if r["status"] == "queued")
    skipped = [r for r in res if r["status"] in ("skipped", "already")]
    return (f"queued {n} resume build(s)" + (f"; {len(skipped)} skipped ({skipped[0]['message']}"
                                              f"{'…' if len(skipped) > 1 else ''})" if skipped else ""))


def do_cancel(conn, p: dict) -> str:
    from resume_builder import queue
    pk = _known_jobs(conn, p["job"])[0]
    return queue.cancel(conn, pk)["message"]


def interactive_command(pk: int) -> str:
    """The shell line Terminal runs (the job ID is validated digits; the repo path is quoted)."""
    return f"cd {shlex.quote(str(paths.ROOT))} && claude \"/resume-builder {int(pk)}\""


def _applescript_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def do_interactive(conn, p: dict) -> str:
    pk = _known_jobs(conn, p["job"])[0]
    line = interactive_command(pk)
    if os.environ.get("HIRESCOPE_LAUNCHER_DRY"):
        return f"would open Terminal: {line}"
    script = f'tell application "Terminal"\n  activate\n  do script {_applescript_str(line)}\nend tell'
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=20)
    return f"opened Terminal: /resume-builder {pk}"


def _after(conn, reason: str) -> None:
    from . import hooks
    hooks.after_change(conn, reason=reason)


def do_bucket(conn, p: dict) -> str:
    from . import bucket
    pks = _known_jobs(conn, p["job"])
    on = p["op"] == "add"
    n = sum(bucket.set_in(conn, pk, on) for pk in pks)
    _after(conn, "bucket")
    return f"Bucket: {'added' if on else 'removed'} {n} job(s)" + (f" ({len(pks) - n} unchanged)" if n < len(pks) else "")


def do_status(conn, p: dict) -> str:
    from .tracker import statuses
    stage = p["stage"]
    pk = _known_jobs(conn, p["job"])[0] if "job" in p else None
    if stage == "undo":
        if not statuses.undo_applied(conn, pk, p["app"]):
            raise LinkError(f"nothing to undo for application {p['app']}")
        _after(conn, "undo applied")
        return f"undone: job {pk} is no longer marked applied"
    if stage == "applied" and pk is not None:
        app = statuses.app_for_job(conn, pk)
        if not app or app["stage"] == "ready":
            app_id = statuses.mark_applied(conn, pk, paths.APPS_DIR, p.get("date"), p.get("app"), p.get("v"))
            _after(conn, "mark applied")
            return f"applied: job {pk} (application {app_id})"
    app_id = p.get("app")
    if not app_id:
        app = statuses.app_for_job(conn, pk)
        if not app:
            raise LinkError(f"job {pk} has no application yet (Mark Applied first)")
        app_id = app["id"]
    try:
        changed = statuses.move_stage(conn, app_id, stage)
    except ValueError as e:
        raise LinkError(str(e)) from None
    _after(conn, "stage change")
    return f"application {app_id}: {stage}" + ("" if changed else " (unchanged)")


def do_external(conn, p: dict) -> str:
    from .config import load_config
    from .external import ExternalError
    from .external_resume import FileRefused, import_and_queue
    try:
        out = import_and_queue(conn, load_config(paths.PREFERENCES), p["file"])
    except (FileRefused, ExternalError, ValueError) as e:
        raise LinkError(str(e)) from None
    _after(conn, "external job")
    return f"{out['note']}; {out['message']}" if out["note"] else out["message"]


HANDLERS = {"external": do_external, "queue": do_queue, "cancel": do_cancel, "interactive": do_interactive, "bucket": do_bucket,
            "status": do_status}


# --- entry point -----------------------------------------------------------------------------------------------------
def _log_line(entry: dict) -> None:
    try:
        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(paths.LOG_DIR / "launcher.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _redact(url: str) -> str:
    return re.sub(r"(text=)[^&]*", r"\1…", url)[:300]


def handle(url: str, db_path: Path | None = None) -> str:
    """Validate and run one link. Returns a one-line summary; raises LinkError for a bad link."""
    from resume_builder.queue import retry_locked
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        action, params = parse(url)
    except LinkError as e:
        _log_line({"at": at, "link": _redact(url or ""), "ok": False, "error": str(e)})
        raise
    conn = db.connect(db_path or paths.DB)
    try:
        try:
            msg = retry_locked(HANDLERS[action], conn, params)
        except LinkError as e:
            _log_line({"at": at, "link": _redact(url), "ok": False, "error": str(e)})
            raise
        db.set_meta(conn, "launcher_last", json.dumps({"at": at, "action": action, "ok": True, "message": msg}))
        conn.commit()
        if action not in ("interactive",):
            from . import live
            try:
                live.write(conn)
            except Exception as e:  # noqa: BLE001 — the action is done; the page catches up on its next read
                log.warning("live.js not written", extra={"error": str(e)[:200]})
        _log_line({"at": at, "link": _redact(url), "ok": True, "message": msg})
        return msg
    finally:
        conn.close()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("HireScope: expected exactly one hirescope:// link", file=sys.stderr)
        return 2
    try:
        print(handle(argv[0]))
        return 0
    except LinkError as e:
        print(f"HireScope: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 — shown as a notification by the launcher app
        log.exception("launcher failed")
        print(f"HireScope: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
