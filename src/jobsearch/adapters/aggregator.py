"""Optional job-search aggregator (off by default): JSearch (RapidAPI) or SerpAPI's Google Jobs.

Turn on with `aggregator.enabled: true` in preferences plus JSEARCH_API_KEY or SERPAPI_API_KEY in .env. Sends only
your search queries (e.g. "software engineer new grad"), the country and a recency window to the provider; never
resume content. Results are aggregator listings (not official); an employer ATS listing of the same posting takes
over. Results come from searches, so a job missing from one run's results is not treated as closed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from .. import secrets
from ..config import Company
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError, safe_url
from .canonical import canonical_key
from .simplify_github import strip_tracking
from .util import parse_dt

PROVIDERS = {"jsearch": "JSEARCH_API_KEY", "serpapi": "SERPAPI_API_KEY"}


class AggregatorAdapter(Adapter):
    """company.board = provider (jsearch | serpapi); company.queries = search queries."""
    name = "aggregator"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=False, official=False,
                                multi_company=True, query_based=True)
    enabled = False            # preferences.aggregator.enabled (set by run.py)
    date_window = "week"

    @classmethod
    def unavailable(cls, company: Company) -> str | None:
        if not cls.enabled:
            return "off (aggregator.enabled: false in preferences)"
        key = PROVIDERS.get(company.board or "")
        if not key:
            return f"unknown provider {company.board!r} (use jsearch or serpapi)"
        return None if secrets.get(key) else f"not set up: add {key} to .env"

    def source_label(self, company: Company) -> str:
        return f"aggregator_{company.board}"

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        provider = company.board or ""
        key = secrets.get(PROVIDERS.get(provider, ""))
        if not key:
            raise SourceError(f"{company.name}: no API key for {provider}")
        jobs: dict[str, RawJob] = {}
        errors = []
        for q in company.queries or ["software engineer new grad"]:
            try:
                rows = self._jsearch(q, key) if provider == "jsearch" else self._serpapi(q, key)
            except Exception as e:  # noqa: BLE001 - one query failing doesn't sink the rest
                errors.append(f"{q!r}: {safe_url(secrets.redact(str(e), PROVIDERS[provider]))}")
                continue
            for r in rows:
                r.source = self.source_label(company)
                jobs.setdefault(r.job_id, r)
        if errors and not jobs:
            raise SourceError(f"{company.name}: " + "; ".join(errors))
        # search results: anything known but not returned this time stays (never closed on a guess)
        # search results: a known job not returned this time stays for 30 days after it was last returned
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        keep = {jid for jid, ts in ctx.known.items() if ts and ts >= cutoff}
        return FetchResult(jobs=list(jobs.values()), notes=[f"{len(jobs)} results from {len(company.queries or [1])} "
                                                            f"queries"] + errors, protected=keep)

    def _jsearch(self, query: str, key: str) -> list[RawJob]:
        url = "https://jsearch.p.rapidapi.com/search?" + urlencode(
            {"query": query, "page": 1, "num_pages": 1, "country": "us", "date_posted": self.date_window})
        data = self.http.get_json(url, headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"})
        out = []
        for x in (data or {}).get("data") or []:
            if not (x.get("job_id") and x.get("job_title") and x.get("employer_name")):
                continue
            link = strip_tracking(x.get("job_apply_link"))
            loc = ", ".join(v for v in (x.get("job_city"), x.get("job_state"), x.get("job_country")) if v)
            if x.get("job_is_remote"):
                loc = f"{loc} · Remote" if loc else "Remote"
            out.append(RawJob(company=x["employer_name"].strip(), job_id=str(x["job_id"]), title=x["job_title"].strip(),
                              source="aggregator_jsearch", source_url="https://jsearch.p.rapidapi.com/search",
                              apply_url=link, location=loc or None, description=x.get("job_description") or "",
                              posted_date=parse_dt(x.get("job_posted_at_datetime_utc")),
                              employment_type=x.get("job_employment_type"), canonical_key=canonical_key(link),
                              extra={"via": f"JSearch ({x.get('job_publisher') or 'aggregator'})"}))
        return out

    def _serpapi(self, query: str, key: str) -> list[RawJob]:
        url = "https://serpapi.com/search.json?" + urlencode(
            {"engine": "google_jobs", "q": query, "location": "United States", "api_key": key})
        data = self.http.get_json(url)
        out = []
        now = datetime.now(timezone.utc)
        for x in (data or {}).get("jobs_results") or []:
            if not (x.get("job_id") and x.get("title") and x.get("company_name")):
                continue
            link = strip_tracking(next((o.get("link") for o in x.get("apply_options") or [] if o.get("link")), None))
            ext = x.get("detected_extensions") or {}
            posted = None
            if (ago := str(ext.get("posted_at") or "")) and ago[:1].isdigit():
                n = int("".join(ch for ch in ago if ch.isdigit()) or 0)
                unit = "hour" if "hour" in ago else "day" if "day" in ago else None
                posted = now - timedelta(hours=n) if unit == "hour" else now - timedelta(days=n) if unit else None
            out.append(RawJob(company=x["company_name"].strip(), job_id=str(x["job_id"])[:200], title=x["title"].strip(),
                              source="aggregator_serpapi", source_url="https://serpapi.com/search.json",
                              apply_url=link, location=x.get("location"), description=x.get("description") or "",
                              posted_date=posted, employment_type=ext.get("schedule_type"),
                              canonical_key=canonical_key(link), extra={"via": f"Google Jobs ({x.get('via') or ''})"}))
        return out
