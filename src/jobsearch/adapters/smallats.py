"""Small ATS job boards with public, unauthenticated career-page data (robots.txt respected; never a login):

BambooHR (verified 2026-10-20: outseer 3 jobs, hycu 16)
  GET https://{co}.bamboohr.com/careers/list          -> {result: [{id, jobOpeningName, location: {city, state},
                                                          departmentLabel, employmentStatusLabel, isRemote}]}
  GET https://{co}.bamboohr.com/careers/{id}/detail   -> {result: {jobOpening: {jobOpeningName, description (HTML),
                                                          location, datePosted, employmentStatusLabel}}}
JazzHR (verified 2026-10-20: seamachines, validity)
  GET https://{co}.applytojob.com/apply               -> HTML with links …/apply/{id}/{Title-Slug}
  GET https://{co}.applytojob.com/apply/{id}/…        -> schema.org JobPosting JSON-LD (title, description, date)
Paylocity (verified 2026-10-20: LineVision, MEDITECH)
  GET https://recruiting.paylocity.com/recruiting/jobs/All/{guid}/…  -> HTML with window.pageData = {Jobs: [{JobId,
                                                          JobTitle, LocationName, PublishedDate, IsRemote}]}
  GET https://recruiting.paylocity.com/Recruiting/Jobs/Details/{id}  -> schema.org JobPosting JSON-LD
Avature career portals (verified 2026-10-20: Bloomberg, Two Sigma, Siemens; robots.txt allows /careers)
  GET https://{host}/{portal}/SearchJobs/?jobRecordsPerPage=50&jobOffset=N  -> HTML result articles (title link
                                                          …/JobDetail/[slug/]{id}, list-item-location)
  GET …/JobDetail/…/{id}                              -> HTML (the article__content blocks are the description)
Lists have no descriptions, so relevant jobs get one detail request each (bounded by ctx.max_details).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError
from .jsonld import HTML_ACCEPT, jobpostings_in_html, posting_to_raw
from .util import join_locations, parse_dt


class _ListThenDetail(Adapter):
    """A list of (id, title, location, item); details only for relevant jobs that need one."""

    def listing(self, company: Company) -> list[tuple[str, str, str | None, dict]]:
        raise NotImplementedError

    def detail(self, company: Company, jid: str, item: dict) -> RawJob | None:
        raise NotImplementedError

    def bare(self, company: Company, jid: str, title: str, loc: str | None, item: dict) -> RawJob:
        raise NotImplementedError

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        try:
            rows = self.listing(company)
        except SourceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {self.name} listing: {e}") from e
        now = datetime.now(timezone.utc)
        jobs, details = [], 0
        for jid, title, loc, item in rows:
            raw = None
            if ctx.wants(jid, title, loc) and ctx.needs_detail(jid, now) and details < ctx.max_details:
                try:
                    raw = self.detail(company, jid, item)
                    details += 1
                except Exception:  # noqa: BLE001 - keep the listing without a JD
                    raw = None
            if raw is None:
                raw = self.bare(company, jid, title, loc, item)
                raw.description = None if jid in ctx.known else ""
            raw.job_id, raw.source, raw.company = jid, self.name, company.name
            jobs.append(raw)
        notes = [f"detail fetches capped at {ctx.max_details}; remaining fetched next run"] \
            if details >= ctx.max_details else []
        return FetchResult(jobs=jobs, notes=notes)


# --- BambooHR --------------------------------------------------------------------------------------------------------
class BambooHRAdapter(_ListThenDetail):
    name = "bamboohr"
    capabilities = Capabilities(full_description=False, posted_date=True, pagination=False, official=True)

    @staticmethod
    def _base(company: Company) -> str:
        if not company.board:
            raise SourceError(f"{company.name}: bamboohr requires 'board' (the {{board}}.bamboohr.com subdomain)")
        return f"https://{company.board}.bamboohr.com/careers"

    @staticmethod
    def _loc(x: dict) -> str | None:
        loc = x.get("location") or {}
        city = join_locations(", ".join(p for p in (loc.get("city"), loc.get("state"), loc.get("addressCountry"))
                                        if p) or None)
        return join_locations("Remote" if x.get("isRemote") or x.get("locationType") == "1" else None, city)

    def listing(self, company):
        data = self.http.get_json(f"{self._base(company)}/list")
        return [(str(x["id"]), (x.get("jobOpeningName") or "").strip(), self._loc(x), x)
                for x in data.get("result") or [] if x.get("id") and x.get("jobOpeningName")]

    def bare(self, company, jid, title, loc, item):
        return RawJob(company=company.name, job_id=jid, title=title, source=self.name,
                      source_url=f"{self._base(company)}/list", apply_url=f"{self._base(company)}/{jid}", location=loc,
                      employment_type=item.get("employmentStatusLabel"),
                      canonical_key=f"bamboohr:{company.board}:{jid}", extra={"department": item.get("departmentLabel")})

    def detail(self, company, jid, item):
        j = (self.http.get_json(f"{self._base(company)}/{jid}/detail").get("result") or {}).get("jobOpening") or {}
        raw = self.bare(company, jid, (j.get("jobOpeningName") or item.get("jobOpeningName") or "").strip(),
                        self._loc(j) or self._loc(item), item)
        raw.description = html_to_text(j.get("description") or "")
        raw.posted_date = parse_dt(j.get("datePosted"))
        return raw

    def check_open(self, company, job_id, apply_url):
        try:
            j = (self.http.get_json(f"{self._base(company)}/{job_id}/detail").get("result") or {}).get("jobOpening") or {}
            return (j.get("jobOpeningStatus") or "Open") == "Open" and bool(j.get("jobOpeningName"))
        except httpx.HTTPStatusError as e:
            return False if e.response.status_code in (404, 410) else None

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        u = urlparse(url)
        m = re.match(r"^/careers/(\d+)", u.path)
        if not (u.hostname or "").endswith(".bamboohr.com") or not m:
            return None
        c = Company(name=company or u.hostname.split(".")[0], source=self.name, board=u.hostname.split(".")[0])
        return self.detail(c, m.group(1), {})


# --- schema.org job pages behind a listing (JazzHR, Paylocity) -------------------------------------------------------
class _JsonLdPages(_ListThenDetail):
    def page(self, company: Company, jid: str, item: dict) -> str:
        raise NotImplementedError

    def detail(self, company, jid, item):
        url = self.page(company, jid, item)
        if not self.http.allowed(url):
            return None
        found = jobpostings_in_html(self.http.get(url, headers=HTML_ACCEPT).text)
        if not found:
            return None
        raw = posting_to_raw(company.name, found[0], url, self.name)
        raw.apply_url = url
        raw.canonical_key = self.key(company, jid)
        return raw

    def key(self, company: Company, jid: str) -> str:
        return f"{self.name}:{jid}"

    def bare(self, company, jid, title, loc, item):
        return RawJob(company=company.name, job_id=jid, title=title, source=self.name,
                      source_url=company.careers_url or "", apply_url=self.page(company, jid, item), location=loc,
                      posted_date=parse_dt(item.get("PublishedDate")), canonical_key=self.key(company, jid))


class JazzHRAdapter(_JsonLdPages):
    name = "jazzhr"
    capabilities = Capabilities(full_description=False, posted_date=True, pagination=False, official=True)
    _LINK = re.compile(r"href=[\"'](https?://([a-z0-9.-]+)/apply/([A-Za-z0-9]{6,})/([^\"'?#]*))[\"']", re.I)

    @staticmethod
    def _base(company: Company) -> str:
        """board: the {board}.applytojob.com subdomain, or the board's own host (careers.flexcar.com)."""
        if not company.board:
            raise SourceError(f"{company.name}: jazzhr requires 'board' (the {{board}}.applytojob.com subdomain)")
        host = company.board if "." in company.board else f"{company.board}.applytojob.com"
        return f"https://{host}/apply"

    def listing(self, company):
        url = self._base(company)
        if not self.http.allowed(url):
            raise SourceError(f"{company.name}: robots.txt disallows {url}")
        html = self.http.get(url, headers=HTML_ACCEPT).text
        out = {}
        host = urlparse(url).hostname
        for full, h, jid, slug in self._LINK.findall(html):
            if h.lower() == host and jid not in out:
                out[jid] = (jid, re.sub(r"[-_+]+", " ", unescape(slug)).strip(), None, {"url": full})
        return list(out.values())

    def page(self, company, jid, item):
        return item.get("url") or f"{self._base(company)}/{jid}"

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        u = urlparse(url)
        m = re.match(r"^/apply/([A-Za-z0-9]{6,})", u.path)
        if not (u.hostname or "").endswith(".applytojob.com") or not m:
            return None
        sub = u.hostname.split(".")[0]
        return self.detail(Company(name=company or sub, source=self.name, board=sub), m.group(1), {"url": url})


class PaylocityAdapter(_JsonLdPages):
    name = "paylocity"
    capabilities = Capabilities(full_description=False, posted_date=True, pagination=False, official=True)
    DETAIL = "https://recruiting.paylocity.com/Recruiting/Jobs/Details/{id}"
    _DATA = re.compile(r"window\.pageData\s*=\s*(\{.*?\});\s*(?:</script>|\n)", re.S)

    def listing(self, company):
        url = company.careers_url or (f"https://recruiting.paylocity.com/recruiting/jobs/All/{company.board}"
                                      if company.board else None)
        if not url:
            raise SourceError(f"{company.name}: paylocity requires careers_url (…/recruiting/jobs/All/{{guid}}/…)")
        html = self.http.get(url, headers=HTML_ACCEPT).text
        m = self._DATA.search(html)
        if not m:
            raise SourceError(f"{company.name}: no job data (window.pageData) on {url}")
        jobs = json.loads(m.group(1)).get("Jobs") or []
        return [(str(j["JobId"]), (j.get("JobTitle") or "").strip(),
                 join_locations("Remote" if j.get("IsRemote") else None, j.get("LocationName")), j)
                for j in jobs if j.get("JobId") and j.get("JobTitle")]

    def page(self, company, jid, item):
        return self.DETAIL.format(id=jid)

    def check_open(self, company, job_id, apply_url):
        try:
            return bool(jobpostings_in_html(self.http.get(self.DETAIL.format(id=job_id), headers=HTML_ACCEPT).text))
        except httpx.HTTPStatusError as e:
            return False if e.response.status_code in (404, 410) else None

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        m = re.search(r"recruiting\.paylocity\.com/recruiting/jobs/details/(\d+)", url, re.I)
        if not m:
            return None
        return self.detail(Company(name=company or "?", source=self.name), m.group(1), {})


# --- Avature ---------------------------------------------------------------------------------------------------------
class AvatureAdapter(_ListThenDetail):
    """company.careers_url = the portal's job search page as you'd open it (…/careers/SearchJobs, …/careers/OpenRoles,
    …/externaljobs/SearchJobs/?search=…); pages are read with jobOffset."""
    name = "avature"
    capabilities = Capabilities(full_description=False, posted_date=False, pagination=True, official=True)
    MAX_PAGES = 80
    _ITEM = re.compile(r"<a[^>]+href=[\"']([^\"']*/JobDetail/[^\"']*?(\d+))/?[\"'][^>]*>(.*?)</a>(.{0,800})", re.S | re.I)
    _LOC = re.compile(r"list-item-location[^>]*>(.*?)<", re.S | re.I)
    _NEXT = re.compile(r'href="[^"]*(?:job|folder)Offset=\d+[^"]*"', re.I)

    def listing(self, company):
        base = company.careers_url
        if not base or "avature" not in (company.source or "avature"):
            raise SourceError(f"{company.name}: avature requires careers_url (the portal's job search page)")
        if not re.search(r"/(?:SearchJobs|OpenRoles)\b", base):      # the portal's root: its job search page
            base = base.split("?")[0].rstrip("/") + "/SearchJobs"
        if not self.http.allowed(base):
            raise SourceError(f"{company.name}: robots.txt disallows {base}")
        out: dict[str, tuple] = {}
        offset, page_url = 0, None        # page 2 on: the portal's own pagination link (jobOffset / folderOffset)
        for _ in range(self.MAX_PAGES):
            html = self.http.get(page_url.format(offset) if page_url else base, headers=HTML_ACCEPT).text
            found = 0
            for href, jid, title, after in self._ITEM.findall(html):
                title = " ".join(unescape(re.sub(r"<[^>]+>", " ", title)).split())
                if not title or jid in out:
                    continue
                loc = self._LOC.search(after)
                loc = " ".join(unescape(loc.group(1)).split()) if loc else None
                out[jid] = (jid, title, loc, {"url": urljoin(base, unescape(href)), "title": title, "loc": loc})
                found += 1
            if found == 0:
                break
            offset += found              # a portal may show fewer per page than asked (Bloomberg: 12)
            if page_url is None:
                nxt = self._NEXT.search(html)
                if not nxt:
                    break                # one page of results
                link = unescape(nxt.group(0))[6:-1]
                page_url = re.sub(r"((?:job|folder)Offset=)\d+", lambda m: m.group(1) + "{}", urljoin(base, link))
        if not out:
            raise SourceError(f"{company.name}: no job results on {base}")
        return list(out.values())

    def bare(self, company, jid, title, loc, item):
        host = urlparse(item["url"]).hostname or ""
        return RawJob(company=company.name, job_id=jid, title=title, source=self.name,
                      source_url=company.careers_url or "", apply_url=item["url"], location=loc,
                      canonical_key=f"avature:{host}:{jid}")

    def detail(self, company, jid, item):
        url = item["url"]
        if not self.http.allowed(url):
            return None
        html = self.http.get(url, headers=HTML_ACCEPT).text
        raw = self.bare(company, jid, item.get("title") or "", item.get("loc"), item)
        found = jobpostings_in_html(html)
        if found:
            raw.description = html_to_text(found[0].get("description") or "")
            raw.posted_date = parse_dt(found[0].get("datePosted"))
        else:
            i = html.find("article__content")
            j = html.find("<footer", i) if i >= 0 else -1
            raw.description = html_to_text(html[max(i - 20, 0):j if j > 0 else i + 40000]) if i >= 0 else ""
        if not raw.location:          # the list didn't show it (Two Sigma, Siemens): the page's "Location" field
            m = re.search(r"\bLocation(?:s|\(s\))?\s*:?\s*\n+((?:\s*-?\s*[^\n]{3,120}\n?){1,6})", raw.description or "")
            lines = [x.strip() for x in (m.group(1).split("\n") if m else []) if x.strip()]
            bullets = [x.lstrip("- ").replace(" - ", ", ") for x in lines if x.startswith("-")]   # "- City - Region - Country"
            raw.location = join_locations(*(bullets or lines[:1])) if lines else None
        if not raw.posted_date and (m := re.search(r"Posted (?:since|on)\s*\n+\s*([^\n]+)", raw.description or "")):
            raw.posted_date = parse_dt(m.group(1))
        return raw

    def check_open(self, company, job_id, apply_url):
        if not apply_url:
            return None
        try:
            return "article__content" in self.http.get(apply_url, headers=HTML_ACCEPT).text
        except httpx.HTTPStatusError as e:
            return False if e.response.status_code in (404, 410) else None
