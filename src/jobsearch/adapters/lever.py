"""Lever Postings API (public, no auth).

Verified 2026-10-12:
  GET https://api.lever.co/v0/postings/{company}?mode=json  -> [posting, ...] (all postings; ETag)
  GET https://api.lever.co/v0/postings/{company}/{id}?mode=json -> one posting
Posting: id, text (title), categories{commitment, location, allLocations, team}, workplaceType,
createdAt (ms epoch), description/descriptionBody (HTML), lists[{text, content(HTML <li>s)}],
additional (HTML), hostedUrl, applyUrl, country.
"""
from __future__ import annotations

import re

from ..config import Company
from ..extract.text import html_to_text
from .base import Adapter, Capabilities, FetchContext, FetchResult, NotModified, RawJob, SourceError
from .util import join_locations, parse_dt

API = "https://api.lever.co/v0/postings/{company}?mode=json"
ONE = "https://api.lever.co/v0/postings/{company}/{id}?mode=json"


def _description(p: dict) -> str:
    html = [p.get("description") or p.get("descriptionBody") or ""]
    for lst in p.get("lists") or []:
        html.append(f"<h3>{lst.get('text') or ''}</h3><ul>{lst.get('content') or ''}</ul>")
    html.append(p.get("additional") or "")
    return html_to_text("".join(html))


class LeverAdapter(Adapter):
    name = "lever"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=False, official=True,
                                conditional=True)

    def _job(self, company: str, p: dict, source_url: str) -> RawJob:
        cats = p.get("categories") or {}
        loc = join_locations(cats.get("allLocations") or cats.get("location"))
        if p.get("workplaceType") == "remote" and loc and "remote" not in loc.lower():
            loc = f"Remote - {loc}"
        return RawJob(
            company=company,
            job_id=p["id"],
            title=(p.get("text") or "").strip(),
            source=self.name,
            source_url=source_url,
            apply_url=p.get("hostedUrl") or p.get("applyUrl"),
            location=loc,
            description=_description(p),
            posted_date=parse_dt(p.get("createdAt")),
            employment_type=cats.get("commitment"),
            canonical_key=f"lever:{p['id']}",
            extra={"team": cats.get("team"), "workplace_type": p.get("workplaceType"), "country": p.get("country")},
        )

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        if not company.board:
            raise SourceError(f"{company.name}: lever requires 'board'")
        url = API.format(company=company.board)
        try:
            resp = self.http.get_conditional(url, ctx.validators.get(url))
        except NotModified:
            return FetchResult(unchanged=True)
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"{company.name}: {url}: {e}") from e
        data = resp.json()
        if not isinstance(data, list):
            raise SourceError(f"{company.name}: unexpected response shape from {url}")
        jobs = [self._job(company.name, p, url) for p in data if p.get("id") and p.get("text")]
        return FetchResult(jobs=jobs, validators={url: self.http.validators_of(resp)})

    def fetch_one(self, url: str, company: str | None = None) -> RawJob | None:
        m = re.search(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})", url)
        if not m:
            return None
        api = ONE.format(company=m.group(1), id=m.group(2))
        return self._job(company or m.group(1), self.http.get_json(api), api)
