"""ATS auto-detection for My Companies.

Fallback order (first hit wins; each step records how it decided):
  1. careers_url is itself an ATS / known careers URL                    -> "careers_url"
  2. cached detection in SQLite (unless it failed twice in a row or     -> "cache"
     careers_url changed)
  3. company is in the verified seed list (config/companies.yaml)        -> "seed list"
  4. company appears in the SimplifyJobs-derived ATS directory           -> "simplify directory"
  5. careers_url page links to / embeds an ATS board                     -> "careers page"
  6. company-name slug probes: Greenhouse, Lever, Ashby, SmartRecruiters -> "name probe"
  7. the careers site's own public JSON job feed (Jibe /api/jobs)        -> "careers feed"
  8. careers_url page carries schema.org JobPosting JSON-LD              -> "json-ld"
  9. nothing works                                                       -> "manual" (never dropped)
Verified 2026-10-12: unknown Greenhouse / Lever / Ashby boards return 404; unknown SmartRecruiters
ids return 200 with totalFound 0, so a SmartRecruiters probe needs at least one posting.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from rapidfuzz import fuzz

from ..config import Company
from ..output.h1b import norm_name
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
    if host == "ats.rippling.com":
        from .rippling import parse_url as rippling_url
        slug, _ = rippling_url(u.geturl())
        return {"source": "rippling", "board": slug} if slug else None
    if host == "jobs.jobvite.com" and first:
        segs = [p for p in path.split("/") if p]
        slug = segs[1] if first == "careers" and len(segs) > 1 else first
        return {"source": "jobvite", "board": slug} if not slug.startswith("__") else None
    if host.endswith(".workable.com") or host == "apply.workable.com":
        from .workable import parse_url as workable_url
        slug, _ = workable_url(u.geturl())
        return {"source": "workable", "board": slug} if slug else None
    if host.endswith(".icims.com") and not host.startswith("www."):
        return {"source": "icims", "host": host, "board": host}
    if host.endswith(".jibeapply.com"):
        return {"source": "jibe", "host": host}
    if host.endswith("amazon.jobs"):
        return {"source": "amazon"}
    if host.endswith("jobs.netflix.net") or host == "jobs.netflix.com":
        return {"source": "netflix"}
    if host.endswith("oraclecloud.com") and "/sites/" in path:      # Oracle Recruiting Cloud career site
        from .oracle_cloud import parse_url as oracle_url
        h, site, _ = oracle_url(u.geturl())
        return {"source": "oracle_cloud", "host": h, "site": site} if site else None
    if host.endswith(".bamboohr.com") and not host.startswith("www."):
        return {"source": "bamboohr", "board": host.split(".")[0]}
    if host.endswith(".applytojob.com") and not host.startswith("www."):
        return {"source": "jazzhr", "board": host.split(".")[0]}
    if host == "recruiting.paylocity.com" and (m := re.search(r"/jobs/all/([0-9a-f-]{36})", path, re.I)):
        return {"source": "paylocity", "board": m.group(1), "careers_url": u.geturl()}
    if host.endswith(".avature.net") or (re.search(r"/(?:SearchJobs|OpenRoles|JobDetail)\b", path)
                                         and host.split(".")[0] in ("careers", "jobs")):
        return {"source": "avature", "careers_url": u.geturl()}
    domain = (parse_qs(u.query).get("domain") or [None])[0]
    if domain and (host.endswith(".eightfold.ai") or path.startswith("/careers")):   # Eightfold PCSX site
        return {"source": "eightfold", "host": host, "board": domain}
    return None


_EMBED = [
    re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=|embed/job_app\?for=)?([A-Za-z0-9_-]+)"),
    re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)"),
    re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    re.compile(r"[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/[A-Za-z0-9_/-]+"),
    re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/[A-Za-z0-9_-]+"),
    re.compile(r"ats\.rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+/jobs"),
    re.compile(r"jobs\.jobvite\.com/(?:careers/)?(?!__)[A-Za-z0-9_-]+"),
    re.compile(r"[a-z0-9-]+\.eightfold\.ai/careers\?domain=[A-Za-z0-9.-]+"),
    re.compile(r"apply\.workable\.com/(?!api/|j/)[A-Za-z0-9_-]+"),
    re.compile(r"(?<![a-z0-9-])(?:careers|jobs|globalcareers|[a-z0-9]+-careers|careers-[a-z0-9-]+)\.icims\.com"),
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
                    data = self.http.get_json(url)
                except Exception:  # noqa: BLE001 - 404 = not this ATS
                    continue
                if source == "greenhouse":
                    # a slug can belong to another company ("thrive"): check the board's own company name
                    owner = next((j.get("company_name") for j in data.get("jobs", []) if j.get("company_name")), None)
                    if owner and fuzz.token_sort_ratio(norm_name(owner), norm_name(name)) < 85:
                        log.info("probe %s/%s belongs to %r, not %r: skipped", source, slug, owner, name)
                        continue
                return {"source": source, "board": slug}
            try:
                d = self.http.get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1")
                if d.get("totalFound"):
                    return {"source": "smartrecruiters", "board": slug}
            except Exception:  # noqa: BLE001
                pass
        return None

    def _own_feed(self, careers_url: str) -> dict | None:
        """The careers site's own public JSON job feed (Jibe sites: /api/jobs), when robots.txt allows it."""
        from .jibe import feed_url
        host = (urlparse(careers_url).hostname or "").lower()
        if not host:
            return None
        url = feed_url(host, 1, limit=1)
        try:
            if not self.http.allowed(url):
                return None
            d = self.http.get_json(url)
        except Exception:  # noqa: BLE001 - no feed here
            return None
        if isinstance(d, dict) and isinstance(d.get("jobs"), list) and "totalCount" in d:
            return {"source": "jibe", "host": host}
        return None

    def _lookup(self, table: dict, name: str):
        """Exact (casefolded) match, then legal-suffix-insensitive match ("Akamai" ~ "Akamai Technologies")."""
        if hit := table.get(name.casefold()):
            return hit
        n = norm_name(name)
        if not n:
            return None
        for k, v in table.items():
            if norm_name(k) == n:
                return v
        return None

    def detect(self, name: str, careers_url: str | None = None, cached: dict | None = None,
               manual_reason: str | None = None) -> Detection:
        if manual_reason:
            return Detection(None, "manual", manual_reason)
        # a careers_url that is itself a board is authoritative (and free), so it beats a cached guess
        if fields := from_url(careers_url):
            return Detection(self._make(name, fields, careers_url), "careers_url")
        # a cached detection is reused until it fails twice, or until the careers_url you gave changes
        if cached and cached.get("fail_count", 0) < 2 and (not careers_url or not cached.get("careers_url")
                                                           or cached["careers_url"] == careers_url):
            return Detection(self._make(name, cached, careers_url), "cache", cached.get("detected_via", ""))
        if seed := self._lookup(self.seed, name):
            return Detection(seed.model_copy(update={"origin": "my_companies", "tier": 1,
                                                     "careers_url": careers_url or seed.careers_url}), "seed list")
        if fields := self._lookup(self.directory, name):
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
        if careers_url and (fields := self._own_feed(careers_url)):
            return Detection(self._make(name, fields, careers_url), "careers feed", fields["host"])
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
