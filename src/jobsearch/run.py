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
from .output.dashboard import coverage_rows, write_data_js
from .output.links import check_links
from .pipeline import ensure_extraction_current, run
from .watchlist import WatchlistError, load_watchlist
from .storage import db

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
            partial_run: bool = False) -> dict:
    """Write dashboard/data.js from the current DB state."""
    by_name = {db.company_key(c.name): c.tier for c in cfg.companies}
    mine = {w.company.casefold() for w in watch}

    def tier_of(j):
        return (1 if j["company"].casefold() in mine else None) or \
            cfg.prefs.company_tiers.tier_of(j["company"]) or by_name.get(j["company_key"])
    meta = {
        "last_run": db.utcnow(),
        "summary": summary,
        "log_path": str(log_file),
        "resume_path": str(resume.path),
        "resume_version_hash": resume.version_hash,
        "partial_run": partial_run,
        "repo_root": str(paths.ROOT),
        "coverage": coverage,
    }
    counts = write_data_js(paths.DASHBOARD / "data.js", db.open_jobs(conn), meta, cfg.prefs.timezone,
                           resume.version_hash, cfg.prefs.top_n, tier_of, {w.company for w in watch})
    log.info("dashboard: %d visible, %d filtered (%d listed) -> %s", counts["visible"], counts["filtered"],
             counts["filtered_listed"], paths.DASHBOARD / "index.html")
    return counts


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="jobsearch.run", description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="fetch and score, but write nothing (DB, JD files, dashboard)")
    p.add_argument("--source", help="only run companies using this adapter (e.g. greenhouse)")
    p.add_argument("--company", help="only run this company (case-insensitive); works for seed, My Companies, "
                                     "or any company name (auto-detects its job system)")
    p.add_argument("--all", action="store_true", help="ignore tier schedules and check every company now")
    p.add_argument("--no-link-check", action="store_true", help="skip the apply-link check")
    p.add_argument("--rescore", action="store_true", help="no fetching: re-extract and re-score stored jobs and rewrite the "
                                                          "dashboard (after editing preferences, config or the resume)")
    p.add_argument("--workers", type=int, default=16, help="parallel fetches (default 16)")
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
        watch = load_watchlist(paths.ROOT / cfg.prefs.my_companies_csv)
    except WatchlistError as e:
        log.error("%s", e)
        return 2
    log.info("resume %s (version %s): %d skills; %d seed companies, %d My Companies",
             args.resume, resume.version_hash, len(resume.skills), len(cfg.companies), len(watch))

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
    db.record_resume_version(conn, resume.version_hash, str(args.resume))
    ensure_extraction_current(conn, cfg, force=args.rescore)
    client = HttpClient(host_intervals={"myworkdayjobs.com": 0.5, "githubusercontent.com": 0.2,
                                         "boards-api.greenhouse.io": 0.25, "api.lever.co": 0.5,
                                         "api.ashbyhq.com": 0.5, "api.smartrecruiters.com": 0.35})
    try:
        summary = run(conn, cfg, resume, [] if args.rescore else None, client, Embedder(conn), jd_dir, watch=watch,
                      only_company=args.company, only_source=args.source, force_all=args.all,
                      workers=args.workers)
        conn.commit()
        links = None
        if not args.no_link_check and not args.dry_run and not args.rescore:
            links = check_links(conn, client, db.open_jobs(conn))
            log.info("apply links: %s", links)
    finally:
        client.close()
    s = summary.as_dict()
    s["links"] = links
    s["scope"] = "rescore" if args.rescore else ("partial" if (args.company or args.source) else "full")
    db.finish_run(conn, run_id, s)

    if args.rescore:  # keep the last real run's summary in the header
        last = conn.execute("SELECT summary FROM runs WHERE summary IS NOT NULL AND dry_run=0 AND "
                            "(json_extract(summary, '$.scope') = 'full' OR (json_extract(summary, '$.scope') IS NULL "
                            "AND json_extract(summary, '$.sources_checked') > 1)) ORDER BY id DESC LIMIT 1").fetchone()
        if last:
            s = {**json.loads(last[0]), "scored": s["scored"], "filtered": s["filtered"], "strong": s["strong"],
                 "by_track": s["by_track"], "rescored_at": db.utcnow()}
    if not args.dry_run:
        publish(conn, cfg, resume, s, coverage_rows(conn, summary, watch), watch, log_file,
                partial_run=bool(args.source or args.company))
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
