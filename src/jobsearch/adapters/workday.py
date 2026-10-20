"""Workday career sites (public "cxs" JSON used by every *.myworkdayjobs.com page; no auth).

Verified 2026-10-12 (NVIDIA, Salesforce):
  POST https://{host}/wday/cxs/{tenant}/{site}/jobs
       {"appliedFacets": {...}, "limit": 20, "offset": N, "searchText": "..."}
    -> {"total", "jobPostings": [{title, externalPath, locationsText, postedOn, bulletFields}], "facets"}
  GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}
    -> {"jobPostingInfo": {jobDescription (HTML), location, additionalLocations, startDate, timeType,
        jobReqId, externalUrl, country}}
Limit is capped at 20 per page. We apply the site's own "United States" location facet when present
and search a few role keywords instead of listing every job (big tenants have 5,000+ postings).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError, SourceUnavailable
from .canonical import workday_key
from .util import join_locations, parse_dt

PAGE = 20
FULL_PAGES = 120          # full runs: up to 2,400 results per search (RTX lists ~2,050; Leidos, NVIDIA ~1,200)
STALE_PAGES = 3           # a keyword search stops after this many pages in a row with no relevant title
# Role searches come from config/title_rules.yaml -> search_queries.workday (why those phrases: see that file).
# Co-op/intern roles come from each site's own early-career facets.
_US = {"united states of america", "united states", "usa", "us"}
_EARLY = re.compile(r"\b(intern|interns|internship|co-?op|student|university|college|graduate|early career|campus|"
                    r"apprentice)", re.I)


def _posted_on(text: str | None, now: datetime) -> tuple[datetime | None, bool]:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago' -> (date, exact?)."""
    t = (text or "").lower()
    if "today" in t:
        return now, True
    if "yesterday" in t:
        return now - timedelta(days=1), True
    if m := re.search(r"(\d+)\+?\s*days? ago", t):
        return (now - timedelta(days=int(m.group(1))), "+" not in t)
    return None, False


def _early_career_facets(facets: list, parent: str | None = None) -> list[tuple[str, str, str]]:
    """(facetParameter, id, descriptor) for facet values like 'Intern (Fixed Term)', 'Co-op', 'Univ Employment',
    'New College Graduate' (job type / job family / worker sub-type facets; never location facets)."""
    out = []
    for f in facets or []:
        param = f.get("facetParameter") or parent
        if param and "location" in param.lower():
            continue
        for v in f.get("values") or []:
            if "facetParameter" in v:
                out += _early_career_facets([v], param)
            elif v.get("id") and _EARLY.search(v.get("descriptor") or ""):
                out.append((param, v["id"], v["descriptor"]))
    return out


def _find_us_facet(facets: list) -> tuple[str, str] | None:
    """Search (nested) facets for a value named United States; return (facetParameter, id)."""
    for f in facets or []:
        for v in f.get("values") or []:
            if "facetParameter" in v:
                if hit := _find_us_facet([v]):
                    return hit
            elif (v.get("descriptor") or "").strip().lower() in _US and v.get("id"):
                return f.get("facetParameter"), v["id"]
    return None


class WorkdayAdapter(Adapter):
    name = "workday"
    capabilities = Capabilities(full_description=False, posted_date=True, pagination=True, official=True,
                                query_based=True)

    @staticmethod
    def _base(company: Company) -> tuple[str, str, str]:
        if not (company.host and company.board and company.site):
            raise SourceError(f"{company.name}: workday requires host, board (tenant) and site")
        return company.host, company.board, company.site

    def _detail(self, host: str, tenant: str, site: str, path: str) -> dict:
        return self.http.get_json(f"https://{host}/wday/cxs/{tenant}/{site}{path}").get("jobPostingInfo") or {}

    def _raw(self, company: str, host: str, tenant: str, site: str, item: dict, info: dict | None,
             now: datetime) -> RawJob:
        path = item.get("externalPath") or ""
        slug = path.rstrip("/").split("/")[-1].lower()
        posted, exact = _posted_on(item.get("postedOn"), now)
        if info and info.get("startDate"):
            posted, exact = parse_dt(info["startDate"]), True
        loc = join_locations(info.get("location"), info.get("additionalLocations")) if info else None
        return RawJob(
            company=company,
            job_id=slug,
            title=(item.get("title") or (info or {}).get("title") or "").strip(),
            source=self.name,
            source_url=f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
            apply_url=(info or {}).get("externalUrl") or f"https://{host}/{site}{path}",
            location=loc or item.get("locationsText"),
            description=html_to_text(info.get("jobDescription") or "") if info else (None if info is None else ""),
            posted_date=posted if exact or info else None,
            employment_type=(info or {}).get("timeType"),
            canonical_key=workday_key(tenant, slug),
            extra={"req_id": (item.get("bulletFields") or [None])[0], "posted_on": item.get("postedOn")},
        )

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        host, tenant, site = self._base(company)
        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        now = datetime.now(timezone.utc)
        try:
            first = self.http.post_json(url, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
        except SourceUnavailable as e:
            raise SourceUnavailable(e.kind, f"{company.name}: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {url}: {e}") from e
        facet = _find_us_facet(first.get("facets") or [])
        applied = {facet[0]: [facet[1]]} if facet else {}
        # tier pages (preferences company_tiers.max_pages: 25 for tier 2), at least 10; keyword results are
        # relevance-ranked, so a search stops after STALE_PAGES pages in a row without one relevant title
        max_pages = FULL_PAGES if ctx.full else max(ctx.max_pages, 10)

        searches = [(q, applied) for q in ctx.queries.get("workday", [])]
        for param, fid, desc in _early_career_facets(first.get("facets") or []):
            searches.append((f"[{desc}]", {**applied, param: [fid]}))
        items: dict[str, dict] = {}
        capped, limited = [], None
        for q, facets in searches:
            offset, total, stale = 0, None, 0
            text = "" if q.startswith("[") else q
            for _ in range(max_pages):
                try:
                    page = self.http.post_json(url, {"appliedFacets": facets, "limit": PAGE, "offset": offset,
                                                     "searchText": text})
                except httpx.HTTPStatusError as e:
                    if e.response.status_code != 429 or not items:
                        raise
                    limited = q            # rate-limited after retries: keep what we have, ask again next run
                    break
                if total is None:
                    total = page.get("total") or 0
                posts = page.get("jobPostings") or []
                hit = False
                for p in posts:
                    if p.get("externalPath"):
                        items.setdefault(p["externalPath"], p)
                        hit = hit or ctx.want(p.get("title") or "", p.get("locationsText"))
                stale = 0 if hit or not text else stale + 1
                offset += PAGE
                if not posts or offset >= total or stale >= STALE_PAGES:
                    break
            else:
                capped.append(f"“{q}” capped at {max_pages * PAGE} of {total}")
            if limited:
                capped.append(f"rate-limited (HTTP 429) during “{limited}”; the remaining searches run next time")
                break

        jobs, details = [], 0
        for path, item in items.items():
            slug = path.rstrip("/").split("/")[-1].lower()
            # info None -> keep the stored JD; {} -> listing without a JD; dict -> fresh detail
            info: dict | None = None if slug in ctx.known else {}
            if ctx.want(item.get("title") or "", item.get("locationsText")) and ctx.needs_detail(slug, now) \
                    and details < ctx.max_details:
                try:
                    info = self._detail(host, tenant, site, path)
                    details += 1
                except Exception:  # noqa: BLE001 - keep the listing without a JD
                    pass
            jobs.append(self._raw(company.name, host, tenant, site, item, info, now))
        partial = "; ".join(capped) or None
        notes = [] if facet else ["no United States location facet found; searched all locations"]
        if details >= ctx.max_details:
            notes.append(f"detail fetches capped at {ctx.max_details}; remaining fetched next run")
        return FetchResult(jobs=jobs, partial=partial, notes=notes)

    def check_open(self, company: Company, job_id: str, apply_url: str | None) -> bool | None:
        u = urlparse(apply_url or "")
        m = re.match(r"^/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)(/job/.+)$", u.path)
        if not u.hostname or not m:
            return None
        try:
            info = self._detail(u.hostname, u.hostname.split(".")[0], m.group(1), m.group(2))
        except httpx.HTTPStatusError as e:
            return False if e.response.status_code in (404, 410) else None
        return bool(info.get("title")) if info.get("posted", True) else False

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        u = urlparse(url)
        m = re.match(r"^/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)(/job/.+)$", u.path)
        if not u.hostname or "myworkdayjobs.com" not in u.hostname or not m:
            return None
        host, tenant, site, path = u.hostname, u.hostname.split(".")[0], m.group(1), m.group(2)
        info = self._detail(host, tenant, site, path)
        return self._raw(company or tenant, host, tenant, site, {"externalPath": path, "title": info.get("title")},
                         info, datetime.now(timezone.utc))
