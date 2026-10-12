"""Write dashboard/data.js (window.JOBS / window.META) so index.html works from file:// with no fetch."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from .packages import package_filename, render_package

DISCLAIMER = ("Scores rank postings against your resume and preferences using local heuristics. "
              "They are not an interview or hiring probability.")


def local_day(iso: str, tz: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")


def linkedin_search_url(company: str, title: str) -> str:
    return "https://www.linkedin.com/jobs/search/?keywords=" + quote_plus(f"{company} {title}")


def build_entry(job: dict, tz: str, resume_hash: str, tier: int | None, full: bool) -> dict:
    e = {
        "id": job["id"],
        "company": job["company"],
        "title": job["title"],
        "job_id": job["job_id"],
        "location": job.get("location") or "Unknown",
        "work_mode": job.get("work_mode") or "unknown",
        "employment_type": job.get("employment_type") or "Not stated",
        "seniority": job.get("seniority"),
        "tier": tier,
        "posted_date": job["posted_date"][:10] if job.get("posted_date") else "Unknown",
        "first_seen": job["first_seen"],
        "day": local_day(job["first_seen"], tz),
        "job_status": job["job_status"],
        "source": job["source"],
        "apply_url": job.get("apply_url") or job.get("source_url"),
        "linkedin_url": linkedin_search_url(job["company"], job["title"]),
        "score": job.get("score"),
        "label": job.get("label"),
        "filtered": bool(job.get("filtered")),
        "filter_reasons": job.get("filter_reasons") or [],
        "flags": {k: v.get("value") for k, v in (job.get("flags") or {}).items()},
        "track": job.get("track_override") or job.get("track") or "full_time",
        "track_auto": job.get("track"),
        "track_evidence": job.get("track_evidence"),
        "level": job.get("level") or "unknown",
        "terms": job.get("terms") or [],
        "link_status": job.get("link_status"),
        "my_company": False,
    }
    if full:
        jd = Path(job["jd_path"]).read_text(encoding="utf-8") if job.get("jd_path") and Path(job["jd_path"]).exists() else ""
        e.update({
            "qual_score": job.get("qual_score"),
            "pref_score": job.get("pref_score"),
            "breakdown": job.get("breakdown"),
            "explanation": job.get("explanation"),
            "gaps": job.get("gaps") or [],
            "flag_evidence": {k: v.get("evidence") for k, v in (job.get("flags") or {}).items()},
            "level_evidence": job.get("level_evidence"),
            "has_jd": (job.get("extra") or {}).get("has_jd", True),
            "sources": job.get("_sources") or [job["source"]],
            "min_reqs": job.get("min_reqs") or [],
            "pref_reqs": job.get("pref_reqs") or [],
            "skills": job.get("skills") or [],
            "yoe_required": job.get("yoe_required"),
            "yoe_preferred": (job.get("extra") or {}).get("yoe_preferred"),
            "degree": job.get("degree"),
            "grad_years": job.get("grad_years") or [],
            "salary": ({"min": job["salary_min"], "max": job["salary_max"], "currency": job.get("salary_currency")}
                       if job.get("salary_max") else None),
            "package_filename": package_filename(job["company"], job["title"], job["job_id"]),
            "package": render_package(job, jd, resume_hash),
        })
    return e


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


def write_data_js(path: Path, jobs: list[dict], meta: dict, tz: str, resume_hash: str, top_n: int,
                  tier_of, my_companies: set[str] | None = None, max_filtered: int = 4000) -> dict:
    """jobs: decoded open job rows (scored). Visible jobs get full detail; filtered ones a summary.

    The Filtered tab lists software-relevant filtered jobs first (title matched but another rule failed),
    then the rest, up to max_filtered; counts per reason cover all of them.
    """
    mine = {c.casefold() for c in (my_companies or set())}
    # top_n per track, so one track never crowds the other out of the dashboard
    ranked = sorted((j for j in jobs if not j.get("filtered")), key=lambda j: -(j.get("score") or 0))
    per_track: dict[str, int] = {}
    visible = []
    for j in ranked:
        t = j.get("track_override") or j.get("track") or "full_time"
        if per_track.get(t, 0) < top_n:
            per_track[t] = per_track.get(t, 0) + 1
            visible.append(j)
    visible_total = len(ranked)
    filtered = [j for j in jobs if j.get("filtered")]
    reason_counts: dict[str, int] = {}
    for j in filtered:
        key = re.sub(r"[:(].*$", "", (j.get("filter_reasons") or ["?"])[0]).strip()
        reason_counts[key] = reason_counts.get(key, 0) + 1
    filtered.sort(key=lambda j: (any(r.startswith("Title does not match") for r in j.get("filter_reasons") or []),
                                 -(datetime.fromisoformat(j["first_seen"]).timestamp())))
    shown_filtered = filtered[:max_filtered]
    entries = [build_entry(j, tz, resume_hash, tier_of(j), True) for j in visible]
    entries += [build_entry(j, tz, resume_hash, tier_of(j), False) for j in shown_filtered]
    for e in entries:
        e["my_company"] = e["company"].casefold() in mine
    meta = {**meta, "disclaimer": DISCLAIMER, "timezone": tz, "visible_count": len(visible),
            "visible_total": visible_total, "top_n_per_track": top_n,
            "filtered_count": len(filtered), "filtered_listed": len(shown_filtered),
            "filtered_reasons": dict(sorted(reason_counts.items(), key=lambda kv: -kv[1]))}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".js.tmp")
    tmp.write_text(
        "// Generated by jobsearch.run — do not edit.\n"
        f"window.META = {json.dumps(meta, ensure_ascii=False)};\n"
        f"window.JOBS = {json.dumps(entries, ensure_ascii=False)};\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return {"visible": len(visible), "filtered": len(filtered), "filtered_listed": len(shown_filtered)}
