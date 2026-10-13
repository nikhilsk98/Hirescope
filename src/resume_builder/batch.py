"""Batch generation for shortlisted jobs: shows the job count and a usage warning, and starts only with --yes (after
you confirmed). Each job still gets its own full generation (prepare → draft → review → truth pass → build), one at
a time; jobs whose current resume is still valid are reused, not regenerated.
"""
from __future__ import annotations

import json

from jobsearch import paths as hs_paths
from jobsearch.storage import db

from . import config as rc
from . import hirescope_bridge, pipeline, versions

EXIT_NEEDS_CONFIRMATION = 3


def shortlisted(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, company, title, COALESCE(track_override, track) AS track FROM jobs WHERE user_status = 'shortlisted' "
        "AND job_status != 'closed' ORDER BY user_status_at DESC, id")]


def plan(conn, limit: int | None = None) -> dict:
    settings = rc.load_settings()
    jobs = shortlisted(conn)
    todo, reuse = [], []
    for j in jobs:
        jd = pipeline.job_dir(j["company"], j["id"], j["title"])
        cur = versions.current(jd) if jd.exists() else None
        (reuse if cur and not versions.detect_edits(jd / f"v{cur['version']}")["edited"] else todo).append(j)
    cap = min(limit or settings.batch_max_jobs, settings.batch_max_jobs)
    return {"jobs": jobs, "todo": todo[:cap], "skipped_over_cap": todo[cap:], "has_resume": reuse, "cap": cap}


def run(which: str, yes: bool, limit: int | None = None) -> int:
    conn = db.connect(hs_paths.DB)
    try:
        p = plan(conn, limit)
    finally:
        conn.close()
    n = len(p["todo"])
    print(f"Batch: {len(p['jobs'])} shortlisted job(s); {len(p['has_resume'])} already have a current resume "
          f"(checked again, reused if nothing changed); {n} need a new generation"
          + (f"; {len(p['skipped_over_cap'])} over the batch cap of {p['cap']} (run again later)"
             if p["skipped_over_cap"] else "") + ".")
    if not p["jobs"]:
        print("Nothing to do: shortlist jobs in the dashboard, Export statuses, and run HireScope (or import).")
        return 0
    print(f"⚠ Usage: each new generation is a full drafter + reviewer + truth pass in Claude Code (roughly the same "
          f"usage as running /resume-builder {n} time(s)). Jobs run one after another.")
    for j in p["todo"]:
        print(f"  - {j['id']}: {j['company']} — {j['title']} ({j['track']})")
    if not yes:
        print("Not started. Confirm with the user, then run: python -m resume_builder batch shortlisted --yes")
        return EXIT_NEEDS_CONFIRMATION
    for j in [*p["todo"], *p["has_resume"]]:
        try:
            hirescope_bridge.update_job(j["id"], "queued", "batch: waiting for its turn")
        except hirescope_bridge.BridgeError as e:
            print(f"Warning: {e}")
    queue = [j["id"] for j in [*p["todo"], *p["has_resume"]]]
    (rc.resumes_dir() / "_cache").mkdir(parents=True, exist_ok=True)
    (rc.resumes_dir() / "_cache" / "batch_queue.json").write_text(json.dumps({"queued_at": pipeline.utcnow(), "jobs": queue},
                                                                  indent=1), encoding="utf-8")
    print(f"Queued {len(queue)} job(s) (dashboard shows 'queued'). Process them in order: "
          + " ".join(str(x) for x in queue))
    return 0
