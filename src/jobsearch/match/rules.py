"""Hard filters. A 'hard' rule hides the job (viewable in the Filtered tab with its reason);
a 'soft' rule only records an issue that the scorer turns into a penalty. Missing data never fails a rule."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from rapidfuzz import fuzz

from ..config import Preferences, TitleRules
from ..extract.normalize import has_word, normalize_title


@dataclass
class TitleMatch:
    kind: str              # "target" | "related" | "none"
    matched: str | None
    ratio: float


def match_title(norm_title: str, prefs: Preferences, rules: TitleRules) -> TitleMatch:
    best = TitleMatch("none", None, 0.0)
    for kind, titles in (("target", prefs.target_titles), ("related", prefs.related_titles)):
        for t in titles:
            nt = normalize_title(t, rules)
            # the wanted title as a contiguous phrase -> match ("software engineer, backend" ⊇ "software engineer").
            # Not a bag of words: "data center electrical engineer" must not match "data engineer".
            if has_word(norm_title, nt):
                r = 100.0
            else:
                r = fuzz.token_sort_ratio(nt, norm_title)  # set_ratio would let "Engineer" match anything
            if r >= rules.fuzzy_threshold and (best.kind == "none" or r > best.ratio):
                best = TitleMatch(kind, t, r)
        if best.kind == "target":
            break
    return best


def location_ok(location: str | None, mode: str, prefs: Preferences) -> bool | None:
    """True/False, or None when the job's location is unknown."""
    if not location and mode == "unknown":
        return None
    loc = (location or "").casefold()
    if mode not in prefs.work_modes and mode != "unknown":
        return False
    if any(p.casefold() in loc for p in prefs.locations):
        return True
    if mode == "remote" and "remote" in prefs.work_modes:
        countries = [c.casefold() for c in prefs.remote_countries]
        # "Remote" with no country, or a remote listing naming an accepted country
        bare = loc.replace("remote", "").strip(" -–,()|")
        return not bare or any(c in loc for c in countries)
    return False if prefs.locations else None


@dataclass
class RuleResult:
    hidden: bool = False
    reasons: list[str] = field(default_factory=list)       # hard-rule failures (why hidden)
    soft_issues: list[str] = field(default_factory=list)   # soft-rule failures (score penalties)
    title: TitleMatch | None = None
    location_ok: bool | None = None


def apply_rules(job: dict, prefs: Preferences, rules: TitleRules, now: datetime | None = None) -> RuleResult:
    now = now or datetime.now(timezone.utc)
    res = RuleResult()

    def fail(rule: str, reason: str):
        mode = getattr(prefs.rules, rule)
        (res.reasons if mode == "hard" else res.soft_issues).append(reason)

    norm = job["normalized_title"]
    if prefs.company_tiers.is_excluded(job["company"]):
        res.reasons.append(f"Company '{job['company']}' is in excluded companies")

    bad = [w for w in prefs.excluded_title_words if has_word(norm, normalize_title(w, rules))]
    bad += [f"level {n.upper()}" for n in rules.excluded_level_numerals if has_word(norm, n)]
    if bad:
        fail("seniority", f"Title contains excluded word(s): {', '.join(bad)}")

    res.title = match_title(norm, prefs, rules)
    if res.title.kind == "none":
        fail("title", "Title does not match target or related titles")

    res.location_ok = location_ok(job.get("location"), job.get("work_mode") or "unknown", prefs)
    if res.location_ok is False:
        fail("location", f"Location '{job.get('location')}' ({job.get('work_mode')}) not in preferred locations/modes")

    yoe = job.get("yoe_required")
    if prefs.max_yoe is not None and yoe is not None and yoe > prefs.max_yoe:
        fail("yoe", f"Requires {yoe:g}+ years (your max: {prefs.max_yoe:g})")

    et = job.get("employment_type")
    if et and prefs.employment_types and et not in prefs.employment_types:
        fail("employment_type", f"Employment type '{et}' not in {prefs.employment_types}")

    posted = job.get("posted_date")
    if prefs.max_posting_age_days and posted:
        age = (now - datetime.fromisoformat(posted)).days
        if age > prefs.max_posting_age_days:
            fail("posting_age", f"Posted {age} days ago (max {prefs.max_posting_age_days})")

    s = prefs.salary
    if s.mode == "hard" and s.min and job.get("salary_max") and job.get("salary_currency") in (None, s.currency) \
            and job["salary_max"] < s.min:
        res.reasons.append(f"Salary max {job['salary_max']:,} below your minimum {s.min:,}")

    res.hidden = bool(res.reasons)
    return res
