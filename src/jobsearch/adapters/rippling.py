"""Rippling ATS job boards (public board API behind every ats.rippling.com/{slug}/jobs page; no auth).

Verified 2026-10-12 (Tive: 29 jobs; Rippling: 608 jobs, all in one response):
  GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs
    -> [{uuid, name, department: {label}, url, workLocation: {label}}]     (unknown slug -> 404)
  GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}
    -> {uuid, name, description: {company, role} (HTML), workLocations: [..], employmentType: {label, id},
        createdOn (ISO), url, companyName}
The list has no descriptions, so relevant jobs get one detail request each (bounded by ctx.max_details).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError
from .util import join_locations, parse_dt

API = "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def parse_url(url: str) -> tuple[str | None, str | None]:
    """ats.rippling.com/[{locale}/]{slug}/jobs[/{uuid}] -> (slug, uuid)."""
    u = urlparse(url)
    if (u.hostname or "").lower() != "ats.rippling.com":
        return None, None
    segs = [p for p in u.path.split("/") if p]
    if segs and re.fullmatch(r"[a-z]{2}(-[A-Z]{2})?", segs[0]):
        segs = segs[1:]
    if not segs or segs[0] == "jobs":
        return None, None
    jid = next((s for s in segs[1:] if re.fullmatch(_UUID, s)), None)
    return segs[0], jid


class RipplingAdapter(Adapter):
    name = "rippling"
    capabilities = Capabilities(full_description=False, posted_date=True, pagination=False, official=True)

    def _detail(self, slug: str, uuid: str) -> dict:
        return self.http.get_json(f"{API.format(slug=slug)}/{uuid}")

    def _raw(self, company: str, slug: str, item: dict, info: dict | None) -> RawJob:
        desc = (info or {}).get("description")
        if isinstance(desc, dict):
            desc = "\n".join(desc.get(k) or "" for k in ("role", "company"))
        locs = (info or {}).get("workLocations") or [(item.get("workLocation") or {}).get("label")]
        et = ((info or {}).get("employmentType") or {}).get("id")
        return RawJob(
            company=company,
            job_id=item["uuid"],
            title=(item.get("name") or (info or {}).get("name") or "").strip(),
            source=self.name,
            source_url=API.format(slug=slug),
            apply_url=item.get("url") or (info or {}).get("url"),
            location=join_locations(*locs),
            description=html_to_text(desc or "") if info else (None if info is None else ""),
            posted_date=parse_dt((info or {}).get("createdOn")),
            employment_type=et,
            canonical_key=f"rippling:{item['uuid']}",
            extra={"department": (item.get("department") or {}).get("label")},
        )

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        if not company.board:
            raise SourceError(f"{company.name}: rippling requires 'board' (the ats.rippling.com slug)")
        url = API.format(slug=company.board)
        try:
            items = self.http.get_json(url)
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {url}: {e}") from e
        if not isinstance(items, list):
            raise SourceError(f"{company.name}: unexpected response shape from {url}")
        now = datetime.now(timezone.utc)
        jobs, details = [], 0
        for item in items:
            if not item.get("uuid") or not item.get("name"):
                continue
            # info None -> keep the stored JD; {} -> listing without a JD; dict -> fresh detail
            info: dict | None = None if item["uuid"] in ctx.known else {}
            loc = (item.get("workLocation") or {}).get("label")
            if ctx.want(item["name"], loc) and ctx.needs_detail(item["uuid"], now) and details < ctx.max_details:
                try:
                    info = self._detail(company.board, item["uuid"])
                    details += 1
                except Exception:  # noqa: BLE001 - keep the listing without a JD
                    pass
            jobs.append(self._raw(company.name, company.board, item, info))
        notes = [f"detail fetches capped at {ctx.max_details}; remaining fetched next run"] \
            if details >= ctx.max_details else []
        return FetchResult(jobs=jobs, notes=notes)

    def check_open(self, company: Company, job_id: str, apply_url: str | None) -> bool | None:
        try:
            return bool(self._detail(company.board, job_id).get("uuid"))
        except httpx.HTTPStatusError as e:
            return False if e.response.status_code in (404, 410) else None

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        slug, jid = parse_url(url)
        if not slug or not jid:
            return None
        info = self._detail(slug, jid)
        return self._raw(company or info.get("companyName") or slug, slug,
                         {"uuid": jid, "name": info.get("name"), "url": info.get("url")}, info)
