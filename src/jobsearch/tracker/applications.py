"""Application tracker logic: stages, follow-ups, ghosted suggestions, duplicate guard, outcome stats, CSV.

The dashboard (dashboard/core.js) implements the same rules for the live page; tests/test_tracker.py runs
both against the same DEMO applications so they cannot drift apart.
"""
from __future__ import annotations

import csv
import io
import re
import statistics
from datetime import date, timedelta

from rapidfuzz import fuzz

STAGES = ("applied", "oa", "interviewing", "offer", "rejected", "ghosted", "withdrawn")
STAGE_LABELS = {"applied": "Applied", "oa": "Online Assessment", "interviewing": "Interviewing", "offer": "Offer",
                "rejected": "Rejected", "ghosted": "Ghosted", "withdrawn": "Withdrawn"}
ACTIVE = ("applied", "oa", "interviewing", "offer")
RESPONSES = ("oa", "interviewing", "offer", "rejected")   # the company answered
NEAR_DUPLICATE = 85   # default only; callers pass preferences applications.near_duplicate_ratio
CSV_COLUMNS = ("company", "title", "track", "stage", "applied_date", "days_since_applied", "next_step",
               "next_step_date", "follow_up", "contact", "referral", "source", "link", "package", "notes")


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def follow_up(app: dict, today: date, window: tuple[int, int] = (7, 10)) -> dict | None:
    """Reminder for an application still at "Applied": due `window[0]` days after applying (or after your last
    follow-up), overdue after `window[1]`. You send follow-ups yourself."""
    if app.get("deleted") or app.get("stage") != "applied":
        return None
    base = _d(app.get("followed_up")) or _d(app.get("applied_date"))
    if base is None:
        return None
    due, late = base + timedelta(days=window[0]), base + timedelta(days=window[1])
    state = "upcoming" if today < due else "due" if today <= late else "overdue"
    return {"due": due.isoformat(), "state": state, "days": (today - base).days}


def last_activity(app: dict) -> date | None:
    days = [_d(app.get("applied_date")), _d(app.get("followed_up")),
            *(_d(e.get("at")) for e in app.get("events") or [] if e.get("kind") in ("stage", "note", "follow_up"))]
    days = [d for d in days if d]
    return max(days) if days else None


def ghost_suggestion(app: dict, today: date, after_days: int = 30) -> bool:
    """Suggest (never set) "Ghosted": still Applied / OA with no update for `after_days`."""
    if app.get("deleted") or app.get("stage") not in ("applied", "oa"):
        return False
    last = last_activity(app)
    return last is not None and (today - last).days >= after_days


def _norm_title(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", t.lower()).split())


def duplicate_warnings(company: str, title: str, job_key: str | None, apps: list[dict], today: date,
                       window_days: int = 30, exclude_id: str | None = None,
                       near_ratio: int = NEAR_DUPLICATE) -> list[dict]:
    """Warn before applying again: the same posting, a near-duplicate role, or other recent applications at the
    same company. Returns [{kind: same_job|near_duplicate|same_company, app_id, message}]."""
    out = []
    ck = " ".join(company.casefold().split())
    recent = []
    for a in apps:
        if a.get("deleted") or a.get("id") == exclude_id:
            continue
        if job_key and a.get("job_key") == job_key:
            out.append({"kind": "same_job", "app_id": a["id"],
                        "message": f"You already applied to this posting on {a.get('applied_date') or '?'}"})
            continue
        if " ".join((a.get("company") or "").casefold().split()) != ck:
            continue
        sim = fuzz.token_sort_ratio(_norm_title(a.get("title") or ""), _norm_title(title))
        if sim >= near_ratio:
            out.append({"kind": "near_duplicate", "app_id": a["id"],
                        "message": f"Similar role at {a['company']}: “{a['title']}” (applied "
                                   f"{a.get('applied_date') or '?'})"})
            continue
        d = _d(a.get("applied_date"))
        if d and (today - d).days <= window_days:
            recent.append(a)
    if recent:
        out.append({"kind": "same_company", "app_id": recent[0]["id"],
                    "message": f"{len(recent)} other application{'s' if len(recent) > 1 else ''} at {company} in the "
                               f"last {window_days} days: " + "; ".join(f"“{a['title']}”" for a in recent[:3])})
    return out


def first_response(app: dict) -> tuple[str, date] | None:
    for e in sorted(app.get("events") or [], key=lambda e: e.get("at") or ""):
        if e.get("kind") == "stage" and e.get("to") in RESPONSES and _d(e.get("at")):
            return e["to"], _d(e["at"])
    if app.get("stage") in RESPONSES:          # stage set without a logged event (e.g. added late)
        return app["stage"], None
    return None


def furthest(app: dict) -> str:
    """Outcome bucket: no response / OA / interview / offer / rejected (furthest stage reached)."""
    reached = {e.get("to") for e in app.get("events") or [] if e.get("kind") == "stage"} | {app.get("stage")}
    for s, label in (("offer", "offer"), ("interviewing", "interview"), ("oa", "OA")):
        if s in reached:
            return label
    return "rejected" if "rejected" in reached else "no response"


def track_stats(apps: list[dict], today: date, weeks: int = 12) -> dict:
    live = [a for a in apps if not a.get("deleted")]
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)   # Monday, `weeks` ago
    per_week = [0] * weeks
    for a in live:
        d = _d(a.get("applied_date"))
        if d and d >= start:
            per_week[min(weeks - 1, (d - start).days // 7)] += 1
    responded = [a for a in live if first_response(a)]
    outcomes = [furthest(a) for a in live]
    days = []
    for a in responded:
        fr, applied = first_response(a), _d(a.get("applied_date"))
        if fr and fr[1] and applied:
            days.append(max(0, (fr[1] - applied).days))
    n = len(live)

    def rate(k):
        return round(100 * k / n, 1) if n else None
    return {
        "total": n,
        "active": sum(1 for a in live if a.get("stage") in ACTIVE),
        "per_week": per_week, "week_start": start.isoformat(),
        "response_rate": rate(len(responded)),
        "oa_rate": rate(sum(1 for o in outcomes if o in ("OA", "interview", "offer"))),
        "interview_rate": rate(sum(1 for o in outcomes if o in ("interview", "offer"))),
        "offers": sum(1 for o in outcomes if o == "offer"),
        "median_days_to_response": statistics.median(days) if days else None,
    }


def outcome_breakdown(apps: list[dict], dim) -> list[dict]:
    """Response rates grouped by `dim(app)` (source, company tier, role type, score label)."""
    groups: dict[str, list] = {}
    for a in apps:
        if not a.get("deleted"):
            groups.setdefault(str(dim(a) or "Unknown"), []).append(furthest(a))
    rows = []
    for k, outs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        n = len(outs)
        rows.append({"group": k, "n": n, "responses": sum(o != "no response" for o in outs),
                     "response_rate": round(100 * sum(o != "no response" for o in outs) / n, 1),
                     "interviews": sum(o in ("interview", "offer") for o in outs),
                     "offers": outs.count("offer")})
    return rows


def to_csv(apps: list[dict], today: date, window: tuple[int, int] = (7, 10)) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for a in sorted((a for a in apps if not a.get("deleted")), key=lambda a: a.get("applied_date") or ""):
        d = _d(a.get("applied_date"))
        fu = follow_up(a, today, window)
        row = {**a, "stage": STAGE_LABELS.get(a.get("stage"), a.get("stage")),
               "days_since_applied": (today - d).days if d else "",
               "follow_up": f"{fu['state']} {fu['due']}" if fu else ""}
        # neutralize spreadsheet formulas in free text (=, +, -, @ at the start of a cell)
        w.writerow([("'" + v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v
                    for v in (row.get(c, "") if row.get(c) is not None else "" for c in CSV_COLUMNS)])
    return buf.getvalue()
