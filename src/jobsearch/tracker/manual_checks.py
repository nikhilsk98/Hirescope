"""Manual checks: companies HireScope can't read (status "Manual check needed") that you check by hand.

The dashboard's Manual Checks view records the date you last checked each one ("Mark checked"); the entries travel
in Export statuses (`manual_checks`), are merged here (newer `updated_at` wins, like statuses), go back to the page
via sync.js, and the weekly digest lists the ones not checked within `manual_checks.remind_days`.
"""
from __future__ import annotations

import json
import re
from datetime import date

from ..storage import db

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HISTORY = 20


def state(checked_at: str | None, today: date, remind_days: int) -> dict:
    """{'state': 'never' | 'due' | 'ok', 'days': days since the last check or None}. Same rule as view.js."""
    if not checked_at or not _DATE.match(checked_at):
        return {"state": "never", "days": None}
    days = (today - date.fromisoformat(checked_at)).days
    return {"state": "due" if days >= remind_days else "ok", "days": days}


def merge(conn, entries: dict, exported: str, iso) -> tuple[int, list[str]]:
    """entries: {company: {checked_at, history, updated_at}} from an export. Returns (applied, errors)."""
    applied, errors = 0, []
    for company, e in (entries or {}).items():
        if not isinstance(e, dict) or not isinstance(company, str) or not company.strip() or len(company) > 200:
            continue
        checked = e.get("checked_at")
        if checked is not None and not (isinstance(checked, str) and _DATE.match(checked)):
            errors.append(f"manual check {company!r}: bad date {checked!r}")
            continue
        at = iso(e.get("updated_at"), exported, exported)
        key = db.company_key(company)
        cur = conn.execute("SELECT updated_at FROM manual_checks WHERE company_key=?", (key,)).fetchone()
        if cur and cur["updated_at"] >= at:
            continue
        hist = [d for d in (e.get("history") or []) if isinstance(d, str) and _DATE.match(d)][-HISTORY:]
        conn.execute("INSERT INTO manual_checks (company_key, company, checked_at, history, updated_at) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(company_key) DO UPDATE SET company=excluded.company, checked_at=excluded.checked_at, "
                     "history=excluded.history, updated_at=excluded.updated_at",
                     (key, company.strip(), checked, json.dumps(hist), at))
        applied += 1
    return applied, errors


def entries(conn) -> dict:
    """SQLite's copy for sync.js: {company: {checked_at, history, updated_at}}."""
    return {r["company"]: {"checked_at": r["checked_at"], "history": json.loads(r["history"] or "[]"),
                           "updated_at": r["updated_at"]}
            for r in conn.execute("SELECT * FROM manual_checks")}


def report(conn, today: date, remind_days: int, companies: set[str] | None = None) -> list[dict]:
    """Every company currently marked manual, with its last check and state; never-checked and overdue first.
    companies: the current watchlist (manual rows of companies that left it, or were renamed, are skipped)."""
    keep = {db.company_key(c) for c in companies} if companies else None
    checks = {r["company_key"]: r["checked_at"] for r in conn.execute("SELECT company_key, checked_at FROM manual_checks")}
    out = []
    # a company's newest row is its current state (an older manual row stays behind when its source changes)
    for r in conn.execute("SELECT s.company, s.url, s.error FROM source_status s WHERE s.status='manual' AND "
                          "COALESCE(s.origin, '') != 'ad_hoc' AND NOT EXISTS (SELECT 1 FROM source_status o WHERE "
                          "o.company_key = s.company_key AND o.source != s.source AND COALESCE(o.last_checked, '') > "
                          "COALESCE(s.last_checked, '')) ORDER BY s.company"):
        if keep is not None and db.company_key(r["company"]) not in keep:
            continue
        checked = checks.get(db.company_key(r["company"]))
        out.append({"company": r["company"], "url": r["url"], "reason": r["error"], "checked_at": checked,
                    **state(checked, today, remind_days)})
    order = {"never": 0, "due": 1, "ok": 2}
    return sorted(out, key=lambda x: (order[x["state"]], -(x["days"] or 0), x["company"].casefold()))
