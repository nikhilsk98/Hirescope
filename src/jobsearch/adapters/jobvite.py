"""Jobvite-hosted career sites (jobs.jobvite.com/{slug}; public pages, no auth, robots.txt respected).

Jobvite's JSON API needs an employer API key, so this reads the employer's own hosted career site:
Verified 2026-10-12 (Imprivata: 41 jobs; Nutanix: 270 jobs over 6 pages):
  GET https://jobs.jobvite.com/{slug}/search?nl=1&p={N}   -> 50 rows per page of
      <a href="/{slug}/job/{id}">title</a> + a "...location" element; "jv-pagination-next" while more remain
  GET https://jobs.jobvite.com/{slug}/job/{id}?nl=1       -> schema.org JobPosting JSON-LD (full description,
      datePosted, employmentType, jobLocation, identifier = id)
`nl=1` keeps Jobvite from redirecting to the employer's iframe page. Some themes list every job on
/{slug}/jobs instead of /search, so that page is the fallback when search shows nothing.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlparse

import httpx

from ..config import Company
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError
from .jsonld import HTML_ACCEPT, jobpostings_in_html, posting_to_raw

BASE = "https://jobs.jobvite.com"
MAX_PAGES = 40


def _text(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html or "")).split()).replace(" ,", ",")


def parse_listing(html: str, slug: str) -> dict[str, tuple[str, str | None]]:
    """job id -> (title, location) from a Jobvite listing page (table rows or theme cards)."""
    out: dict[str, tuple[str, str | None]] = {}
    links = list(re.finditer(rf'<a[^>]+href="/{re.escape(slug)}/job/([A-Za-z0-9]+)[^"]*"[^>]*>(.*?)</a>',
                             html or "", re.S | re.I))
    for i, m in enumerate(links):
        inner, loc = m.group(2), None
        if lm := re.search(r'<(div|span)[^>]*class="[^"]*location[^"]*"[^>]*>(.*?)</\1>', inner, re.S | re.I):
            loc, inner = lm.group(2), inner.replace(lm.group(0), "")
        else:   # location cell/div after the link, before the next job link
            tail = html[m.end(): links[i + 1].start() if i + 1 < len(links) else m.end() + 2000]
            if lm := re.search(r'<(td|div|span)[^>]*class="[^"]*location[^"]*"[^>]*>(.*?)</\1>', tail, re.S | re.I):
                loc = lm.group(2)
        title = _text(inner)
        if title:
            out.setdefault(m.group(1), (title, _text(loc) or None if loc else None))
    return out


class JobviteAdapter(Adapter):
    name = "jobvite"
    capabilities = Capabilities(full_description=False, posted_date=True, pagination=True, official=True)

    def _page(self, url: str) -> str:
        if not self.http.allowed(url):
            raise SourceError(f"robots.txt disallows {url}")
        return self.http.get(url, headers=HTML_ACCEPT).text

    def _detail(self, slug: str, jid: str) -> dict | None:
        found = jobpostings_in_html(self._page(f"{BASE}/{slug}/job/{jid}?nl=1"))
        return found[0] if found else None

    def _raw(self, company: str, slug: str, jid: str, title: str, loc: str | None, posting: dict | None) -> RawJob:
        url = f"{BASE}/{slug}/job/{jid}"
        if posting:
            raw = posting_to_raw(company, posting, url, self.name)
            raw.title = raw.title or title
            raw.location = raw.location or loc
        else:
            raw = RawJob(company=company, job_id=jid, title=title, source=self.name, source_url=url, location=loc,
                         description=None if posting is None else "")
        raw.job_id, raw.apply_url, raw.canonical_key = jid, url, f"jobvite:{slug}:{jid}"
        return raw

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        slug = company.board
        if not slug:
            raise SourceError(f"{company.name}: jobvite requires 'board' (the jobs.jobvite.com slug)")
        listing: dict[str, tuple[str, str | None]] = {}
        partial = None
        try:
            for p in range(MAX_PAGES):
                html = self._page(f"{BASE}/{slug}/search?nl=1&p={p}")
                rows = parse_listing(html, slug)
                new = {k: v for k, v in rows.items() if k not in listing}
                listing.update(new)
                if not new or "jv-pagination-next" not in html:
                    break
            else:
                partial = f"listing capped at {MAX_PAGES} pages"
            if not listing:
                listing = parse_listing(self._page(f"{BASE}/{slug}/jobs?nl=1"), slug)
        except SourceError as e:
            raise SourceError(f"{company.name}: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {BASE}/{slug}: {e}") from e
        if not listing:
            raise SourceError(f"{company.name}: no jobs listed on {BASE}/{slug}/search or /jobs")

        now = datetime.now(timezone.utc)
        jobs, details = [], 0
        for jid, (title, loc) in listing.items():
            # posting None -> keep the stored JD; {} -> listing without a JD; dict -> fresh detail
            posting: dict | None = None if jid in ctx.known else {}
            if ctx.want(title, loc) and ctx.needs_detail(jid, now) and details < ctx.max_details:
                try:
                    posting = self._detail(slug, jid) or {}
                    details += 1
                except Exception:  # noqa: BLE001 - keep the listing without a JD
                    pass
            jobs.append(self._raw(company.name, slug, jid, title, loc, posting))
        notes = [f"detail fetches capped at {ctx.max_details}; remaining fetched next run"] \
            if details >= ctx.max_details else []
        return FetchResult(jobs=jobs, partial=partial, notes=notes)

    def check_open(self, company: Company, job_id: str, apply_url: str | None) -> bool | None:
        try:
            return self._detail(company.board, job_id) is not None
        except httpx.HTTPStatusError as e:
            return False if e.response.status_code in (404, 410) else None

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        u = urlparse(url)
        m = re.match(r"^/(?:careers/)?([^/]+)/job/([A-Za-z0-9]+)", u.path)
        if (u.hostname or "").lower() != "jobs.jobvite.com" or not m:
            return None
        posting = self._detail(m.group(1), m.group(2))
        if not posting:
            return None
        org = posting.get("hiringOrganization")
        name = company or (org.get("name") if isinstance(org, dict) else org) or m.group(1)
        return self._raw(name, m.group(1), m.group(2), posting.get("title") or "", None, posting)
