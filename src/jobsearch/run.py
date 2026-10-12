"""Entry point: python -m jobsearch.run [--dry-run] [--source X] [--company Y] [--verbose]"""
from __future__ import annotations

import argparse
import faulthandler
import json
import signal
import logging
import logging.handlers
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import paths
from .adapters.base import HttpClient
from .config import load_config
from .extract.resume import parse_resume
from .match.semantic import Embedder
from .output.dashboard import coverage_rows, select_visible, write_data_js
from .output.h1b import load_h1b, match_companies
from .output.links import check_links
from .pipeline import compute_similar, ensure_extraction_current, run
from .watchlist import WatchlistError, load_watchlist, resolve
from .storage import db
from .tracker import feedback, referrals, statuses

log = logging.getLogger("jobsearch")


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        d = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
             "logger": record.name, "msg": record.getMessage()}
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d)


def setup_logging(verbose: bool) -> Path:
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = paths.LOG_DIR / "jobsearch.log"
    fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(_JsonFormatter())
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers[:] = [fh, ch]
    for noisy in ("httpx", "httpcore", "sentence_transformers", "urllib3", "filelock", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file


def publish(conn, cfg, resume, summary: dict, coverage: list[dict], watch, log_file: Path,
            partial_run: bool = False, last_run: str | None = None, learned=None, imported: dict | None = None) -> dict:
    """Write dashboard/data.js from the current DB state."""
    by_name = {db.company_key(c.name): c.tier for c in cfg.companies}
    mine = {w.company.casefold() for w in watch}

    def tier_of(j):
        return (1 if j["company"].casefold() in mine else None) or \
            cfg.prefs.company_tiers.tier_of(j["company"]) or by_name.get(j["company_key"])
    meta = {
        "last_run": last_run or db.utcnow(),
        "summary": summary,
        "log_path": str(log_file),
        "resume_path": str(resume.path),
        "resume_version_hash": resume.version_hash,
        "partial_run": partial_run,
        "repo_root": str(paths.ROOT),
        "coverage": coverage,
        "learned": (learned or feedback.learn(db.labeled_jobs(conn), cfg.prefs)).describe(),
        "applications_cfg": cfg.prefs.applications.model_dump(),
        "statuses_import": imported or {},
        "runs": db.recent_runs(conn),
    }
    jobs = db.open_jobs(conn)
    shown = select_visible(jobs, cfg.prefs.top_n)
    conn_file = paths.ROOT / cfg.prefs.connections_csv
    if conn_file.exists():
        try:
            people = referrals.load(conn_file)
            names = sorted({j["company"] for j in shown} | {r[0] for r in conn.execute("SELECT company FROM applications")})
            meta["referrals"] = referrals.match(names, people, paths.CONFIG / "company_aliases.yaml")
            meta["connections"] = len(people)
        except (OSError, ValueError) as e:
            log.warning("referral finder: %s: %s", conn_file, e)
    statuses.write_sync_js(paths.SYNC_JS, statuses.sync_payload(conn))
    compute_similar(conn, Embedder(conn), shown)            # "More like this"
    jobs = db.open_jobs(conn)
    h1b = {}
    sh = cfg.prefs.sponsorship_history
    if sh.enabled:
        f = (paths.ROOT / sh.file) if not Path(sh.file).is_absolute() else Path(sh.file)
        if f.exists():
            try:
                h1b = match_companies(sorted({j["company"] for j in shown}), load_h1b(f))
            except ValueError as e:
                log.warning("sponsorship history: %s", e)
        else:
            log.warning("sponsorship history enabled but %s not found", f)
    counts = write_data_js(paths.DASHBOARD / "data.js", jobs, meta, cfg.prefs.timezone,
                           resume.version_hash, cfg.prefs.top_n, tier_of, {w.company for w in watch}, h1b=h1b)
    log.info("dashboard: %d visible, %d filtered (%d listed), %d detail chunks -> %s", counts["visible"],
             counts["filtered"], counts["filtered_listed"], counts["chunks"], paths.DASHBOARD / "index.html")
    return counts


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="jobsearch.run", description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="fetch and score, but write nothing (DB, JD files, dashboard)")
    p.add_argument("--source", help="only run companies using this adapter (e.g. greenhouse)")
    p.add_argument("--company", help="only run this company (case-insensitive); works for seed, My Companies, "
                                     "or any company name (auto-detects its job system)")
    p.add_argument("--all", action="store_true", help="ignore tier schedules and check every company now")
    p.add_argument("--watchlist", action="store_true", help="only check inputs/my_companies.csv companies")
    p.add_argument("--no-link-check", action="store_true", help="skip the apply-link check")
    p.add_argument("--rescore", action="store_true", help="no fetching: re-extract and re-score stored jobs and rewrite the "
                                                          "dashboard (after editing preferences, config or the resume)")
    p.add_argument("--workers", type=int, default=16, help="parallel fetches (default 16)")
    p.add_argument("--statuses", type=Path, help="import this dashboard export instead of the newest "
                                                 "jobsearch-statuses*.json in statuses_import_dir")
    p.add_argument("--no-import", action="store_true", help="don't import dashboard statuses this run")
    p.add_argument("--no-email", action="store_true", help="skip email sources (LinkedIn job-alert emails over IMAP)")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--resume", type=Path, default=paths.RESUME, help="resume markdown (default inputs/master_resume.md)")
    p.add_argument("--prefs", type=Path, default=paths.PREFERENCES, help="preferences yaml (default inputs/preferences.yaml)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    faulthandler.register(signal.SIGUSR1)  # `kill -USR1 <pid>` dumps every thread's stack to stderr
    log_file = setup_logging(args.verbose)
    try:
        cfg = load_config(args.prefs)
        resume = parse_resume(args.resume, cfg.synonyms)
    except (FileNotFoundError, ValueError) as e:
        log.error("%s", e)
        return 2

    try:
        watch = resolve(load_watchlist(paths.ROOT / cfg.prefs.my_companies_csv), paths.CONFIG / "company_aliases.yaml")
    except WatchlistError as e:
        log.error("%s", e)
        return 2
    log.info("resume %s (version %s): %d skills; %d seed companies, %d My Companies",
             args.resume, resume.version_hash, len(resume.skills), len(cfg.companies), len(watch))

    from .adapters.aggregator import AggregatorAdapter
    from .adapters.linkedin_alerts import LinkedInAlertsAdapter
    LinkedInAlertsAdapter.disabled = args.no_email
    AggregatorAdapter.enabled = cfg.prefs.aggregator.enabled
    AggregatorAdapter.date_window = cfg.prefs.aggregator.date_posted

    conn = db.connect(paths.DB)
    tmp = None
    if args.dry_run:  # work on an in-memory copy so nothing persists
        mem = sqlite3.connect(":memory:")
        conn.backup(mem)
        conn.close()
        mem.row_factory = sqlite3.Row
        conn = mem
        tmp = tempfile.TemporaryDirectory(prefix="jobsearch-dry-")
        jd_dir = Path(tmp.name)
    else:
        jd_dir = paths.JD_DIR

    run_id = db.start_run(conn, args.dry_run)
    run_started = db.utcnow()
    imported = {}
    if not args.no_import:
        try:
            if args.statuses:
                res = statuses.merge(conn, statuses.load_export(args.statuses), None if args.dry_run else paths.APPS_DIR)
                res.file = str(args.statuses)
            else:
                res = statuses.import_newest(conn, Path(cfg.prefs.statuses_import_dir).expanduser(),
                                             None if args.dry_run else paths.APPS_DIR,
                                             None if args.dry_run else paths.IMPORTS_DIR)
            imported = res.as_dict()
            if res.errors:
                log.warning("statuses import: %d entries skipped: %s", len(res.errors), "; ".join(res.errors[:5]))
        except (OSError, ValueError) as e:
            log.error("statuses import failed: %s", e)
            imported = {"skipped_reason": f"failed: {e}"}
    learned = feedback.learn(db.labeled_jobs(conn), cfg.prefs)
    if learned.labeled:
        log.info("feedback learning: %s", learned.describe()["status"])
    db.record_resume_version(conn, resume.version_hash, str(args.resume))
    last_fetch = None
    ensure_extraction_current(conn, cfg)   # re-extracts only when extraction code or config changed
    client = HttpClient(host_intervals={"myworkdayjobs.com": 0.5, "githubusercontent.com": 0.2,
                                         "boards-api.greenhouse.io": 0.25, "api.lever.co": 0.5,
                                         "api.ashbyhq.com": 0.5, "api.smartrecruiters.com": 0.35,
                                         "hacker-news.firebaseio.com": 0.05, "jsearch.p.rapidapi.com": 1.0,
                                         "serpapi.com": 1.0})
    try:
        summary = run(conn, cfg, resume, [] if args.rescore else None, client, Embedder(conn), jd_dir, watch=watch,
                      only_company=args.company, only_source=args.source, force_all=args.all,
                      workers=args.workers, watchlist_only=args.watchlist, learned=learned)
        conn.commit()
        links = None
        if not args.no_link_check and not args.dry_run and not args.rescore:
            links = check_links(conn, client, db.open_jobs(conn))
            log.info("apply links: %s", links)
    finally:
        client.close()
    s = summary.as_dict()
    s["links"] = links
    s["reopened"] = [f"{r['company']}: {r['title']}" for r in statuses.reopened_shortlisted(conn, run_started)]
    for r in s["reopened"]:
        log.warning("REOPENED (you had it shortlisted): %s", r)
    s["scope"] = "rescore" if args.rescore else ("partial" if (args.company or args.source or args.watchlist) else "full")
    db.finish_run(conn, run_id, s)

    if args.rescore:  # keep the last real run's summary in the header
        last = conn.execute("SELECT summary, finished_at FROM runs WHERE summary IS NOT NULL AND dry_run=0 AND "
                            "(json_extract(summary, '$.scope') = 'full' OR (json_extract(summary, '$.scope') IS NULL "
                            "AND json_extract(summary, '$.sources_checked') > 1)) ORDER BY id DESC LIMIT 1").fetchone()
        last_fetch = None
        if last:
            last_fetch = last[1]
            s = {**json.loads(last[0]), "scored": s["scored"], "filtered": s["filtered"], "strong": s["strong"],
                 "reopened": s["reopened"],
                 "by_track": s["by_track"], "rescored_at": db.utcnow(), "scope": "full"}
    if not args.dry_run:
        publish(conn, cfg, resume, s, coverage_rows(conn, summary, watch), watch, log_file,
                partial_run=bool(args.source or args.company or args.watchlist), last_run=last_fetch if args.rescore else None,
                learned=learned, imported=imported)
    conn.close()
    if tmp:
        tmp.cleanup()

    log.info("run summary: %s", json.dumps({k: v for k, v in s.items() if k != "new_strong"}))
    ok, attempted = s["sources_ok"], s["sources_checked"]
    log.info("coverage: %d of %d companies/sources checked successfully this run (%d not due, skipped)",
             ok, attempted, s["sources_skipped"])
    for f in s["failures"]:
        log.warning("INCOMPLETE COVERAGE: %s (%s) failed: %s", f["company"], f["source"], f["error"])
    for m in s["manual"]:
        log.warning("MANUAL CHECK NEEDED: %s %s (%s)", m["company"], m["url"] or "", m["error"])
    for p in s["partial"]:
        log.warning("PARTIAL: %s (%s): %s", p["company"], p["source"], p["why"])
    return 0 if s["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
