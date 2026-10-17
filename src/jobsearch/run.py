"""Entry point: python -m jobsearch.run [--dry-run] [--source X] [--company Y] [--verbose]"""
from __future__ import annotations

import argparse
import faulthandler
import json
import os
import signal
import logging
import logging.handlers
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import yaml

from . import external, paths
from .adapters.base import HttpClient
from .config import load_config
from .extract.resume import parse_resume
from .match.semantic import Embedder
from .output.dashboard import coverage_rows, eng_blogs, select_visible, write_data_js
from .output.h1b import load_h1b, match_companies
from .output.links import check_links
from . import pipeline
from .pipeline import compute_similar, ensure_extraction_current, extraction_stale, run
from .watchlist import WatchlistError, load_watchlist, resolve
from .storage import db
from .tracker import feedback, referrals, statuses
from .ops import alerts, backup, digest, excel, health, notify
from .ops.lock import Locked, RunLock
from .ops.seasons import track_seasons
from datetime import datetime
from zoneinfo import ZoneInfo

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


def import_dirs(setting: str | list[str]) -> list[Path]:
    """statuses_import_dir (one folder or a list): ~ expands, relative paths are inside the repo."""
    out = []
    for d in [setting] if isinstance(setting, str) else setting:
        p = Path(d).expanduser()
        out.append(p if p.is_absolute() else paths.ROOT / p)
    return out


def exit_code(summary: dict) -> int:
    """1 only for real source failures. "Manual check needed" companies (unsupported job systems, marked on purpose)
    and partial listings are reported in the summary and on the dashboard, not treated as errors. A one-off non-JSON
    response (retried with backoff, still failing) is a warning in every run (quick or full): it fails the run only
    when the same source also failed on its previous check."""
    fails = [f for f in summary.get("failures") or [] if not (f.get("transient") and not f.get("repeated"))]
    return 1 if fails else 0


def publish(conn, cfg, resume, summary: dict, coverage: list[dict], watch, log_file: Path,
            partial_run: bool = False, last_run: str | None = None, learned=None, imported: dict | None = None,
            seasons: list[dict] | None = None) -> dict:
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
        "manual_checks_cfg": cfg.prefs.manual_checks.model_dump(),
        "statuses_import": imported or {},
        "runs": db.recent_runs(conn),
        "seasons": seasons or [],
        "health_alerts": summary.get("health_alerts") or [],
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
    if made := statuses.ensure_applications(conn, paths.APPS_DIR):     # every job marked applied is in My Applications
        log.info("applications created for jobs marked applied", extra={"n": len(made)})
    external.write_packages(conn, resume.version_hash)                 # job packages of the jobs you added
    conn.commit()
    try:   # Resume Builder: resume statuses + versions → dashboard/resumes.js (and application links)
        from resume_builder.hirescope_bridge import refresh_all
        refresh_all(paths.DB)
    except Exception as e:  # noqa: BLE001 — the job run must not fail because of resume data
        log.warning("resume statuses not refreshed", extra={"error": str(e)[:200]})
    try:   # external jobs whose resume is READY → My Applications "Ready to apply"
        from .tracker import ready_apps
        if line := ready_apps.ensure(conn):
            log.info(line)
    except Exception as e:  # noqa: BLE001
        log.warning("ready-to-apply applications not updated", extra={"error": str(e)[:200]})
    statuses.write_sync_js(paths.SYNC_JS, statuses.sync_payload(conn))   # after refresh_all: includes resume links
    try:   # live.js / launcher.js: the open dashboard's queue, worker and launcher state (zero-CLI resumes)
        from . import live
        live.ensure_files(conn)
    except Exception as e:  # noqa: BLE001
        log.warning("live.js not written", extra={"error": str(e)[:200]})
    compute_similar(conn, Embedder(conn), shown, min_sim=cfg.prefs.scoring.similar_min)   # "More like this"
    jobs = db.open_jobs(conn)
    if paths.ENG_BLOGS.exists():
        meta["eng_blogs"] = eng_blogs({j["company"] for j in shown}, yaml.safe_load(paths.ENG_BLOGS.read_text(encoding="utf-8")) or {})
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
                           resume.version_hash, cfg.prefs.top_n, tier_of, {w.company for w in watch}, h1b=h1b,
                           apply_cfg=cfg.prefs.apply_today, title_rules=cfg.title_rules,
                           fresh_hours=cfg.prefs.notifications.early_bird_hours)
    log.info("dashboard: %d visible, %d filtered (%d listed), %d detail chunks -> %s", counts["visible"],
             counts["filtered"], counts["filtered_listed"], counts["chunks"], paths.DASHBOARD / "index.html")
    from .output.dashboard import write_search_js
    n = write_search_js(paths.DASHBOARD / "search.js", conn, counts["ids_visible"], counts["ids_listed"])
    log.info("search index: %d jobs -> %s", n, paths.DASHBOARD / "search.js")
    return counts


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="jobsearch.run", description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="fetch and score, but write nothing (DB, JD files, dashboard)")
    p.add_argument("--source", help="only run companies using this adapter (e.g. greenhouse)")
    p.add_argument("--company", help="only run this company (case-insensitive); works for seed, My Companies, "
                                     "or any company name (auto-detects its job system)")
    p.add_argument("--all", action="store_true", help="ignore tier schedules and check every company now")
    p.add_argument("--watchlist", action="store_true", help="only check inputs/companies/my_companies.csv companies")
    p.add_argument("--no-link-check", action="store_true", help="skip the apply-link check")
    p.add_argument("--rescore", action="store_true", help="no fetching: re-extract and re-score stored jobs and rewrite the "
                                                          "dashboard (after editing preferences, config or the resume)")
    p.add_argument("--workers", type=int, default=16, help="parallel fetches (default 16)")
    p.add_argument("--statuses", type=Path, help="import this dashboard export instead of the newest "
                                                 "jobsearch-statuses*.json in statuses_import_dir")
    p.add_argument("--no-import", action="store_true", help="don't import dashboard statuses this run")
    p.add_argument("--no-email", action="store_true", help="skip email sources (LinkedIn job-alert emails over IMAP)")
    p.add_argument("--quick", action="store_true", help="quick check: My Companies + tier-1 only, no link check, "
                                                        "no backup/digest (what launchd runs every 2–3 hours)")
    p.add_argument("--export-excel", nargs="?", const="", metavar="FILE", type=str,
                   help="also write an .xlsx (default data.nosync/exports/hirescope-<date>.xlsx)")
    p.add_argument("--digest", action="store_true", help="write the weekly digest now (default: first run on Sunday)")
    p.add_argument("--no-notify", action="store_true", help="no macOS notifications this run")
    p.add_argument("--no-backup", action="store_true", help="skip the daily backup this run")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--resume", type=Path, default=paths.RESUME, help="resume markdown (default inputs/resume/master_resume.md)")
    p.add_argument("--prefs", type=Path, default=paths.PREFERENCES, help="preferences yaml (default inputs/preferences.yaml)")
    return p.parse_args(argv)


EX_TEMPFAIL = 75   # offline: launchd's next scheduled run retries


def main(argv=None) -> int:
    """Lock (one run at a time; stale locks replaced), offline check, then the run itself."""
    args = parse_args(argv)
    faulthandler.register(signal.SIGUSR1)  # `kill -USR1 <pid>` dumps every thread's stack to stderr
    log_file = setup_logging(args.verbose)
    if not (args.rescore or args.dry_run) and not alerts.wait_online():
        log.error("offline (no network for about a minute): nothing fetched; the next scheduled run will retry")
        return EX_TEMPFAIL
    lock = RunLock(paths.STATE_DIR / "run.lock", stale_hours=3, what="quick check" if args.quick else "run")
    try:
        lock.acquire()
    except Locked as e:
        log.warning("%s — skipping this %s", e, "quick check" if args.quick else "run")
        return 0 if args.quick else 3
    try:
        return _main(args, log_file)
    finally:
        lock.release()


def _main(args, log_file: Path) -> int:
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
    sched = cfg.prefs.schedule
    deadline = None   # quick checks: stop waiting for sources after the limit (monotonic: awake time on macOS)
    if args.quick and sched.quick_time_limit_minutes:
        deadline = time.monotonic() + sched.quick_time_limit_minutes * 60
    log.info("resume %s (version %s): %d skills; %d seed companies, %d My Companies",
             args.resume, resume.version_hash, len(resume.skills), len(cfg.companies), len(watch))

    from .adapters.aggregator import AggregatorAdapter
    from .adapters.linkedin_alerts import LinkedInAlertsAdapter
    LinkedInAlertsAdapter.disabled = args.no_email
    notify.ENABLED = cfg.prefs.notifications.enabled and not args.no_notify and not args.dry_run
    AggregatorAdapter.enabled = cfg.prefs.aggregator.enabled
    AggregatorAdapter.date_window = cfg.prefs.aggregator.date_posted

    conn = db.connect(paths.DB)
    if moved := db.repair_moved_paths(conn, paths.JD_DIR, paths.APPS_DIR):
        log.info("stored file paths repaired after a move", extra={"rows": moved})
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

    if not args.dry_run and (n := db.close_interrupted_runs(conn)):
        log.info("marked earlier unfinished run(s) as interrupted", extra={"runs": n})
    run_id = db.start_run(conn, args.dry_run)
    run_started = db.utcnow()
    imported = {}
    if not args.no_import:
        try:
            if args.statuses:
                res = statuses.merge(conn, statuses.load_export(args.statuses), None if args.dry_run else paths.APPS_DIR)
                res.file = str(args.statuses)
            else:
                res = statuses.import_newest(conn, import_dirs(cfg.prefs.statuses_import_dir),
                                             None if args.dry_run else paths.APPS_DIR,
                                             None if args.dry_run else paths.IMPORTS_DIR)
            imported = res.as_dict()
            if res.errors:
                log.warning("statuses import: %d entries skipped: %s", len(res.errors), "; ".join(res.errors[:5]))
        except (OSError, ValueError) as e:
            log.error("statuses import failed: %s", e)
            imported = {"skipped_reason": f"failed: {e}"}
    ext = []
    if not args.dry_run:   # external jobs: the drop folder + "Add external job" files from the dashboard
        ext = external.import_pending(conn, cfg, paths.EXTERNAL_INBOX, import_dirs(cfg.prefs.statuses_import_dir))
        for o in ext:
            (log.info if o.status == "new" else log.warning)("external job %s", external.summary_line(o))
    learned = feedback.learn(db.labeled_jobs(conn), cfg.prefs)
    if learned.labeled:
        log.info("feedback learning: %s", learned.describe()["status"])
    db.record_resume_version(conn, resume.version_hash, str(args.resume))
    last_fetch = None
    if args.quick and extraction_stale(conn):
        # re-extracting every stored job takes ~7 min interactively and 20+ under launchd's background priority
        log.info("extraction code/config changed: re-extracting stored jobs waits for the next full run")
    else:
        ensure_extraction_current(conn, cfg)   # re-extracts only when extraction code or config changed
    client = HttpClient(host_intervals={"myworkdayjobs.com": 0.5, "githubusercontent.com": 0.2,
                                         "boards-api.greenhouse.io": 0.25, "api.lever.co": 0.5,
                                         "api.ashbyhq.com": 0.5, "api.smartrecruiters.com": 0.35,
                                         "hacker-news.firebaseio.com": 0.05, "jsearch.p.rapidapi.com": 1.0,
                                         "serpapi.com": 1.0, "api.rippling.com": 0.5, "jobs.jobvite.com": 0.5,
                                         "eightfold.ai": 1.5, "careers.microsoft.com": 1.5,
                                         "careers.qualcomm.com": 1.5,
                                         "icims.com": 0.5, "apply.workable.com": 0.5})
    try:
        summary = run(conn, cfg, resume, [] if args.rescore else None, client, Embedder(conn), jd_dir, watch=watch,
                      only_company=args.company, only_source=args.source, force_all=args.all,
                      workers=args.workers, watchlist_only=args.watchlist, learned=learned, quick=args.quick,
                      source_timeout=sched.source_timeout_minutes * 60 or None, deadline=deadline)
        conn.commit()
        links = None
        if not args.no_link_check and not args.dry_run and not args.rescore and not args.quick:
            links = check_links(conn, client, db.open_jobs(conn))
            log.info("apply links: %s", links)
    finally:
        client.close()
    s = summary.as_dict()
    s["links"] = links
    s["reopened"] = [f"{r['company']}: {r['title']}" for r in statuses.reopened_shortlisted(conn, run_started)]
    for r in s["reopened"]:
        log.warning("REOPENED (you had it shortlisted): %s", r)
    s["external"] = [external.summary_line(o) for o in ext]
    s["scope"] = "rescore" if args.rescore else "quick" if args.quick else (
        "partial" if (args.company or args.source or args.watchlist) else "full")
    s["health_alerts"] = [] if args.rescore else health.check(
        summary.outcomes, lambda c, src: db.recent_job_counts(conn, c, src, 5), cfg.prefs.health)
    for a in s["health_alerts"]:
        log.warning("SOURCE HEALTH: %s", a["message"])
    rows, early = alerts.new_strong(conn, run_started, cfg.prefs.notifications.early_bird_hours)
    s["new_strong_rows"], s["early_bird"] = ([] if args.rescore else rows[:50]), ([] if args.rescore else early[:50])
    db.finish_run(conn, run_id, s)

    if args.rescore:  # keep the last real run's summary in the header
        last = conn.execute("SELECT summary, finished_at FROM runs WHERE summary IS NOT NULL AND dry_run=0 AND "
                            "(json_extract(summary, '$.scope') = 'full' OR (json_extract(summary, '$.scope') IS NULL "
                            "AND json_extract(summary, '$.sources_checked') > 1)) ORDER BY id DESC LIMIT 1").fetchone()
        last_fetch = None
        if last:
            last_fetch = last[1]
            s = {**json.loads(last[0]), "scored": s["scored"], "filtered": s["filtered"], "strong": s["strong"],
                 "reopened": s["reopened"], "external": s["external"],
                 "by_track": s["by_track"], "rescored_at": db.utcnow(), "scope": "full"}
    if not args.dry_run:
        tz = ZoneInfo(cfg.prefs.timezone)
        today = datetime.now(tz).date()
        seasons = track_seasons(conn, today, rules=cfg.title_rules,
                                remind_days=cfg.prefs.notifications.season_remind_days)
        if not args.rescore:
            s["season_reminders"] = alerts.season_reminders(conn, seasons)
        publish(conn, cfg, resume, s, coverage_rows(conn, summary, watch), watch, log_file,
                partial_run=bool(args.source or args.company or args.watchlist or args.quick),
                last_run=last_fetch if args.rescore else None, learned=learned, imported=imported, seasons=seasons)
        if not args.quick:
            if not args.no_backup:
                try:
                    b = backup.backup(paths.DB, backup.backups_dir(cfg.prefs.backups.dir), today.isoformat(),
                                      keep=cfg.prefs.backups.keep, extra_dirs=(paths.IMPORTS_DIR, paths.APPS_DIR))
                    s["backup"] = str(b) if b else None
                except (OSError, sqlite3.Error) as e:
                    log.error("backup failed: %s", e)
            dg_dir = paths.DIGESTS_DIR
            if args.digest or digest.due(today, cfg.prefs.notifications.digest_weekday, dg_dir):
                p = digest.write(conn, dg_dir, today, seasons, tuple(cfg.prefs.applications.follow_up_days),
                                 cfg.prefs.manual_checks.remind_days, {w.company for w in watch})
                s["digest"] = str(p)
                notify.notify("HireScope weekly digest", f"Your week in review is ready: {p.name}", "data.nosync/digests")
        if args.export_excel is not None:
            out = Path(args.export_excel) if args.export_excel else paths.EXPORTS_DIR / f"hirescope-{today}.xlsx"
            s["excel"] = str(excel.export(conn, out, cfg.prefs.top_n))
            log.info("excel export: %s", s["excel"])
        if not args.rescore:
            alerts.send(s, cfg.prefs.notifications.early_bird_hours)
        text = alerts.summary_text(s)
        (paths.LOG_DIR / "last_run_summary.txt").write_text(text + "\n", encoding="utf-8")
        print(text, file=sys.stderr)
    conn.close()
    if tmp:
        tmp.cleanup()

    log.info("run summary: %s", json.dumps({k: v for k, v in s.items() if k not in ("new_strong", "new_strong_rows")}))
    ok, attempted = s["sources_ok"], s["sources_checked"]
    log.info("coverage: %d of %d companies/sources checked successfully this run (%d not due, skipped)",
             ok, attempted, s["sources_skipped"])
    for f in s["failures"]:
        log.warning("INCOMPLETE COVERAGE: %s (%s) failed: %s", f["company"], f["source"], f["error"])
    for m in s["manual"]:
        log.warning("MANUAL CHECK NEEDED: %s %s (%s)", m["company"], m["url"] or "", m["error"])
    for p in s["partial"]:
        log.warning("PARTIAL: %s (%s): %s", p["company"], p["source"], p["why"])
    return exit_code(s)


if __name__ == "__main__":
    rc = main()
    if pipeline.ABANDONED:   # a timed-out fetch thread is still running: exit without joining it
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(rc)
    sys.exit(rc)
