"""Phase 3: two-track rules, graduation/start windows, eligibility, deadlines, ghost signals, boosts. (DEMO data)"""
from datetime import datetime, timedelta, timezone

import pytest

from jobsearch.extract.dates import (experience_restriction, parse_deadline, parse_grad_windows, parse_start_windows,
                                     summer_only)
from jobsearch.match.rules import apply_rules
from jobsearch.match.score import ghost_signals, score_job
from jobsearch.match.semantic import SemanticResult

from .test_match import job

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def w(text):
    return [x.as_dict() for x in parse_grad_windows(text)]


@pytest.mark.parametrize("text,start,end", [
    ("Candidates graduating between December 2026 and June 2027.", "2026-12", "2027-06"),
    ("Must be Class of 2026.", "2025-12", "2026-08"),
    ("Expected graduation by May 2027.", "2000-01", "2027-05"),
    ("Graduating in Spring 2027 or later.", "2027-01", "2099-12"),
    ("Pursuing a degree with graduation date of Dec 2027 or later", "2027-12", "2099-12"),
    ("graduation from Fall 2026 - Summer 2027", "2026-08", "2027-08"),
])
def test_grad_windows(text, start, end):
    got = w(text)
    assert got and (got[0]["start"], got[0]["end"]) == (start, end) and got[0]["evidence"]


def test_no_grad_window_unless_stated():
    assert w("We hire great engineers.") == []


@pytest.mark.parametrize("text,terms,windows", [
    ("Start date: January 2027", [], [("2027-01", "2027-01")]),
    ("This co-op runs January - June 2027", [], [("2027-01", "2027-06")]),
    ("Co-op term: Jan–Aug 2027", [], [("2027-01", "2027-08")]),
    ("", ["Spring 2027"], [("2027-01", "2027-05")]),
    ("", ["Summer 2027"], [("2027-05", "2027-08")]),
    ("", ["Jan 2027 start"], [("2027-01", "2027-01")]),
])
def test_start_windows(text, terms, windows):
    assert [(x.start, x.end) for x in parse_start_windows(text, terms)] == windows


def test_summer_only():
    assert summer_only(["Summer 2027"], "SWE Intern", "")
    assert not summer_only(["Summer 2027", "Fall 2027"], "SWE Intern", "")
    assert not summer_only(["Summer 2027"], "SWE Co-op (6-month)", "")
    assert not summer_only([], "SWE Intern", "")             # unknown term: not summer-only


def test_experience_restriction_only_when_stated():
    assert "no more than 1 year" in experience_restriction(
        "Ideal candidates have no more than 1 year of full-time work experience after graduation.")
    assert experience_restriction("Graduated within the last 12 months.")
    assert experience_restriction("3+ years of experience required") is None


def test_deadlines():
    assert parse_deadline("Please apply by October 31, 2026.")[0] == "2026-10-31"
    assert parse_deadline("Application deadline: 11/15/2026")[0] == "2026-11-15"
    assert parse_deadline("", "2026-12-01T00:00:00Z")[0] == "2026-12-01"
    assert parse_deadline("No deadline mentioned") == (None, None)


def rules(cfg, **kw):
    return apply_rules(job(**kw), cfg.prefs, cfg.title_rules, NOW)


def test_summer_only_internship_filtered_on_coop_track(cfg):
    r = rules(cfg, track="coop_intern", normalized_title="software engineer intern", summer_only=1,
              terms=["Summer 2027"])
    assert r.hidden and "Summer-only" in r.reasons[0]
    p = cfg.prefs.model_copy(deep=True)
    p.tracks.coop_intern.filter_summer_only = False
    assert not apply_rules(job(track="coop_intern", normalized_title="software engineer intern", summer_only=1),
                           p, cfg.title_rules, NOW).hidden


def test_coop_start_window(cfg):
    ok = rules(cfg, track="coop_intern", normalized_title="software engineer co op",
               start_windows=[{"start": "2027-01", "end": "2027-06", "evidence": "Jan-Jun 2027"}], terms=["Spring 2027"])
    assert not ok.hidden and ok.signals["start"]["status"] == "in window"
    assert any(k == "term" for k, _ in ok.boosts)
    late = rules(cfg, track="coop_intern", normalized_title="software engineer co op",
                 start_windows=[{"start": "2027-09", "end": "2027-12", "evidence": "Fall 2027"}])
    assert late.signals["start"]["status"] == "outside window" and late.soft_issues   # soft by default
    unknown = rules(cfg, track="coop_intern", normalized_title="software engineer co op")
    assert unknown.signals["start"]["status"] == "not stated" and not unknown.hidden and not unknown.soft_issues


def test_full_time_start_and_new_grad(cfg):
    early = rules(cfg, start_windows=[{"start": "2026-11", "end": "2026-11", "evidence": "start immediately"}])
    assert early.signals["start"]["status"] == "too early" and early.soft_issues
    ng = rules(cfg, title="Software Engineer, New Grad 2027", normalized_title="software engineer new graduate 2027",
               level="entry", terms=[])
    assert ng.signals.get("new_grad") and any(k == "new_grad" for k, _ in ng.boosts)


def test_grad_window_mismatch_filtered_and_match_boosted(cfg):
    bad = rules(cfg, grad_windows=[{"start": "2025-12", "end": "2026-08", "evidence": "Class of 2026"}])
    assert bad.hidden and "Class of 2026" in bad.reasons[0]
    good = rules(cfg, grad_windows=[{"start": "2026-12", "end": "2027-06", "evidence": "Dec 2026 - Jun 2027"}])
    # demo graduation 2027-05 is inside; Aug 2027 graduates would be inside with the 2-month slack
    assert not good.hidden and any(k == "grad_match" for k, _ in good.boosts)


def test_new_grad_never_filtered_for_experience(cfg):
    r = rules(cfg, title="Software Engineer, New Grad", normalized_title="software engineer new graduate", level="entry",
              eligibility_note="no more than 1 year of full-time experience", yoe_required=0)
    assert not r.hidden and r.signals["check_eligibility"]


def test_ghost_signals(cfg):
    g = ghost_signals(job(posted_date=(NOW - timedelta(days=120)).isoformat(), repost_count=2,
                          title="Software Engineer - Talent Pool"), cfg.prefs, NOW)
    reasons = " ".join(r for r, _ in g)
    assert "120 days" in reasons and "Reposted" in reasons and "Talent Pool" in reasons


def test_boosts_capped_and_shown(cfg, resume):
    j = job(title="Software Engineer, New Grad (Class of 2027)", normalized_title="software engineer new graduate class of 2027",
            level="entry", terms=["Class of 2027"],
            grad_windows=[{"start": "2026-12", "end": "2027-08", "evidence": "Class of 2027"}])
    r = apply_rules(j, cfg.prefs, cfg.title_rules, NOW)
    res = score_job(j, resume, cfg.prefs, r, SemanticResult(None, {"experience": None, "projects": None,
                                                                    "coursework": None}, []),
                    cfg.synonyms, cfg.companies[0], NOW)
    kinds = [b["reason"] for b in res.breakdown["bonuses"]]
    assert any("New Grad" in k for k in kinds) and any("Graduation window" in k for k in kinds)
    assert res.breakdown["bonus_total"] - res.breakdown["freshness"]["points"] <= cfg.prefs.boosts.cap


def test_stale_postings_filtered(cfg):
    r = rules(cfg, posted_date=(NOW - timedelta(days=400)).isoformat())
    assert r.hidden and "stale" in r.reasons[0]


def test_business_development_intern_is_not_software(cfg):
    from jobsearch.extract.normalize import normalize_title
    from jobsearch.match.rules import match_title
    n = normalize_title("Technology Alliances Business Development Intern", cfg.title_rules)
    assert match_title(n, "coop_intern", cfg.prefs, cfg.title_rules).kind == "none"
    n = normalize_title("Software Development Intern", cfg.title_rules)
    assert match_title(n, "coop_intern", cfg.prefs, cfg.title_rules).kind == "coop"


# --- work authorization (needs sponsorship, not a US citizen) -------------------------------------
def auth_prefs(cfg):
    p = cfg.prefs.model_copy(deep=True)
    p.work_authorization.needs_sponsorship = True
    p.work_authorization.us_citizen = False
    return p


def flag(value, evidence="quoted line"):
    return {"value": value, "evidence": evidence}


def test_full_time_explicit_restrictions_filtered_with_quote(cfg):
    p = auth_prefs(cfg)
    ns = apply_rules(job(flags={"sponsorship": flag("No sponsorship", "We are unable to sponsor visas.")}), p, cfg.title_rules, NOW)
    assert ns.hidden and "unable to sponsor" in ns.reasons[0]
    assert apply_rules(job(flags={"citizenship": flag("US citizenship required")}), p, cfg.title_rules, NOW).hidden
    assert apply_rules(job(flags={"citizenship": flag("US person required (export control)")}), p, cfg.title_rules, NOW).hidden
    assert apply_rules(job(flags={"clearance": flag("Clearance required")}), p, cfg.title_rules, NOW).hidden


def test_coop_no_sponsorship_kept_with_cpt_opt_flag(cfg):
    p = auth_prefs(cfg)
    coop = dict(track="coop_intern", normalized_title="software engineer intern")
    r = apply_rules(job(**coop, flags={"sponsorship": flag("No sponsorship", "No visa sponsorship for this role.")}), p, cfg.title_rules, NOW)
    assert not r.hidden and r.signals["check_cpt_opt"]
    assert apply_rules(job(**coop, flags={"citizenship": flag("US citizenship required")}), p, cfg.title_rules, NOW).hidden
    assert apply_rules(job(**coop, flags={"clearance": flag("Clearance required")}), p, cfg.title_rules, NOW).hidden


def test_silence_never_filters(cfg):
    p = auth_prefs(cfg)
    r = apply_rules(job(flags={k: {"value": "Not stated", "evidence": None} for k in ("sponsorship", "citizenship", "clearance")}),
                    p, cfg.title_rules, NOW)
    assert not r.hidden
    assert not apply_rules(job(flags={"clearance": flag("Clearance preferred")}), p, cfg.title_rules, NOW).hidden


def test_flag_extraction_us_person_and_optional_clearance():
    from jobsearch.extract.flags import extract_flags
    f = extract_flags("Due to ITAR requirements, candidates must be a U.S. person.")
    assert f["citizenship"]["value"] == "US person required (export control)"
    assert extract_flags("Ability to obtain a security clearance is a plus.")["clearance"]["value"] == "Clearance preferred"
    assert extract_flags("Must hold an active Secret security clearance.")["clearance"]["value"] == "Clearance required"
    assert extract_flags("We have offices around the world and 5 years of growth.")["citizenship"]["value"] == "Not stated"


def test_work_authorization_flag_precision():
    from jobsearch.extract.flags import extract_flags as ef
    # export-license wording is not visa sponsorship
    assert ef("must be able to work under U.S. export laws without sponsorship for an export license.")["sponsorship"]["value"] == "Not stated"
    assert ef("We are unable to sponsor or take over sponsorship of an employment visa.")["sponsorship"]["value"] == "No sponsorship"
    # boilerplate about other positions is not this job's requirement
    assert ef("U.S. Citizenship is required for all positions with a government clearance and certain other "
              "restricted positions.")["citizenship"]["value"] == "Not stated"
    assert ef("Applicant must be a U.S. citizen, lawful permanent resident of the United States, or other protected "
              "individual.")["citizenship"]["value"] == "US person required (export control)"
    assert ef("U.S. citizenship is required.")["citizenship"]["value"] == "US citizenship required"
    assert ef("Applicants may need to meet eligibility requirements for access to classified information, including a "
              "security clearance.")["clearance"]["value"] != "Clearance required"


def test_j1_only_statement_is_not_general_no_sponsorship():
    from jobsearch.extract.flags import extract_flags as ef
    assert ef("DoorDash does not support students on J-1 visas at this time.")["sponsorship"]["value"] == "Not stated"
    assert ef("We do not sponsor H-1B or J-1 visas.")["sponsorship"]["value"] == "No sponsorship"


# --- past or mismatched seasons (co-op track) -------------------------------------------------------------------------
@pytest.mark.parametrize("terms,past", [
    (["Summer 2026"], ["Summer 2026"]),
    (["Fall 2026"], ["Fall 2026"]),
    (["Winter 2026"], ["Winter 2026"]),                       # Dec 2025 – Feb 2026: over
    (["Summer 2026", "Fall 2026"], ["Summer 2026", "Fall 2026"]),
    (["Nov 2026 start"], ["Nov 2026 start"]),                 # starts before the co-op window
    (["Spring 2027"], []), (["Winter 2027"], []), (["January 2027"], []), (["Jan 2027 start"], []),
    (["Fall 2026", "Spring 2027"], []),                       # one current term keeps the job
    (["Class of 2027"], []), ([], []),                        # no season stated: never hidden
])
def test_season_passed(terms, past):
    from jobsearch.extract.dates import season_passed
    assert season_passed(terms, "2026-12", "2026-10") == past


def test_season_passed_without_a_window_uses_today_only():
    from jobsearch.extract.dates import season_passed
    assert season_passed(["Fall 2026"], None, "2026-10") == []           # not over yet
    assert season_passed(["Summer 2026"], None, "2026-10") == ["Summer 2026"]


def test_past_season_filtered_on_coop_track(cfg):
    r = rules(cfg, track="coop_intern", normalized_title="software engineer co op", level="intern", terms=["Fall 2026"])
    assert r.hidden and any(x.startswith("Season has passed: Fall 2026") for x in r.reasons)
    r = rules(cfg, track="coop_intern", normalized_title="software engineer co op", level="intern", terms=["Winter 2026"])
    assert r.hidden and any("Season has passed: Winter 2026" in x for x in r.reasons)
    for ok in (["Spring 2027"], ["January 2027"], ["Fall 2026", "Spring 2027"], []):
        r = rules(cfg, track="coop_intern", normalized_title="software engineer co op", level="intern", terms=ok)
        assert not any("Season has passed" in x for x in r.reasons), ok
    # full-time track: never this rule
    r = rules(cfg, track="full_time", normalized_title="software engineer", terms=["Fall 2026"])
    assert not any("Season has passed" in x for x in r.reasons)
    # soft mode: kept, recorded as a soft issue
    p = cfg.prefs.model_copy(deep=True); p.rules.season_passed = "soft"
    r = apply_rules(job(track="coop_intern", normalized_title="software engineer co op", level="intern",
                        terms=["Fall 2026"]), p, cfg.title_rules, NOW)
    assert not r.hidden and any("Season has passed" in x for x in r.soft_issues)
