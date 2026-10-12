"""Greenhouse Job Board API (public, no auth).

Verified 2026-10-12: GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
returns {"jobs": [...], "meta": {"total": N}} with every job in one response (no paging).
Each job has id, title, absolute_url, location.name, content (HTML-escaped HTML),
first_published, updated_at, company_name.
"""
from __future__ import annotations

from datetime import datetime

from dateutil import parser as dateparser

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, RawJob, SourceError

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


def _dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return dateparser.isoparse(value)
    except (ValueError, TypeError):
        return None


class GreenhouseAdapter(Adapter):
    name = "greenhouse"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=False, official=True)

    def fetch(self, company: Company, since: datetime | None = None) -> list[RawJob]:
        if not company.board:
            raise SourceError(f"{company.name}: greenhouse requires 'board' in companies.yaml")
        url = API.format(board=company.board)
        try:
            data = self.http.get_json(url)
        except Exception as e:  # noqa: BLE001 - surface any failure as a source error
            raise SourceError(f"{company.name}: {url}: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError(f"{company.name}: unexpected response shape from {url}")

        jobs = []
        for j in data["jobs"]:
            if not j.get("id") or not j.get("title"):
                continue
            # `since` is ignored: the full list is needed for closed-job detection.
            jobs.append(RawJob(
                company=company.name,
                job_id=str(j["id"]),
                title=j["title"].strip(),
                source=self.name,
                source_url=url,
                apply_url=j.get("absolute_url"),
                location=(j.get("location") or {}).get("name"),
                description=html_to_text(j.get("content") or ""),
                posted_date=_dt(j.get("first_published")),
                updated_at=_dt(j.get("updated_at")),
                extra={"departments": [d.get("name") for d in j.get("departments") or [] if d.get("name")]},
            ))
        return jobs
