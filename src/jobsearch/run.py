"""Entry point: python -m jobsearch.run [--dry-run] [--source X] [--company Y] [--verbose]"""
from __future__ import annotations

import argparse
import json
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
from .output.dashboard import write_data_js
from .pipeline import run
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


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="jobsearch.run", description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="fetch and score, but write nothing (DB, JD files, dashboard)")
    p.add_argument("--source", help="only run companies using this adapter (e.g. greenhouse)")
    p.add_argument("--company", help="only run this company (case-insensitive)")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--resume", type=Path, default=paths.RESUME, help="resume markdown (default inputs/master_resume.md)")
    p.add_argument("--prefs", type=Path, default=paths.PREFERENCES, help="preferences yaml (default inputs/preferences.yaml)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    log_file = setup_logging(args.verbose)
    try:
        cfg = load_config(args.prefs)
        resume = parse_resume(args.resume, cfg.synonyms)
    except (FileNotFoundError, ValueError) as e:
        log.error("%s", e)
        return 2

    companies = cfg.companies
    if args.source:
        companies = [c for c in companies if c.source == args.source]
    if args.company:
        companies = [c for c in companies if c.name.casefold() == args.company.casefold()]
        if not companies:
            log.error("company %r not found in config/companies.yaml", args.company)
            return 2
    log.info("resume %s (version %s): %d skills; %d companies to check",
             args.resume, resume.version_hash, len(resume.skills), len(companies))

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
    client = HttpClient()
    try:
        summary = run(conn, cfg, resume, companies, client, Embedder(conn), jd_dir)
        conn.commit()
    finally:
        client.close()
    s = summary.as_dict()
    db.finish_run(conn, run_id, s)

    if not args.dry_run:
        by_name = {db.company_key(c.name): c.tier for c in cfg.companies}
        tier_of = lambda j: cfg.prefs.company_tiers.tier_of(j["company"]) or by_name.get(j["company_key"])  # noqa: E731
        meta = {
            "last_run": db.utcnow(),
            "summary": s,
            "log_path": str(log_file),
            "resume_path": str(args.resume),
            "resume_version_hash": resume.version_hash,
            "partial_run": bool(args.source or args.company),
        }
        counts = write_data_js(paths.DASHBOARD / "data.js", db.open_jobs(conn), meta, cfg.prefs.timezone,
                               resume.version_hash, cfg.prefs.top_n, tier_of)
        log.info("dashboard: %d visible, %d filtered -> %s", counts["visible"], counts["filtered"],
                 paths.DASHBOARD / "index.html")
    conn.close()
    if tmp:
        tmp.cleanup()

    log.info("run summary: %s", json.dumps({k: v for k, v in s.items() if k != "new_strong"}))
    for f in s["failures"]:
        log.warning("INCOMPLETE COVERAGE: %s (%s) failed: %s", f["company"], f["source"], f["error"])
    return 0 if s["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
