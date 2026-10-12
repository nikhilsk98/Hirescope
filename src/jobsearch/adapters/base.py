"""Adapter contract and a polite, retrying HTTP client shared by all adapters."""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

import httpx

from ..config import Company

log = logging.getLogger(__name__)

USER_AGENT = "jobsearch-local/0.1 (personal job search tool; single user; contact via repo owner)"


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
    description: str = ""          # plain text, already de-HTMLed
    posted_date: datetime | None = None
    updated_at: datetime | None = None
    employment_type: str | None = None  # only when the source states it
    extra: dict = field(default_factory=dict)


class SourceError(Exception):
    """A source could not be fetched. Logged and isolated; never fatal to a run."""


@dataclass(frozen=True)
class Capabilities:
    full_description: bool        # list endpoint returns full JD text
    posted_date: bool             # source exposes a posting/first-published date
    pagination: bool
    official: bool                # an official public API from the ATS


class Adapter:
    name: ClassVar[str]
    capabilities: ClassVar[Capabilities]

    def __init__(self, client: "HttpClient"):
        self.http = client

    def fetch(self, company: Company, since: datetime | None = None) -> list[RawJob]:
        raise NotImplementedError


class HttpClient:
    """httpx wrapper: timeouts, exponential backoff with jitter, per-host spacing."""

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, timeout: float = 20.0, retries: int = 3, min_interval: float = 1.0,
                 transport: httpx.BaseTransport | None = None):
        self.retries = retries
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
            transport=transport,
        )

    def _wait_for_host(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            delay = self.min_interval - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)
        self._last[host] = time.monotonic()

    def get(self, url: str, **kw) -> httpx.Response:
        host = httpx.URL(url).host
        err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait_for_host(host)
            try:
                resp = self.client.get(url, **kw)
            except httpx.TransportError as e:
                err = e
            else:
                if resp.status_code not in self.RETRY_STATUS:
                    resp.raise_for_status()
                    return resp
                err = httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            if attempt < self.retries:
                backoff = (2 ** attempt) + random.uniform(0, 0.5)
                log.warning("GET %s failed (%s); retry %d in %.1fs", url, err, attempt + 1, backoff)
                time.sleep(backoff)
        assert err is not None
        raise err

    def get_json(self, url: str, **kw):
        return self.get(url, **kw).json()

    def close(self) -> None:
        self.client.close()
