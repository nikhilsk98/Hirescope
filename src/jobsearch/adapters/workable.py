"""Workable job boards (apply.workable.com/{slug}; the public widget API Workable provides for embedding a company's
job list on its own site; no auth; robots.txt allows everything).

Verified 2026-10-12 (Hugging Face: 6 jobs, Velsera: 11, Thrive: 48):
  GET https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true
    -> {"name", "description", "jobs": [{shortcode, title, employment_type, telecommuting, department, url,
        application_url, published_on, created_at, country, city, state, locations: [...], description (HTML)}]}
Every job (with its description) comes in one response; an unknown slug answers 404.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError
from .util import join_locations, parse_dt

API = "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"


def parse_url(url: str) -> tuple[str | None, str | None]:
    """apply.workable.com/{slug}[/j/{shortcode}] or {slug}.workable.com -> (slug, shortcode)."""
    u = urlparse(url if "://" in url else "https://" + url)
    host = (u.hostname or "").lower()
    segs = [p for p in u.path.split("/") if p]
    if host == "apply.workable.com" and segs and segs[0] not in ("api", "j"):
        code = segs[2] if len(segs) >= 3 and segs[1] == "j" else None
        return segs[0], code
    if host.endswith(".workable.com") and host not in ("www.workable.com", "apply.workable.com", "jobs.workable.com"):
        return host.split(".")[0], (segs[1] if len(segs) >= 2 and segs[0] == "j" else None)
    return None, None


def _location(j: dict) -> str | None:
    place = lambda x: ", ".join(p for p in (x.get("city"), x.get("region") or x.get("state"), x.get("country")) if p)  # noqa: E731
    loc = join_locations([place(x) for x in j.get("locations") or [] if isinstance(x, dict)]) or place(j) or None
    if str(j.get("telecommuting")).lower() == "true":
        return f"Remote - {j.get('country') or 'anywhere'}" + (f" | {loc}" if loc and loc != j.get("country") else "")
    return loc


class WorkableAdapter(Adapter):
    name = "workable"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=False, official=True)

    def _raw(self, company: str, slug: str, j: dict) -> RawJob:
        code = j["shortcode"]
        return RawJob(
            company=company,
            job_id=code,
            title=(j.get("title") or "").strip(),
            source=self.name,
            source_url=API.format(slug=slug),
            apply_url=j.get("url") or f"https://apply.workable.com/{slug}/j/{code}/",
            location=_location(j),
            description=html_to_text(j.get("description") or ""),
            posted_date=parse_dt(j.get("published_on") or j.get("created_at")),
            employment_type=j.get("employment_type") or None,
            canonical_key=f"workable:{code}",
            extra={"department": j.get("department") or None, "function": j.get("function") or None},
        )

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        if not company.board:
            raise SourceError(f"{company.name}: workable requires 'board' (the apply.workable.com slug)")
        url = API.format(slug=company.board)
        try:
            data = self.http.get_json(url)
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {url}: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError(f"{company.name}: unexpected response shape from {url}")
        jobs = [self._raw(company.name, company.board, j) for j in data["jobs"] if j.get("shortcode") and j.get("title")]
        notes = ["0 open jobs on the board right now"] if not jobs else []
        return FetchResult(jobs=jobs, notes=notes)

    def check_open(self, company: Company, job_id: str, apply_url: str | None) -> bool | None:
        try:
            data = self.http.get_json(API.format(slug=company.board))
        except httpx.HTTPStatusError as e:
            return False if e.response.status_code in (404, 410) else None
        return any(j.get("shortcode") == job_id for j in data.get("jobs") or [])

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        slug, code = parse_url(url)
        if not slug or not code:
            return None
        data = self.http.get_json(API.format(slug=slug))
        j = next((x for x in data.get("jobs") or [] if x.get("shortcode") == code), None)
        return self._raw(company or data.get("name") or slug, slug, j) if j else None
