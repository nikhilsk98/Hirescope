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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import paths
from .adapters.base import HttpClient, RawJob
from .adapters.canonical import canonical_key
from .adapters.detect import from_url
from .adapters.jsonld import HTML_ACCEPT, CareersPageAdapter, jobpostings_in_html, posting_to_raw
from .adapters.registry import ADAPTERS
from .config import load_config
from .extract.resume import parse_resume
from .match.semantic import Embedder
from .pipeline import RunSummary, score_all, to_record
from .storage import db
from .watchlist import load_watchlist
from .watchlist import resolve as resolve_watchlist

log = logging.getLogger("jobsearch.add_url")
SOURCE = "added_url"


from .adapters.util import NO_FETCH_HOSTS, no_fetch  # noqa: F401,E402  (sites never fetched; re-exported)


class UnreadableURL(ValueError):
    """The posting page can't be read automatically; `reason` says why (blocked, needs JavaScript, gone, …). Paste
    the JD instead."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"Could not read {url} automatically ({reason}). Paste the job description instead "
                         "(--jd-file FILE or --paste, plus --company and --title).")
        self.url, self.reason = url, reason


def _why_not(e: Exception) -> str:
    """A short, honest reason a page could not be read."""
    from .adapters.base import SourceUnavailable
    if isinstance(e, SourceUnavailable):
        return f"blocked: {e}" if e.kind == "blocked" else str(e)
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in (401, 403, 429):
            return f"blocked: the site refused automated reading (HTTP {code})"
        if code in (404, 410):
            return f"the posting is gone or the link is wrong (HTTP {code})"
        return f"the site answered HTTP {code}"
    if isinstance(e, httpx.TransportError):
        return f"couldn't reach the site ({type(e).__name__})"
    if "robots.txt" in str(e):
        return "the site's robots.txt doesn't allow reading it"
    return f"{type(e).__name__}: {str(e)[:120]}"


def _page_reason(resp) -> str:
    """Why a page that loaded has no readable posting: a bot check, a JavaScript-only page, or no job data."""
    body = resp.text or ""
    text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<[^>]+>", " ", body, flags=re.S | re.I)
    words = len(text.split())
    if resp.status_code == 202 or not body.strip():
        return f"blocked: the site answered with an empty bot-check page (HTTP {resp.status_code})"
    if re.search(r"captcha|are you a robot|verify you are human|challenge-platform|perimeterx|akamai bot", body, re.I):
        return "blocked: the site shows a bot check (not bypassed)"
    if words < 150 and len(re.findall(r"<script\b", body, re.I)) >= 2:
        return "the page builds itself with JavaScript; its HTML has no job data"
    return "no job data (schema.org JobPosting) on the page"


def resolve(url: str, client: HttpClient, company: str | None) -> tuple[RawJob | None, str]:
    """(the posting, how it was read) or (None, why it couldn't be read)."""
    if no_fetch(url):
        return None, "not read: this site's terms don't allow automated access, so paste the JD"
    fields = from_url(url) or {}
    adapter_cls = ADAPTERS.get(fields.get("source", ""))
    adapter_err = None
    if adapter_cls is not None:
        try:
            raw = adapter_cls(client).fetch_one(url, company=company)
            if raw:
                return raw, f"{adapter_cls.name} API"
        except Exception as e:  # noqa: BLE001
            log.info("%s adapter could not read %s: %s", adapter_cls.name, url, e)
            adapter_err = e
    try:
        if not client.allowed(url):
            return None, "the site's robots.txt doesn't allow reading it"
        resp = client.get(url, headers=HTML_ACCEPT)
        found = jobpostings_in_html(resp.text)
        if found:
            return posting_to_raw(company or "", found[0], url, CareersPageAdapter.name), "JSON-LD on the page"
        why = _page_reason(resp)
    except Exception as e:  # noqa: BLE001
        log.info("JSON-LD read failed for %s: %s", url, e)
        why = _why_not(e)
    if adapter_err is not None and not why.startswith("blocked"):
        why = f"{adapter_cls.name}: {_why_not(adapter_err)}; page: {why}"
    return None, why


def add_job(conn, cfg, url: str, client: HttpClient, *, company: str | None = None, title: str | None = None,
            jd_text: str | None = None, location: str | None = None, track: str | None = None,
            jd_dir: Path | None = paths.JD_DIR, resolved: tuple[RawJob | None, str] | None = None
            ) -> tuple[int, str, str]:
    """Returns (job pk, status new|updated|seen, how it was read). Raises UnreadableURL when the page can't be read
    and no JD was given. `resolved`: resolve()'s answer when the caller already read the page."""
    raw, how = (None, "manual") if jd_text else (resolved or resolve(url, client, company))
    if raw is None:
        if not jd_text:
            raise UnreadableURL(url, how)
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


def rescore_and_publish(conn, cfg, resume, log_file: Path) -> None:
    """Score stored jobs and rewrite the dashboard, keeping the last run's summary and coverage in the header."""
    from .run import publish
    score_all(conn, cfg, resume, Embedder(conn), RunSummary())
    conn.commit()
    last = conn.execute("SELECT summary FROM runs WHERE summary IS NOT NULL AND dry_run=0 ORDER BY id DESC LIMIT 1"
                        ).fetchone()
    prev = json.loads(last[0]) if last else {}
    data_js = paths.DASHBOARD / "data.js"
    cov = (json.loads(data_js.read_text(encoding="utf-8").split("window.META = ", 1)[1].split(";\n", 1)[0])
           .get("coverage", []) if data_js.exists() else [])
    publish(conn, cfg, resume, prev, cov, resolve_watchlist(load_watchlist(paths.ROOT / cfg.prefs.my_companies_csv),
                                                 paths.CONFIG / "company_aliases.yaml"), log_file)
    conn.commit()


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

    from .run import setup_logging
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
    rescore_and_publish(conn, cfg, resume, log_file)
    row = db.decode_row(conn.execute("SELECT * FROM jobs WHERE id=?", (pk,)).fetchone())
    from .external_resume import queue_resume
    q = queue_resume(conn, pk, origin="external")   # the job you added gets its resume (same queue and build)
    conn.close()
    state = "Filtered: " + "; ".join(row["filter_reasons"] or []) if row["filtered"] else \
        f"score {row['score']} ({row['label']})"
    print(f"{status}: {row['company']} — {row['title']} [{row['track_override'] or row['track']}] via {how}; {state}")
    print(f"resume: {'queued (builds in the background)' if q['status'] == 'queued' else q['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
