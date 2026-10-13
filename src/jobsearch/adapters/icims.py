"""iCIMS career portals ({portal}.icims.com; public pages, no auth, robots.txt respected).

iCIMS has no public job API, so this reads what each portal publishes for search engines:
Verified 2026-10-12 (HealthEdge: 67 jobs, Seismic: 19, Atlassian global careers: 97):
  GET https://{portal}.icims.com/robots.txt   -> allows /jobs/ (some portals answer "Disallow: /": then nothing is read)
  GET https://{portal}.icims.com/sitemap.xml  -> <url><loc>…/jobs/{id}/{slug}/job</loc><lastmod>…</lastmod></url>
  GET https://{portal}.icims.com/jobs/{id}/{slug}/job?in_iframe=1 -> schema.org JobPosting JSON-LD (full description,
      datePosted, employmentType, jobLocation); without in_iframe=1 the page is only the frame around it.
Pages are cached by <lastmod>, prefiltered by the title words in their slug, and capped per run (SitemapJsonLdAdapter).
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..config import Company
from .base import FetchContext, FetchResult, RawJob, SourceError
from .jsonld import SitemapJsonLdAdapter

_JOB = re.compile(r"/jobs/(\d+)/")


class IcimsAdapter(SitemapJsonLdAdapter):
    name = "icims"

    def job_id(self, url: str) -> str:
        m = _JOB.search(urlparse(url).path)
        return m.group(1) if m else super().job_id(url)

    def slug_title(self, url: str) -> str:   # …/jobs/{id}/{title-slug}/job
        segs = [p for p in urlparse(url).path.split("/") if p]
        return segs[-2].replace("-", " ") if len(segs) >= 4 and segs[-1] == "job" else super().slug_title(url)

    def page_url(self, url: str) -> str:
        return url + ("&" if "?" in url else "?") + "in_iframe=1"

    def finish(self, raw: RawJob, url: str) -> None:
        raw.apply_url = url
        raw.canonical_key = f"icims:{urlparse(url).hostname}:{raw.job_id}"

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        host = company.host or company.board
        if not host:
            raise SourceError(f"{company.name}: icims requires 'host' (e.g. careers-acme.icims.com)")
        if not self.http.allowed(f"https://{host}/jobs/search"):
            raise SourceError(f"{company.name}: robots.txt on {host} disallows job pages")
        company = company.model_copy(update={"board": f"https://{host}/sitemap.xml", "site": r"/jobs/\d+/[^/]+/job"})
        return super().fetch(company, ctx)
