"""Hard filters. A 'hard' rule hides the job (viewable in the Filtered tab with its reason);
a 'soft' rule only records an issue that the scorer turns into a penalty. Missing data never fails a rule.

Two tracks (Co-op/Intern, Full-time) share these rules; level/YOE windows follow preferences.levels/yoe:
staff+/manager excluded, senior kept as Stretch when it asks <= yoe.match_max years, 7+ years filtered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache

from rapidfuzz import fuzz

from ..config import Preferences, TitleRules
from ..extract.normalize import has_word, normalize_title
from ..extract.dates import Window, season_passed
from ..extract.track import COOP, FULL_TIME, UNCLEAR



@lru_cache(maxsize=8)
def _new_grad_re(patterns: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(patterns) + r")\b", re.I) if patterns else re.compile(r"(?!)")


def is_new_grad_title(title: str, rules: TitleRules) -> bool:
    """New Grad / early-career title (config/title_rules.yaml -> new_grad_patterns)."""
    return bool(_new_grad_re(tuple(rules.new_grad_patterns)).search(title or ""))


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
    for p in rules.non_level_phrases:
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
    boosts: list[tuple[str, str]] = field(default_factory=list)   # (kind, reason): new_grad | term | grad_match
    signals: dict = field(default_factory=dict)                     # new_grad, check_eligibility, start, grad


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
    if res.title.kind == "none" and (sem := job.get("_semantic_title")):
        res.title = TitleMatch("semantic", sem["profile"], sem["similarity"] * 100)
        res.signals["meaning_match"] = sem
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
    if et in prefs.excluded_employment_types:
        fail("employment_type", f"Employment type is {et}")

    posted = job.get("posted_date")
    if posted:
        age = (now - datetime.fromisoformat(posted)).days
        if age > prefs.ghost.stale_days:
            fail("stale_posting", f"Posted {age} days ago — likely stale (filtered after {prefs.ghost.stale_days} days)")
        elif prefs.max_posting_age_days and age > prefs.max_posting_age_days:
            fail("posting_age", f"Posted {age} days ago (max {prefs.max_posting_age_days})")

    _two_track(job, prefs, rules, res, fail, now)
    _work_authorization(job, prefs, res, fail)

    s = prefs.salary
    if s.mode == "hard" and s.min and job.get("salary_max") and job.get("salary_currency") in (None, s.currency) \
            and job["salary_max"] < s.min:
        res.reasons.append(f"Salary max {job['salary_max']:,} below your minimum {s.min:,}")

    res.hidden = bool(res.reasons)
    return res


def _fmt_window(w: dict) -> str:
    a, b = w["start"], w["end"]
    if a <= "2000-12":
        return f"by {b}"
    if b >= "2099-01":
        return f"{a} or later"
    return a if a == b else f"{a} – {b}"


def _two_track(job: dict, prefs: Preferences, rules: TitleRules, res: RuleResult, fail, now: datetime) -> None:
    """Co-op/intern and full-time track rules from preferences.tracks (start windows, preferred terms), plus the
    summer-only filter, graduation windows, New Grad and eligibility signals. Unstated dates are uncertain,
    never a rejection."""
    t = prefs.tracks
    title = job.get("title") or job.get("normalized_title") or ""
    terms = job.get("terms") or []
    evid = " ".join([title, *terms, *(w.get("evidence") or "" for w in (job.get("start_windows") or [])),
                     *(w.get("evidence") or "" for w in (job.get("grad_windows") or []))]).lower()

    # graduation window (both tracks)
    my_grad = (prefs.graduation_date or "")[:7] or None
    gws = job.get("grad_windows") or []
    if gws and my_grad:
        hits = [w for w in gws if Window(w["start"], w["end"], "").contains(my_grad, prefs.grad_window_slack_months)]
        if hits:
            res.signals["grad"] = {"status": "match", "evidence": hits[0].get("evidence")}
            res.boosts.append(("grad_match", f"Graduation window {_fmt_window(hits[0])} includes yours ({my_grad})"))
        else:
            w = gws[0]
            res.signals["grad"] = {"status": "mismatch", "evidence": w.get("evidence")}
            fail("grad_window", f"Requires graduation {_fmt_window(w)}; yours is {my_grad} — “{w.get('evidence')}”")
    else:
        res.signals["grad"] = {"status": "not stated"}

    sws = job.get("start_windows") or []
    if res.track == COOP:
        if t.coop_intern.filter_summer_only and job.get("summer_only"):
            fail("summer_only", "Summer-only internship (filtered: tracks.coop_intern.filter_summer_only)")
        lo, hi = t.coop_intern.start_window or ("", "")
        if past := season_passed(terms, lo or None, f"{now.year:04d}-{now.month:02d}"):
            fail("season_passed", f"Season has passed: {', '.join(past)} (over, or starts before your co-op window"
                                  f"{f' from {lo}' if lo else ''})")
        if sws and t.coop_intern.start_window:
            ok = [w for w in sws if lo <= w["start"][:7] <= hi]
            if ok:
                res.signals["start"] = {"status": "in window", "evidence": ok[0].get("evidence")}
            else:
                res.signals["start"] = {"status": "outside window", "evidence": sws[0].get("evidence")}
                fail("start_window", f"Starts {_fmt_window(sws[0])}, outside your co-op window {lo} – {hi}")
        else:
            res.signals["start"] = {"status": "not stated"}
        if term := next((x for x in t.coop_intern.boost_terms if x.lower() in evid), None):
            res.boosts.append(("term", f"States a preferred term (“{term}”)"))
    elif res.track == FULL_TIME:
        start_from = t.full_time.start_from
        if sws and start_from:
            if all(w["end"][:7] < start_from for w in sws):
                res.signals["start"] = {"status": "too early", "evidence": sws[0].get("evidence")}
                fail("start_window", f"Starts {_fmt_window(sws[0])}, before your {start_from} start")
            else:
                res.signals["start"] = {"status": "ok", "evidence": sws[0].get("evidence")}
        else:
            res.signals["start"] = {"status": "not stated"}
        if term := next((x for x in t.full_time.boost_terms if x.lower() in evid), None):
            res.boosts.append(("term", f"States a preferred start (“{term}”)"))
        if is_new_grad_title(title, rules) or job.get("source") == "simplify_newgrad":
            res.signals["new_grad"] = True
            res.boosts.append(("new_grad", "New Grad / early-career role (top priority)"))
    if job.get("eligibility_note"):
        res.signals["check_eligibility"] = job["eligibility_note"]


def _work_authorization(job: dict, prefs: Preferences, res: RuleResult, fail) -> None:
    """Only what the JD explicitly states (flags default to 'Not stated'; silence never filters anything).

    U.S. person (export control) requirements follow work_authorization.us_person (both tracks); export control that
    only might apply is kept with a "Check eligibility: export control" signal quoting the line.
    Full-time: no sponsorship (if you need it), US citizens / US persons only (if you aren't one), or security
    clearance required -> filtered with the quote. Co-op/Intern: citizens / US persons only or clearance required
    -> filtered; "no sponsorship" alone follows work_authorization.coop_no_sponsorship: "flag" (default) keeps it
    with a "Check eligibility (CPT/OPT)" flag, since co-ops and internships are often allowed on CPT; "filter" hides it.
    """
    auth = prefs.work_authorization
    flags = job.get("flags") or {}
    spons = flags.get("sponsorship") or {}
    cit = flags.get("citizenship") or {}
    clr = flags.get("clearance") or {}
    q = lambda f: f" — “{f.get('evidence')}”" if f.get("evidence") else ""  # noqa: E731
    not_person = auth.us_person is False or (auth.us_person is None and auth.us_citizen is False)
    if auth.us_citizen is False and cit.get("value", "").startswith("US citizenship required"):
        fail("work_authorization", f"{cit['value']}{q(cit)}")
    if not_person and cit.get("value", "").startswith("US person required"):
        fail("work_authorization", f"{cit['value']}{q(cit)}")
    exp = flags.get("export_control") or {}
    if auth.us_person is not True and exp.get("value", "").startswith("Possibly required"):
        # scoped ("positions requiring…") or hedged ("may require access to export-controlled…"): kept, flagged
        note = f"Export control — “{exp.get('evidence')}”"
        prev = res.signals.get("check_eligibility")
        res.signals["check_eligibility"] = f"{prev}; {note}" if prev else note
    if auth.us_citizen is False and clr.get("value") == "Clearance required":
        fail("work_authorization", f"Security clearance required{q(clr)}")
    if auth.needs_sponsorship and spons.get("value") == "No sponsorship":
        if res.track == COOP and auth.coop_no_sponsorship == "flag":
            res.signals["check_cpt_opt"] = spons.get("evidence") or "States no visa sponsorship"
        else:
            fail("work_authorization", f"States no visa sponsorship{q(spons)}")


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
