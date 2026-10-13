"""Write the dashboard data, all loadable from file:// with no fetch:

  dashboard/data.js            window.META (run summary, coverage, skill gaps, queues...) and window.JOBS
                               (compact summaries: everything cards, filters and sorting need)
  dashboard/details/d_NNN.js   per-job details (JD, package, keywords, breakdown, requirement status),
                               loaded on demand with a <script> tag when a job is opened
"""
from __future__ import annotations

import json
import math
import re
import shutil
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from ..config import ApplyToday
from .packages import package_filename, render_package

DISCLAIMER = ("Scores rank postings against your resume and preferences using local heuristics. "
              "They are not an interview or hiring probability.")
ATS_NOTE = "Coverage measures keyword overlap, not your chance of passing an ATS."
CHUNK_SIZE = 40


def local_day(iso: str, tz: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")


def linkedin_search_url(company: str, title: str) -> str:
    return "https://www.linkedin.com/jobs/search/?keywords=" + quote_plus(f"{company} {title}")


def _jd(job: dict) -> str:
    p = job.get("jd_path")
    return Path(p).read_text(encoding="utf-8") if p and Path(p).exists() else ""


def requirement_status(lines: list[str], keywords: list[dict]) -> list[list]:
    """[[line, '✓'|'✗'|'?', [keyword terms in the line]]]: ✓ every recognized keyword in the line is matched
    (or synonym-matched), ✗ at least one is missing, ? no recognizable keyword (can't tell)."""
    out = []
    for line in lines:
        low = line.lower()
        kws = [k for k in keywords if k["term"].lower() in low]
        if not kws:
            out.append([line, "?", []])
            continue
        missing = [k["term"] for k in kws if k["status"] == "missing"]
        out.append([line, "✗" if missing else "✓", [k["term"] for k in kws]])
    return out


def _badges(job: dict, today: str, latest_day: str, tz: str, mine: set[str], closing_days: int = 7) -> list[str]:
    sig = job.get("signals") or {}
    b = []
    if job["job_status"] == "updated":
        b.append("reopened" if job.get("repost_count") else "updated")
    elif local_day(job["first_seen"], tz) == latest_day:
        b.append("new")
    else:
        b.append("seen")          # listed in an earlier run and unchanged since
    fh = sig.get("fresh_hours")
    if fh is not None and fh <= 48:
        b.append("fresh")
    if job.get("deadline") and today <= job["deadline"] <= (date.fromisoformat(today) + timedelta(days=closing_days)).isoformat():
        b.append("closing")
    if sig.get("ghost"):
        b.append("ghost")
    if sig.get("new_grad"):
        b.append("newgrad")
    if sig.get("check_eligibility"):
        b.append("eligibility")
    if job.get("link_status") == "broken":
        b.append("broken")
    if (job.get("extra") or {}).get("has_jd") is False:
        b.append("nojd")
    if sig.get("meaning_match"):
        b.append("meaning")
    if job["company"].casefold() in mine:
        b.append("mine")
    return b


def build_entry(job: dict, tz: str, tier: int | None, today: str, latest_day: str, mine: set[str],
                closing_days: int = 7) -> dict:
    flags = {k: v.get("value") for k, v in (job.get("flags") or {}).items()}
    sig = job.get("signals") or {}
    e = {
        "id": job["id"],
        "company": job["company"],
        "title": job["title"],
        "job_id": job["job_id"],
        "source": job["source"],
        "track": job.get("track_override") or job.get("track") or "full_time",
        "track_auto": job.get("track"),
        "level": job.get("level") or "unknown",
        "location": job.get("location") or "Unknown",
        "work_mode": job.get("work_mode") or "unknown",
        "employment_type": job.get("employment_type") or "Not stated",
        "tier": tier,
        "posted": job["posted_date"][:10] if job.get("posted_date") else None,
        "first_seen": job["first_seen"],
        "discovered": bool(sig.get("discovered_proxy")),
        "fresh_h": sig.get("fresh_hours"),
        "day": local_day(job["first_seen"], tz),
        "job_status": job["job_status"],
        "apply_url": job.get("apply_url") or job.get("source_url"),
        "linkedin_url": linkedin_search_url(job["company"], job["title"]),
        "score": job.get("score"),
        "qual": None if job.get("qual_score") is None else round(job["qual_score"], 1),
        "label": job.get("label"),
        "filtered": bool(job.get("filtered")),
        "flags": flags,
        "badges": _badges(job, today, latest_day, tz, mine, closing_days),
        "terms": job.get("terms") or [],
        "deadline": job.get("deadline"),
        "salary": ({"min": job["salary_min"], "max": job["salary_max"], "currency": job.get("salary_currency")}
                   if job.get("salary_max") else None),
        "my_company": job["company"].casefold() in mine,
    }
    if job.get("filtered"):
        e["reasons"] = job.get("filter_reasons") or []
        return e
    kw = job.get("keywords") or {}
    ks = kw.get("keywords") or []
    if ks:
        req_missing = [k["term"] for k in ks if k["section"] == "required" and k["status"] == "missing"]
        e["ats"] = {"c": kw.get("coverage"), "rh": kw.get("required_hit"), "rt": kw.get("required_total"),
                    "m": sum(1 for k in ks if k["status"] == "matched"),
                    "s": sum(1 for k in ks if k["status"] == "synonym"),
                    "x": sum(1 for k in ks if k["status"] == "missing"), "top": req_missing[:3]}
    e["similar"] = job.get("similar") or []
    e["soft"] = job.get("filter_reasons") or []
    return e


def build_detail(job: dict, resume_hash: str) -> dict:
    jd = _jd(job)
    kw = job.get("keywords") or {}
    ks = kw.get("keywords") or []
    x = job.get("extra") or {}
    return {
        "breakdown": job.get("breakdown"),
        "explanation": job.get("explanation"),
        "gaps": job.get("gaps") or [],
        "flag_evidence": {k: v.get("evidence") for k, v in (job.get("flags") or {}).items()},
        "reqs": {"min": requirement_status(job.get("min_reqs") or [], ks),
                 "pref": requirement_status(job.get("pref_reqs") or [], ks)},
        "skills": job.get("skills") or [],
        "keywords": kw,
        "jd": jd,
        "package": render_package(job, jd, resume_hash),
        "package_filename": package_filename(job["company"], job["title"], job["job_id"]),
        "yoe": job.get("yoe_required"), "yoe_evidence": job.get("yoe_evidence"), "yoe_pref": x.get("yoe_preferred"),
        "degree": job.get("degree"),
        "grad_windows": job.get("grad_windows") or [], "start_windows": job.get("start_windows") or [],
        "signals": job.get("signals") or {},
        "level_evidence": job.get("level_evidence"), "track_evidence": job.get("track_evidence"),
        "deadline_evidence": job.get("deadline_evidence"), "eligibility": job.get("eligibility_note"),
        "link_status": job.get("link_status"),
    }


def skill_gaps(visible: list[dict], now: datetime) -> dict:
    """Most-requested skills you lack across your top-scored jobs, per track, last 7 and 30 days."""
    out: dict[str, dict] = {}
    for days in (7, 30):
        cutoff = (now - timedelta(days=days)).isoformat()
        for track in ("coop_intern", "full_time"):
            counts: Counter = Counter()
            weights: Counter = Counter()
            examples: dict[str, list] = defaultdict(list)
            n = 0
            for j in visible:
                if (j.get("track_override") or j.get("track")) not in (track, "unclear") or j.get("label") == "Weak":
                    continue
                if j["first_seen"] < cutoff and (j.get("posted_date") or "") < cutoff:
                    continue
                n += 1
                for k in (j.get("keywords") or {}).get("keywords") or []:
                    if k["status"] == "missing" and k["section"] in ("required", "preferred") and \
                            k["category"] not in ("other", "degree_field"):
                        counts[k["canonical"]] += 1
                        weights[k["canonical"]] += k["weight"]
                        if len(examples[k["canonical"]]) < 3:
                            examples[k["canonical"]].append({"id": j["id"], "title": j["title"], "company": j["company"]})
            out[f"{track}:{days}"] = {"jobs": n, "skills": [
                {"skill": s, "jobs": c, "weight": round(weights[s], 1), "examples": examples[s]}
                for s, c in counts.most_common(25)]}
    return out


def apply_today(visible: list[dict], today: str, per_track: int = 20, cfg: ApplyToday | None = None) -> dict:
    """Candidates for the per-track Apply Today queue (the page drops applied/skipped ones and shows
    preferences apply_today.size). Score + urgency points from preferences apply_today: fresh, deadline, Strong."""
    a = cfg or ApplyToday()
    out = {}
    soon = (date.fromisoformat(today) + timedelta(days=a.closing_soon_days)).isoformat()
    for track in ("coop_intern", "full_time"):
        cands = []
        for j in visible:
            t = j.get("track_override") or j.get("track")
            if t not in (track, "unclear") or j.get("label") not in ("Strong", "Stretch"):
                continue
            if j.get("deadline") and j["deadline"] < today:
                continue
            fh = (j.get("signals") or {}).get("fresh_hours")
            urgency = a.fresh_24h if fh is not None and fh <= 24 else a.fresh_72h if fh is not None and fh <= 72 else 0
            if j.get("deadline") and j["deadline"] <= soon:
                urgency += a.closing_soon
            if j.get("label") == "Strong":
                urgency += a.strong
            cands.append((j["score"] + urgency, j["id"]))
        out[track] = [pk for _, pk in sorted(cands, reverse=True)[:per_track]]
    return out


def missed_matches(jobs: list[dict], tier_of, now: datetime, limit: int = 60, rules=None) -> list[int]:
    """Weekly false-negative review: low-scored or title-filtered jobs from tier 1/2 companies in the last 7 days."""
    from ..match.prefilter import candidate
    cutoff = (now - timedelta(days=7)).isoformat()
    picks = []
    for j in jobs:
        if j["first_seen"] < cutoff or (tier_of(j) or 3) > 2:
            continue
        if not j.get("filtered") and j.get("label") == "Weak":
            picks.append((j.get("qual_score") or 0, j["id"]))
        elif j.get("filtered") and candidate(j, j.get("filter_reasons") or [], rules):
            picks.append((0, j["id"]))
    return [pk for _, pk in sorted(picks, reverse=True)[:limit]]


def eng_blogs(companies, blogs: dict[str, str]) -> dict[str, str]:
    """company (as on the jobs) -> official engineering blog URL from config/eng_blogs.yaml, matched ignoring
    case and legal suffixes. Companies without one get a search link on the page instead."""
    from .h1b import norm_name
    by_norm = {norm_name(k): v for k, v in blogs.items() if isinstance(v, str) and v.startswith(("http://", "https://"))}
    return {c: by_norm[norm_name(c)] for c in companies if norm_name(c) in by_norm}


def coverage_rows(conn, summary, watch) -> list[dict]:
    """One row per company/source: status (ok / failed / manual / skipped), source, last checked, open roles."""
    watch_keys = {w.company.casefold() for w in watch}
    open_counts = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT s.company_key, s.source, COUNT(DISTINCT s.job_pk) FROM job_sources s JOIN jobs j ON j.id = s.job_pk "
        "WHERE j.job_status != 'closed' AND s.miss_count = 0 GROUP BY s.company_key, s.source")}
    this_run = {(o.company.casefold(), o.source): o for o in [*summary.outcomes, *summary.skipped]}
    rows = []
    for r in conn.execute("SELECT * FROM source_status WHERE COALESCE(origin, '') != 'ad_hoc' ORDER BY company"):
        r = dict(r)
        o = this_run.get((r["company"].casefold(), r["source"]))
        multi = r["source"].startswith("simplify")
        rows.append({
            "company": r["company"], "source": r["source"],
            # not checked this run -> "skipped" (not due); a --rescore run checks nothing, so keep the last status
            "status": o.status if o else (r["status"] if not this_run or r["status"] != "ok" else "skipped"),
            "last_status": r["status"], "last_checked": r["last_checked"], "last_ok": r.get("last_ok"),
            "error": r["error"] if r["status"] != "ok" else None, "notes": r["error"] if r["status"] == "ok" else None,
            "partial": r.get("partial"), "detected_via": r.get("detected_via"), "tier": r.get("tier"),
            "url": r.get("url"), "origin": r.get("origin") or "seed",
            "my_company": r["company"].casefold() in watch_keys,
            "jobs_listed": r["job_count"],
            "open_roles": (sum(v for (ck, src), v in open_counts.items() if src == r["source"]) if multi
                           else open_counts.get((r["company_key"], r["source"]), 0)),
            "checked_this_run": bool(o and o.status != "skipped"),
        })
    return rows


def select_visible(jobs: list[dict], top_n: int) -> list[dict]:
    """Highest-scoring visible jobs, top_n per track (so one track never crowds out the other)."""
    ranked = sorted((j for j in jobs if not j.get("filtered")), key=lambda j: -(j.get("score") or 0))
    per_track: dict[str, int] = {}
    out = []
    for j in ranked:
        t = j.get("track_override") or j.get("track") or "full_time"
        if per_track.get(t, 0) < top_n:
            per_track[t] = per_track.get(t, 0) + 1
            out.append(j)
    return out


def write_data_js(path: Path, jobs: list[dict], meta: dict, tz: str, resume_hash: str, top_n: int,
                  tier_of, my_companies: set[str] | None = None, max_filtered: int = 4000,
                  now: datetime | None = None, h1b: dict | None = None, apply_cfg: ApplyToday | None = None,
                  title_rules=None) -> dict:
    """jobs: decoded open job rows (scored). top_n visible jobs per track get details; filtered ones a summary.

    The Filtered tab lists missed-match candidates and software-relevant filtered jobs first (title matched but
    another rule failed), then the rest, up to max_filtered; counts per reason cover all of them.
    """
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(tz)).date().isoformat()
    mine = {c.casefold() for c in (my_companies or set())}
    visible = select_visible(jobs, top_n)
    visible_ids = {j["id"] for j in visible}
    n_visible_total = sum(1 for j in jobs if not j.get("filtered"))

    filtered = [j for j in jobs if j.get("filtered")]
    reason_counts: dict[str, int] = {}
    for j in filtered:
        key = re.sub(r"[:(—].*$", "", (j.get("filter_reasons") or ["?"])[0]).strip()
        reason_counts[key] = reason_counts.get(key, 0) + 1
    apply_cfg = apply_cfg or ApplyToday()
    missed = missed_matches(jobs, tier_of, now, rules=title_rules)
    missed_set = set(missed)
    filtered.sort(key=lambda j: (j["id"] not in missed_set,
                                 any(r.startswith("Title does not match") for r in j.get("filter_reasons") or []),
                                 -(datetime.fromisoformat(j["first_seen"]).timestamp())))
    shown_filtered = filtered[:max_filtered]

    days = sorted({local_day(j["first_seen"], tz) for j in visible}, reverse=True)
    latest_day = days[0] if days else today
    entries = [build_entry(j, tz, tier_of(j), today, latest_day, mine, apply_cfg.closing_soon_days)
               for j in [*visible, *shown_filtered]]
    shown_ids = {e["id"] for e in entries}
    for e in entries:
        e["similar"] = [i for i in e.get("similar", []) if i in visible_ids]
        if e["id"] in missed_set:
            e["missed"] = True

    # per-job details, chunked
    details_dir = path.parent / "details"
    if details_dir.exists():
        shutil.rmtree(details_dir)
    details_dir.mkdir(parents=True)
    n_chunks = max(1, math.ceil(len(visible) / CHUNK_SIZE))
    chunks: dict[int, dict] = defaultdict(dict)
    for j in visible:
        chunks[j["id"] % n_chunks][str(j["id"])] = build_detail(j, resume_hash)
    for e in entries:
        if e["id"] in visible_ids:
            e["chunk"] = e["id"] % n_chunks
    for c, body in chunks.items():
        (details_dir / f"d_{c}.js").write_text(
            f"window.__details && window.__details({json.dumps(body, ensure_ascii=False)});\n", encoding="utf-8")

    meta = {**meta, "disclaimer": DISCLAIMER, "ats_note": ATS_NOTE, "timezone": tz, "today": today,
            "visible_count": len(visible), "visible_total": n_visible_total, "top_n_per_track": top_n,
            "filtered_count": len(filtered), "filtered_listed": len(shown_filtered),
            "filtered_reasons": dict(sorted(reason_counts.items(), key=lambda kv: -kv[1])),
            "skill_gaps": skill_gaps(visible, now),
            "apply_today": {t: [i for i in ids if i in shown_ids] for t, ids in apply_today(visible, today, cfg=apply_cfg).items()},
            "ui_cfg": {"apply_today_size": apply_cfg.size, "closing_soon_days": apply_cfg.closing_soon_days},
            "missed": [i for i in missed if i in shown_ids],
            "h1b": h1b or {},
            "learned": meta.get("learned") or {"status": "No statuses imported yet.", "adjustments": []},
            "days": days}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".js.tmp")
    tmp.write_text(
        "// Generated by jobsearch.run — do not edit.\n"
        f"window.META = {json.dumps(meta, ensure_ascii=False)};\n"
        f"window.JOBS = {json.dumps(entries, ensure_ascii=False, separators=(',', ':'))};\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return {"visible": len(visible), "filtered": len(filtered), "filtered_listed": len(shown_filtered),
            "chunks": len(chunks)}
