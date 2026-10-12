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
