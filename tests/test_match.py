"""Hard filters, title rules, semantic matching, score components and labels. (DEMO data)"""
from datetime import datetime, timezone

from jobsearch.match.rules import apply_rules, location_ok, match_title
from jobsearch.match.score import label_for, score_job
from jobsearch.match.semantic import SemanticResult, jd_chunks, semantic_match

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def job(**kw):
    base = dict(id=1, company="DemoCo", company_key="democo", title="Software Engineer (DEMO)",
                normalized_title="software engineer demo", location="Boston, MA", work_mode="onsite",
                employment_type=None, posted_date="2026-10-08T00:00:00+00:00", yoe_required=None,
                salary_min=None, salary_max=None, salary_currency=None, min_reqs=["Python", "Go", "Kafka"],
                pref_reqs=[], skills=["Python", "Go", "Kafka"], grad_years=[], degree=None, flags={})
    base.update(kw)
    return base


def no_sem():
    return SemanticResult(None, {"experience": None, "projects": None, "coursework": None}, [])


def test_title_match_phrase_not_bag_of_words(cfg):
    p, r = cfg.prefs, cfg.title_rules
    assert match_title("software engineer backend", p, r).kind == "target"
    assert match_title("engineer software", p, r).kind == "target"           # reordered
    assert match_title("data engineer product", p, r).kind == "related"
    assert match_title("data center electrical engineer", p, r).kind == "none"
    assert match_title("engineer", p, r).kind == "none"
    assert match_title("account executive", p, r).kind == "none"


def test_hard_filters(cfg):
    p, r = cfg.prefs, cfg.title_rules
    assert not apply_rules(job(), p, r, NOW).hidden
    res = apply_rules(job(title="Senior SWE", normalized_title="senior software engineer"), p, r, NOW)
    assert res.hidden and "senior" in res.reasons[0]
    assert apply_rules(job(normalized_title="software engineer iii"), p, r, NOW).hidden
    assert apply_rules(job(normalized_title="account executive"), p, r, NOW).hidden
    assert apply_rules(job(location="London, UK"), p, r, NOW).hidden


def test_soft_rules_do_not_hide(cfg):
    res = apply_rules(job(yoe_required=5, employment_type="internship"), cfg.prefs, cfg.title_rules, NOW)
    assert not res.hidden and len(res.soft_issues) == 2


def test_missing_data_never_fails_a_rule(cfg):
    res = apply_rules(job(location=None, work_mode="unknown", posted_date=None), cfg.prefs, cfg.title_rules, NOW)
    assert not res.hidden and res.location_ok is None


def test_excluded_company_and_hard_salary(cfg):
    p = cfg.prefs.model_copy(deep=True)
    p.company_tiers.excluded = ["DemoCo"]
    assert apply_rules(job(), p, cfg.title_rules, NOW).hidden
    p = cfg.prefs.model_copy(deep=True)
    p.salary.mode = "hard"
    assert apply_rules(job(salary_min=50000, salary_max=60000, salary_currency="USD"), p, cfg.title_rules, NOW).hidden
    assert not apply_rules(job(), p, cfg.title_rules, NOW).hidden  # salary not stated -> not filtered


def test_remote_location_rules(cfg):
    p = cfg.prefs
    assert location_ok("Remote - US", "remote", p) is True
    assert location_ok("Remote - Canada", "remote", p) is False
    assert location_ok(None, "unknown", p) is None


def test_semantic_match_with_fake_embedder(embedder, resume):
    lines = {s: resume.bullets(s) for s in ("experience", "projects", "coursework")}
    chunks = ["Built a Python service that ingests events into PostgreSQL", "Deploy to Kubernetes on AWS with Terraform",
              "Write pytest suites"]
    sem = semantic_match(embedder, lines, chunks)
    assert sem.score is not None and sem.score > 0
    assert sem.evidence[0].resume_line.startswith("Built a Python FastAPI")
    unrelated = semantic_match(embedder, lines, ["Negotiate enterprise contracts", "Manage quarterly sales quota", "Travel"])
    assert unrelated.score < sem.score
    # cached: a second call does not re-encode
    n = embedder.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    semantic_match(embedder, lines, chunks)
    assert embedder.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == n


def test_jd_chunks_prefers_requirements():
    assert jd_chunks("x" * 50, ["Python experience required here", "Go experience required here", "SQL experience needed here"], []) \
        == ["Python experience required here", "Go experience required here", "SQL experience needed here"]
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


def test_yoe_over_max_penalized_and_gap(cfg, resume):
    ok = score(cfg, resume, job(yoe_required=1))
    over = score(cfg, resume, job(yoe_required=5))
    assert over.overall < ok.overall - 20
    assert any("5+" in g for g in over.gaps)


def test_grad_year_match(cfg, resume):
    good = score(cfg, resume, job(grad_years=[2027]))
    bad = score(cfg, resume, job(grad_years=[2025]))
    assert good.breakdown["qualification"]["parts"]["level"]["score"] > bad.breakdown["qualification"]["parts"]["level"]["score"]


def test_red_flag_penalties_only_when_relevant(cfg, resume):
    flags = {"sponsorship": {"value": "No sponsorship"}, "citizenship": {"value": "Not stated"}}
    base = score(cfg, resume, job(flags=flags))           # demo prefs: needs_sponsorship false
    assert not any("sponsorship" in p["reason"] for p in base.breakdown["penalties"])
    cfg.prefs.work_authorization.needs_sponsorship = True
    hit = score(cfg, resume, job(flags=flags))
    assert hit.overall < base.overall and any("sponsorship" in g for g in hit.gaps)


def test_freshness_and_unknown_posting_date(cfg, resume):
    fresh = score(cfg, resume, job())
    old = score(cfg, resume, job(posted_date="2026-07-01T00:00:00+00:00"))
    unknown = score(cfg, resume, job(posted_date=None))
    pf = lambda r: r.breakdown["preference"]["parts"]["freshness"]["score"]  # noqa: E731
    assert pf(fresh) == 100 and pf(old) < 50 and pf(unknown) is None


def test_labels(cfg):
    assert label_for(70, cfg.prefs) == "Strong"
    assert label_for(69.9, cfg.prefs) == "Stretch"
    assert label_for(50, cfg.prefs) == "Stretch"
    assert label_for(49.9, cfg.prefs) == "Weak"


def test_score_bounded(cfg, resume):
    res = score(cfg, resume, job(yoe_required=15, employment_type="internship",
                                 flags={"clearance": {"value": "Clearance required"}}))
    assert 0 <= res.overall <= 100
