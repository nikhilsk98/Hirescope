"""Two-track hard filters, title/level/YOE rules, semantic matching, score components and labels. (DEMO data)"""
from datetime import datetime, timedelta, timezone

import pytest

from jobsearch.extract.normalize import normalize_title
from jobsearch.match.rules import apply_rules, match_title, quick_relevant
from jobsearch.match.score import label_for, score_job
from jobsearch.match.semantic import SemanticResult, jd_chunks, semantic_match

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def job(**kw):
    base = dict(id=1, company="DemoCo", company_key="democo", title="Software Engineer (DEMO)",
                normalized_title="software engineer demo", location="Boston, MA", location_us=1, remote_us=0,
                work_mode="onsite", employment_type=None, posted_date="2026-10-08T00:00:00+00:00",
                yoe_required=None, salary_min=None, salary_max=None, salary_currency=None,
                min_reqs=["Python", "Go", "Kafka"], pref_reqs=[], skills=["Python", "Go", "Kafka"], grad_years=[],
                degree=None, flags={}, track="full_time", level="unknown")
    base.update(kw)
    return base


def no_sem():
    return SemanticResult(None, {"experience": None, "projects": None, "coursework": None}, [])


def nt(cfg, title):
    return normalize_title(title, cfg.title_rules)


@pytest.mark.parametrize("title,track,kind", [
    ("Software Engineer", "full_time", "core"),
    ("Software Engineer II, Payments", "full_time", "core"),
    ("SDE I", "full_time", "core"),
    ("Backend Engineer - Platform", "full_time", "core"),
    ("Full-Stack Developer", "full_time", "core"),
    ("Member of Technical Staff", "full_time", "core"),
    ("Data Engineer, Product", "full_time", "related"),
    ("Data Center Electrical Engineer", "full_time", "none"),
    ("Account Executive", "full_time", "none"),
    ("Software Engineering Co-op (Spring 2027)", "coop_intern", "coop"),
    ("SWE Intern", "coop_intern", "coop"),
    ("Co-op, Backend Development", "coop_intern", "coop"),
    ("Mechanical Engineering Intern", "coop_intern", "none"),
    ("Marketing Intern", "coop_intern", "none"),
])
def test_title_match_per_track(cfg, title, track, kind):
    assert match_title(nt(cfg, title), track, cfg.prefs, cfg.title_rules).kind == kind


def rules_for(cfg, **kw):
    return apply_rules(job(**kw), cfg.prefs, cfg.title_rules, NOW)


def test_entry_and_mid_are_full_matches(cfg):
    for lvl, yoe in (("entry", 0), ("mid", 3), ("mid", 5), ("unknown", None)):
        r = rules_for(cfg, level=lvl, yoe_required=yoe)
        assert not r.hidden and not r.stretch_reasons, (lvl, yoe)


def test_yoe_window(cfg):
    assert rules_for(cfg, yoe_required=6).stretch_reasons and not rules_for(cfg, yoe_required=6).hidden
    r = rules_for(cfg, yoe_required=7)
    assert r.hidden and "7+" in r.reasons[0]


def test_senior_is_stretch_when_at_most_5_years(cfg):
    r = rules_for(cfg, normalized_title="senior software engineer", level="senior", yoe_required=4)
    assert not r.hidden and r.stretch_reasons
    r = rules_for(cfg, normalized_title="senior software engineer", level="senior", yoe_required=8)
    assert r.hidden


@pytest.mark.parametrize("title,level", [("staff software engineer", "staff_plus"),
                                         ("principal software engineer", "staff_plus"),
                                         ("engineering manager", "manager"),
                                         ("software architect", "staff_plus"),
                                         ("head of engineering", "manager")])
def test_staff_plus_and_managers_excluded(cfg, title, level):
    assert rules_for(cfg, normalized_title=title, level=level).hidden


def test_member_of_technical_staff_not_excluded(cfg):
    assert not rules_for(cfg, normalized_title="member of technical staff", level="unknown").hidden


def test_usa_only_and_unknown_location_is_uncertain(cfg):
    assert rules_for(cfg, location="London, UK", location_us=0).hidden
    r = rules_for(cfg, location=None, location_us=None, work_mode="unknown")
    assert not r.hidden and r.location_ok is None
    assert not rules_for(cfg, location="Remote (US)", location_us=1, remote_us=1, work_mode="remote").hidden


def test_contract_roles_soft(cfg):
    r = rules_for(cfg, employment_type="contract")
    assert not r.hidden and r.soft_issues


def test_track_disabled(cfg):
    p = cfg.prefs.model_copy(deep=True)
    p.tracks.coop_intern.enabled = False
    assert apply_rules(job(track="coop_intern", normalized_title="software engineer intern"), p,
                       cfg.title_rules, NOW).hidden


def test_track_override_wins(cfg):
    r = apply_rules(job(track="unclear", track_override="coop_intern", normalized_title="software engineer"),
                    cfg.prefs, cfg.title_rules, NOW)
    assert r.track == "coop_intern"


def test_excluded_company_and_hard_salary(cfg):
    p = cfg.prefs.model_copy(deep=True)
    p.company_tiers.excluded = ["DemoCo"]
    assert apply_rules(job(), p, cfg.title_rules, NOW).hidden
    p = cfg.prefs.model_copy(deep=True)
    p.salary.mode, p.salary.min = "hard", 100000
    assert apply_rules(job(salary_min=50000, salary_max=60000, salary_currency="USD"), p, cfg.title_rules, NOW).hidden
    assert not apply_rules(job(), p, cfg.title_rules, NOW).hidden  # salary not stated -> not filtered


def test_quick_relevant(cfg):
    q = lambda t, loc=None: quick_relevant(t, loc, cfg.prefs, cfg.title_rules)  # noqa: E731
    assert q("Software Engineer", "Seattle, WA")
    assert q("Software Engineering Intern", None)
    assert not q("Software Engineer", "Bengaluru, India")
    assert not q("Staff Software Engineer", "Seattle, WA")
    assert not q("Account Executive", "Boston, MA")
    assert q("Senior Software Engineer", "Austin, TX")   # senior may be a Stretch: fetch it


def test_semantic_match_with_fake_embedder(embedder, resume):
    lines = {s: resume.bullets(s) for s in ("experience", "projects", "coursework")}
    chunks = ["Built a Python service that ingests events into PostgreSQL", "Deploy to Kubernetes on AWS with Terraform",
              "Write pytest suites"]
    sem = semantic_match(embedder, lines, chunks)
    assert sem.score is not None and sem.score > 0
    assert sem.evidence[0].resume_line.startswith("Built a Python FastAPI")
    unrelated = semantic_match(embedder, lines, ["Negotiate enterprise contracts", "Manage quarterly sales quota", "Travel"])
    assert unrelated.score < sem.score
    n = embedder.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    semantic_match(embedder, lines, chunks)
    assert embedder.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == n


def test_jd_chunks_prefers_requirements():
    reqs = ["Python experience required here", "Go experience required here", "SQL experience needed here"]
    assert jd_chunks("x" * 50, reqs, []) == reqs
    assert jd_chunks("- " + "a" * 45 + "\nshort", [], []) == ["a" * 45]


def score(cfg, resume, j):
    rules = apply_rules(j, cfg.prefs, cfg.title_rules, NOW)
    return score_job(j, resume, cfg.prefs, rules, no_sem(), cfg.synonyms, cfg.companies[0], NOW)


def test_skill_component_and_gaps(cfg, resume):
    res = score(cfg, resume, job())
    parts = res.breakdown["qualification"]["parts"]
    assert round(parts["skills"]["score"]) == 67           # Python, Go yes; Kafka no
    assert res.missing_skills == ["Kafka"] and any("Kafka" in g for g in res.gaps)
    assert "semantic" in res.breakdown["uncertain"]       # no data -> uncertainty, not a zero


def test_preferred_skills_count_half(cfg, resume):
    res = score(cfg, resume, job(min_reqs=["Python"], pref_reqs=["Kafka"], skills=["Python", "Kafka"]))
    assert round(res.breakdown["qualification"]["parts"]["skills"]["score"]) == 67  # (1 + 0)/(1 + 0.5)
    assert res.missing_skills == []


def test_entry_level_not_penalized_for_experience(cfg, resume):
    entry = score(cfg, resume, job(level="entry", yoe_required=0))
    mid = score(cfg, resume, job(level="mid", yoe_required=4))
    assert entry.breakdown["qualification"]["parts"]["level"]["score"] == 100
    assert mid.breakdown["qualification"]["parts"]["level"]["score"] == 100
    assert not entry.breakdown["penalties"] and not mid.breakdown["penalties"]


def test_stretch_band_penalized_and_capped(cfg, resume):
    ok = score(cfg, resume, job(yoe_required=3))
    over = score(cfg, resume, job(yoe_required=6))
    assert over.overall < ok.overall and over.label != "Strong"
    assert any("6+" in g for g in over.gaps)


def test_grad_year_match(cfg, resume):
    good = score(cfg, resume, job(grad_years=[2027]))
    bad = score(cfg, resume, job(grad_years=[2025]))
    assert good.breakdown["qualification"]["parts"]["level"]["score"] > bad.breakdown["qualification"]["parts"]["level"]["score"]


def test_freshness_is_a_capped_bonus(cfg, resume):
    now = NOW
    f = lambda **kw: score(cfg, resume, job(**kw)).breakdown["freshness"]["points"]  # noqa: E731
    assert f(posted_date=(now - timedelta(hours=5)).isoformat()) == 8          # last 24h: biggest boost
    assert f(posted_date=(now - timedelta(hours=50)).isoformat()) == 4         # 24-72h
    assert 0 < f(posted_date=(now - timedelta(days=10)).isoformat()) < 4       # decaying
    assert f(posted_date=(now - timedelta(days=40)).isoformat()) == 0
    assert f(posted_date=None) == 0                                            # unknown: no bonus, no penalty
    coop = f(posted_date=(now - timedelta(hours=5)).isoformat(), track="coop_intern",
             normalized_title="software engineer intern")
    assert coop == 10                                                          # 8 * 1.5 = 12, capped at 10


def test_fresh_weak_cannot_jump_far_ahead(cfg, resume):
    strong = score(cfg, resume, job(posted_date=(NOW - timedelta(days=30)).isoformat()))
    weak = score(cfg, resume, job(posted_date=(NOW - timedelta(hours=1)).isoformat(), min_reqs=["Rust", "Scala", "Kafka"],
                                  skills=["Rust", "Scala", "Kafka"]))
    assert weak.overall - strong.overall <= cfg.prefs.freshness.cap


def test_labels(cfg):
    assert label_for(70, cfg.prefs) == "Strong"
    assert label_for(69.9, cfg.prefs) == "Stretch"
    assert label_for(50, cfg.prefs) == "Stretch"
    assert label_for(49.9, cfg.prefs) == "Weak"


def test_score_bounded(cfg, resume):
    res = score(cfg, resume, job(yoe_required=6, employment_type="contract",
                                 flags={"clearance": {"value": "Clearance required"}}))
    assert 0 <= res.overall <= 100


def test_no_jd_is_neutral_not_a_match(cfg, resume):
    res = score(cfg, resume, job(min_reqs=[], pref_reqs=[], skills=[], extra={"has_jd": False}))
    assert res.qualification == 50.0 and res.label != "Strong"
    assert any("No job description" in g for g in res.gaps)
