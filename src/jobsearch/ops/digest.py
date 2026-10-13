"""Weekly digest: a static HTML page in data.nosync/digests/YYYY-MM-DD.html (new Strong matches, applications sent,
follow-ups due, top skill gaps, outcome stats, hiring seasons coming up). Written on the first run of
`notifications.digest_weekday` (default Sunday) or with `--digest`. All text is escaped; no scripts, no network."""
from __future__ import annotations

import html
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..output.dashboard import skill_gaps
from ..storage import db
from ..tracker import applications as A
from ..tracker import statuses as S

TRACKS = {"coop_intern": "Co-op / Internships", "full_time": "Full-time"}
CSS = """body{font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:860px;margin:32px auto;padding:0 16px;
color:#1c1c1e;background:#f6f6f4}h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.card{background:#fff;border:1px solid #e3e3e0;border-radius:12px;padding:12px 16px;margin:8px 0}table{border-collapse:collapse;width:100%}
td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #eee;font-size:14px}.muted{color:#66666d}.n{font-weight:700;font-size:22px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}a{color:#3157d5}
@media (prefers-color-scheme: dark){body{background:#131315;color:#ececef}.card{background:#1c1c1f;border-color:#2e2e33}
td,th{border-color:#2e2e33}.muted{color:#9898a1}a{color:#8aa6ff}}"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _short(s: str, n: int = 110) -> str:
    return s if len(s) <= n else s[:n].rsplit(" · ", 1)[0] + " · …"


def build(conn, today: date, seasons: list[dict] | None = None, window: tuple[int, int] = (7, 10),
          remind_days: int = 7, companies: set[str] | None = None) -> str:
    since = (datetime.combine(today, datetime.min.time(), timezone.utc) - timedelta(days=7)).isoformat()
    jobs = db.open_jobs(conn)
    strong = [j for j in jobs if not j.get("filtered") and j.get("label") == "Strong" and j["first_seen"] >= since]
    apps = S.applications(conn, include_deleted=False, with_jd=False)
    sent = [a for a in apps if (a.get("applied_date") or "") >= (today - timedelta(days=7)).isoformat()]
    due = [(a, f) for a in apps if (f := A.follow_up(a, today, window)) and f["state"] != "upcoming"]
    gaps = skill_gaps([j for j in jobs if not j.get("filtered")], datetime.now(timezone.utc))
    parts = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
             f"<title>HireScope weekly digest {today}</title><style>{CSS}</style></head><body>",
             f"<h1>Weekly digest</h1><div class=muted>Week ending {today:%A %b %d, %Y} · generated locally by HireScope</div>",
             "<div class='tiles' style='margin-top:16px'>"]
    for label, n in (("Strong matches first seen this week", len(strong)), ("Applications sent", len(sent)),
                     ("Follow-ups due", len(due)), ("Open applications", sum(a["stage"] in A.ACTIVE for a in apps))):
        parts.append(f"<div class=card><div class=n>{n}</div><div class=muted>{_e(label)}</div></div>")
    parts.append("</div>")
    for t, name in TRACKS.items():
        rows = sorted((j for j in strong if (j.get("track_override") or j.get("track")) in (t, "unclear")),
                      key=lambda j: -(j.get("score") or 0))[:15]
        parts.append(f"<h2>New Strong matches — {_e(name)} ({len(rows)})</h2><div class=card><table>")
        for j in rows:
            url = j.get("apply_url") or j.get("source_url") or ""
            link = f"<a href='{_e(url)}'>{_e(j['title'])}</a>" if url.startswith("http") else _e(j["title"])
            parts.append(f"<tr><td>{round(j.get('score') or 0)}</td><td>{link}<div class=muted>{_e(j['company'])} · "
                         f"{_e(_short(j.get('location') or ''))}</div></td></tr>")
        parts.append("</table>" + ("" if rows else "<span class=muted>None this week.</span>") + "</div>")
    parts.append(f"<h2>Follow-ups due ({len(due)})</h2><div class=card>")
    parts += [f"<div>{_e(a['company'])} — {_e(a['title'])} <span class=muted>· {f['state']} since {f['due']}</span></div>"
              for a, f in due] or ["<span class=muted>None — you send follow-ups yourself.</span>"]
    parts.append("</div><h2>Outcome stats</h2><div class=card><table><tr><th>Track</th><th>Applications</th>"
                 "<th>Response rate</th><th>Interview rate</th><th>Offers</th><th>Median days to response</th></tr>")
    for t, name in TRACKS.items():
        st = A.track_stats([a for a in apps if a.get("track") == t], today)
        pct = lambda x: "—" if x is None else f"{x}%"  # noqa: E731
        parts.append(f"<tr><td>{_e(name)}</td><td>{st['total']}</td><td>{pct(st['response_rate'])}</td>"
                     f"<td>{pct(st['interview_rate'])}</td><td>{st['offers']}</td>"
                     f"<td>{'—' if st['median_days_to_response'] is None else st['median_days_to_response']}</td></tr>")
    parts.append("</table></div><h2>Top skill gaps (last 30 days)</h2><div class=card>")
    for t, name in TRACKS.items():
        sk = (gaps.get(f"{t}:30") or {}).get("skills", [])[:8]
        parts.append(f"<div><b>{_e(name)}:</b> " + (", ".join(f"{_e(s['skill'])} ({s['jobs']})" for s in sk) or
                                                     "<span class=muted>none</span>") + "</div>")
    parts.append("<div class=muted style='margin-top:6px'>Gaps only — never add a skill you don't have.</div></div>")
    from ..tracker import manual_checks
    todo = [m for m in manual_checks.report(conn, today, remind_days, companies) if m["state"] != "ok"]
    parts.append(f"<h2>Manual checks due ({len(todo)})</h2><div class=card>")
    for m in todo[:40]:
        when = "never checked" if m["state"] == "never" else f"last checked {m['checked_at']} ({m['days']} days ago)"
        name = (f"<a href='{_e(m['url'])}'>{_e(m['company'])}</a>" if (m.get("url") or "").startswith("http")
                else _e(m["company"]))
        parts.append(f"<div>{name} <span class=muted>· {_e(when)}</span></div>")
    if len(todo) > 40:
        parts.append(f"<div class=muted>… and {len(todo) - 40} more (dashboard → Manual Checks)</div>")
    parts.append((f"<div class=muted style='margin-top:6px'>Companies HireScope can't read; flagged when not checked in "
                  f"{remind_days} days. Mark them checked in the dashboard's Manual Checks view.</div>" if todo else
                  "<span class=muted>All manual companies checked recently.</span>") + "</div>")
    soon = [s for s in seasons or [] if s.get("remind")][:10]
    if soon:
        parts.append("<h2>Hiring seasons coming up</h2><div class=card>")
        parts += [f"<div>{_e(s['company'])} — {_e(TRACKS.get(s['track'], 'New grad') if s['track'] != 'new_grad' else 'New grad')}: "
                  f"{_e(s['status'])} <span class=muted>({_e(s['confidence'])})</span></div>" for s in soon]
        parts.append("</div>")
    parts.append("<p class=muted>Scores rank postings against your resume and preferences; they are not an interview "
                 "or hiring probability.</p></body></html>")
    return "\n".join(parts)


def due(today: date, weekday: str, out_dir: Path) -> bool:
    return today.strftime("%A").lower() == weekday.lower() and not (out_dir / f"{today}.html").exists()


def write(conn, out_dir: Path, today: date, seasons=None, window=(7, 10), remind_days: int = 7,
          companies: set[str] | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{today}.html"
    p.write_text(build(conn, today, seasons, window, remind_days, companies), encoding="utf-8")
    return p
