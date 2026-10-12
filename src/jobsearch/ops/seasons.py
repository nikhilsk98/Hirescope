"""Hiring-season tracker, from your own DB history: when each company first posted intern/co-op and new-grad roles
each year, the predicted window next year, and a reminder shortly before it.

Evidence per company and track: the earliest posting date (or first-seen date when the posting date is unknown) per
calendar year among its co-op/intern or New Grad roles (title_rules new_grad_patterns), open or closed. The prediction repeats the latest observed
month next year; with one year of history it is a guess and labeled so.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from ..config import load_title_rules
from ..match.rules import is_new_grad_title


def _kind(row, rules) -> str | None:
    """New grad = the same title patterns the rules use (config/title_rules.yaml -> new_grad_patterns)."""
    t = row["track_override"] or row["track"]
    if t == "coop_intern":
        return "coop_intern"
    sig = json.loads(row["signals"]) if row["signals"] else {}
    if t == "full_time" and (sig.get("new_grad") or is_new_grad_title(row["title"] or "", rules)):
        return "new_grad"
    return None


def track_seasons(conn, today: date, companies: set[str] | None = None, rules=None,
                  remind_days: int = 21) -> list[dict]:
    """remind_days: preferences notifications.season_remind_days."""
    rules = rules or load_title_rules()
    first: dict[tuple[str, str], dict[int, str]] = {}
    open_now: set[tuple[str, str]] = set()
    for r in conn.execute("SELECT company, title, track, track_override, signals, posted_date, first_seen, job_status "
                          "FROM jobs"):
        k = _kind(r, rules)
        if not k or (companies is not None and r["company"] not in companies):
            continue
        d = (r["posted_date"] or r["first_seen"] or "")[:10]
        if not d:
            continue
        y = int(d[:4])
        cur = first.setdefault((r["company"], k), {})
        if y not in cur or d < cur[y]:
            cur[y] = d
        if r["job_status"] != "closed":
            open_now.add((r["company"], k))
    out = []
    for (company, k), years in first.items():
        last_year = max(years)
        last = date.fromisoformat(years[last_year])
        nxt = last.replace(year=last.year + 1) if not (last.month == 2 and last.day == 29) else date(last.year + 1, 3, 1)
        while nxt < today - timedelta(days=60):   # long past: roll forward to the next occurrence
            nxt = nxt.replace(year=nxt.year + 1)
        days = (nxt - today).days
        status = ("open now" if (company, k) in open_now else
                  f"expected in {days} days" if 0 <= days <= remind_days else
                  f"expected around {nxt:%b %Y}")
        out.append({"company": company, "track": k, "years": {str(y): v for y, v in sorted(years.items())},
                    "predicted": nxt.isoformat()[:7], "days_until": days, "open_now": (company, k) in open_now,
                    "status": status, "confidence": "repeating pattern" if len(years) >= 2 else "one year of history",
                    "remind": (company, k) not in open_now and 0 <= days <= remind_days})
    out.sort(key=lambda s: (not s["remind"], s["days_until"] if s["days_until"] >= 0 else 999, s["company"]))
    return out
