"""inputs/companies/my_companies.csv: company, careers_url (optional), notes (optional).

Every row is checked on every run, regardless of tier, with full pagination. Blank lines and lines
starting with '#' are ignored; duplicate company names keep the first row.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass
class WatchItem:
    company: str
    careers_url: str | None = None
    notes: str | None = None
    names: list[str] | None = None        # original CSV names merged into this item
    manual_reason: str | None = None      # set when the name can't be mapped to a current employer


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


@lru_cache(maxsize=4)
def load_aliases(path: Path) -> tuple[dict[str, str], dict[str, str], tuple[str, ...]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    key = lambda s: " ".join(str(s).casefold().split())  # noqa: E731
    return ({key(k): str(v) for k, v in (raw.get("aliases") or {}).items()},
            {key(k): str(v) for k, v in (raw.get("manual") or {}).items()},
            tuple(raw.get("legal_suffixes") or []))


def brand_name(name: str, aliases: dict[str, str], suffixes: tuple[str, ...]) -> str:
    """'Toast, Inc.' -> 'Toast'; aliases first ('ITA Software LLC' -> 'Google'), then legal-suffix stripping."""
    k = " ".join(name.casefold().split())
    if k in aliases:
        return aliases[k]
    out = re.sub(r"\s*\([^)]*\)", "", name).strip()          # "(formerly ...)", "(check ...)"
    if " / " in out:
        out = out.split(" / ")[-1].strip()                   # "Legal Name / Brand"
    if suffixes:
        pat = re.compile(rf"[,\s]+(?:{'|'.join(re.escape(x) for x in suffixes)})\.?$", re.I)
        for _ in range(3):
            new = pat.sub("", out).strip(" ,")
            if new == out:
                break
            out = new
    return out or name


def resolve(items: list[WatchItem], aliases_path: Path) -> list[WatchItem]:
    """Map legal names to current brands and merge rows that share a brand (keeping notes and URLs)."""
    aliases, manual, suffixes = load_aliases(aliases_path)
    merged: dict[str, WatchItem] = {}
    for it in items:
        k = " ".join(it.company.casefold().split())
        reason = manual.get(k)
        brand = it.company if reason else brand_name(it.company, aliases, suffixes)
        reason = reason or manual.get(" ".join(brand.casefold().split()))   # manual by brand ("Google")
        cur = merged.get(brand.casefold())
        if cur is None:
            merged[brand.casefold()] = WatchItem(brand, it.careers_url, it.notes, [it.company], reason)
        else:
            cur.names.append(it.company)
            cur.careers_url = cur.careers_url or it.careers_url
            cur.manual_reason = cur.manual_reason or reason
            if it.notes and it.notes not in (cur.notes or ""):
                cur.notes = f"{cur.notes}; {it.notes}" if cur.notes else it.notes
    return list(merged.values())
