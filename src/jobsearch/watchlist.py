"""inputs/my_companies.csv: company, careers_url (optional), notes (optional).

Every row is checked on every run, regardless of tier, with full pagination. Blank lines and lines
starting with '#' are ignored; duplicate company names keep the first row.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WatchItem:
    company: str
    careers_url: str | None = None
    notes: str | None = None


class WatchlistError(ValueError):
    pass


def parse_watchlist(text: str) -> list[WatchItem]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return []
    rows = list(csv.reader(io.StringIO("\n".join(lines))))
    header = [h.strip().lower() for h in rows[0]]
    if "company" in header:
        idx = {name: header.index(name) for name in ("company", "careers_url", "notes") if name in header}
        rows = rows[1:]
    else:  # no header: positional company, careers_url, notes
        idx = {"company": 0, "careers_url": 1, "notes": 2}
    out: dict[str, WatchItem] = {}
    for n, row in enumerate(rows, start=2):
        def col(name):
            i = idx.get(name)
            return row[i].strip() if i is not None and i < len(row) and row[i].strip() else None
        name = col("company")
        if not name:
            raise WatchlistError(f"my_companies.csv line {n}: missing company name")
        url = col("careers_url")
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        out.setdefault(name.casefold(), WatchItem(name, url, col("notes")))
    return list(out.values())


def load_watchlist(path: Path) -> list[WatchItem]:
    return parse_watchlist(path.read_text(encoding="utf-8-sig")) if path.exists() else []
