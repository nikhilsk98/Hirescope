"""Hard filters. A 'hard' rule hides the job (viewable in the Filtered tab with its reason);
a 'soft' rule only records an issue that the scorer turns into a penalty. Missing data never fails a rule.

Two tracks (Co-op/Intern, Full-time) share these rules; level/YOE windows follow preferences.levels/yoe:
staff+/manager excluded, senior kept as Stretch when it asks <= yoe.match_max years, 7+ years filtered.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from rapidfuzz import fuzz

from ..config import Preferences, TitleRules
from ..extract.normalize import has_word, normalize_title
from ..extract.track import COOP, FULL_TIME, UNCLEAR

# phrases that contain level-like words but are not levels
NON_LEVEL_PHRASES = ("member of technical staff",)


@dataclass
class TitleMatch:
    kind: str              # "core" | "related" | "coop" | "none"
    matched: str | None
    ratio: float


def _phrase_or_fuzzy(norm_title: str, phrases: list[str], rules: TitleRules) -> tuple[str | None, float]:
    best, best_r = None, 0.0
    for t in phrases:
        nt = normalize_title(t, rules)  # cached
        # the wanted title as a contiguous phrase -> match ("software engineer, backend" contains "software engineer").
        # Not a bag of words: "data center electrical engineer" must not match "data engineer".
        r = 100.0 if has_word(norm_title, nt) else fuzz.token_sort_ratio(nt, norm_title)
        if r >= rules.fuzzy_threshold and r > best_r:
            best, best_r = t, r
            if r == 100.0:
                break
    return best, best_r


def match_title(norm_title: str, track: str, prefs: Preferences, rules: TitleRules) -> TitleMatch:
    core = [*prefs.target_titles, *rules.core_titles]
    related = [*prefs.related_titles, *rules.related_titles]
    if track in (COOP, UNCLEAR):
        # "Software Engineering Co-op", "SDE Intern", "Co-op - Backend Development"
        for w in rules.coop_role_words:
            if has_word(norm_title, normalize_title(w, rules)):
                if track == COOP or any(has_word(norm_title, x) for x in ("co op", "intern", "internship")):
                    return TitleMatch("coop", w, 100.0)
    for kind, phrases in (("core", core), ("related", related)):
        m, r = _phrase_or_fuzzy(norm_title, phrases, rules)
        if m:
            return TitleMatch(kind, m, r)
    return TitleMatch("none", None, 0.0)


def excluded_words(norm_title: str, prefs: Preferences, rules: TitleRules) -> list[str]:
    t = norm_title
    for p in NON_LEVEL_PHRASES:
        t = t.replace(p, " ")
    return [w for w in prefs.excluded_title_words if has_word(t, normalize_title(w, rules))]


@dataclass
class RuleResult:
    hidden: bool = False
    reasons: list[str] = field(default_factory=list)       # hard-rule failures (why hidden)
    soft_issues: list[str] = field(default_factory=list)   # soft-rule failures (score penalties)
    stretch_reasons: list[str] = field(default_factory=list)  # kept, but the label is capped at Stretch
    title: TitleMatch | None = None
    location_ok: bool | None = None
    track: str = FULL_TIME


def effective_track(job: dict) -> str:
    return job.get("track_override") or job.get("track") or FULL_TIME


def apply_rules(job: dict, prefs: Preferences, rules: TitleRules, now: datetime | None = None) -> RuleResult:
    now = now or datetime.now(timezone.utc)
    res = RuleResult(track=effective_track(job))

    def fail(rule: str, reason: str):
        mode = getattr(prefs.rules, rule)
        (res.reasons if mode == "hard" else res.soft_issues).append(reason)

    norm = job["normalized_title"]
    if prefs.company_tiers.is_excluded(job["company"]):
        res.reasons.append(f"Company '{job['company']}' is in excluded companies")

    tracks = prefs.tracks
    if res.track == COOP and not tracks.coop_intern.enabled:
        res.reasons.append("Co-op/Intern track is disabled in preferences")
    if res.track == FULL_TIME and not tracks.full_time.enabled:
        res.reasons.append("Full-time track is disabled in preferences")

    if bad := excluded_words(norm, prefs, rules):
        fail("seniority", f"Title contains excluded word(s): {', '.join(bad)}")

    level = job.get("level") or "unknown"
    yoe = job.get("yoe_required")
    y = prefs.yoe
    if level in prefs.levels.exclude and not bad:
        fail("seniority", f"Level {level} is excluded ({job.get('level_evidence') or 'from title'})")
    elif level in prefs.levels.stretch:
        if yoe is not None and yoe > y.match_max:
            fail("seniority", f"Senior role requiring {yoe:g}+ years (senior is kept only when ≤ {y.match_max:g})")
        else:
            res.stretch_reasons.append("Senior title" + ("" if yoe is not None else " (years not stated)"))

    res.title = match_title(norm, res.track, prefs, rules)
    if res.title.kind == "none":
        fail("title", "Title does not match core, related, or co-op/intern software titles")

    us, remote_us = job.get("location_us"), job.get("remote_us")
    mode = job.get("work_mode") or "unknown"
    if prefs.country.upper() in ("US", "USA"):
        res.location_ok = None if us is None else bool(us)
        if us is not None and not us:
            fail("location", f"Not in the US: {job.get('location') or 'unknown location'}")
    if mode != "unknown" and mode not in prefs.work_modes and not (mode == "remote" and remote_us):
        fail("location", f"Work mode '{mode}' not in {prefs.work_modes}")

    if yoe is not None:
        if yoe >= y.filter_min:
            fail("yoe", f"Requires {yoe:g}+ years (filtered at {y.filter_min:g}+)")
        elif yoe > y.match_max:
            res.stretch_reasons.append(f"Requires {yoe:g}+ years (match window 0–{y.match_max:g})")

    et = job.get("employment_type")
    if et in ("contract", "part_time", "temporary"):
        fail("employment_type", f"Employment type is {et}")

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


def quick_relevant(title: str, location: str | None, prefs: Preferences, rules: TitleRules) -> bool:
    """Cheap pre-check before fetching a job's details: plausible title for either track, not an
    excluded level, and not clearly outside the US. Errs on the side of fetching."""
    from ..extract.locations import normalize_location
    from ..extract.track import classify_track
    norm = normalize_title(title, rules)
    track = classify_track(title).track
    if match_title(norm, track, prefs, rules).kind == "none":
        return False
    if excluded_words(norm, prefs, rules):
        return False
    if prefs.country.upper() in ("US", "USA") and location and normalize_location(location).us is False:
        return False
    return True
