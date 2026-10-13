"""Referral finder: match your LinkedIn connections export (inputs/linkedin/Connections.csv, from LinkedIn's official
"Get a copy of your data" download) to companies on the dashboard. Never scrapes LinkedIn; never contacts anyone.

Matching: the connection's company and the job's company are reduced to their brand (config/company_aliases.yaml,
legal suffixes stripped) and compared exactly, with a small typo tolerance. A connection whose company only
*starts with* the job's company ("Google Cloud" for Google) is listed separately as a possible match.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from rapidfuzz import fuzz

from ..watchlist import brand_name, load_aliases

TYPO_RATIO = 94


def parse_connections(text: str) -> list[dict]:
    """LinkedIn's CSV starts with a few 'Notes:' lines before the header row."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if "First Name" in ln and "Company" in ln), None)
    if start is None:
        raise ValueError("no 'First Name … Company' header row: is this LinkedIn's Connections.csv?")
    out = []
    for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        company = (r.get("Company") or "").strip()
        name = " ".join(x for x in ((r.get("First Name") or "").strip(), (r.get("Last Name") or "").strip()) if x)
        if company and name:
            out.append({"name": name, "company": company, "position": (r.get("Position") or "").strip(),
                        "url": (r.get("URL") or "").strip(), "connected_on": (r.get("Connected On") or "").strip()})
    return out


def _key(name: str, aliases, suffixes) -> str:
    b = brand_name(name, aliases, suffixes)
    return " ".join(re.sub(r"[^a-z0-9&+]+", " ", b.casefold()).split())


def match(companies: list[str], connections: list[dict], aliases_path: Path) -> dict[str, dict]:
    """company -> {"people": [...], "possible": [...]} for companies with at least one connection."""
    aliases, _, suffixes = load_aliases(aliases_path)
    by_key: dict[str, list[dict]] = {}
    for c in connections:
        by_key.setdefault(_key(c["company"], aliases, suffixes), []).append(c)
    out = {}
    for company in companies:
        k = _key(company, aliases, suffixes)
        if not k:
            continue
        people, possible = [], []
        for ck, conns in by_key.items():
            if ck == k or (len(k) >= 5 and fuzz.ratio(ck, k) >= TYPO_RATIO):
                people += conns
            elif ck.startswith(k + " ") and len(k) >= 3:
                possible += conns
        if people or possible:
            strip = lambda xs: [{f: x[f] for f in ("name", "position", "company", "url")} for x in xs]  # noqa: E731
            out[company] = {"people": strip(people)[:25], "possible": strip(possible)[:10],
                            "count": len(people), "possible_count": len(possible)}
    return out


def load(path: Path) -> list[dict]:
    return parse_connections(path.read_text(encoding="utf-8-sig")) if path.exists() else []
