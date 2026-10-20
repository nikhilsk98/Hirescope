"""Add a job you found outside HireScope (an "External" job), scored and packaged now.

    ./scripts/add_job.sh --company Acme --role "Software Engineer Co-op" --file jd.pdf [--url URL] [--track coop]
    pbcopy-ed JD:  pbpaste | ./scripts/add_job.sh --company Acme --role "Backend Engineer" --paste --track fulltime
    ./scripts/add_job.sh --json ~/Downloads/hirescope-external-job-….json     # one saved by the dashboard form
    ./scripts/add_job.sh --pending        # everything in inputs/external_jobs/ + the dashboard's saved files
    ./scripts/add_job.sh --url URL [--company X --role Y]                    # URL only: same as add_url.sh

The JD file may be .txt, .md, .pdf or .docx; company / role / URL / location / track may also come from "Key: value"
lines at the top of a text file or from its name ("Acme - Software Engineer.pdf"). The job is stored with source
"external", scored (tracks, level, sponsorship flags, ATS keywords), given a job package, and shown on the dashboard
with an "External" badge, and its tailored resume is queued (the same queue and build as every other job). If it
matches a job HireScope already has, nothing is created and the existing job is named instead — its resume is queued
if it has none (--force adds it anyway). Exit codes: 0 added, 3 duplicate (not added), 2 error.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import external, paths
from .config import load_config
from .extract.resume import parse_resume
from .storage import db

log = logging.getLogger("jobsearch.add_job")
EXIT_DUPLICATE = 3


def _track_arg(v: str) -> str:
    try:
        return external.norm_track(v)
    except external.ExternalError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="add_job.sh", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--company")
    p.add_argument("--role", "--title", dest="role")
    p.add_argument("--location")
    p.add_argument("--url", help="the posting / application link (optional with a JD)")
    p.add_argument("--track", type=_track_arg, help="coop | fulltime (default: detected from the JD)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--file", type=Path, help="JD file: .txt, .md, .pdf or .docx")
    g.add_argument("--paste", action="store_true", help="read the JD text from stdin")
    g.add_argument("--text", help="the JD text itself")
    g.add_argument("--json", type=Path, help="a hirescope-external-job-*.json saved by the dashboard")
    g.add_argument("--pending", action="store_true",
                   help="import every pending file now (inputs/external_jobs/ and the dashboard's saved files)")
    p.add_argument("--force", action="store_true", help="add it even if it looks like a job HireScope already has")
    p.add_argument("--prefs", type=Path, default=paths.PREFERENCES)
    p.add_argument("--resume", type=Path, default=paths.RESUME)
    return p.parse_args(argv)


def collect(args) -> list[tuple[external.ExternalJob, bool]]:
    kw = dict(company=args.company, role=args.role, url=args.url, location=args.location, track=args.track)
    if args.file:
        if not args.file.is_file():
            raise external.ExternalError(f"no such file: {args.file}")
        return [(external.from_file(args.file, origin="file", **kw), args.force)]
    if args.json:
        job, force = external.from_json(json.loads(args.json.read_text(encoding="utf-8")))
        return [(job, force or args.force)]
    text = args.text if args.text is not None else sys.stdin.read()
    return [(external.from_text(text, origin="paste", **kw), args.force)]


def describe(conn, o: external.Outcome) -> str:
    if o.status != "new":
        line = external.summary_line(o)
        if o.status == "duplicate" and o.job_pk:
            line += f"\n  open it: dashboard/index.html#fulltime/all?job={o.job_pk}   (or --force to add it anyway)"
        return line
    row = db.decode_row(conn.execute("SELECT * FROM jobs WHERE id=?", (o.job_pk,)).fetchone())
    track = row["track_override"] or row["track"]
    sig = row.get("signals") or {}
    kw = row.get("keywords") or {}
    flags = {k: v.get("value") for k, v in (row.get("flags") or {}).items() if v.get("value") not in (None, "Not stated")}
    out = [f"added: {row['company']} — {row['title']} (job {row['id']}, External)",
           f"  track {track} · level {row.get('level') or 'unknown'} · score "
           f"{'—' if row['score'] is None else round(row['score'])} ({row['label'] or 'not scored'})"
           + (f" · ATS keyword coverage {round(kw['coverage'])}%" if kw.get("coverage") is not None else ""),
           f"  flags: {', '.join(f'{k}: {v}' for k, v in flags.items()) or 'none stated'}"]
    if sig.get("rules_would_hide"):
        out.append("  note: your rules would have hidden it (shown because you added it): "
                   + "; ".join(sig["rules_would_hide"]))
    from .output.packages import package_filename
    pkg = paths.PACKAGES_DIR / paths.slug(row["company"]) / package_filename(row["company"], row["title"], row["job_id"])
    out.append(f"  job package: {pkg}")
    return "\n".join(out)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not (args.file or args.paste or args.text is not None or args.json or args.pending):
        if not args.url:
            print("add_job.sh: give the JD (--file, --paste, --text), --json, --pending, or --url", file=sys.stderr)
            return 2
        from . import add_url        # URL only: the Add job by URL behavior, unchanged
        rest = [args.url, *(["--company", args.company] if args.company else []),
                *(["--title", args.role] if args.role else []), *(["--location", args.location] if args.location else []),
                *(["--track", args.track] if args.track else []), "--prefs", str(args.prefs), "--resume", str(args.resume)]
        return add_url.main(rest)

    from .add_url import rescore_and_publish
    from .run import setup_logging
    log_file = setup_logging(False)
    cfg = load_config(args.prefs)
    resume = parse_resume(args.resume, cfg.synonyms)
    conn = db.connect(paths.DB)
    try:
        if args.pending:
            from .run import import_dirs
            outcomes = external.import_pending(conn, cfg, paths.EXTERNAL_INBOX, import_dirs(cfg.prefs.statuses_import_dir))
            if not outcomes:
                print(f"nothing pending in {paths.EXTERNAL_INBOX} or the dashboard's download folders")
        else:
            try:
                outcomes = [external.import_external(conn, cfg, job, force=force) for job, force in collect(args)]
            except (external.ExternalError, OSError, ValueError) as e:
                print(f"add_job.sh: {e}", file=sys.stderr)
                return 2
        if any(o.status == "new" for o in outcomes):
            rescore_and_publish(conn, cfg, resume, log_file)
        from .external_resume import queue_resume
        for o in outcomes:
            print(describe(conn, o))
            if o.status in ("new", "duplicate") and o.job_pk:     # same queue, same build as every other job
                q = queue_resume(conn, o.job_pk, origin="external")
                print(f"  resume: {'queued (builds in the background)' if q['status'] == 'queued' else q['message']}")
    finally:
        conn.close()
    if any(o.status == "error" for o in outcomes):
        return 2
    return EXIT_DUPLICATE if outcomes and all(o.status == "duplicate" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
