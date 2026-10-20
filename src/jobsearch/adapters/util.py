"""Small helpers shared by adapters."""
from __future__ import annotations

from datetime import datetime, timezone

from dateutil import parser as dateparser


def parse_dt(value) -> datetime | None:
    """ISO strings, epoch seconds/milliseconds, or None. Naive results are treated as UTC."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            v = float(value)
            dt = datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc)
        else:
            dt = dateparser.parse(str(value))
    except (ValueError, TypeError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def join_locations(*parts) -> str | None:
    out: list[str] = []
    for p in parts:
        if isinstance(p, (list, tuple)):
            out.extend(str(x) for x in p if x)
        elif p:
            out.append(str(p))
    return " | ".join(dict.fromkeys(x.strip() for x in out if x and x.strip())) or None


# Sites whose terms forbid automated access (docs/experimental_big_tech.md) or that we never scrape (LinkedIn): their
# pages are never fetched; a job there is added with a pasted JD instead.
NO_FETCH_HOSTS = ("linkedin.com", "apple.com", "metacareers.com", "facebook.com", "meta.com")
# Readable in principle but bot-blocked or JavaScript-only (company_aliases.yaml manual notes): not worth a request each
# run to enrich a SimplifyJobs listing that links there
UNREADABLE_HOSTS = ("tesla.com", "lifeattiktok.com", "joinbytedance.com", "google.com", "uber.com", "citadel.com",
                    "citadelsecurities.com", "ibm.com", "mathworks.com", "shopify.com")


def _host_in(url: str, hosts) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def no_fetch(url: str) -> bool:
    return _host_in(url, NO_FETCH_HOSTS)


def unreadable(url: str) -> bool:
    return _host_in(url, NO_FETCH_HOSTS + UNREADABLE_HOSTS)
