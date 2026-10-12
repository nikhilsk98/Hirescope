"""Adapter contract and a polite, retrying, thread-safe HTTP client shared by all adapters.

Adapters never touch the database. They get a FetchContext (what we already know, how deep to page,
a cheap title/location prefilter, cached validators) and return a FetchResult; the pipeline writes.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, ClassVar

import httpx

from ..config import Company
from .robots import Robots

log = logging.getLogger(__name__)

USER_AGENT = "jobsearch-local/0.2 (personal job search tool; single user; polite rate limits)"
DETAIL_REFRESH_DAYS = 7


@dataclass
class RawJob:
    """A posting as returned by a source, before extraction/scoring."""
    company: str
    job_id: str
    title: str
    source: str
    source_url: str
    apply_url: str | None = None
    location: str | None = None
    description: str | None = ""   # plain text; None = detail not re-fetched (unchanged, keep stored JD)
    posted_date: datetime | None = None
    updated_at: datetime | None = None
    employment_type: str | None = None  # only when the source states it
    canonical_key: str | None = None
    track_hint: str | None = None       # feed-level hint, e.g. an internships-only list
    terms: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


class SourceError(Exception):
    """A source could not be fetched. Logged and isolated; never fatal to a run."""


@dataclass(frozen=True)
class Capabilities:
    full_description: bool        # list endpoint returns full JD text
    posted_date: bool             # source exposes a posting/first-published date
    pagination: bool
    official: bool                # the employer's own ATS / careers system (vs an aggregator list)
    conditional: bool = False     # supports ETag / If-None-Match
    multi_company: bool = False   # one feed lists many companies (closed detection spans the feed)


def _always(title: str, location: str | None) -> bool:
    return True


@dataclass
class FetchContext:
    since: datetime | None = None
    full: bool = True                       # full pagination (tier 1 / My Companies)
    max_pages: int = 50                     # cap when not full
    want: Callable[[str, str | None], bool] = _always   # cheap prefilter before fetching details
    known: dict[str, str | None] = field(default_factory=dict)  # job id -> detail_fetched_at ISO
    validators: dict[str, dict] = field(default_factory=dict)   # url -> {"etag","last_modified"}
    pages: dict[str, dict] = field(default_factory=dict)        # url -> {"lastmod","payload"} (page cache)
    max_details: int = 400                  # detail fetches allowed this run for this company

    def needs_detail(self, job_id: str, now: datetime | None = None) -> bool:
        ts = self.known.get(job_id)
        if job_id not in self.known or not ts:
            return True
        age = (now or datetime.now().astimezone()) - datetime.fromisoformat(ts)
        return age.days >= DETAIL_REFRESH_DAYS


@dataclass
class FetchResult:
    jobs: list[RawJob] = field(default_factory=list)
    unchanged: bool = False                 # conditional request said nothing changed
    validators: dict[str, dict] = field(default_factory=dict)  # url -> new {"etag","last_modified"}
    pages: dict[str, dict] = field(default_factory=dict)       # url -> new {"lastmod","payload"}
    partial: str | None = None              # why coverage is incomplete (page cap, detail cap...)
    seen_ids: set[str] | None = None        # all ids present at the source (if more than `jobs`)
    notes: list[str] = field(default_factory=list)
    directory: dict[str, dict] | None = None  # company -> ATS fields learned from a feed (for detection)


class Adapter:
    name: ClassVar[str]
    capabilities: ClassVar[Capabilities]

    def __init__(self, client: "HttpClient"):
        self.http = client

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        raise NotImplementedError

    def source_label(self, company: Company) -> str:
        """The `source` value stored for this company's listings (a feed may use its own label)."""
        return self.name

    def fetch_one(self, url: str) -> RawJob | None:
        """Fetch a single posting by its public URL (Add job by URL). Optional."""
        return None


class NotModified(Exception):
    pass


class HttpClient:
    """httpx wrapper: timeouts, exponential backoff with jitter, per-host spacing (thread-safe), robots.txt."""

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, timeout: float = 30.0, retries: int = 3, min_interval: float = 1.0,
                 transport: httpx.BaseTransport | None = None, host_intervals: dict[str, float] | None = None):
        self.retries = retries
        self.min_interval = min_interval
        self.host_intervals = host_intervals or {}
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()
        self._robots: dict[str, Robots | None] = {}
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
            transport=transport,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )

    def _interval(self, host: str) -> float:
        for suffix, v in self.host_intervals.items():
            if host.endswith(suffix):
                return v
        return self.min_interval

    def _wait_for_host(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next.get(host, 0.0))
            self._next[host] = slot + self._interval(host)
        if slot > now:
            time.sleep(slot - now)

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        host = httpx.URL(url).host
        err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait_for_host(host)
            try:
                resp = self.client.request(method, url, **kw)
            except httpx.TransportError as e:
                err = e
            else:
                if resp.status_code == 304:
                    raise NotModified(url)
                if resp.status_code not in self.RETRY_STATUS:
                    resp.raise_for_status()
                    return resp
                err = httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                if resp.status_code == 429 and (ra := resp.headers.get("retry-after", "")).isdigit():
                    time.sleep(min(int(ra), 60))
            if attempt < self.retries:
                backoff = (2 ** attempt) + random.uniform(0, 0.5)
                log.warning("%s %s failed (%s); retry %d in %.1fs", method, url, err, attempt + 1, backoff)
                time.sleep(backoff)
        assert err is not None
        raise err

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def get_json(self, url: str, **kw):
        return self.get(url, **kw).json()

    def post_json(self, url: str, payload: dict, **kw):
        return self.request("POST", url, json=payload, **kw).json()

    def get_conditional(self, url: str, validators: dict | None, **kw) -> httpx.Response:
        """GET with If-None-Match / If-Modified-Since. Raises NotModified on 304."""
        headers = dict(kw.pop("headers", {}) or {})
        if validators:
            if validators.get("etag"):
                headers["If-None-Match"] = validators["etag"]
            if validators.get("last_modified"):
                headers["If-Modified-Since"] = validators["last_modified"]
        return self.get(url, headers=headers, **kw)

    @staticmethod
    def validators_of(resp: httpx.Response) -> dict:
        return {"etag": resp.headers.get("etag"), "last_modified": resp.headers.get("last-modified")}

    def allowed(self, url: str) -> bool:
        """robots.txt check for HTML/page fetches (APIs are designed for programmatic access)."""
        u = httpx.URL(url)
        base = f"{u.scheme}://{u.host}"
        with self._lock:
            known = base in self._robots
            rp = self._robots.get(base)
        if not known:
            try:
                r = self.client.get(base + "/robots.txt", timeout=15)
                rp = Robots(r.text) if r.status_code < 400 else None   # no robots.txt -> allowed
            except httpx.HTTPError:
                rp = None
            with self._lock:
                self._robots[base] = rp
        path = u.raw_path.decode() or "/"
        return True if rp is None else rp.allowed(path)

    def close(self) -> None:
        self.client.close()
