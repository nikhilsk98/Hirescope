"""SimplifyJobs GitHub lists (internships incl. off-season, and new grad). Aggregator, not official.

Verified 2026-10-12 (repo names change by year; URLs live in config/companies.yaml):
  https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json
    (16,992 entries, 4,643 active; `terms` include Spring/Winter/Fall 2027 off-season roles)
  https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json
    (19,484 entries, 3,116 active)
Entry: id, company_name, title, url (usually the employer's ATS posting), locations[], date_posted /
date_updated (epoch s), active, is_visible, terms[], sponsorship, degrees[], category.

Listings carry no JD. For relevant listings not seen before, the JD is pulled from the linked employer
ATS (Greenhouse / Lever / Ashby / Workday / SmartRecruiters) via that adapter's fetch_one, capped per run.
The listing stays a SimplifyJobs record (so it closes when Simplify drops it); an official adapter
that later lists the same posting (same canonical key) takes the record over.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..config import Company
from ..extract.track import COOP, FULL_TIME
from .base import Adapter, Capabilities, FetchContext, FetchResult, NotModified, RawJob, SourceError
from .canonical import canonical_key
from .detect import from_url
from .util import parse_dt, unreadable

log = logging.getLogger(__name__)
# employer ATSs whose adapter can read one posting by URL (fetch_one); any other employer page is tried for schema.org
# JobPosting data (robots.txt respected), e.g. Jibe sites like careers.amd.com
ENRICH_SOURCES = {"greenhouse", "lever", "ashby", "workday", "smartrecruiters", "rippling", "workable", "eightfold",
                  "jobvite", "oracle_cloud", "bamboohr", "jazzhr", "paylocity"}


def strip_tracking(url: str | None) -> str | None:
    if not url:
        return url
    u = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in ("ref", "source", "src", "gh_src")]
    return urlunparse(u._replace(query=urlencode(q)))


def _edited_since(raw: RawJob, detail_at: str | None) -> bool:
    """Simplify changed the listing after its JD was last fetched (e.g. its terms: Summer 2026 → N/A)."""
    if not (raw.updated_at and detail_at):
        return False
    try:
        return raw.updated_at > datetime.fromisoformat(detail_at)
    except ValueError:
        return False


class SimplifyAdapter(Adapter):
    """company.board = listings.json URL; company.site = 'coop_intern' | 'full_time' (feed's track)."""
    name = "simplify"
    capabilities = Capabilities(full_description=False, posted_date=True, pagination=False, official=False,
                                conditional=True, multi_company=True)

    def source_label(self, company: Company) -> str:
        return {COOP: "simplify_internships", FULL_TIME: "simplify_newgrad"}.get(company.site or "", "simplify")

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        url = company.board
        if not url:
            raise SourceError(f"{company.name}: simplify requires 'board' (listings.json URL)")
        hint = company.site if company.site in (COOP, FULL_TIME) else None
        source = self.source_label(company)
        try:
            resp = self.http.get_conditional(url, ctx.validators.get(url), headers={"Accept": "application/json"})
        except NotModified:
            return FetchResult(unchanged=True)
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {url}: {e}") from e
        data = resp.json()
        if not isinstance(data, list):
            raise SourceError(f"{company.name}: unexpected response shape from {url}")

        from .registry import ADAPTERS  # local import: registry imports this module
        enrichers = {n: ADAPTERS[n](self.http) for n in (*ENRICH_SOURCES, "jsonld") if n in ADAPTERS}
        jobs, to_enrich = [], []
        for x in data:
            if not (x.get("active") and x.get("is_visible", True) and x.get("id") and x.get("title")):
                continue
            link = strip_tracking(x.get("url"))
            locs = x.get("locations") or []
            raw = RawJob(
                company=(x.get("company_name") or "").strip() or "Unknown",
                job_id=str(x["id"]),
                title=x["title"].strip(),
                source=source,
                source_url=url,
                apply_url=link,
                location=" | ".join(locs) if locs else None,
                description=None if x["id"] in ctx.known else "",     # None: keep the stored JD
                posted_date=parse_dt(x.get("date_posted")),
                updated_at=parse_dt(x.get("date_updated")),
                canonical_key=canonical_key(link),
                track_hint=hint,
                terms=[t for t in (x.get("terms") or []) if t and t != "N/A"],
                extra={"simplify_sponsorship": x.get("sponsorship"), "simplify_category": x.get("category"),
                       "degrees": x.get("degrees")},
            )
            ats = (raw.canonical_key or "").split(":")[0]
            board = (from_url(link) or {}).get("board", "")
            # a JD is wanted for a new listing, a known one never enriched (a capped run left it), one older than the
            # refresh window, or one Simplify edited since (terms, title): re-read and re-stored whole
            want_jd = raw.description == "" or ctx.needs_detail(raw.job_id) or _edited_since(raw, ctx.known.get(raw.job_id))
            if (ats, board.casefold()) in ctx.covered:
                pass  # the employer's own board is fetched this run; that adapter supplies the JD
            elif want_jd and link and ctx.wants(raw.job_id, raw.title, raw.location) and not unreadable(link):
                to_enrich.append((raw, ats if ats in enrichers else "jsonld"))
            jobs.append(raw)
        # never-enriched first, then newest; capped per run; the rest are enriched on later runs
        to_enrich.sort(key=lambda t: (bool(ctx.known.get(t[0].job_id)),
                                      -(t[0].posted_date.timestamp() if t[0].posted_date else 0)))
        capped = len(to_enrich) > ctx.max_details
        to_enrich = to_enrich[:ctx.max_details]

        def enrich(item):
            raw, ats = item
            try:
                return raw, enrichers[ats].fetch_one(raw.apply_url, company=raw.company)
            except Exception as e:  # noqa: BLE001 - keep the listing without a JD
                log.debug("simplify enrich failed %s: %s", raw.apply_url, e)
                return raw, None
        enriched = failed = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            for raw, full in pool.map(enrich, to_enrich):
                if full is None:
                    failed += 1
                    continue
                enriched += 1
                raw.description = full.description or ""
                raw.terms = list(dict.fromkeys([*raw.terms, *(full.terms or [])]))
                raw.location = full.location or raw.location
                raw.posted_date = full.posted_date or raw.posted_date
                raw.employment_type = full.employment_type
                raw.extra.update({k: v for k, v in full.extra.items() if k == "deadline"})
        notes = [f"JD pulled from employer ATS for {enriched} listings" + (f" ({failed} failed)" if failed else "")]
        partial = None
        if capped:
            notes.append(f"JD enrichment capped at {ctx.max_details} newest listings; the rest are enriched on later runs")
        from .detect import directory_from_simplify  # noqa: F811
        return FetchResult(jobs=jobs, validators={url: self.http.validators_of(resp)}, notes=notes, partial=partial,
                           directory=directory_from_simplify([x for x in data if x.get("active")]))
