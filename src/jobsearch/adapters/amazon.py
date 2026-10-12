"""amazon.jobs public search JSON (the endpoint the amazon.jobs search page itself calls). Best effort.

Verified 2026-10-12:
  GET https://www.amazon.jobs/en/search.json?category[]=software-development&country=USA
      &result_limit=100&offset=N&sort=recent
    -> {"hits": N, "jobs": [{id_icims, title, job_path, location, normalized_location, posted_date
        ("October 10, 2026"), description, basic_qualifications, preferred_qualifications,
        job_schedule_type, ...}]}
robots.txt only disallows /internal paths. Interns are listed with job_schedule_type "full-time",
so the track comes from the title, not the schedule type.
"""
from __future__ import annotations

from urllib.parse import urlencode

import httpx

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError
from .util import parse_dt

BASE = "https://www.amazon.jobs/en/search.json"
PAGE = 100
SEARCHES = [
    {"category[]": "software-development"},
    {"base_query": "intern"},
    {"base_query": "co-op"},
    {"base_query": "new grad"},
]


def _description(j: dict) -> str:
    html = (f"<div>{j.get('description') or ''}</div>"
            f"<h3>Basic Qualifications</h3><div>{j.get('basic_qualifications') or ''}</div>"
            f"<h3>Preferred Qualifications</h3><div>{j.get('preferred_qualifications') or ''}</div>")
    return html_to_text(html)


def _job_page(apply_url: str | None) -> str | None:
    return apply_url if apply_url and "amazon.jobs" in apply_url else None


class AmazonAdapter(Adapter):
    name = "amazon"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=True, official=True,
                                query_based=True)

    def _raw(self, company: str, j: dict, source_url: str) -> RawJob:
        jid = str(j.get("id_icims") or j.get("id"))
        page = f"https://www.amazon.jobs{j['job_path']}" if j.get("job_path") else None
        return RawJob(
            company=company,
            job_id=jid,
            title=(j.get("title") or "").strip(),
            source=self.name,
            source_url=source_url,
            apply_url=page or j.get("url_next_step"),
            location=j.get("normalized_location") or j.get("location"),
            description=_description(j),
            posted_date=parse_dt(j.get("posted_date")),
            employment_type=None,  # job_schedule_type says full-time even for interns
            canonical_key=f"amazon:{jid}",
            extra={"business_category": j.get("business_category"), "job_category": j.get("job_category"),
                   "team": (j.get("team") or {}).get("label") if isinstance(j.get("team"), dict) else None},
        )

    def check_open(self, company: Company, job_id: str, apply_url: str | None) -> bool | None:
        url = _job_page(apply_url)
        if not url:
            return None
        try:
            r = self.http.get(url, headers={"Accept": "text/html"})
        except httpx.HTTPStatusError as e:
            return False if e.response.status_code in (404, 410) else None
        return "/jobs/" in str(r.url)   # closed postings redirect to the search page

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        max_pages = 40 if ctx.full else ctx.max_pages
        jobs: dict[str, RawJob] = {}
        capped = []
        for search in SEARCHES:
            offset, hits = 0, None
            for _ in range(max_pages):
                params = {**search, "country": "USA", "result_limit": PAGE, "offset": offset, "sort": "recent"}
                url = f"{BASE}?{urlencode(params)}"
                try:
                    data = self.http.get_json(url, headers={"Accept": "application/json"})
                except Exception as e:  # noqa: BLE001
                    raise SourceError(f"{company.name}: {url}: {e}") from e
                if data.get("error"):
                    raise SourceError(f"{company.name}: {url}: {data['error']}")
                hits = data.get("hits", 0) if hits is None else hits
                for j in data.get("jobs") or []:
                    if j.get("title") and (j.get("id_icims") or j.get("id")):
                        r = self._raw(company.name, j, f"{BASE}?{urlencode(search)}")
                        jobs.setdefault(r.job_id, r)
                offset += PAGE
                if offset >= hits or not data.get("jobs"):
                    break
            else:
                capped.append(f"{search} capped at {max_pages * PAGE} of {hits}")
        return FetchResult(jobs=list(jobs.values()), partial="; ".join(capped) or None)
