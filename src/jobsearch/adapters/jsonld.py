"""schema.org JobPosting (JSON-LD) adapters for careers sites without a known ATS API.

Two modes, both respecting robots.txt and never logging in:
  * sitemap: company.board is a sitemap (or sitemap index) URL; job pages are filtered by URL pattern,
    prefiltered by the title words in their slug, cached by <lastmod>, then parsed for JobPosting JSON-LD.
    Used for Netflix (verified 2026-10-12: explore.jobs.netflix.net job pages carry JobPosting JSON-LD;
    its JSON search API returns "PCSX is not enabled").
  * page: company.careers_url is fetched; JobPosting blocks on the page are used, and same-site links that
    look like job pages are followed (bounded). Used as the My Companies fallback.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import urljoin, urlparse

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError
from .util import join_locations, parse_dt

log = logging.getLogger(__name__)

_LD = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_JOB_LINK = re.compile(r"/(jobs?|careers?|positions?|openings?|opportunit(y|ies)|roles?)/", re.I)
MAX_PAGE_LINKS = 60
HTML_ACCEPT = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def jobpostings_in_html(html: str) -> list[dict]:
    out: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                out.append(node)
            for key in ("@graph", "itemListElement", "item"):
                if key in node:
                    walk(node[key])
    for m in _LD.finditer(html or ""):
        try:
            walk(json.loads(unescape(m.group(1)).strip()) if "&quot;" in m.group(1) else json.loads(m.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def _place(p) -> str | None:
    if not isinstance(p, dict):
        return str(p) if p else None
    a = p.get("address") or {}
    if isinstance(a, str):
        return a
    country = a.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    parts: list[str] = []
    for x in (a.get("addressLocality"), a.get("addressRegion"), country):
        for piece in str(x or "").split(","):
            piece = piece.strip()
            if piece and piece.casefold() not in (q.casefold() for q in parts):
                parts.append(piece)
    return ", ".join(parts) or p.get("name")


def posting_to_raw(company: str, d: dict, page_url: str, source: str) -> RawJob:
    locs = d.get("jobLocation")
    locs = locs if isinstance(locs, list) else [locs] if locs else []
    loc = join_locations([_place(x) for x in locs])
    if d.get("jobLocationType") == "TELECOMMUTE":
        req = d.get("applicantLocationRequirements")
        req = req if isinstance(req, list) else [req] if req else []
        names = [r.get("name") for r in req if isinstance(r, dict) and r.get("name")]
        loc = join_locations(*(f"Remote - {n}" for n in names), loc if loc and "remote" not in loc.lower() else None) \
            or "Remote"
    ident = d.get("identifier")
    jid = ident.get("value") if isinstance(ident, dict) else ident if isinstance(ident, (str, int)) else None
    url = d.get("url") or page_url
    if not jid:
        jid = hashlib.sha1(url.encode()).hexdigest()[:16]
    et = d.get("employmentType")
    if isinstance(et, list):
        et = ", ".join(et)
    org = d.get("hiringOrganization")
    return RawJob(
        company=company or (org.get("name") if isinstance(org, dict) else None) or urlparse(url).hostname or "?",
        job_id=str(jid),
        title=unescape(d.get("title") or "").strip(),
        source=source,
        source_url=page_url,
        apply_url=url,
        location=loc,
        description=html_to_text(d.get("description") or ""),
        posted_date=parse_dt(d.get("datePosted")),
        employment_type=et,
        extra={**({"deadline": d["validThrough"]} if d.get("validThrough") else {})},
    )


def _url_id(url: str) -> str:
    """Stable id from a job-page URL: its long numeric id when present, else a hash of the URL."""
    if m := re.search(r"/(\d{6,})(?:[-_/]|$)", urlparse(url).path):
        return m.group(1)
    return hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:16]


def _slug_title(url: str) -> str:
    seg = urlparse(url).path.rstrip("/").split("/")[-1]
    seg = re.sub(r"^\d+[-_]?", "", seg)
    return re.sub(r"[-_]+", " ", seg)


class SitemapJsonLdAdapter(Adapter):
    """company.board = sitemap URL; company.site = regex a job-page URL must match."""
    name = "jsonld_sitemap"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=False, official=True)

    def _sitemap_urls(self, url: str, depth: int = 0) -> list[tuple[str, str | None]]:
        xml = self.http.get(url, headers={"Accept": "application/xml,text/xml"}).text
        root = ET.fromstring(xml.encode())
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        if root.tag.endswith("sitemapindex") and depth < 2:
            out = []
            for sm in root.findall("s:sitemap", ns):
                loc = sm.findtext("s:loc", default="", namespaces=ns).strip()
                if loc and "_cat" not in loc:   # skip category sitemaps
                    out.extend(self._sitemap_urls(loc, depth + 1))
            return out
        return [(u.findtext("s:loc", default="", namespaces=ns).strip(),
                 u.findtext("s:lastmod", default=None, namespaces=ns)) for u in root.findall("s:url", ns)]

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        if not company.board:
            raise SourceError(f"{company.name}: sitemap adapter requires 'board' (sitemap URL)")
        pattern = re.compile(company.site or r"/jobs?/")
        try:
            urls = [(u, lm) for u, lm in self._sitemap_urls(company.board) if u and pattern.search(u)]
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: sitemap {company.board}: {e}") from e
        if not urls:
            raise SourceError(f"{company.name}: sitemap has no URLs matching {pattern.pattern}")

        jobs, pages, fetched, blocked = [], {}, 0, 0
        seen: set[str] = set()
        for url, lastmod in urls:
            jid = _url_id(url)
            seen.add(jid)            # listed at the source, whether or not we fetch it
            cached = ctx.pages.get(url)
            if cached and cached.get("payload") and cached.get("lastmod") == lastmod:
                d = json.loads(cached["payload"])
            elif not ctx.want(_slug_title(url), _slug_title(url)):
                continue             # slug says the role is not relevant: don't fetch the page
            elif fetched >= ctx.max_details:
                continue
            elif not self.http.allowed(url):
                blocked += 1
                continue
            else:
                try:
                    found = jobpostings_in_html(self.http.get(url, headers=HTML_ACCEPT).text)
                except Exception as e:  # noqa: BLE001
                    log.debug("jsonld page failed %s: %s", url, e)
                    continue
                fetched += 1
                if not found:
                    continue
                d = found[0]
                pages[url] = {"lastmod": lastmod, "payload": json.dumps(d)}
            raw = posting_to_raw(company.name, d, url, self.name if self.name != "jsonld_sitemap" else "jsonld")
            raw.job_id = jid
            jobs.append(raw)
        notes = [f"{blocked} pages disallowed by robots.txt"] if blocked else []
        partial = f"page fetches capped at {ctx.max_details}" if fetched >= ctx.max_details else None
        return FetchResult(jobs=jobs, pages=pages, notes=notes, partial=partial, seen_ids=seen)


class NetflixAdapter(SitemapJsonLdAdapter):
    name = "netflix"
    SITEMAP = "https://explore.jobs.netflix.net/careers/sitemap_index.xml?domain=netflix.com&microsite=netflix.com"

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        company = company.model_copy(update={"board": company.board or self.SITEMAP,
                                             "site": company.site or r"/careers/job/"})
        return super().fetch(company, ctx)


class CareersPageAdapter(Adapter):
    """My Companies fallback: JSON-LD on a custom careers page and the job pages it links to."""
    name = "jsonld"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=False, official=True)

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        url = company.careers_url
        if not url:
            raise SourceError(f"{company.name}: no careers_url to read")
        if not self.http.allowed(url):
            raise SourceError(f"{company.name}: robots.txt disallows {url}")
        try:
            html = self.http.get(url, headers=HTML_ACCEPT).text
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {url}: {e}") from e
        postings = [(d, url) for d in jobpostings_in_html(html)]
        if not postings:
            host = urlparse(url).hostname
            links = []
            for href in re.findall(r"href=[\"']([^\"'#]+)[\"']", html):
                full = urljoin(url, unescape(href))
                if urlparse(full).hostname == host and _JOB_LINK.search(urlparse(full).path) and full != url:
                    links.append(full)
            for link in list(dict.fromkeys(links))[:MAX_PAGE_LINKS]:
                if not self.http.allowed(link):
                    continue
                try:
                    postings += [(d, link) for d in jobpostings_in_html(self.http.get(link, headers=HTML_ACCEPT).text)]
                except Exception:  # noqa: BLE001
                    continue
        if not postings:
            raise SourceError(f"{company.name}: no schema.org JobPosting data on {url} or its job links")
        jobs = {}
        for d, page in postings:
            r = posting_to_raw(company.name, d, page, self.name)
            jobs.setdefault(r.job_id, r)
        return FetchResult(jobs=list(jobs.values()))

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        if not self.http.allowed(url):
            raise SourceError(f"robots.txt disallows {url}")
        found = jobpostings_in_html(self.http.get(url, headers=HTML_ACCEPT).text)
        return posting_to_raw(company or "", found[0], url, self.name) if found else None
