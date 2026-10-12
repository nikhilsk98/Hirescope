"""Feedback learning from your statuses: bounded, transparent score adjustments.

Positive: shortlisted / package exported / applied / interviewing / offer (interested counts half).
Negative: skipped, and only for the features its reason is about:
    company -> company · location -> location · too senior / too junior -> level ·
    missing skills -> the job's missing skills · not interested in role -> skills and domain.
Skips for compensation, visa/sponsorship, start date, duplicate or other teach nothing (they're not about
which jobs you like). Rejections are the company's decision, not your preference, so they are ignored too.

Per feature value: points = max_per_feature * (pos - neg) / (pos + neg + 2), once it has min_evidence labeled
jobs. Each group (company, skills, domain, location, level) is capped at +/-max_per_feature per job and all
groups together at +/-max_total. Component weights are nudged by at most +/-weight_nudge_max (relative) toward
the parts that separate your positives from your negatives. Learning never un-hides a filtered job.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from ..config import Feedback, Preferences
from ..extract.locations import preferred_match

POSITIVE = {"shortlisted": 1.0, "package exported": 1.0, "applied": 1.0, "interviewing": 1.0, "offer": 1.0,
            "interested": 0.5}
REASON_GROUPS = {"company": ("company",), "location": ("location",), "too senior": ("level",),
                 "too junior": ("level",), "missing skills": ("missing_skills",),
                 "not interested in role": ("skills", "domain")}
GROUPS = ("company", "skills", "domain", "location", "level")
GROUP_LABEL = {"company": "Company", "skills": "Skill", "domain": "Domain", "location": "Location", "level": "Level"}


def features(job: dict, prefs: Preferences) -> dict[str, set[str]]:
    kws = (job.get("keywords") or {}).get("keywords") or []
    loc = preferred_match(job.get("location"), prefs.preferred_locations)
    if not loc:
        if job.get("remote_us"):
            loc = "Remote (US)"
        elif m := re.search(r",\s*([A-Z]{2})\b", job.get("location") or ""):
            loc = m.group(1)
    bd = job.get("breakdown") or {}
    co = job["company"].casefold()
    not_company = lambda xs: {x for x in xs if x.casefold() != co}   # noqa: E731 - "Stripe" the product is the company
    return {
        "company": {job["company"]},
        "skills": not_company(job.get("skills") or []),
        "missing_skills": not_company(bd.get("missing_skills") or []),
        "domain": not_company(k["canonical"] for k in kws if k.get("category") == "domain"),
        "location": {loc} if loc else set(),
        "level": {job["level"]} if job.get("level") and job["level"] != "unknown" else set(),
    }


@dataclass
class Adj:
    points: float
    pos: float
    neg: float


@dataclass
class Learned:
    cfg: Feedback
    adjustments: dict[str, dict[str, Adj]] = field(default_factory=dict)
    weight_nudges: dict[str, float] = field(default_factory=dict)   # part -> relative change (+0.1 = +10%)
    labeled: int = 0
    positives: int = 0
    negatives: int = 0
    ignored: dict[str, int] = field(default_factory=dict)            # skip reason -> count (taught nothing)
    suggestions: list[str] = field(default_factory=list)

    def for_job(self, job: dict, prefs: Preferences) -> list[tuple[str, float]]:
        if not self.adjustments:
            return []
        f = features(job, prefs)
        out = []
        for g in GROUPS:
            hits = [(v, self.adjustments[g][v]) for v in f.get(g, ()) if v in self.adjustments.get(g, {})]
            if not hits:
                continue
            total = sum(a.points for _, a in hits)
            cap = self.cfg.max_per_feature
            total = max(-cap, min(cap, total))
            names = ", ".join(v for v, _ in sorted(hits, key=lambda h: -abs(h[1].points))[:3])
            if round(total, 1):
                out.append((f"Learned ({GROUP_LABEL[g].lower()}: {names})", round(total, 1)))
        s = sum(p for _, p in out)
        if abs(s) > self.cfg.max_total:   # scale down proportionally to the per-job cap
            k = self.cfg.max_total / abs(s)
            out = [(r, round(p * k, 1)) for r, p in out]
        return out

    def apply_weights(self, prefs: Preferences) -> Preferences:
        if not self.weight_nudges:
            return prefs
        p = prefs.model_copy(deep=True)
        for parts in (p.weights.qualification_parts, p.weights.preference_parts):
            for k in parts:
                if k in self.weight_nudges:
                    parts[k] = round(parts[k] * (1 + self.weight_nudges[k]), 4)
        return p

    def describe(self) -> dict:
        rows = []
        for g in GROUPS:
            for v, a in self.adjustments.get(g, {}).items():
                rows.append({"group": GROUP_LABEL[g], "value": v, "points": a.points, "positive": a.pos,
                             "negative": a.neg})
        rows.sort(key=lambda r: -abs(r["points"]))
        weights = [{"part": k.replace("_", " "), "change_pct": round(100 * v)} for k, v in
                   sorted(self.weight_nudges.items(), key=lambda kv: -abs(kv[1]))]
        lines = [f"{r['group']} {r['value']}: {r['points']:+.1f} ({r['positive']:g} positive, {r['negative']:g} "
                 f"negative)" for r in rows[:12]]
        lines += [f"Weight {w['part']}: {w['change_pct']:+d}%" for w in weights]
        if not self.labeled:
            status = "No statuses imported yet. Shortlist / skip (with a reason) on the dashboard, then Export statuses."
        elif not rows and not weights:
            status = (f"{self.labeled} labeled jobs so far; adjustments start once a company, skill or location has "
                      f"{self.cfg.min_evidence}+ labeled jobs.")
        else:
            status = (f"From {self.labeled} labeled jobs ({self.positives} positive, {self.negatives} negative). "
                      f"Capped at ±{self.cfg.max_per_feature:g} per group and ±{self.cfg.max_total:g} per job.")
        return {"status": status, "adjustments": lines, "rows": rows, "weights": weights,
                "ignored": self.ignored, "suggestions": self.suggestions,
                "caps": {"per_feature": self.cfg.max_per_feature, "per_job": self.cfg.max_total,
                         "weight_nudge_max": self.cfg.weight_nudge_max, "min_evidence": self.cfg.min_evidence}}


def learn(jobs: list[dict], prefs: Preferences) -> Learned:
    """jobs: decoded job rows (open and closed) with user_status / skip_reason."""
    cfg = prefs.feedback
    L = Learned(cfg)
    if not cfg.enabled:
        return L
    pos: dict[str, dict[str, float]] = {g: defaultdict(float) for g in GROUPS}
    neg: dict[str, dict[str, float]] = {g: defaultdict(float) for g in GROUPS}
    part_scores: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
    filtered_pos: dict[str, int] = defaultdict(int)
    ignored: dict[str, int] = defaultdict(int)
    for j in jobs:
        st = j.get("user_status")
        if not st or st == "rejected":
            continue
        f = features(j, prefs)
        if st in POSITIVE:
            w = POSITIVE[st]
            L.labeled += 1
            L.positives += 1
            for g in GROUPS:
                for v in f[g]:
                    pos[g][v] += w
            side = 0
            if j.get("filtered"):
                filtered_pos[re.sub(r"[:(—].*$", "", (j.get("filter_reasons") or ["?"])[0]).strip()] += 1
        elif st == "skipped":
            reason = (j.get("skip_reason") or "other").strip().lower()
            groups = REASON_GROUPS.get(reason)
            if not groups:
                ignored[reason] += 1
                continue
            L.labeled += 1
            L.negatives += 1
            for g in groups:
                for v in f[g]:
                    neg["skills" if g == "missing_skills" else g][v] += 1
            side = 1
        else:
            continue
        bd = j.get("breakdown") or {}
        for block in ("qualification", "preference"):
            for k, p in ((bd.get(block) or {}).get("parts") or {}).items():
                if p.get("score") is not None:
                    part_scores[k][side].append(p["score"])
    for g in GROUPS:
        for v in set(pos[g]) | set(neg[g]):
            p, n = pos[g].get(v, 0.0), neg[g].get(v, 0.0)
            if p + n < cfg.min_evidence:
                continue
            pts = round(cfg.max_per_feature * (p - n) / (p + n + 2), 1)
            if pts:
                L.adjustments.setdefault(g, {})[v] = Adj(pts, p, n)
    for k, (ps, ns) in part_scores.items():
        if len(ps) >= cfg.weight_min_jobs and len(ns) >= cfg.weight_min_jobs:
            delta = (sum(ps) / len(ps) - sum(ns) / len(ns)) / 100
            nudge = max(-cfg.weight_nudge_max, min(cfg.weight_nudge_max, round(delta, 2)))
            if nudge:
                L.weight_nudges[k] = nudge
    L.ignored = dict(ignored)
    for reason, n in sorted(filtered_pos.items(), key=lambda kv: -kv[1]):
        L.suggestions.append(f"You marked {n} filtered job{'s' if n > 1 else ''} as positive (filtered for "
                             f"“{reason}”). If that rule is too strict, adjust it in inputs/preferences.yaml or "
                             f"config/title_rules.yaml — learning never un-hides filtered jobs on its own.")
    return L
