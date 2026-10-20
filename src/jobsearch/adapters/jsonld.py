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
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError
from .util import join_locations, parse_dt

log = logging.getLogger(__name__)

_LD = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_JOB_LINK = re.compile(r"/(jobs?|careers?|positions?|openings?|opportunit(y|ies)|roles?)/", re.I)
MAX_PAGE_LINKS = 60
_PLACEHOLDERS = {"unavailable", "n/a", "na", "none", "null", "-"}
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
    if not out and _MICRODATA.search(html or ""):
        if d := _microdata_posting(html):
            out.append(d)
    return out


# schema.org JobPosting as microdata (itemtype/itemprop attributes) instead of JSON-LD: SAP SuccessFactors career sites
# (verified 2026-10-20: apply.careers.dynatrace.com, jobs.vista.com, ascendlearning.jobs.hr.cloud.sap job pages)
_MICRODATA = re.compile(r"itemtype=[\"']https?://schema\.org/JobPosting[\"']", re.I)


class _Props(HTMLParser):
    """itemprop values: a <meta>'s content, else the element's inner HTML (description) or text."""

    VOID = {"meta", "link", "img", "br", "hr", "input", "source", "area", "base", "col", "embed", "param", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.props: dict[str, str] = {}
        self.open: list[list] = []          # [prop, tag, depth, parts]
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        prop = a.get("itemprop")
        if prop and (tag == "meta" or "content" in a):
            self.props.setdefault(prop, a.get("content") or "")
        for o in self.open:
            o[3].append(self.get_starttag_text() or "")
        if tag in self.VOID:
            return
        self.depth += 1
        if prop and prop not in self.props and "content" not in a:
            self.open.append([prop, tag, self.depth, []])

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        for o in list(self.open):
            if o[1] == tag and o[2] == self.depth:
                self.open.remove(o)
                self.props.setdefault(o[0], "".join(o[3]).strip())
            else:
                o[3].append(f"</{tag}>")
        self.depth -= 1

    def handle_data(self, data):
        for o in self.open:
            o[3].append(data.replace("<", "&lt;"))


def _microdata_posting(html: str) -> dict | None:
    p = _Props()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - malformed pages: whatever was read so far
        pass
    m = p.props
    title = re.sub(r"<[^>]+>", " ", m.get("title") or "")
    if not title.strip():
        return None
    return {"@type": "JobPosting", "title": " ".join(title.split()), "description": m.get("description") or "",
            "datePosted": m.get("datePosted"), "employmentType": m.get("employmentType"),
            "validThrough": m.get("validThrough"), "hiringOrganization": {"name": m.get("hiringOrganization")},
            "jobLocation": {"address": {"addressLocality": m.get("addressLocality"),
                                        "addressRegion": m.get("addressRegion"),
                                        "addressCountry": m.get("addressCountry")}}}


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
            if piece.casefold() in _PLACEHOLDERS:   # iCIMS fills unknown address parts with "UNAVAILABLE"
                continue
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
        company=" ".join(str(company or (org.get("name") if isinstance(org, dict) else None) or urlparse(url).hostname
                         or "?").split()),     # " Keurig Dr Pepper " on their pages
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
    """The title words in a job URL: its last segment with words (…/job/Boston-Software-Engineer/1440363800/ and
    …/job/boston/software-engineer/27595/102189634112 end in ids)."""
    segs = [unquote(x) for x in urlparse(url).path.rstrip("/").split("/") if x]
    seg = next((x for x in reversed(segs) if re.search(r"[A-Za-z]{3}", x)), "")
    seg = re.sub(r"^\d+[-_]?", "", seg)
    return re.sub(r"[-_+]+", " ", seg)


class SitemapJsonLdAdapter(Adapter):
    """company.board = sitemap URL; company.site = regex a job-page URL must match."""
    name = "jsonld_sitemap"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=False, official=True)

    # hooks for sitemap-based ATS adapters (iCIMS)
    def job_id(self, url: str) -> str:
        return _url_id(url)

    def page_url(self, url: str) -> str:
        """The URL actually fetched for a sitemap job page."""
        return url

    def slug_title(self, url: str) -> str:
        """Title words in the job page URL (the cheap relevance prefilter before fetching it)."""
        return _slug_title(url)

    def finish(self, raw: RawJob, url: str) -> None:
        """Last touches on a parsed posting (apply link, canonical key)."""

    def _sitemap_urls(self, url: str, depth: int = 0) -> list[tuple[str, str | None]]:
        xml = self.http.get(url, headers={"Accept": "application/xml,text/xml"}).text
        root = ET.fromstring(xml.encode())
        # any sitemap namespace (SuccessFactors uses Google's old http://www.google.com/schemas/sitemap/0.9)
        local = lambda e: e.tag.rsplit("}", 1)[-1]  # noqa: E731
        kids = lambda e, name: [c for c in e if local(c) == name]  # noqa: E731
        text = lambda e, name: next((c.text or "" for c in e if local(c) == name), None)  # noqa: E731
        if local(root) == "sitemapindex" and depth < 2:
            out = []
            for sm in kids(root, "sitemap"):
                loc = (text(sm, "loc") or "").strip()
                if loc and "_cat" not in loc:   # skip category sitemaps
                    out.extend(self._sitemap_urls(loc, depth + 1))
            return out
        if root.tag == "rss":                 # a job feed as the sitemap (NTT DATA's SuccessFactors site)
            out = []
            for it in root.iter("item"):
                link = (it.findtext("link") or "").strip()
                if not link:
                    continue
                title = (it.findtext("title") or "").strip()
                loc = None
                if m := re.match(r"^(.*?)\s*\(([^()]*,[^()]*)\)\s*$", title):   # "Role (San Francisco, CA, US)"
                    title, loc = m.group(1), m.group(2)
                self._feed[link] = {"@type": "JobPosting", "title": title, "description": unescape(it.findtext("description") or ""),
                                    "datePosted": it.findtext("pubDate"), "jobLocation": loc}
                out.append((link, it.findtext("pubDate")))
            return out
        return [((text(u, "loc") or "").strip(), text(u, "lastmod")) for u in kids(root, "url")]

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        if not company.board:
            raise SourceError(f"{company.name}: sitemap adapter requires 'board' (sitemap URL)")
        pattern = re.compile(company.site or r"/jobs?/")
        self._feed: dict[str, dict] = {}
        try:
            urls = [(u, lm) for u, lm in self._sitemap_urls(company.board) if u and pattern.search(u)]
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: sitemap {company.board}: {e}") from e
        if not urls:
            raise SourceError(f"{company.name}: sitemap has no URLs matching {pattern.pattern}")

        jobs, pages, fetched, blocked = [], {}, 0, 0
        seen: set[str] = set()
        for url, lastmod in urls:
            jid = self.job_id(url)
            seen.add(jid)            # listed at the source, whether or not we fetch it
            cached = ctx.pages.get(url)
            if url in self._feed:              # the feed item is the posting: nothing to fetch
                d = self._feed[url]
                if not ctx.want(d["title"], d.get("jobLocation")):
                    continue
            elif cached and cached.get("payload") and cached.get("lastmod") == lastmod:
                d = json.loads(cached["payload"])
            elif not ctx.want(self.slug_title(url), self.slug_title(url)):
                continue             # slug says the role is not relevant: don't fetch the page
            elif fetched >= ctx.max_details:
                continue
            elif not self.http.allowed(self.page_url(url)):
                blocked += 1
                continue
            else:
                try:
                    found = jobpostings_in_html(self.http.get(self.page_url(url), headers=HTML_ACCEPT).text)
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
            self.finish(raw, url)
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
