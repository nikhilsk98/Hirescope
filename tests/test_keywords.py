"""ATS keyword capture, requirement status, skill gaps / apply today / missed matches, H-1B history,
meaning-based pre-filter. (DEMO data)"""
from datetime import datetime, timedelta, timezone

from jobsearch.extract.keywords import compare, extract_keywords, resume_terms, vocab_for
from jobsearch.match.prefilter import candidate, rescue
from jobsearch.output.dashboard import apply_today, missed_matches, requirement_status, skill_gaps
from jobsearch.output.h1b import load_h1b, match_companies, norm_name

from .conftest import FakeEmbedder

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def kws(cfg, text, mins=(), prefs=(), company=None):
    return extract_keywords(text, list(mins), list(prefs), vocab_for(cfg.synonyms), company)


def test_exact_phrasing_and_acronym_kept_together(cfg):
    ks = kws(cfg, "", ["Experience with Amazon Web Services (AWS) and CI/CD pipelines"])
    terms = {k.term for k in ks}
    assert "Amazon Web Services (AWS)" in terms and "CI/CD" in terms
    aws = next(k for k in ks if k.canonical == "AWS")
    assert aws.section == "required"


def test_multiword_terms_stay_intact_and_stoplist(cfg):
    ks = kws(cfg, "", ["Strong communication skills and experience with distributed systems and machine learning"])
    terms = {k.term.lower() for k in ks}
    assert "distributed systems" in terms and "machine learning" in terms
    assert not terms & {"communication", "skills", "strong", "experience"}


def test_required_vs_preferred_tagging_and_weights(cfg):
    ks = kws(cfg, "Python Python Python. Kafka.", ["Python"], ["Kafka"])
    py = next(k for k in ks if k.canonical == "Python")
    kafka = next(k for k in ks if k.canonical == "Kafka")
    assert py.section == "required" and kafka.section == "preferred" and py.weight > kafka.weight


def test_company_name_is_not_a_keyword(cfg):
    assert "Figma" not in {k.term for k in kws(cfg, "", ["Figma plugins and React"], company="Figma")}


def test_compare_matched_synonym_missing_and_coverage(cfg, resume):
    v = vocab_for(cfg.synonyms)
    ks = extract_keywords("", ["Python, k8s and Rust"], ["Kafka"], v)
    rep = compare(ks, resume_terms(resume), v)
    by = {k.canonical: k for k in rep.keywords}
    assert by["Python"].status == "matched" and by["Python"].evidence
    assert by["Kubernetes"].status == "synonym" and by["Kubernetes"].term == "k8s" and by["Kubernetes"].resume_term == "Kubernetes"
    assert by["Rust"].status == "missing" and by["Kafka"].status == "missing"
    # required weighted higher: Python(3) + k8s(3) of total 3+3+3+1.5
    assert rep.coverage == round(100 * 6 / 10.5, 1) and (rep.required_hit, rep.required_total) == (2, 3)


def test_unknown_tech_terms_from_requirement_lines(cfg):
    ks = kws(cfg, "", ["Experience with FooDB and TurboStream or WASM"])
    assert {"FooDB", "TurboStream", "WASM"} <= {k.term for k in ks}


def test_requirement_status_lines():
    keywords = [{"term": "Python", "status": "matched"}, {"term": "Rust", "status": "missing"}]
    rows = requirement_status(["Python experience", "Python and Rust", "Great attitude"], keywords)
    assert [r[1] for r in rows] == ["✓", "✗", "?"]


def _job(i, **kw):
    base = dict(id=i, company=f"Co{i}", title=f"SWE {i}", track="full_time", label="Strong", score=80,
                first_seen=NOW.isoformat(), posted_date=None, signals={}, deadline=None, filtered=0,
                keywords={"keywords": [{"canonical": "Rust", "term": "Rust", "section": "required", "status": "missing",
                                        "weight": 3, "category": "skills"}]})
    base.update(kw)
    return base


def test_skill_gaps_apply_today_missed():
    jobs = [_job(1), _job(2, signals={"fresh_hours": 5}), _job(3, label="Weak", score=40),
            _job(4, deadline="2026-10-10"), _job(5, track="coop_intern")]
    g = skill_gaps(jobs, NOW)
    assert g["full_time:7"]["skills"][0]["skill"] == "Rust" and g["full_time:7"]["skills"][0]["jobs"] == 3
    q = apply_today(jobs, "2026-10-12")
    assert q["full_time"][0] == 2                      # fresh Strong job first
    assert 3 not in q["full_time"] and 4 not in q["full_time"]   # Weak / past deadline excluded
    assert q["coop_intern"] == [5]
    m = missed_matches([_job(6, label="Weak", qual_score=60), _job(7, filtered=1, title="Platform Developer",
                                                                 filter_reasons=["Title does not match core, related, or co-op/intern software titles"])],
                       lambda j: 1, NOW)
    assert set(m) == {6, 7}
    assert missed_matches([_job(8, label="Weak", first_seen=(NOW - timedelta(days=9)).isoformat())], lambda j: 1, NOW) == []


def test_h1b_history(tmp_path):
    f = tmp_path / "h1b.csv"
    f.write_text("Fiscal Year,Employer (Petitioner) Name,Initial Approval,Initial Denial,Continuing Approval\n"
                 "2025,STRIPE INC,120,3,200\n2024,\"Stripe, Inc.\",100,1,150\n2025,DEMOCORP LLC,1,0,0\n", encoding="utf-8")
    data = load_h1b(f)
    assert norm_name("Stripe, Inc.") == "stripe" and data["stripe"] == {"2025": 320, "2024": 250}
    m = match_companies(["Stripe", "Unknown Startup"], data)
    assert m["Stripe"]["total"] == 570 and "Unknown Startup" not in m


def test_prefilter_candidates_and_rescue():
    title_only = ["Title does not match core, related, or co-op/intern software titles"]
    assert candidate({"title": "Platform Developer II"}, title_only)
    assert not candidate({"title": "Account Executive"}, title_only)               # no tech hint
    assert not candidate({"title": "Platform Developer"}, [*title_only, "Not in the US: London"])  # other rules too
    emb = FakeEmbedder()
    hits = rescue(emb, [(1, "Software developer writing, testing and deploying application code"),
                        (2, "Retail store associate stocking shelves")], threshold=0.5, margin=0.0)
    assert 1 in hits and 2 not in hits


def test_umbrella_terms_satisfied_by_concrete_resume_skills(cfg, resume):
    v = vocab_for(cfg.synonyms)
    rep = compare(extract_keywords("", ["Version control and relational databases"], [], v), resume_terms(resume), v)
    by = {k.canonical: k for k in rep.keywords}
    assert by["Version Control"].status == "synonym" and by["Version Control"].resume_term == "Git"
    assert by["Relational Databases"].status == "synonym" and by["Relational Databases"].resume_term == "PostgreSQL"


def test_prefilter_requires_software_word():
    title_only = ["Title does not match core, related, or co-op/intern software titles"]
    assert not candidate({"title": "Design Engineer Intern"}, title_only)
    assert not candidate({"title": "Systems Engineering Intern"}, title_only)
    assert candidate({"title": "Senior .NET Developer"}, title_only)
