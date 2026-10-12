"""SmartRecruiters Posting API (public, no auth).

Verified 2026-10-12 (ServiceNow, Canva):
  GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=N&country=us
    -> {"totalFound", "content": [{id, name, releasedDate, location{city, region, country, remote},
        typeOfEmployment{label}, experienceLevel{id,label}, ref}]}
  GET https://api.smartrecruiters.com/v1/companies/{id}/postings/{postingId}
    -> {..., postingUrl, applyUrl, jobAd: {sections: {companyDescription, jobDescription, qualifications,
        additionalInformation}: {title, text(HTML)}}}
Note: an unknown company id returns 200 with totalFound 0 (not 404), so 0 jobs cannot prove the id exists.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError
from .util import parse_dt

LIST = "https://api.smartrecruiters.com/v1/companies/{co}/postings?limit=100&offset={offset}&country=us"
ONE = "https://api.smartrecruiters.com/v1/companies/{co}/postings/{id}"


def _location(loc: dict) -> str | None:
    if not loc:
        return None
    parts = [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper() or None]
    s = ", ".join(p for p in parts if p)
    return f"Remote - {s}" if loc.get("remote") else s or None


def _description(d: dict) -> str:
    secs = ((d.get("jobAd") or {}).get("sections") or {})
    html = []
    for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
        sec = secs.get(key) or {}
        if sec.get("text"):
            html.append(f"<h3>{sec.get('title') or ''}</h3>{sec['text']}")
    return html_to_text("".join(html))


class SmartRecruitersAdapter(Adapter):
    name = "smartrecruiters"
    capabilities = Capabilities(full_description=False, posted_date=True, pagination=True, official=True)

    def _raw(self, company: str, co: str, p: dict, detail: dict | None) -> RawJob:
        d = detail or {}
        return RawJob(
            company=company,
            job_id=str(p["id"]),
            title=(p.get("name") or "").strip(),
            source=self.name,
            source_url=LIST.format(co=co, offset=0),
            apply_url=d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{co}/{p['id']}",
            location=_location(p.get("location") or {}),
            description=_description(d) if detail else (None if detail is None else ""),
            posted_date=parse_dt(p.get("releasedDate")),
            employment_type=(p.get("typeOfEmployment") or {}).get("label"),
            canonical_key=f"smartrecruiters:{p['id']}",
            extra={"experience_level": (p.get("experienceLevel") or {}).get("label"),
                   "function": (p.get("function") or {}).get("label")},
        )

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        co = company.board
        if not co:
            raise SourceError(f"{company.name}: smartrecruiters requires 'board' (company identifier)")
        now = datetime.now(timezone.utc)
        items, offset, total = [], 0, None
        max_pages = 50 if ctx.full else ctx.max_pages
        try:
            for _ in range(max_pages):
                page = self.http.get_json(LIST.format(co=co, offset=offset))
                total = page.get("totalFound", 0) if total is None else total
                items.extend(page.get("content") or [])
                offset += 100
                if offset >= total or not page.get("content"):
                    break
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {LIST.format(co=co, offset=offset)}: {e}") from e
        partial = f"capped at {len(items)} of {total}" if total and len(items) < total else None

        jobs, details = [], 0
        for p in items:
            if not p.get("id"):
                continue
            pid = str(p["id"])
            detail: dict | None = None if pid in ctx.known else {}   # None -> keep the stored JD
            if ctx.want(p.get("name") or "", _location(p.get("location") or {})) and ctx.needs_detail(pid, now) \
                    and details < ctx.max_details:
                try:
                    detail = self.http.get_json(ONE.format(co=co, id=pid))
                    details += 1
                except Exception:  # noqa: BLE001
                    pass
            jobs.append(self._raw(company.name, co, p, detail))
        notes = ["0 postings: SmartRecruiters also returns 0 for unknown company ids"] if not total else []
        return FetchResult(jobs=jobs, partial=partial, notes=notes)

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        m = re.search(r"smartrecruiters\.com/([^/]+)/(\d{6,})", url)
        if not m:
            return None
        d = self.http.get_json(ONE.format(co=m.group(1), id=m.group(2)))
        return self._raw(company or (d.get("company") or {}).get("name") or m.group(1), m.group(1), d, d)
