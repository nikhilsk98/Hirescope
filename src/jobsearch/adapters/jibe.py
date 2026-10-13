"""Jibe career sites: a company careers site's own public JSON job feed (careers.amd.com, careers.rivian.com,
careers.publicisgroupe.com, se.jibeapply.com…; robots.txt allows it; no auth). Applications go to the employer's ATS
(often iCIMS), but the listing and full description come from the careers site itself.

Verified 2026-10-12 (AMD: 659 US jobs, Rivian: 766, Schneider Electric: 823, N-able: 7):
  GET https://{host}/api/jobs?page={N}&limit=100&country=United%20States
    -> {"totalCount", "count", "jobs": [{"data": {slug, req_id, title, description, qualifications,
        responsibilities (HTML), city, state, country, posted_date, employment_type, apply_url,
        hiring_organization, tags…tags9 (brand / category labels)}}]}
company.site (optional) keeps only jobs whose tags or hiring organization equal it, e.g. "Publicis Sapient" on the
Publicis Groupe site.
"""
from __future__ import annotations

from urllib.parse import urlencode, urlparse

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError
from .util import parse_dt

PAGE = 100
MAX_PAGES = 40


def feed_url(host: str, page: int, limit: int = PAGE) -> str:
    return f"https://{host}/api/jobs?{urlencode({'page': page, 'limit': limit, 'country': 'United States'})}"


def _matches(d: dict, want: str | None) -> bool:
    if not want:
        return True
    w = want.casefold()
    vals = [d.get("hiring_organization")] + [v for k, x in d.items() if k.startswith("tags") for v in (x if isinstance(x, list) else [x])]
    return any(isinstance(v, str) and v.casefold() == w for v in vals)


class JibeAdapter(Adapter):
    name = "jibe"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=True, official=True)

    def _raw(self, company: str, host: str, d: dict) -> RawJob:
        slug = str(d.get("slug") or d.get("req_id"))
        html = "".join(f"<h3>{t}</h3>{d[k]}" if t else d[k] for k, t in
                       (("description", ""), ("responsibilities", "Responsibilities"), ("qualifications", "Qualifications"))
                       if d.get(k))
        loc = ", ".join(p for p in (d.get("city"), d.get("state"), d.get("country")) if p) or d.get("full_location")
        return RawJob(
            company=company,
            job_id=slug,
            title=(d.get("title") or "").strip(),
            source=self.name,
            source_url=feed_url(host, 1),
            apply_url=f"https://{host}/careers-home/jobs/{slug}?lang=en-us",
            location=loc,
            description=html_to_text(html),
            posted_date=parse_dt(d.get("posted_date") or d.get("create_date")),
            employment_type=d.get("employment_type") or None,
            canonical_key=f"jibe:{host}:{slug}",
            extra={"req_id": d.get("req_id"), "ats_apply_url": d.get("apply_url")},
        )

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        host = company.host or urlparse(company.careers_url or "").hostname
        if not host:
            raise SourceError(f"{company.name}: jibe requires 'host' (the careers site)")
        if not self.http.allowed(feed_url(host, 1)):
            raise SourceError(f"{company.name}: robots.txt on {host} disallows /api/jobs")
        jobs: dict[str, RawJob] = {}
        total, partial, url = None, None, ""
        try:
            for page in range(1, MAX_PAGES + 1):
                url = feed_url(host, page)
                data = self.http.get_json(url)
                if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
                    raise SourceError(f"{company.name}: {url}: not a Jibe job feed")
                total = data.get("totalCount", 0) if total is None else total
                for item in data["jobs"]:
                    d = item.get("data") or {}
                    if (d.get("slug") or d.get("req_id")) and d.get("title") and _matches(d, company.site):
                        r = self._raw(company.name, host, d)
                        jobs.setdefault(r.job_id, r)
                if not data["jobs"] or page * PAGE >= total:
                    break
            else:
                partial = f"listing capped at {MAX_PAGES * PAGE} of {total}"
        except SourceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {url}: {e}") from e
        return FetchResult(jobs=list(jobs.values()), partial=partial)
