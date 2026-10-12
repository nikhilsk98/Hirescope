"""Greenhouse Job Board API (public, no auth).

Verified 2026-10-12:
  GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
    -> {"jobs": [...], "meta": {"total": N}}, every job in one response; supports ETag / 304.
  GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?content=true -> one job.
Each job has id, title, absolute_url, location.name, content (HTML-escaped HTML), first_published,
updated_at, application_deadline, company_name.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, NotModified, RawJob, SourceError
from .util import parse_dt

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
ONE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?content=true"


class GreenhouseAdapter(Adapter):
    name = "greenhouse"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=False, official=True,
                                conditional=True)

    def _job(self, company: str, j: dict, source_url: str) -> RawJob:
        return RawJob(
            company=company,
            job_id=str(j["id"]),
            title=j["title"].strip(),
            source=self.name,
            source_url=source_url,
            apply_url=j.get("absolute_url"),
            location=(j.get("location") or {}).get("name"),
            description=html_to_text(j.get("content") or ""),
            posted_date=parse_dt(j.get("first_published")),
            updated_at=parse_dt(j.get("updated_at")),
            canonical_key=f"greenhouse:{j['id']}",
            extra={"departments": [d.get("name") for d in j.get("departments") or [] if d.get("name")],
                   **({"deadline": j["application_deadline"]} if j.get("application_deadline") else {})},
        )

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        if not company.board:
            raise SourceError(f"{company.name}: greenhouse requires 'board'")
        url = API.format(board=company.board)
        try:
            resp = self.http.get_conditional(url, ctx.validators.get(url))
        except NotModified:
            return FetchResult(unchanged=True)
        except Exception as e:  # noqa: BLE001 - surface any failure as a source error
            raise SourceError(f"{company.name}: {url}: {e}") from e
        data = resp.json()
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError(f"{company.name}: unexpected response shape from {url}")
        # `since` is ignored: the full list is needed for closed-job detection.
        jobs = [self._job(company.name, j, url) for j in data["jobs"] if j.get("id") and j.get("title")]
        return FetchResult(jobs=jobs, validators={url: self.http.validators_of(resp)})

    @staticmethod
    def parse_url(url: str) -> tuple[str | None, str | None]:
        u = urlparse(url)
        qs = parse_qs(u.query)
        if m := re.search(r"greenhouse\.io/(?:embed/job_app)?([A-Za-z0-9_-]+)?/?(?:jobs/(\d+))?", url):
            board = qs.get("for", [m.group(1)])[0]
            jid = m.group(2) or qs.get("token", [None])[0] or qs.get("gh_jid", [None])[0]
            return board, jid
        return None, qs.get("gh_jid", [None])[0]

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        board, jid = self.parse_url(url)
        if not board or not jid:
            return None
        j = self.http.get_json(ONE.format(board=board, id=jid))
        return self._job(company or j.get("company_name") or board, j, ONE.format(board=board, id=jid))
