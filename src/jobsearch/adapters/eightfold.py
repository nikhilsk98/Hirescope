"""Eightfold "PCSX" career sites (the public JSON behind {host}/careers?domain=...; no auth).

Verified 2026-10-12 (Microsoft apply.careers.microsoft.com, PayPal paypal.eightfold.ai; robots.txt allows /api/pcsx):
  GET https://{host}/api/pcsx/search?domain={domain}&query={q}&location={loc}&start={N}
    -> {"data": {"count", "positions": [{id, displayJobId, name, locations, standardizedLocations, postedTs,
        creationTs, department, workLocationOption, atsJobId, positionUrl}]}}       10 positions per page
  GET https://{host}/api/pcsx/position_details?position_id={id}&domain={domain}&hl=en
    -> {"data": {jobDescription (HTML), publicUrl, efcustomTextEmploymentType, ...}}  (unknown id -> 404)
Older Eightfold sites answer "Not authorized for PCSX" on these endpoints; they are not supported.
Small sites are listed in full (United States only); big ones are searched by role keyword like Workday.
Microsoft answers 429 to bursts, so its host is spaced out in run.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError
from .util import join_locations

PAGE = 10
LIST_ALL_MAX = 300      # at most this many US jobs: list them all instead of searching by keyword
US = "United States"


def parse_url(url: str) -> tuple[str | None, str | None, str | None]:
    """{host}/careers[/job/{id}]?domain={domain} -> (host, domain, position id)."""
    u = urlparse(url if "://" in url else "https://" + url)
    domain = (parse_qs(u.query).get("domain") or [None])[0]
    segs = [p for p in u.path.split("/") if p]
    pid = segs[2] if len(segs) >= 3 and segs[:2] == ["careers", "job"] else None
    return (u.hostname or "").lower() or None, domain, pid


def _ts(v) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc) if v else None
    except (TypeError, ValueError, OSError):
        return None


class EightfoldAdapter(Adapter):
    name = "eightfold"
    capabilities = Capabilities(full_description=False, posted_date=True, pagination=True, official=True,
                                query_based=True)

    @staticmethod
    def _base(company: Company) -> tuple[str, str]:
        if not (company.host and company.board):
            raise SourceError(f"{company.name}: eightfold requires host and board (the site's domain= value)")
        return company.host, company.board

    def _search(self, host: str, domain: str, query: str, location: str, start: int) -> dict:
        qs = urlencode({"domain": domain, "query": query, "location": location, "start": start})
        d = self.http.get_json(f"https://{host}/api/pcsx/search?{qs}")
        if not isinstance(d.get("data"), dict):
            raise SourceError(f"{host}: {d.get('message') or 'unexpected response'}")
        return d["data"]

    def _detail(self, host: str, domain: str, pid: str) -> dict:
        qs = urlencode({"position_id": pid, "domain": domain, "hl": "en"})
        return self.http.get_json(f"https://{host}/api/pcsx/position_details?{qs}").get("data") or {}

    def _raw(self, company: str, host: str, domain: str, p: dict, info: dict | None) -> RawJob:
        pid = str(p["id"])
        et = (info or {}).get("efcustomTextEmploymentType")
        return RawJob(
            company=company,
            job_id=pid,
            title=(p.get("name") or (info or {}).get("name") or "").strip(),
            source=self.name,
            source_url=f"https://{host}/api/pcsx/search?domain={domain}",
            apply_url=(info or {}).get("publicUrl") or f"https://{host}{p.get('positionUrl') or '/careers/job/' + pid}",
            location=join_locations(p.get("locations") or (info or {}).get("locations")),
            description=html_to_text(info.get("jobDescription") or "") if info else (None if info is None else ""),
            posted_date=_ts(p.get("postedTs") or p.get("creationTs")),
            employment_type=", ".join(et) if isinstance(et, list) else et,
            canonical_key=f"eightfold:{domain}:{pid}",
            extra={"req_id": p.get("displayJobId") or p.get("atsJobId"), "department": p.get("department"),
                   "work_option": p.get("workLocationOption")},
        )

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        host, domain = self._base(company)
        try:
            first = self._search(host, domain, "", US, 0)
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: https://{host}/api/pcsx/search: {e}") from e
        location = US if first.get("count") else ""     # a site without US jobs (or US naming): all locations
        # results are ranked by relevance, so the first 200 per keyword are the ones that matter
        max_pages = 20 if ctx.full else min(ctx.max_pages, 10)
        if (first.get("count") or 0) <= LIST_ALL_MAX:
            searches = [""]
            max_pages = max(max_pages, -(-LIST_ALL_MAX // PAGE))
        else:
            searches = list(ctx.queries.get("eightfold", []))

        items: dict[str, dict] = {}
        capped = []
        for q in searches:
            start, total = 0, None
            for _ in range(max_pages):
                page = first if (q == "" and start == 0 and location == US) else \
                    self._search(host, domain, q, location, start)
                total = page.get("count") or 0 if total is None else total
                posts = page.get("positions") or []
                for p in posts:
                    if p.get("id"):
                        items.setdefault(str(p["id"]), p)
                start += PAGE
                if not posts or start >= total:
                    break
            else:
                capped.append(f"“{q or 'all'}” capped at {max_pages * PAGE} of {total}")

        now = datetime.now(timezone.utc)
        jobs, details = [], 0
        for pid, p in items.items():
            # info None -> keep the stored JD; {} -> listing without a JD; dict -> fresh detail
            info: dict | None = None if pid in ctx.known else {}
            if ctx.want(p.get("name") or "", join_locations(p.get("locations"))) and ctx.needs_detail(pid, now) \
                    and details < ctx.max_details:
                try:
                    info = self._detail(host, domain, pid)
                    details += 1
                except Exception:  # noqa: BLE001 - keep the listing without a JD
                    pass
            jobs.append(self._raw(company.name, host, domain, p, info))
        notes = [] if location else ["no United States jobs found; searched all locations"]
        if details >= ctx.max_details:
            notes.append(f"detail fetches capped at {ctx.max_details}; remaining fetched next run")
        return FetchResult(jobs=jobs, partial="; ".join(capped) or None, notes=notes)

    def check_open(self, company: Company, job_id: str, apply_url: str | None) -> bool | None:
        host, domain = self._base(company)
        try:
            return bool(self._detail(host, domain, job_id).get("id"))
        except httpx.HTTPStatusError as e:
            return False if e.response.status_code in (404, 410) else None

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        host, domain, pid = parse_url(url)
        if not host or not domain or not pid:
            return None
        info = self._detail(host, domain, pid)
        if not info.get("id"):
            return None
        p = {"id": pid, "name": info.get("name"), "locations": info.get("locations"),
             "postedTs": info.get("postedTs"), "positionUrl": info.get("positionUrl")}
        return self._raw(company or domain.split(".")[0].title(), host, domain, p, info)
