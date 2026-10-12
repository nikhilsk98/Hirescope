"""After-run alerts: new Strong matches (with early birds — posted within `early_bird_hours`), reopened roles you had
shortlisted, broken-looking sources. One macOS notification per kind; the same facts go into the run summary."""
from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

from . import notify

PROBE_HOSTS = (("boards-api.greenhouse.io", 443), ("raw.githubusercontent.com", 443))


def online(timeout: float = 5.0, hosts=PROBE_HOSTS) -> bool:
    for host, port in hosts:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def new_strong(conn, since: str, early_hours: int = 48) -> tuple[list[dict], list[dict]]:
    """(new Strong matches first seen since `since`, the subset posted within early_hours)."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id, company, title, posted_date, score, COALESCE(track_override, track) AS track FROM jobs "
        "WHERE first_seen >= ? AND filtered = 0 AND label = 'Strong' AND job_status != 'closed' ORDER BY score DESC",
        (since,))]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=early_hours)).isoformat()
    early = [r for r in rows if r["posted_date"] and r["posted_date"] >= cutoff]
    return rows, early


def send(summary: dict, early_hours: int = 48) -> list[str]:
    """Notifications for a finished run. Returns the titles sent (or that would be sent)."""
    out = []
    strong, early = summary.get("new_strong_rows") or [], summary.get("early_bird") or []
    if strong:
        top = "; ".join(f"{r['company']} — {r['title']}" for r in (early or strong)[:3])
        title = f"HireScope: {len(strong)} new Strong match{'es' if len(strong) > 1 else ''}"
        sub = f"{len(early)} early bird{'s' if len(early) != 1 else ''} (posted < {early_hours}h)" if early else None
        notify.notify(title, top, sub)
        out.append(title)
    if summary.get("reopened"):
        title = f"HireScope: {len(summary['reopened'])} shortlisted role(s) reopened"
        notify.notify(title, "; ".join(summary["reopened"][:3]))
        out.append(title)
    if summary.get("health_alerts"):
        title = f"HireScope: {len(summary['health_alerts'])} source(s) look broken"
        notify.notify(title, "; ".join(a["message"] for a in summary["health_alerts"][:2]), "See Coverage")
        out.append(title)
    return out


def summary_text(s: dict) -> str:
    """Human-readable run summary (also written to data/logs/last_run_summary.txt)."""
    lines = [f"HireScope run ({s.get('scope', 'full')}) finished {datetime.now().astimezone():%Y-%m-%d %H:%M}",
             f"  coverage: {s.get('sources_ok', 0)} of {s.get('sources_checked', 0)} sources OK · "
             f"{s.get('sources_failed', 0)} failed · {s.get('sources_manual', 0)} manual · "
             f"{s.get('sources_partial', 0)} partial · {s.get('sources_skipped', 0)} not due/off",
             f"  jobs: {s.get('jobs_found', 0):,} listed · {s.get('new', 0)} new · {s.get('updated', 0)} updated · "
             f"{s.get('closed', 0)} closed · {s.get('scored', 0):,} visible · {s.get('strong', 0):,} Strong"]
    for t, b in (s.get("by_track") or {}).items():
        lines.append(f"  {t}: {b.get('visible', 0):,} visible ({b.get('strong', 0)} Strong, {b.get('stretch', 0)} Stretch)")
    if s.get("new_strong_rows"):
        lines.append(f"  new Strong matches: {len(s['new_strong_rows'])} ({len(s.get('early_bird') or [])} early birds)")
        lines += [f"    {r['score']:.0f}  {r['company']} — {r['title']}" for r in s["new_strong_rows"][:10]]
    for a in s.get("health_alerts") or []:
        lines.append(f"  HEALTH: {a['message']}")
    for r in s.get("reopened") or []:
        lines.append(f"  REOPENED: {r}")
    for f in (s.get("failures") or [])[:10]:
        lines.append(f"  failed: {f['company']} ({f['source']})")
    if s.get("backup"):
        lines.append(f"  backup: {s['backup']}")
    if s.get("digest"):
        lines.append(f"  weekly digest: {s['digest']}")
    if s.get("excel"):
        lines.append(f"  excel: {s['excel']}")
    return "\n".join(lines)
