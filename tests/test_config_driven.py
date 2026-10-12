"""Job preferences come from inputs/preferences.yaml and config/, not code (DEMO data)."""
from datetime import datetime, timezone

from jobsearch.adapters.base import FetchContext
from jobsearch.config import Preferences, load_title_rules
from jobsearch.match.rules import apply_rules, is_new_grad_title
from jobsearch.match.score import score_job
from jobsearch.match.semantic import SemanticResult
from jobsearch.output.dashboard import apply_today

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def job(**kw):
    base = dict(company="DemoCo", title="Software Engineer (DEMO)", normalized_title="software engineer", job_id="1",
                source="greenhouse", location="Boston, MA", location_us=True, remote_us=False, work_mode="onsite",
                level="entry", yoe_required=None, flags={}, track="full_time", employment_type=None, min_reqs=[],
                pref_reqs=[], skills=[], grad_years=[], extra={})
    base.update(kw)
    return base


def test_neutral_code_defaults_have_no_personal_dates():
    p = Preferences()
    assert p.graduation_date is None and p.tracks.coop_intern.start_window is None
    assert p.tracks.full_time.start_from is None and p.tracks.coop_intern.boost_terms == []
    assert p.aggregator.queries == []
    # rules still run without them (no start-window rule at all)
    r = apply_rules(job(track="coop_intern", start_windows=[{"start": "2027-06", "end": "2027-08", "evidence": "x"}]),
                    p, load_title_rules(), NOW)
    assert not r.hidden and r.signals["start"]["status"] == "not stated"


def test_coop_no_sponsorship_policy(cfg):
    j = job(track="coop_intern", title="Software Engineering Co-op (DEMO)", normalized_title="software engineering co op",
            level="intern", flags={"sponsorship": {"value": "No sponsorship", "evidence": "No visa sponsorship (DEMO)"}})
    p = cfg.prefs.model_copy(deep=True)
    p.work_authorization.needs_sponsorship = True
    assert apply_rules(j, p, cfg.title_rules, NOW).signals.get("check_cpt_opt")
    p.work_authorization.coop_no_sponsorship = "filter"
    assert apply_rules(j, p, cfg.title_rules, NOW).hidden


def test_scoring_values_come_from_preferences(cfg, resume):
    p = cfg.prefs.model_copy(deep=True)
    rules = apply_rules(job(), p, cfg.title_rules, NOW)
    sem = SemanticResult(None, {"experience": None, "projects": None, "coursework": None}, [])
    a = score_job(job(), resume, p, rules, sem, cfg.synonyms, None, NOW)
    p.scoring.tier_fit = {1: 100, 2: 100, 3: 100}
    b = score_job(job(), resume, p, rules, sem, cfg.synonyms, None, NOW)
    assert a.breakdown["preference"]["parts"]["company_tier"]["score"] == 50
    assert b.breakdown["preference"]["parts"]["company_tier"]["score"] == 100 and b.overall > a.overall
    p.scoring.neutral_qualification = 80
    assert score_job(job(), resume, p, rules, sem, cfg.synonyms, None, NOW).qualification == 80


def test_employment_types_and_grad_slack(cfg):
    p = cfg.prefs.model_copy(deep=True)
    p.rules.employment_type = "hard"
    assert apply_rules(job(employment_type="contract"), p, cfg.title_rules, NOW).hidden
    p.excluded_employment_types = []
    assert not apply_rules(job(employment_type="contract"), p, cfg.title_rules, NOW).hidden
    gw = [{"start": "2027-01", "end": "2027-05", "evidence": "graduating Jan-May 2027 (DEMO)"}]   # yours: 2027-05 (+2 slack)
    p.graduation_date = "2027-07"
    assert not apply_rules(job(grad_windows=gw), p, cfg.title_rules, NOW).hidden
    p.grad_window_slack_months = 0
    assert apply_rules(job(grad_windows=gw), p, cfg.title_rules, NOW).hidden


def test_boost_reason_names_your_term(cfg):
    r = apply_rules(job(terms=["Class of 2027"]), cfg.prefs, cfg.title_rules, NOW)
    assert ("term", "States a preferred start (“Class of 2027”)") in r.boosts


def test_vocabularies_live_in_config():
    rules = load_title_rules()
    assert is_new_grad_title("Software Engineer, New Grad 2027", rules)
    assert not is_new_grad_title("Software Engineer, New Grad 2027", rules.model_copy(update={"new_grad_patterns": []}))
    q = FetchContext().queries
    assert q["workday"] and q["smartrecruiters"] and q["amazon"][0] == {"category[]": "software-development"}
    assert rules.role_profile and rules.contrast_profiles and rules.tech_hint_words and rules.posting_role_words


def test_apply_today_weights_from_preferences(cfg):
    jobs = [dict(job(job_id=str(i)), id=i, label="Stretch", score=60.0, signals={"fresh_hours": h})
            for i, h in ((1, 10), (2, 500))]
    jobs[1]["score"] = 65.0
    a = cfg.prefs.apply_today.model_copy()
    assert apply_today(jobs, "2026-10-12", cfg=a)["full_time"] == [1, 2]          # +10 fresh beats +5 score
    a.fresh_24h = 0
    assert apply_today(jobs, "2026-10-12", cfg=a)["full_time"] == [2, 1]
