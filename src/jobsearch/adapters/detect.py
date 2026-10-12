"""ATS auto-detection for My Companies.

Fallback order (first hit wins; each step records how it decided):
  1. cached detection in SQLite (unless it failed twice in a row)       -> "cache"
  2. careers_url is itself an ATS / known careers URL                    -> "careers_url"
  3. company is in the verified seed list (config/companies.yaml)        -> "seed list"
  4. company appears in the SimplifyJobs-derived ATS directory           -> "simplify directory"
  5. careers_url page links to / embeds an ATS board                     -> "careers page"
  6. company-name slug probes: Greenhouse, Lever, Ashby, SmartRecruiters -> "name probe"
  7. careers_url page carries schema.org JobPosting JSON-LD              -> "json-ld"
  8. nothing works                                                       -> "manual" (never dropped)
Verified 2026-10-12: unknown Greenhouse / Lever / Ashby boards return 404; unknown SmartRecruiters
ids return 200 with totalFound 0, so a SmartRecruiters probe needs at least one posting.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from ..config import Company
from .base import HttpClient
from .jsonld import HTML_ACCEPT, jobpostings_in_html

log = logging.getLogger(__name__)


@dataclass
class Detection:
    company: Company | None     # ready-to-fetch company config; None -> manual
    via: str
    detail: str = ""


def from_url(url: str | None) -> dict | None:
    """ATS fields from a careers/board/posting URL, or None."""
    if not url:
        return None
    u = urlparse(url if "://" in url else "https://" + url)
    host, path = (u.hostname or "").lower(), u.path
    first = next((p for p in path.split("/") if p), None)
    if "greenhouse.io" in host:
        if m := re.search(r"[?&]for=([A-Za-z0-9_-]+)", url):
            return {"source": "greenhouse", "board": m.group(1)}
        if host.startswith("boards-api"):
            m = re.search(r"/boards/([A-Za-z0-9_-]+)", path)
            return {"source": "greenhouse", "board": m.group(1)} if m else None
        if first and first not in ("embed", "v1"):
            return {"source": "greenhouse", "board": first}
    if host == "jobs.lever.co" and first:
        return {"source": "lever", "board": first}
    if host == "api.lever.co" and (m := re.search(r"/postings/([^/?]+)", path)):
        return {"source": "lever", "board": m.group(1)}
    if host == "jobs.ashbyhq.com" and first:
        return {"source": "ashby", "board": first}
    if host.endswith("myworkdayjobs.com"):
        segs = [p for p in path.split("/") if p]
        if segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
            segs = segs[1:]
        if segs and segs[0] == "wday" and len(segs) >= 4:  # /wday/cxs/{tenant}/{site}/...
            return {"source": "workday", "host": host, "board": segs[2], "site": segs[3]}
        if segs:
            return {"source": "workday", "host": host, "board": host.split(".")[0], "site": segs[0]}
    if "smartrecruiters.com" in host and first:
        if host.startswith("api.") and (m := re.search(r"/companies/([^/]+)", path)):
            return {"source": "smartrecruiters", "board": m.group(1)}
        return {"source": "smartrecruiters", "board": first}
    if host.endswith("amazon.jobs"):
        return {"source": "amazon"}
    if host.endswith("jobs.netflix.net") or host == "jobs.netflix.com":
        return {"source": "netflix"}
    return None


_EMBED = [
    re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=|embed/job_app\?for=)?([A-Za-z0-9_-]+)"),
    re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)"),
    re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    re.compile(r"[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/[A-Za-z0-9_/-]+"),
    re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/[A-Za-z0-9_-]+"),
]


def slugs(name: str) -> list[str]:
    base = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology|labs|group|holdings)\b\.?",
                  "", name.lower())
    words = re.findall(r"[a-z0-9]+", base)
    full = re.findall(r"[a-z0-9]+", name.lower())
    out = ["".join(words), "-".join(words), "".join(full), "-".join(full)]
    return [s for s in dict.fromkeys(out) if s]


class Detector:
    def __init__(self, http: HttpClient, seed: list[Company], directory: dict[str, dict] | None = None):
        self.http = http
        self.seed = {c.name.casefold(): c for c in seed}
        self.directory = {k.casefold(): v for k, v in (directory or {}).items()}

    def _make(self, name: str, fields: dict, careers_url: str | None, tier: int = 1) -> Company:
        return Company(name=name, tier=tier, careers_url=careers_url, origin="my_companies",
                       **{k: v for k, v in fields.items() if k in ("source", "board", "host", "site")})

    def _probe(self, name: str) -> dict | None:
        for slug in slugs(name):
            for source, url in (("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"),
                                ("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1"),
                                ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}")):
                try:
                    self.http.get(url)
                    return {"source": source, "board": slug}
                except Exception:  # noqa: BLE001 - 404 = not this ATS
                    continue
            try:
                d = self.http.get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1")
                if d.get("totalFound"):
                    return {"source": "smartrecruiters", "board": slug}
            except Exception:  # noqa: BLE001
                pass
        return None

    def detect(self, name: str, careers_url: str | None = None, cached: dict | None = None) -> Detection:
        if cached and cached.get("fail_count", 0) < 2:
            return Detection(self._make(name, cached, careers_url), "cache", cached.get("detected_via", ""))
        if fields := from_url(careers_url):
            return Detection(self._make(name, fields, careers_url), "careers_url")
        if seed := self.seed.get(name.casefold()):
            return Detection(seed.model_copy(update={"origin": "my_companies", "tier": 1,
                                                     "careers_url": careers_url or seed.careers_url}), "seed list")
        if fields := self.directory.get(name.casefold()):
            return Detection(self._make(name, fields, careers_url), "simplify directory")
        html = None
        if careers_url:
            try:
                if self.http.allowed(careers_url):
                    html = self.http.get(careers_url, headers=HTML_ACCEPT).text
            except Exception as e:  # noqa: BLE001
                log.info("careers page fetch failed for %s: %s", name, e)
            if html:
                for pat in _EMBED:
                    if m := pat.search(html):
                        hit = m.group(0)
                        if fields := from_url("https://" + hit if not hit.startswith("http") else hit):
                            return Detection(self._make(name, fields, careers_url), "careers page", hit)
        if fields := self._probe(name):
            return Detection(self._make(name, fields, careers_url), "name probe", fields["board"])
        if html and jobpostings_in_html(html):
            return Detection(self._make(name, {"source": "jsonld"}, careers_url), "json-ld")
        if careers_url:
            # job links might still carry JSON-LD; the jsonld adapter follows them and reports if not
            return Detection(self._make(name, {"source": "jsonld"}, careers_url), "json-ld (links)")
        return Detection(None, "manual", "no careers_url and no ATS found for the company name")


def directory_from_simplify(listings: list[dict]) -> dict[str, dict]:
    """company name -> ATS fields, from the employer URLs in SimplifyJobs listings (most common wins)."""
    from collections import Counter
    votes: dict[str, Counter] = {}
    for x in listings:
        name = (x.get("company_name") or "").strip()
        fields = from_url(x.get("url"))
        if name and fields and fields["source"] in ("greenhouse", "lever", "ashby", "workday", "smartrecruiters"):
            votes.setdefault(name, Counter())[tuple(sorted(fields.items()))] += 1
    return {name: dict(c.most_common(1)[0][0]) for name, c in votes.items()}
