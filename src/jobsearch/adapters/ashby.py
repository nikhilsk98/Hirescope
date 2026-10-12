"""Ashby Job Board API (public, no auth).

Verified 2026-10-12:
  GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true
    -> {"apiVersion", "jobs": [...]} (all listed jobs; ETag)
Job: id, title, location, secondaryLocations[{location}], isRemote, workplaceType, employmentType
(FullTime | Intern | PartTime | Contract | Temporary), publishedAt, jobUrl, applyUrl, descriptionHtml,
isListed, compensation{compensationTierSummary}.
"""
from __future__ import annotations

import re

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, NotModified, RawJob, SourceError
from .util import join_locations, parse_dt

API = "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"


class AshbyAdapter(Adapter):
    name = "ashby"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=False, official=True,
                                conditional=True)

    def _job(self, company: str, j: dict, source_url: str) -> RawJob:
        locs = [j.get("location"), *[s.get("location") for s in j.get("secondaryLocations") or []]]
        loc = join_locations(locs)
        if j.get("isRemote") and loc and "remote" not in loc.lower():
            loc = f"{loc} | Remote"
        desc = html_to_text(j.get("descriptionHtml") or "") or (j.get("descriptionPlain") or "")
        comp = (j.get("compensation") or {}).get("compensationTierSummary")
        if comp and "$" in comp and comp not in desc:
            desc += f"\n\nCompensation: {comp}"
        return RawJob(
            company=company,
            job_id=j["id"],
            title=(j.get("title") or "").strip(),
            source=self.name,
            source_url=source_url,
            apply_url=j.get("jobUrl") or j.get("applyUrl"),
            location=loc,
            description=desc,
            posted_date=parse_dt(j.get("publishedAt")),
            employment_type=j.get("employmentType"),
            canonical_key=f"ashby:{j['id']}",
            extra={"workplace_type": j.get("workplaceType"), "department": j.get("department"),
                   "team": j.get("team")},
        )

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        if not company.board:
            raise SourceError(f"{company.name}: ashby requires 'board'")
        url = API.format(board=company.board)
        try:
            resp = self.http.get_conditional(url, ctx.validators.get(url))
        except NotModified:
            return FetchResult(unchanged=True)
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {url}: {e}") from e
        data = resp.json()
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError(f"{company.name}: unexpected response shape from {url}")
        jobs = [self._job(company.name, j, url) for j in data["jobs"]
                if j.get("id") and j.get("title") and j.get("isListed", True)]
        return FetchResult(jobs=jobs, validators={url: self.http.validators_of(resp)})

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", url)
        if not m:
            return None
        api = API.format(board=m.group(1))
        for j in self.http.get_json(api).get("jobs", []):
            if j.get("id") == m.group(2):
                return self._job(company or m.group(1), j, api)
        return None
