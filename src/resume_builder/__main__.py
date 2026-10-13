"""python -m resume_builder <command> — the deterministic half of /resume-builder (see SKILL.md).

  check                      dependencies and inputs
  prepare <job> [--profile]  evidence bank, JD analysis, baseline + brief for the drafter (work/)
  build <job> [--content F]  validate content, render, compile, check the PDF, repair (max 3), save v<N>
  show <job>                 versions and the current one
  schema                     the content JSON schema
Job = the HireScope job ID (the number in the dashboard URL: #…&job=123).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as rc
from . import pipeline, versions
from .compile import CompileError
from .config import InputError
from .content_schema import ContentError, json_schema


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


def cmd_check(args) -> int:
    missing = pipeline.check_dependencies()
    try:
        hashes = pipeline.check_inputs()
        settings = rc.load_settings()
        from .evidence import load_bank
        bank = load_bank()
    except InputError as e:
        print(f"Input problem: {e}")
        return 2
    if missing:
        print("Missing dependencies:\n  - " + "\n  - ".join(missing))
        return 2
    print(f"OK: tools present ({pipeline.tool_versions()}); inputs: {', '.join(hashes)}; "
          f"{len(bank.items)} evidence items in {len(bank.entries)} entries; profiles: {', '.join(settings.profiles)}")
    for w in bank.warnings:
        print(f"note: {w}")
    return 0


def cmd_prepare(args) -> int:
    out = pipeline.prepare(args.job, args.profile)
    a = out["analysis"]
    cats = {}
    for r in a["requirements"]:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    print(f"Prepared {a['job']['company']} — {a['job']['title']} (job {a['job']['pk']}, {a['track']}, "
          f"profile {a['profile']['picked']})")
    print(f"  requirements: {len(a['requirements'])} {cats}; keywords: {len(a['keywords'])}; "
          f"injection warnings: {len(a['injection_warnings'])}")
    print(f"  brief:    {out['work']}/brief.md\n  baseline: {out['baseline']}\n  write:    {out['work']}/content.json")
    for w in out["bank_warnings"]:
        print(f"  note: {w}")
    return 0


def cmd_build(args) -> int:
    from jobsearch.storage import db as hs_db
    from jobsearch import paths as hs_paths
    from .jd_analysis import resolve_job
    conn = hs_db.connect(hs_paths.DB)
    try:
        job = resolve_job(conn, args.job)
    finally:
        conn.close()
    jd = pipeline.job_dir(job["company"], job["id"])
    with versions.JobLock(jd):
        res = pipeline.build(args.job, Path(args.content) if args.content else None)
        out = pipeline.finalize(res)
    rep = res["report"]
    print(f"{out['status'].upper()}: v{out['version']} → {out['version_dir']}")
    if out["pdf"]:
        print(f"  PDF: {out['pdf']}")
    for a in res["attempts"]:
        print(f"  attempt {a['attempt']}: {'ok' if a['ok'] else 'failed'} ({a['pages']} page(s))")
    for r in res["repairs"]:
        print(f"  repair: {r}")
    for r in out["reasons"]:
        print(f"  ✗ {r}")
    for w in (rep.warnings() if rep else []):
        print(f"  ! {w.name}: {w.detail}")
    return 0 if out["status"] == "ready" else 1


def cmd_show(args) -> int:
    from jobsearch.storage import db as hs_db
    from jobsearch import paths as hs_paths
    from .jd_analysis import resolve_job
    conn = hs_db.connect(hs_paths.DB)
    try:
        job = resolve_job(conn, args.job)
    finally:
        conn.close()
    jd = pipeline.job_dir(job["company"], job["id"])
    _print({"job_dir": str(jd), "current": versions.current(jd), "versions": versions.versions_summary(jd)})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m resume_builder", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p = sub.add_parser("prepare")
    p.add_argument("job")
    p.add_argument("--profile")
    p = sub.add_parser("build")
    p.add_argument("job")
    p.add_argument("--content")
    p = sub.add_parser("show")
    p.add_argument("job")
    sub.add_parser("schema")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "schema":
            _print(json_schema())
            return 0
        return {"check": cmd_check, "prepare": cmd_prepare, "build": cmd_build, "show": cmd_show}[args.cmd](args)
    except (InputError, ContentError, CompileError, versions.LockedError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
