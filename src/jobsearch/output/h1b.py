"""Optional sponsorship history from a USCIS H-1B Employer Data Hub CSV that you download yourself
(https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub). Off by default.

This is COMPANY HISTORY (past petitions approved for an employer), never job-level eligibility.
Column names vary by year, so they are matched loosely: an employer column, an optional fiscal-year column,
and every column whose name contains "Approval" (denials are ignored).
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz, process

_SUFFIX = re.compile(r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|plc|lp|llp|"
                     r"holdings|group|technologies|technology|usa|us|na|the)\b\.?", re.I)


def norm_name(name: str) -> str:
    return " ".join(_SUFFIX.sub(" ", re.sub(r"[^a-z0-9 ]", " ", name.lower())).split())


def load_h1b(path: Path) -> dict[str, dict[str, int]]:
    """normalized employer name -> {fiscal year or 'all': approvals}."""
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        first = sample.splitlines()[0] if sample else ""
        delim = "\t" if first.count("\t") > first.count(",") else ","
        reader = csv.DictReader(f, delimiter=delim)
        cols = reader.fieldnames or []
        emp = next((c for c in cols if "employer" in c.lower() or "petitioner" in c.lower()), None)
        fy = next((c for c in cols if "fiscal" in c.lower() or c.strip().lower() == "year"), None)
        approvals = [c for c in cols if "approv" in c.lower()]
        if not emp or not approvals:
            raise ValueError(f"{path}: expected an employer column and approval columns, got {cols}")
        for row in reader:
            name = norm_name(row.get(emp) or "")
            if not name:
                continue
            n = 0
            for c in approvals:
                try:
                    n += int(str(row.get(c) or "0").replace(",", "") or 0)
                except ValueError:
                    pass
            out[name][str(row.get(fy) or "all").strip()] += n
    return {k: dict(v) for k, v in out.items()}


def match_companies(companies: list[str], data: dict[str, dict[str, int]], threshold: int = 92) -> dict[str, dict]:
    """company -> {"employer": matched name, "by_year": {...}, "total": n} for confident fuzzy matches only."""
    keys = list(data)
    out = {}
    for c in companies:
        n = norm_name(c)
        if not n:
            continue
        if n in data:
            hit, score = n, 100
        else:
            best = process.extractOne(n, keys, scorer=fuzz.token_sort_ratio) if keys else None
            if not best or best[1] < threshold:
                continue
            hit, score = best[0], best[1]
        by_year = data[hit]
        out[c] = {"employer": hit, "by_year": dict(sorted(by_year.items())), "total": sum(by_year.values()),
                  "match": score}
    return out
