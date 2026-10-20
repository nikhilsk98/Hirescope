"""Oracle Recruiting Cloud career sites (the public REST API behind every …/hcmUI/CandidateExperience/…/sites/<site>
page; no auth; robots.txt allows it).

Verified 2026-10-20 (JPMorgan Chase CX_1001: 1,601 "software" hits; Oracle CX_45001; Akamai CX_1; Fortinet CX_2001):
  GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true
      &expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},facetsList=LOCATIONS,
      limit=25,offset=N,keyword="…",locationId={United States facet id}
    -> {items: [{TotalJobsCount, locationsFacet: [{Id, Name}], requisitionList: [{Id, Title, PostedDate,
        PrimaryLocation, PrimaryLocationCountry, secondaryLocations: [{Name}], ShortDescriptionStr, JobFamily}]}]}
  GET …/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id="{id}",siteNumber={site}
    -> {items: [{Title, ExternalDescriptionStr, ExternalResponsibilitiesStr, ExternalQualificationsStr,
        ExternalPostedStartDate, PrimaryLocation, JobSchedule}]}
Searches use the role keywords in config/title_rules.yaml (search_queries.oracle_cloud) inside the site's own
"United States" location facet; results are relevance-ranked, so a search stops once a few pages in a row have no
relevant title.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError, SourceUnavailable
from .util import join_locations, parse_dt

API = "https://{host}/hcmRestApi/resources/latest/{res}"
PAGE = 25
FULL_PAGES = 40           # 1,000 results per search
STALE_PAGES = 3           # relevance-ranked: stop after this many pages in a row with no relevant title
DEFAULT_QUERIES = ["software", "developer", "intern", "data engineer", "cloud", "new grad"]


def parse_url(url: str) -> tuple[str | None, str | None, str | None]:
    """…oraclecloud.com/hcmUI/CandidateExperience/en/sites/{site}/job/{id} -> (host, site, id)."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if not host.endswith("oraclecloud.com"):
        return None, None, None
    m = re.search(r"/sites/([A-Za-z0-9_]+)(?:/(?:job|requisitions/preview)/(\d+))?", u.path)
    return (host, m.group(1), m.group(2)) if m else (host, None, None)


class OracleCloudAdapter(Adapter):
    name = "oracle_cloud"
    capabilities = Capabilities(full_description=False, posted_date=True, pagination=True, official=True,
                                query_based=True)

    @staticmethod
    def _base(company: Company) -> tuple[str, str]:
        if not (company.host and company.site):
            raise SourceError(f"{company.name}: oracle_cloud requires host (…oraclecloud.com) and site (e.g. CX_1001)")
        return company.host, company.site

    def _search(self, host: str, site: str, keyword: str, offset: int, location_id: str | None,
                limit: int = PAGE) -> dict:
        finder = (f"findReqs;siteNumber={site},facetsList=LOCATIONS,limit={limit},offset={offset},"
                  f"keyword=\"{keyword}\",sortBy=RELEVANCY" + (f",locationId={location_id}" if location_id else ""))
        url = (API.format(host=host, res="recruitingCEJobRequisitions") + "?onlyData=true&expand=requisitionList."
               f"secondaryLocations&finder={quote(finder, safe='=,;')}")
        items = self.http.get_json(url).get("items") or []
        return items[0] if items else {}

    def _detail(self, host: str, site: str, jid: str) -> dict:
        finder = quote(f'ById;Id="{jid}",siteNumber={site}', safe="=,;")
        url = API.format(host=host, res="recruitingCEJobRequisitionDetails") + f"?expand=all&onlyData=true&finder={finder}"
        items = self.http.get_json(url).get("items") or []
        return items[0] if items else {}

    @staticmethod
    def apply_url(host: str, site: str, jid: str) -> str:
        return f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}"

    def _raw(self, company: str, host: str, site: str, r: dict, info: dict | None) -> RawJob:
        desc = None
        if info:
            desc = html_to_text("\n".join(info.get(k) or "" for k in (
                "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")))
        elif info is not None:
            desc = ""
        locs = [r.get("PrimaryLocation"), *[x.get("Name") for x in r.get("secondaryLocations") or []]]
        return RawJob(
            company=company,
            job_id=str(r["Id"]),
            title=(r.get("Title") or (info or {}).get("Title") or "").strip(),
            source=self.name,
            source_url=API.format(host=host, res="recruitingCEJobRequisitions") + f"?siteNumber={site}",
            apply_url=self.apply_url(host, site, str(r["Id"])),
            location=join_locations(*locs),
            description=desc,
            posted_date=parse_dt((info or {}).get("ExternalPostedStartDate") or r.get("PostedDate")),
            employment_type=(info or {}).get("JobSchedule"),
            canonical_key=f"oracle_cloud:{host.split('.')[0]}:{r['Id']}",
            extra={"job_family": r.get("JobFamily"), "summary": r.get("ShortDescriptionStr")},
        )

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        host, site = self._base(company)
        try:
            first = self._search(host, site, "software", 0, None, limit=1)   # an empty keyword returns no facets
        except SourceUnavailable as e:
            raise SourceUnavailable(e.kind, f"{company.name}: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {host} {site}: {e}") from e
        us = next((str(f["Id"]) for f in first.get("locationsFacet") or []
                   if (f.get("Name") or "").strip().lower() in ("united states", "united states of america")), None)
        max_pages = FULL_PAGES if ctx.full else max(ctx.max_pages, 4)
        rows: dict[str, dict] = {}
        capped = []
        for q in ctx.queries.get("oracle_cloud") or DEFAULT_QUERIES:
            stale = 0
            for page in range(max_pages):
                res = self._search(host, site, q, page * PAGE, us)
                reqs = res.get("requisitionList") or []
                hit = False
                for r in reqs:
                    if r.get("Id"):
                        rows.setdefault(str(r["Id"]), r)
                        hit = hit or ctx.want(r.get("Title") or "", r.get("PrimaryLocation"))
                stale = 0 if hit else stale + 1
                if len(reqs) < PAGE or (page + 1) * PAGE >= (res.get("TotalJobsCount") or 0) or stale >= STALE_PAGES:
                    break
            else:
                capped.append(f"“{q}” capped at {max_pages * PAGE} of {res.get('TotalJobsCount')}")
        now = datetime.now(timezone.utc)
        jobs, details = [], 0
        for jid, r in rows.items():
            info: dict | None = None if jid in ctx.known else {}
            if ctx.want(r.get("Title") or "", r.get("PrimaryLocation")) and ctx.needs_detail(jid, now) \
                    and details < ctx.max_details:
                try:
                    info = self._detail(host, site, jid)
                    details += 1
                except Exception:  # noqa: BLE001 - keep the listing without a JD
                    pass
            jobs.append(self._raw(company.name, host, site, r, info))
        notes = [] if us else ["no United States location facet found; searched all locations"]
        if details >= ctx.max_details:
            notes.append(f"detail fetches capped at {ctx.max_details}; remaining fetched next run")
        return FetchResult(jobs=jobs, partial="; ".join(capped) or None, notes=notes)

    def check_open(self, company: Company, job_id: str, apply_url: str | None) -> bool | None:
        host, site = self._base(company)
        try:
            return bool(self._detail(host, site, job_id).get("Title"))
        except httpx.HTTPStatusError as e:
            return False if e.response.status_code in (404, 410) else None

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        host, site, jid = parse_url(url)
        if not (host and site and jid):
            return None
        info = self._detail(host, site, jid)
        if not info.get("Title"):
            return None
        return self._raw(company or host.split(".")[0], host, site,
                         {"Id": jid, "Title": info.get("Title"), "PrimaryLocation": info.get("PrimaryLocation")}, info)
