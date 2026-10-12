"""Add one job by URL: python -m jobsearch.add_url <url> [--company X] [--title T] [--jd-file F | --paste]

Order: the matching ATS adapter (Greenhouse / Lever / Ashby / Workday / SmartRecruiters) -> schema.org
JobPosting JSON-LD on the page (robots.txt respected) -> a JD you provide (--jd-file / --paste, e.g. for
school co-op boards behind a login: copy the text yourself). The job is then scored and tracked like
any other, and the dashboard is rewritten. Its canonical key means a later ATS fetch merges with it.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .adapters.base import HttpClient, RawJob
from .adapters.canonical import canonical_key
from .adapters.detect import from_url
from .adapters.jsonld import CareersPageAdapter
from .adapters.registry import ADAPTERS
from .config import load_config
from .extract.resume import parse_resume
from .match.semantic import Embedder
from .pipeline import RunSummary, score_all, to_record
from .storage import db
from .watchlist import load_watchlist

log = logging.getLogger("jobsearch.add_url")
SOURCE = "added_url"


def resolve(url: str, client: HttpClient, company: str | None) -> tuple[RawJob | None, str]:
    fields = from_url(url) or {}
    adapter_cls = ADAPTERS.get(fields.get("source", ""))
    if adapter_cls is not None:
        try:
            raw = adapter_cls(client).fetch_one(url, company=company)
            if raw:
                return raw, f"{adapter_cls.name} API"
        except Exception as e:  # noqa: BLE001
            log.info("%s adapter could not read %s: %s", adapter_cls.name, url, e)
    try:
        raw = CareersPageAdapter(client).fetch_one(url, company=company)
        if raw:
            return raw, "JSON-LD on the page"
    except Exception as e:  # noqa: BLE001
        log.info("JSON-LD read failed for %s: %s", url, e)
    return None, "unreadable"


def add_job(conn, cfg, url: str, client: HttpClient, *, company: str | None = None, title: str | None = None,
            jd_text: str | None = None, location: str | None = None, track: str | None = None,
            jd_dir: Path | None = paths.JD_DIR) -> tuple[int, str, str]:
    """Returns (job pk, status new|updated|seen, how it was read)."""
    raw, how = (None, "manual") if jd_text else resolve(url, client, company)
    if raw is None:
        if not jd_text:
            raise ValueError(f"Could not read {url} automatically. Re-run with --jd-file FILE (or --paste) "
                             "plus --company and --title to add it manually.")
        if not (company and title):
            raise ValueError("Manual entry needs --company and --title.")
        raw = RawJob(company=company, job_id=canonical_key(url) or url, title=title, source=SOURCE, source_url=url,
                     apply_url=url, location=location, description=jd_text)
        how = "manual JD"
    raw.source = SOURCE
    raw.source_url = url
    raw.apply_url = raw.apply_url or url
    raw.canonical_key = raw.canonical_key or canonical_key(url)
    if company:
        raw.company = company
    if title:
        raw.title = title
    if location:
        raw.location = location
    if not raw.posted_date:
        raw.extra["posted_date_unknown"] = True
    rec = to_record(raw, cfg, jd_dir, official=True)
    pk, status = db.upsert_job(conn, rec)
    if track:
        db.save_scores(conn, pk, track_override=track)
    db.save_scores(conn, pk, extra={**rec.extra, "added_by_url": datetime.now(timezone.utc).isoformat(),
                                    "added_via": how})
    return pk, status, how


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="jobsearch.add_url", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url")
    p.add_argument("--company")
    p.add_argument("--title")
    p.add_argument("--location")
    p.add_argument("--track", choices=["coop_intern", "full_time"], help="override the detected track")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--jd-file", type=Path, help="text file with the job description (manual entry)")
    g.add_argument("--paste", action="store_true", help="read the job description from stdin (manual entry)")
    p.add_argument("--prefs", type=Path, default=paths.PREFERENCES)
    p.add_argument("--resume", type=Path, default=paths.RESUME)
    args = p.parse_args(argv)

    from .run import publish, setup_logging
    log_file = setup_logging(False)
    cfg = load_config(args.prefs)
    resume = parse_resume(args.resume, cfg.synonyms)
    jd = args.jd_file.read_text(encoding="utf-8") if args.jd_file else (sys.stdin.read() if args.paste else None)
    conn = db.connect(paths.DB)
    client = HttpClient()
    try:
        pk, status, how = add_job(conn, cfg, args.url, client, company=args.company, title=args.title,
                                  jd_text=jd, location=args.location, track=args.track)
    except ValueError as e:
        log.error("%s", e)
        return 2
    finally:
        client.close()
    summary = RunSummary()
    score_all(conn, cfg, resume, Embedder(conn), summary)
    conn.commit()
    row = db.decode_row(conn.execute("SELECT * FROM jobs WHERE id=?", (pk,)).fetchone())
    last = conn.execute("SELECT summary FROM runs WHERE summary IS NOT NULL AND dry_run=0 ORDER BY id DESC LIMIT 1"
                        ).fetchone()
    prev = json.loads(last[0]) if last else {}
    cov = (json.loads(open(paths.DASHBOARD / "data.js").read().split("window.META = ", 1)[1].split(";\n", 1)[0])
           .get("coverage", []) if (paths.DASHBOARD / "data.js").exists() else [])
    publish(conn, cfg, resume, prev, cov, load_watchlist(paths.ROOT / cfg.prefs.my_companies_csv), log_file)
    conn.close()
    state = "Filtered: " + "; ".join(row["filter_reasons"] or []) if row["filtered"] else \
        f"score {row['score']} ({row['label']})"
    print(f"{status}: {row['company']} — {row['title']} [{row['track_override'] or row['track']}] via {how}; {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
