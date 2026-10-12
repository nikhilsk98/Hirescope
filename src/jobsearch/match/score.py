"""Qualification fit + preference fit -> overall 0-100 with a transparent breakdown.

Missing data is uncertainty: a component with no data is left out of its weighted average
(and listed under `uncertain`), never counted as a match or a miss.
This score ranks postings against your resume/preferences; it is NOT an interview probability.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import Company, Preferences
from ..extract.resume import Resume
from ..extract.skills import extract_skills
from .rules import RuleResult
from .semantic import SemanticResult

PENALTY_SOFT_RULE = 5
PENALTY_NO_SPONSORSHIP = 25
PENALTY_CITIZENSHIP = 30
PENALTY_CLEARANCE = 10
PENALTY_YOE_PER_YEAR = 8
PENALTY_YOE_CAP = 30
_DEGREE_RANK = {"Bachelor's": 1, "Master's": 2, "PhD": 3}


@dataclass
class Part:
    score: float | None
    weight: float
    detail: str


@dataclass
class ScoreResult:
    overall: float
    qualification: float | None
    preference: float | None
    label: str
    breakdown: dict
    explanation: str
    gaps: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)


def _weighted(parts: dict[str, Part]) -> tuple[float | None, list[str]]:
    have = {k: p for k, p in parts.items() if p.score is not None and p.weight > 0}
    missing = [k for k, p in parts.items() if p.score is None and p.weight > 0]
    total = sum(p.weight for p in have.values())
    if not total:
        return None, missing
    return sum(p.score * p.weight for p in have.values()) / total, missing


def _resume_degree(resume: Resume) -> int:
    text = " ".join(e.heading for e in resume.sections.get("education", [])).lower()
    if re.search(r"ph\.?d|doctor", text):
        return 3
    if re.search(r"\bm\.?s\.?\b|master|m\.eng|mba", text):
        return 2
    if re.search(r"\bb\.?s\.?\b|\bb\.?a\.?\b|bachelor|b\.?tech|b\.?e\.?\b", text):
        return 1
    return 0


def label_for(score: float, prefs: Preferences) -> str:
    if score >= prefs.thresholds.strong:
        return "Strong"
    if score >= prefs.thresholds.stretch:
        return "Stretch"
    return "Weak"


def score_job(job: dict, resume: Resume, prefs: Preferences, rules: RuleResult, sem: SemanticResult,
              synonyms: dict[str, list[str]], company: Company | None, now: datetime | None = None) -> ScoreResult:
    now = now or datetime.now(timezone.utc)
    gaps: list[str] = []
    resume_skills = set(resume.skills)

    # --- Qualification -------------------------------------------------
    req_skills = extract_skills("\n".join(job.get("min_reqs") or []), synonyms)
    pref_skills = [s for s in extract_skills("\n".join(job.get("pref_reqs") or []), synonyms) if s not in req_skills]
    if not req_skills and not pref_skills:  # unstructured JD: treat every mentioned skill as required
        req_skills = list(job.get("skills") or [])
    matched = [s for s in [*req_skills, *pref_skills] if s in resume_skills]
    missing_req = [s for s in req_skills if s not in resume_skills]
    denom = len(req_skills) + 0.5 * len(pref_skills)
    if denom:
        num = sum(1 for s in req_skills if s in resume_skills) + 0.5 * sum(1 for s in pref_skills if s in resume_skills)
        skills_score = 100 * num / denom
        skills_detail = f"{len([s for s in req_skills if s in resume_skills])}/{len(req_skills)} required" + (
            f", {len([s for s in pref_skills if s in resume_skills])}/{len(pref_skills)} preferred" if pref_skills else "")
    else:
        skills_score, skills_detail = None, "No recognizable skills in JD"
    if missing_req:
        gaps.append("Skills not in resume: " + ", ".join(missing_req))

    sem_detail = ("experience {experience}, projects {projects}, coursework {coursework}".format(
        **{k: ("–" if v is None else f"{v:.0f}") for k, v in sem.sections.items()}) if sem.score is not None
        else "Not enough JD/resume text")

    # Level fit: entry and mid roles are both full matches (no penalty for being "below" 4+ years of
    # experience, none for mid roles because of graduation status). Senior is a Stretch.
    level_bits, level_notes = [], []
    if rules.title and rules.title.kind != "none":
        level_bits.append(85 if rules.title.kind == "related" else 100)
        level_notes.append(f"title ~ {rules.title.matched} ({rules.title.kind})")
    lvl = job.get("level") or "unknown"
    if lvl != "unknown":
        level_bits.append({"intern": 100, "entry": 100, "mid": 100, "senior": 60}.get(lvl, 30))
        level_notes.append(f"level {lvl}")
    yoe = job.get("yoe_required")
    y = prefs.yoe
    if yoe is not None:
        level_bits.append(100 if yoe <= y.match_max else 60 if yoe <= y.stretch_max else 20)
        level_notes.append(f"{yoe:g}+ yrs required")
        if yoe > y.match_max:
            gaps.append(f"Requires {yoe:g}+ years; your match window is 0–{y.match_max:g}")
    grad = job.get("grad_years") or []
    my_grad = int(prefs.graduation_date[:4]) if prefs.graduation_date else None
    if grad and my_grad:
        ok = my_grad in grad
        level_bits.append(100 if ok else 25)
        level_notes.append(f"grad year(s) {grad}")
        if not ok:
            gaps.append(f"Targets graduation year(s) {grad}; yours is {my_grad}")
    deg = job.get("degree")
    if deg and _resume_degree(resume) and _DEGREE_RANK[deg] > _resume_degree(resume):
        gaps.append(f"Mentions {deg} degree; resume shows a lower degree")
        level_bits.append(40)
    level_score = sum(level_bits) / len(level_bits) if level_bits else None

    w = prefs.weights.qualification_parts
    qual_parts = {
        "skills": Part(skills_score, w.get("skills", 0), skills_detail),
        "semantic": Part(sem.score, w.get("semantic", 0), sem_detail),
        "level": Part(level_score, w.get("level", 0), "; ".join(level_notes) or "No level signals"),
    }
    qual, qual_missing = _weighted(qual_parts)

    # --- Preference ----------------------------------------------------
    loc_score, loc_detail = None, "Location unknown"
    if rules.location_ok is not None:
        loc_score = 0.0
        if rules.location_ok:
            loc = (job.get("location") or "").casefold()
            if prefs.preferred_locations and any(p.casefold() in loc for p in prefs.preferred_locations):
                loc_score, loc_detail = 100.0, "preferred location"
            elif job.get("remote_us"):
                loc_score, loc_detail = 90.0, "remote (US)"
            else:
                loc_score = 100.0 if not prefs.preferred_locations else 75.0
                loc_detail = "in the US"
    tier = prefs.company_tiers.tier_of(job["company"]) or (company.tier if company else None)
    tier_score = {1: 100, 2: 75, 3: 50}.get(tier) if tier else None

    fresh_score, fresh_detail = None, "Posting date unknown"
    if job.get("posted_date"):
        age = max(0, (now - datetime.fromisoformat(job["posted_date"])).days)
        horizon = prefs.max_posting_age_days or 90
        fresh_score = 100 if age <= 7 else max(0.0, 100 * (1 - (age - 7) / max(1, horizon - 7)))
        fresh_detail = f"posted {age} days ago"

    et = job.get("employment_type")
    et_score = None if not et else (0.0 if et in ("contract", "part_time", "temporary") else 100.0)

    sal_score, sal_detail = None, "Salary not stated"
    s = prefs.salary
    if s.min and job.get("salary_max") and job.get("salary_currency") in (None, s.currency):
        sal_score = 100 if job["salary_max"] >= s.min else max(0, 100 * job["salary_max"] / s.min - 50)
        sal_detail = f"{job.get('salary_currency') or ''} {job['salary_min']:,}–{job['salary_max']:,}".strip()

    w = prefs.weights.preference_parts
    pref_parts = {
        "location": Part(loc_score, w.get("location", 0),
                         f"{job.get('location') or 'Unknown'} · {job.get('work_mode')} ({loc_detail})"),
        "company_tier": Part(tier_score, w.get("company_tier", 0), f"tier {tier}" if tier else "Untiered"),
        "freshness": Part(fresh_score, w.get("freshness", 0), fresh_detail),
        "employment_type": Part(et_score, w.get("employment_type", 0), et or "Not stated"),
        "salary": Part(sal_score, w.get("salary", 0), sal_detail),
    }
    pref, pref_missing = _weighted(pref_parts)

    # --- Penalties -----------------------------------------------------
    penalties = [{"reason": r, "points": PENALTY_SOFT_RULE} for r in rules.soft_issues
                 if not r.startswith("Requires ")]  # YOE gets its own scaled penalty below
    if yoe is not None and yoe > y.match_max:
        penalties.append({"reason": f"Requires {yoe:g}+ years (match window 0–{y.match_max:g})",
                          "points": min(PENALTY_YOE_CAP, PENALTY_YOE_PER_YEAR * (yoe - y.match_max))})
    flags = job.get("flags") or {}
    auth = prefs.work_authorization
    if auth.needs_sponsorship and flags.get("sponsorship", {}).get("value") == "No sponsorship":
        penalties.append({"reason": "States no visa sponsorship", "points": PENALTY_NO_SPONSORSHIP})
        gaps.append("Posting states no sponsorship; you need sponsorship")
    if auth.us_citizen is False and flags.get("citizenship", {}).get("value") == "US citizenship required":
        penalties.append({"reason": "US citizenship required", "points": PENALTY_CITIZENSHIP})
        gaps.append("Posting requires US citizenship")
    if flags.get("clearance", {}).get("value") == "Clearance required":
        penalties.append({"reason": "Security clearance required", "points": PENALTY_CLEARANCE})

    wq, wp = prefs.weights.qualification, prefs.weights.preference
    if qual is not None and pref is not None:
        base = (qual * wq + pref * wp) / (wq + wp)
    else:
        base = qual if qual is not None else (pref if pref is not None else 0.0)
    overall = max(0.0, min(100.0, base - sum(p["points"] for p in penalties)))
    label = label_for(overall, prefs)
    if rules.stretch_reasons and label == "Strong":   # senior / 6-year roles are Stretch at best
        label = "Stretch"

    def dump(parts):
        return {k: {"score": None if p.score is None else round(p.score, 1), "weight": p.weight, "detail": p.detail}
                for k, p in parts.items()}

    breakdown = {
        "overall": round(overall, 1),
        "qualification": {"score": None if qual is None else round(qual, 1), "weight": wq, "parts": dump(qual_parts)},
        "preference": {"score": None if pref is None else round(pref, 1), "weight": wp, "parts": dump(pref_parts)},
        "penalties": penalties,
        "stretch_reasons": rules.stretch_reasons,
        "uncertain": qual_missing + pref_missing,
        "semantic_sections": sem.sections,
        "evidence": [{"jd": e.jd_line, "resume": e.resume_line, "section": e.section, "similarity": e.similarity}
                     for e in sem.evidence],
        "matched_skills": matched,
        "missing_skills": missing_req,
        "skill_evidence": {sk: resume.skill_evidence.get(sk) for sk in matched if resume.skill_evidence.get(sk)},
    }

    if sem.evidence:
        expl = f"{skills_detail}. Closest resume evidence: “{sem.evidence[0].resume_line}”"
    else:
        expl = skills_detail
    return ScoreResult(round(overall, 1), qual, pref, label, breakdown, expl, gaps,
                       matched, missing_req)
