"""Master v4 format + resume_rules.md v4.1 (DEMO data: fixtures/demo_master_v4.md, demo_template_v4.tex)."""
import shutil
import subprocess
import unicodedata

import pytest

from resume_builder import config as rc
from resume_builder import jd_analysis, pipeline, review, rules_v4, v4build
from resume_builder.config import load_settings
from resume_builder.content_schema import Bullet, SkillItem
from resume_builder.evidence import build_bank

from .conftest import FIX, insert_job, needs_tex


@pytest.fixture
def v4env(rb_env):
    shutil.copy(FIX / "demo_master_v4.md", rc.MASTER_RESUME)
    shutil.copy(FIX / "demo_template_v4.tex", rc.TEMPLATE)
    return rb_env


def _job(env, jd, title="Software Engineer", track="full_time", job_id="v4-1", location="Boston, MA", level=None,
         keywords=None):
    from jobsearch.storage import db
    conn = db.connect(env.db_path)
    pk = insert_job(conn, env.jd_dir, company="DemoCo V4", title=title, track=track, job_id=job_id, jd=jd,
                    min_reqs=[ln[2:] for ln in jd.splitlines() if ln.startswith("- ")], keywords=keywords or {
                        "coverage": 0, "required_hit": 0, "required_total": 0, "keywords": []})
    conn.execute("UPDATE jobs SET location=?, level=COALESCE(?, level) WHERE id=?", (location, level, pk))
    conn.commit()
    conn.close()
    job, pkg = jd_analysis.load_analysis_inputs(str(pk))
    return jd_analysis.analyze(job, pkg, build_bank(), load_settings())


def _kw(term, section="required", weight=6.0):
    return {"canonical": term, "term": term, "section": section, "count": 1, "weight": weight, "category": "tools",
            "status": "missing", "resume_term": None, "evidence": None, "evidence_section": None}


# --- parsing + statuses -----------------------------------------------------------------------------------------------
def test_v4_parse_statuses_builder_notes_and_exclusions(v4env):
    b = build_bank()
    assert b.v4 and set(b.entries) == {"edu-demo", "edu-sample", "exp-freelance", "exp-democorp", "exp-tiny",
                                       "proj-alpha", "proj-beta"}
    texts = [i.text for i in b.items.values()]
    assert not any("Builder" in t for t in texts)                          # instructions, never content
    assert any("Builder" in n for ns in b.v4["notes"].values() for n in ns)
    st = {x["id"]: x["status"] for x in b.excluded}
    assert st["exp-freelance-c3"] == "pending"                            # pending bullet
    assert st["exp-freelance-m-booking-conversions"] == "unverified"      # unverified metric …
    assert st["exp-freelance-c2"].startswith("uses an unverified metric")  # … and the bullet stating it
    assert st["exp-freelance-legacy"] == "legacy" and "course-quantum-basket-weaving" in st
    assert not any("Old Chain" in t or "Old Shop" in t for t in texts)    # excluded role / project
    assert "Blockchain Intern, Old Chain Co" in b.v4["excluded_roles"]
    r = b.v4["roles"]
    assert r["exp-freelance"]["pick"] == [4, 5] and r["exp-democorp"]["pick"] == [3, 4] and r["exp-tiny"]["pick"] == [2, 2]
    assert r["exp-freelance"]["titles"] == ["Software Developer", "Software Engineer (Freelance)",
                                            "Full-Stack Software Engineer (Freelance)"]
    assert "Oracle" in r["exp-freelance"]["skill_only"] and r["exp-democorp"]["barred"] == {"Node.js": "exp-democorp"}
    assert b.v4["overlaps"] == [["exp-freelance-a2", "exp-freelance-b2"]]
    assert b.v4["education"]["edu-demo"]["gpa"] == "3.8/4.0" and b.v4["education"]["edu-sample"]["gpa"] is None
    assert b.v4["contact"]["relocation"] == "Boston, MA (Open to Relocation)"
    sk = b.v4["skills"]
    assert sk["tier"]["Docker Compose"] == "extended" and sk["tier"]["Go"] == "self-reported"
    assert sk["tier"]["LightGBM"] == "planned" and sk["tier"]["Redis"] == "self-reported"   # self-reported beats planned
    assert sk["excluded"] == ["Azure", "RabbitMQ"] and sk["tier"]["C++"] == "core" and sk["tier"]["C"] == "self-reported"


def test_v4_checklist_ticks_without_evidence_are_self_reported_excluded_wins(v4env):
    rc.TOOL_CHECKLIST.write_text("## Tools\n- [x] Terraform | Where: Multiple roles | Level: production\n"
                                 "- [x] RabbitMQ | Where: Multiple roles | Level: production\n"
                                 "- [x] Kafka | Where: Multiple roles | Level: production\n- [ ] Helm | Where: | Level:\n")
    b = build_bank()
    tf = next(i for i in b.items.values() if i.text == "Terraform")
    assert tf.category == "self-reported" and b.v4["skills"]["tier"]["Terraform"] == "self-reported"
    assert not any(i.text == "RabbitMQ" for i in b.items.values())      # the master's Excluded list wins
    assert any("RabbitMQ" in w for w in b.warnings)
    assert b.v4["skills"]["tier"]["Kafka"] == "core"                     # evidence in the master: its tier


# --- rule 3: the default snapshot ---------------------------------------------------------------------------------
def test_v4_default_baseline_is_the_snapshot(v4env):
    b, s = build_bank(), load_settings()
    c = pipeline.assign_ids(v4build.baseline(None, b, s))
    snap = b.v4["snapshot"]
    assert [[x.evidence_ids[0] for x in e.bullets] for e in c.experience] == [r["bullets"] for r in snap["experience"]]
    assert [p.entry_id for p in c.projects] == ["proj-alpha", "proj-beta"]
    assert c.section_order == ["education", "skills", "experience", "projects"] and c.summary is None
    assert [r.category for r in c.skills][-1] == "Certifications"
    assert [x.name for x in c.skills[-1].items] == ["AWS Certified Developer – Associate (DVA-C02)"]   # no Azure
    assert [b.items[x].text for x in c.education[0].course_ids] == ["Distributed Systems", "Cloud Computing"]
    assert c.contact_location == "default"
    assert pipeline.structure_problems(c, b, s, None) == []
    assert sum(len(e.bullets) for e in c.experience) == 9


def _lines(pdf):
    t = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, text=True).stdout
    return [" ".join(x.split()) for x in unicodedata.normalize("NFKC", t).splitlines() if x.strip()]


@needs_tex()
def test_v4_default_resume_reproduces_the_template(v4env):
    """Rule 3 regression: no JD → the snapshot, and its PDF text is the template compiled as written, line by line."""
    out = pipeline.build_default()
    rep = out["report"]
    assert out["status"] == "ready", [f"{x.name}: {x.detail}" for x in rep.errors()]
    assert out["matches_template"] is True
    assert out["pdf"].endswith("/Demo_Candidate_Resume.pdf")    # the Contact name, not "# Master Resume — …"
    ref = rc.resumes_dir() / "_default" / pipeline.WORK / "reference" / "template.pdf"
    assert _lines(ref) == _lines(out["pdf"])
    checks = {x.name: x for x in rep.checks}
    for name in ("no_type3_fonts", "bullets_extract_as_dot", "ligatures_extract_as_letters", "latin_modern_fonts",
                 "layout_margins_0_5in", "layout_body_10_5", "body_size", "contact_one_line", "en_dashes"):
        assert checks[name].ok, (name, checks[name].detail)
    raw = rep.extracted_text
    assert "ﬁ" not in raw and "–" in raw and raw.count("•") == 13


# --- rules 24–25: the same order for every level ------------------------------------------------------------------
def test_v4_section_order_for_every_level(v4env):
    s = load_settings()
    a = _job(v4env, "Requirements\n- 5+ years of Java\n- Kafka", title="Senior Software Engineer", level="mid")
    c = pipeline.assign_ids(v4build.baseline(a, build_bank(), s))
    assert c.section_order == ["education", "skills", "experience", "projects"]
    assert len(c.projects) == 1                                              # rule 48: mid-level → 1 project
    c.section_order = ["education", "skills", "projects", "experience"]
    assert any("rules 24–25" in p for p in rules_v4.problems(c, build_bank(), s, a))


# --- rule 2: precedence -------------------------------------------------------------------------------------------
def test_v4_master_notes_set_selection_but_never_relax_truth(v4env):
    b, s = build_bank(), load_settings()
    c = pipeline.assign_ids(v4build.baseline(None, b, s))
    c.experience[0].bullets = c.experience[0].bullets[:3]                  # the note says 4–5 for this role
    probs = rules_v4.problems(c, b, s, None)
    assert any("the master says 4–5" in p for p in probs)
    c = pipeline.assign_ids(v4build.baseline(None, b, s))
    c.experience[0].bullets[1].text = "Implemented a HIPAA-ready reporting dashboard for the supplier."
    assert any("rule 13" in p for p in rules_v4.problems(c, b, s, None))   # a truthfulness rule always applies


# --- rule 53: skill tiers -----------------------------------------------------------------------------------------
def test_v4_skill_tiers_extended_self_reported_planned(v4env):
    b, s = build_bank(), load_settings()
    a = _job(v4env, "Requirements\n- Go, C, Linux, Ansible and Helm on Linux servers\n- Docker Compose for local "
                    "environments\n- Java and Spring Boot", keywords={"coverage": 0, "required_hit": 0,
                                                                      "required_total": 0, "keywords": []})
    c = pipeline.assign_ids(v4build.baseline(a, b, s))
    names = [x.name for r in c.skills for x in r.items]
    self_rep = [n for n in names if rules_v4.tier_of(b, n) == "self-reported"]
    assert len(self_rep) == 4 and "Docker Compose" in names                 # at most 4; extended when mentioned
    assert not [p for p in rules_v4.problems(c, b, s, a) if "skills" in p]
    c.skills[0].items.append(SkillItem(name="LightGBM", evidence_ids=["skill-c"]))
    probs = rules_v4.problems(c, b, s, a)
    assert any("planned" in p and "LightGBM" in p for p in probs)
    c.skills[0].items.pop()
    c.skills[1].items.append(SkillItem(name="Go", evidence_ids=["skill-go"]))
    c.skills[1].items.append(SkillItem(name="Redis", evidence_ids=["skill-redis"]))
    p53 = rules_v4.problems(c, b, s, a)
    assert any("planned" in p and "Redis" in p for p in p53)        # planned for Beta: never in Skills (rule 50, v4.2)
    a3 = _job(v4env, "Requirements\n- Java and Redis caching", job_id="v4-redis")
    c3 = pipeline.assign_ids(v4build.baseline(a3, b, s))
    assert "Redis" not in [x.name for r in c3.skills for x in r.items]       # even when the JD asks for it
    assert not [p for p in rules_v4.problems(c3, b, s, a3) if "Redis" in p]  # … and the Beta design bullet may name it
    c2 = pipeline.assign_ids(v4build.baseline(None, b, s))
    c2.experience[1].bullets[0].text = "Contributed to Demo Bank's modernization, deploying Go services on Linux."
    assert any("never inside a bullet" in p for p in rules_v4.problems(c2, b, s, None))
    c3 = pipeline.assign_ids(v4build.baseline(None, b, s))
    c3.skills[0].items.append(SkillItem(name="Docker Compose", evidence_ids=["skill-docker-compose"]))
    a2 = _job(v4env, "Requirements\n- Java", job_id="v4-2")
    assert any("extended skill the JD doesn't mention" in p for p in rules_v4.problems(c3, b, s, a2))


# --- rules 8, 11–14, 29, 31, 37, 50, 78, overlaps -------------------------------------------------------------------
def test_v4_content_rules(v4env):
    b, s = build_bank(), load_settings()

    def base():
        return pipeline.assign_ids(v4build.baseline(None, b, s))

    def probs(c, a=None):
        return rules_v4.problems(c, b, s, a)
    c = base()                                      # rule 8: a second p95 figure (freelance B1 + DemoCorp)
    c.experience[0].bullets[1] = Bullet(text=b.items["exp-freelance-b1"].text, evidence_ids=["exp-freelance-b1"])
    assert any("rule 8" in p and "latency" in p for p in probs(c))
    c = base()                                      # rule 8: deployment + pipeline duration
    c.experience[1].bullets[0] = Bullet(text=b.items["exp-democorp-c1"].text, evidence_ids=["exp-democorp-c1"])
    assert any("rule 8" in p and "duration" in p for p in probs(c))
    c = base()                                      # rule 11
    c.experience[1].bullets[0].text = "Designed the architecture of Demo Bank's platform across 3 microservices."
    assert any("rule 11" in p for p in probs(c))
    c = base()                                      # rule 12
    c.experience[0].bullets[1].text = "Implemented a React and Oracle reporting dashboard for the supplier."
    c.experience[1].bullets[1].text = "Migrated 4 messaging workflows to Kafka with Node.js consumers and idempotency."
    p12 = [p for p in probs(c) if "rule 12" in p]
    assert any("Oracle" in p for p in p12) and any("Node.js" in p for p in p12)
    c = base()                                      # rule 14 + intern-sized
    c.experience[2].bullets[0].text = "Owned 5 Java/Spring Boot REST endpoints, making inventory pages 30% faster."
    p14 = probs(c)
    assert any("rule 14" in p for p in p14) and any("intern-sized" in p for p in p14)
    c = base()                                      # rule 78
    c.experience[2].bullets[1].text = "Learned JUnit by adding 20 unit tests for backend validation with Docker Compose."
    assert any("rule 78" in p for p in probs(c))
    c = base()                                      # rule 50
    c.projects[1].bullets[0].text = "Building a DEMO two-stage recommender that improved NDCG by 12%."
    p50 = [p for p in probs(c) if "rule 50" in p]
    assert len(p50) == 2
    c = base()                                      # overlap note: A2 and B2
    c.experience[0].bullets[1] = Bullet(text=b.items["exp-freelance-a2"].text, evidence_ids=["exp-freelance-a2"])
    c.experience[0].bullets.append(Bullet(text=b.items["exp-freelance-b2"].text, evidence_ids=["exp-freelance-b2"]))
    assert any("overlapping bullets" in p for p in probs(c))
    c = base()                                      # extended intern bullet without a JD need
    c.experience[2].bullets[1] = Bullet(text=b.items["exp-tiny-ext1"].text, evidence_ids=["exp-tiny-ext1"])
    assert any("extended-bank bullet" in p for p in probs(c))
    c = base()                                      # rule 37: titles
    c.experience[0].title = "Full-Stack Software Engineer (Freelance)"
    assert not [p for p in probs(c) if "title" in p]
    c.experience[0].title = "Software Developer | Full Stack & AWS"
    assert any("title" in p for p in probs(c))
    c = base()                                      # rule 29: certifications
    c.skills[-1].items = [SkillItem(name="Microsoft Certified: Azure Fundamentals (AZ-900)",
                                    evidence_ids=["cert-microsoft-certified-azure-fundamentals-a"])]
    p29 = [p for p in probs(c) if "rule 29" in p]
    assert any("always show" in p for p in p29) and any("only for cloud roles" in p for p in p29)
    a = _job(v4env, "Requirements\n- Java and Spring Boot on Azure", title="Cloud Engineer", job_id="v4-3",
             location="Remote (US)")
    cc = pipeline.assign_ids(v4build.baseline(a, b, s))                  # cloud + Azure JD → AZ-900; remote → rule 31
    assert len(cc.skills[-1].items) == 2 and cc.contact_location == "relocation"
    assert not [p for p in rules_v4.problems(cc, b, s, a) if "rule 29" in p or "rule 31" in p]
    cc.contact_location = "default"
    assert any("rule 31" in p for p in rules_v4.problems(cc, b, s, a))
    lint = review.lint(base(), b, s, None)
    assert not [x for x in lint if x["rule"] == "sector label"]          # snapshot names its sectors


def test_v4_relocation_by_location():
    assert rules_v4.wants_relocation({"job": {"location": "Remote (US)"}})
    assert rules_v4.wants_relocation({"job": {"location": "HQ - San Diego, CA"}})
    assert not rules_v4.wants_relocation({"job": {"location": "Waltham, Massachusetts, United States"}})
    assert not rules_v4.wants_relocation({"job": {"location": "Spring, Texas · Westford, Massachusetts"}})
    assert not rules_v4.wants_relocation(None)


def test_v4_text_for_tex():
    assert v4build.tex("Feb 2022 – Jan 2025") == "Feb 2022 -- Jan 2025"
    assert v4build.tex("certified workflows & 35%") == r"certif{}ied workf{}lows \& 35\%"
    assert v4build.bar("C++17, Python | Team of 3") == r"C++17, Python \,\textbar\, Team of 3"


def test_v4_layout_spec_regexes(v4env):
    tex = rc.TEMPLATE.read_text()
    assert all(__import__("re").search(rx, tex) for _n, rx, _w in v4build.SPEC)
    drift = tex.replace("left=0.5in", "left=0.75in")
    assert not __import__("re").search(dict((n, rx) for n, rx, _w in v4build.SPEC)["margins_0_5in"], drift)


# --- found in the real runs ---------------------------------------------------------------------------------------
def test_v4_repair_respects_master_minimums_and_snapshot_is_not_weak(v4env):
    """Overflow trimming (rule 67) never cuts a role below the master's pick range, and the approved snapshot bullets
    are never treated as 'weak' (they got position-based priorities 3–4 and the GitHub Actions bullet was cut)."""
    from resume_builder import validate
    b, s = build_bank(), load_settings()
    c = pipeline.assign_ids(v4build.baseline(None, b, s))
    assert all(x.priority <= 2 for _s, _e, x in c.all_bullets())
    floors = {k: (r.get("pick") or [0])[0] for k, r in b.v4["roles"].items()}
    gaps = {"entry": 3.0, "template_entry": 3.0, "leading": 12.0}
    f1 = pipeline.repair(c, validate.Report(pages=2, overflow_lines=40), 1, s, gaps, set(), floors)
    f2 = pipeline.repair(f1.content, validate.Report(pages=2, overflow_lines=40), 2, s, f1.gaps, set(), floors)
    per = {e.entry_id: len(e.bullets) for e in f2.content.experience}
    assert per["exp-freelance"] >= 4 and per["exp-democorp"] >= 3 and per["exp-tiny"] >= 2
    assert any("removed project" in a for a in f2.actions)          # projects go before the role minimums
    f3 = pipeline.repair(f2.content, validate.Report(pages=2, overflow_lines=1), 3, s, f2.gaps, set(), floors)
    assert f3.gaps["entry"] == 2.0 and any("entrygap" in a for a in f3.actions)            # rule 67 spacing
    f4 = pipeline.repair(f3.content, validate.Report(pages=2, overflow_lines=1), 3, s, f3.gaps, set(), floors)
    assert f4.gaps["leading"] == 11.8


def test_v4_profile_limited_to_target_tracks_and_tool_questions_only(v4env):
    a = _job(v4env, "Requirements\n- Machine learning, deep learning, LLMs, AI models and ML pipelines\n- Python",
             title="ML Platform Intern", track="coop_intern", job_id="v4-ml",
             keywords={"coverage": 0, "required_hit": 0, "required_total": 0, "keywords": [
                 _kw("Rust"), {**_kw("OE"), "category": "other"}, {**_kw("code review"), "category": "methodologies"}]})
    assert a["profile"]["picked"] in build_bank().v4["targets"]["profiles"]       # SWE / Data Engineer → backend, data
    from resume_builder import keywords
    q = {x["keyword"] for x in keywords.plan(a, build_bank(), load_settings())["questions"]}
    assert "Rust" in q and not {"OE", "code review"} & q


def test_v4_azure_certificate_display_name(v4env):
    a = _job(v4env, "Requirements\n- Java on Azure", title="Cloud Engineer", job_id="v4-az")
    c = pipeline.assign_ids(v4build.baseline(a, build_bank(), load_settings()))
    assert [x.name for x in c.skills[-1].items][-1] == "Microsoft Azure Fundamentals (AZ-900)"


def test_v4_post_repair_rules_are_rechecked(v4env):
    """A build whose automatic repairs broke a rule is not 'ready' (finalize re-runs the structure / rules checks)."""
    import json
    from resume_builder import versions
    from .conftest import reviewed_flow
    if shutil.which("tectonic") is None:
        pytest.skip("tectonic not installed")
    jid = _job(v4env, "Requirements\n- Java and Spring Boot", job_id="v4-rr")["job"]["pk"]
    pipeline.prepare(str(jid))
    jd = pipeline.job_dir("DemoCo V4", jid, "Software Engineer")
    reviewed_flow(v4env, jd / pipeline.WORK, job=jid)
    with versions.JobLock(jd):
        res = pipeline.build(str(jid))
        res["content"].experience[1].bullets = res["content"].experience[1].bullets[:1]   # as a careless repair would
        fin = pipeline.finalize(res)
    assert fin["status"] == "failed" and any("rules_after_repair" in r for r in fin["reasons"])
    m = json.loads((jd / f"v{fin['version']}" / "manifest.json").read_text())
    assert m["status"] == "failed"


@needs_tex()
def test_v4_relocation_drops_github_from_the_contact_line(v4env):
    """Rule 31 (v4.2): remote / out-of-state jobs → "Boston, MA (Open to Relocation)" and no GitHub link in the
    contact line (the projects link GitHub); the contact line stays on one line."""
    from resume_builder import render
    b, s = build_bank(), load_settings()
    a = _job(v4env, "Requirements\n- Java and Spring Boot", job_id="v4-reloc", location="Austin, TX")
    c = pipeline.assign_ids(v4build.baseline(a, b, s))
    assert c.contact_location == "relocation"
    parts = render.split_template(rc.TEMPLATE.read_text(), s)
    h = v4build.patch_header(parts.header_text, c, b)
    assert "(Open to Relocation)" in h and "github.com/demo-candidate}" not in h and "linkedin.com/in/democ" in h
    res, rep, _ = pipeline.compile_and_validate(c, b, s, v4env.root / "reloc-build",
                                                {"entry": 3.0, "template_entry": 3.0, "leading": 12.0})
    checks = {x.name: x for x in rep.checks}
    assert checks["contact_one_line"].ok and checks["links"].ok, (checks["contact_one_line"].detail, checks["links"].detail)
    assert "+1 (555)" in rep.extracted_text                        # no need to drop "+1" now


def test_v4_declines_and_coursework_level_skills(v4env):
    """evidence_additions.md: a confirmed "no" (decline) is never listed, asked or counted; a coursework-level skill
    may be listed in Technical Skills — labelled "(coursework)" — only when the JD asks, never inside a bullet."""
    from resume_builder import interview, keywords
    b0 = build_bank()
    interview.save_answer("skills", "Have you used Helm?", "no", True, b0, keyword="Helm", where="—")
    interview.save_answer("skills", "Have you used Haskell? If yes, where?", "Used Haskell in coursework at Demo "
                          "University", True, b0, keyword="Haskell", level="coursework", where="Demo University coursework")
    text = rc.ADDITIONS.read_text()
    assert "- Keyword: Helm\n" in text and "- Answer: no\n" in text and "- Level: coursework\n" in text
    b, s = build_bank(), load_settings()
    assert "Helm" in b.declined() and b.v4["skills"]["tier"]["Helm"] == "declined"
    assert b.v4["skills"]["tier"]["Haskell"] == "coursework"
    a = _job(v4env, "Requirements\n- Java, Haskell and Helm\n- Spring Boot", job_id="v4-cw",
             keywords={"coverage": 0, "required_hit": 0, "required_total": 0, "keywords": [_kw("Helm"), _kw("Haskell")]})
    c = pipeline.assign_ids(v4build.baseline(a, b, s))
    names = [x.name for r in c.skills for x in r.items]
    assert "Haskell (coursework)" in c.skills[0].items[-1].name and "Helm" not in names
    assert not [p for p in rules_v4.problems(c, b, s, a) if "skills" in p]
    from resume_builder import verify
    assert verify.verify(c, b).ok                       # the "(coursework)" label doesn't hide the skill from the truth check
    plan = keywords.plan(a, b, s)
    assert any(g.startswith("Helm") for g in plan["known_gaps"]) and "Helm" not in {q["keyword"] for q in plan["questions"]}
    c.skills[0].items[-1].name = "Haskell"                                                      # unlabelled
    assert any("(coursework)" in p for p in rules_v4.problems(c, b, s, a))
    c2 = pipeline.assign_ids(v4build.baseline(None, b, s))
    c2.experience[1].bullets[0].text = "Contributed to Demo Bank's modernization with Haskell services."
    assert any("never inside a bullet" in p for p in rules_v4.problems(c2, b, s, None))
    c3 = pipeline.assign_ids(v4build.baseline(None, b, s))
    c3.skills[0].items.append(SkillItem(name="Helm", evidence_ids=["skill-c"]))
    assert any("haven't used" in p for p in rules_v4.problems(c3, b, s, None))
    from jobsearch.config import load_synonyms
    from jobsearch.extract.resume import parse_resume
    r = parse_resume(rc.MASTER_RESUME, load_synonyms())
    assert "Haskell" in r.skills and "Helm" not in r.skills


def test_v4_check_and_tags_wording(v4env, capsys):
    """A v4 master: excluded items are reported as correctly left out (by reason), tools via the Skills tiers."""
    from resume_builder.__main__ import main
    rc.TOOL_CHECKLIST.write_text("## Tools\n- [x] Terraform | Where: Multiple roles | Level: production\n"
                                 "- [x] Kafka | Where: Multiple roles | Level: production\n")
    assert main(["check"]) == 0
    out = capsys.readouterr().out
    assert "not confirmed" not in out and "ticked tools" not in out
    assert "skills by tier: core" in out and "self-reported" in out
    assert "tool_checklist.md: 2 ticked — 1 already in the master's inventory" in out and "1 self-reported" in out
    assert "correctly left out — Legacy bullets (reference only): 1 (Freelance)" in out
    assert "correctly left out — excluded by your decision" in out and "Old Shop App" in out
    assert "planned course" in out and "pending item" in out and "bullet stating an unverified metric" in out
    assert main(["tags"]) == 0
    t = capsys.readouterr().out
    assert "v4 format" in t and "No [confirmed] tags are needed" in t and "untagged" not in t


# --- resume_rules.md v4.3: selection quality (rules 79–84, 67) ---------------------------------------------------------
def test_v43_tdd_is_never_implied_by_test_writing(v4env):
    """Rule 82: a bullet or a 'direct' requirement may claim TDD only from evidence that states it; coverage never
    counts TDD as present through unit-test bullets; a JD's 'test-driven software development' asks for TDD."""
    from resume_builder import reports
    from resume_builder.content_schema import RequirementMap
    b, s = build_bank(), load_settings()
    c = pipeline.assign_ids(v4build.baseline(None, b, s))
    assert not [p for p in rules_v4.problems(c, b, s, None) if "rule 82" in p]
    c.experience[2].bullets[1].text = ("Added 20 JUnit unit tests through test-driven development and containerized "
                                       "the app with Docker Compose.")
    assert any("rule 82" in p and c.experience[2].bullets[1].id in p for p in rules_v4.problems(c, b, s, None))
    c = pipeline.assign_ids(v4build.baseline(None, b, s))
    tests_id = c.experience[2].bullets[1].evidence_ids[0]
    c.requirements = [RequirementMap(text="Disciplined approach to test-driven software development",
                                     category="direct", evidence_ids=[tests_id])]
    assert any("requirements" in p and "rule 82" in p for p in rules_v4.problems(c, b, s, None))
    c.requirements[0].category = "transferable"
    assert not [p for p in rules_v4.problems(c, b, s, None) if "rule 82" in p]
    cov = reports.coverage(c, b, {"keywords": [{**_kw("Test-Driven Development"), "supported": True}]})
    assert "unit tests" in reports.resume_text(c, b) and not cov["keywords"][0]["represented"]
    a = {"keywords": [], "requirements": [], "required": [], "preferred": [], "mentioned": [],
         "responsibilities": ["Strong and disciplined approach to test-driven software development"]}
    assert rules_v4.mentions(a, "Test-Driven Development") and rules_v4.mentions(a, "Test-Driven Development (TDD)")
    a["responsibilities"] = ["Write unit tests for every service"]
    assert not rules_v4.mentions(a, "Test-Driven Development")


def test_v43_client_is_introduced_before_a_back_reference(v4env):
    """Rule 84: 'the supplier' needs an earlier bullet that introduces the bakery supplier (or its own 'a … supplier');
    overflow trimming never removes the bullet that introduces a client a later bullet refers back to."""
    from resume_builder import validate
    b, s = build_bank(), load_settings()
    c = pipeline.assign_ids(v4build.baseline(None, b, s))
    assert rules_v4.backref_problems(c, b) == []                     # snapshot: A1 (intro) then B ("the supplier")
    fl = c.experience[0].bullets
    fl[0], fl[1] = fl[1], fl[0]
    probs = rules_v4.problems(c, b, s, None)
    assert any("rule 84" in p and fl[0].id in p for p in probs)
    fl[0].text = fl[0].text.replace("for the supplier", "for a DEMO bakery supplier")      # introduces it itself
    assert not [p for p in rules_v4.problems(c, b, s, None) if "rule 84" in p]
    c = pipeline.assign_ids(v4build.baseline(None, b, s))
    intro = c.experience[0].bullets[0]
    intro.priority = 5                                                # the only "weak" bullet is the introduction
    gaps = {"entry": 3.0, "template_entry": 3.0, "leading": 12.0}
    f = pipeline.repair(c, validate.Report(pages=2, overflow_lines=3), 1, s, gaps, set(), {}, b, None)
    kept = f.content.experience[0].bullets if f else c.experience[0].bullets
    assert intro.id in {x.id for x in kept} and rules_v4.backref_problems(f.content if f else c, b) == []
    f2 = pipeline.repair(c, validate.Report(pages=2, overflow_lines=3), 1, s, gaps, set(), {})   # no bank: no guard
    assert intro.id not in {x.id for x in f2.content.experience[0].bullets}


def test_v43_overflow_drops_jd_irrelevant_skills_before_coursework_or_projects(v4env, monkeypatch):
    """Rule 67 (v4.3): JD-irrelevant skills (not named by the JD, not in any bullet) go before coursework or a project,
    only where that saves a rendered line; certifications and JD skills stay."""
    from resume_builder import validate
    b, s = build_bank(), load_settings()
    a = _job(v4env, "Requirements\n- React and Java\n- Spring Boot", job_id="v4-sk")
    c = pipeline.assign_ids(v4build.baseline(a, b, s))
    monkeypatch.setattr(pipeline, "SKILL_ROW_CHARS", 40)             # make the rows wrap
    gaps = {"entry": 3.0, "template_entry": 3.0, "leading": 12.0}
    f = pipeline.repair(c, validate.Report(pages=2, overflow_lines=0), 1, s, gaps, set(), {}, b, a)
    assert any(x.startswith("removed JD-irrelevant skills") for x in f.actions)
    assert "removed optional coursework" not in f.actions and f.content.education[0].course_ids
    assert len(f.content.projects) == len(c.projects)
    before = {x.name for r in c.skills for x in r.items}
    after = {x.name for r in f.content.skills for x in r.items}
    shown = " ".join(x.text for _s, _e, x in c.all_bullets())
    for gone in before - after:
        assert not rules_v4.jd_relevant_skill(a, gone) and gone not in shown
    assert {"React", "Java", "Spring Boot"} <= after
    assert [x.name for x in f.content.skills[-1].items] == [x.name for x in c.skills[-1].items]   # certifications
    # nothing irrelevant left that saves a line → coursework goes next, as before
    f2 = pipeline.repair(c, validate.Report(pages=2, overflow_lines=0), 1, s, gaps, {"section:skills"}, {}, b, a)
    assert "removed optional coursework" in f2.actions and not any("skills" in x for x in f2.actions)


def test_v43_skills_trace_to_the_master_and_ranking_reads_responsibilities(v4env):
    """Rule 83: a Skills / certification item must cite its own master entry. Rule 79: an accomplishment that speaks
    to the JD's responsibilities ranks above one that doesn't, with the same technologies."""
    b, s = build_bank(), load_settings()
    c = pipeline.assign_ids(v4build.baseline(None, b, s))
    assert rules_v4.skill_trace_problems(c, b) == []
    c.skills[0].items.append(SkillItem(name="Go", evidence_ids=[c.skills[0].items[0].evidence_ids[0]]))   # wrong id
    c.skills[-1].items[0].name = "AWS Certified Solutions Architect – Professional"
    p = rules_v4.skill_trace_problems(c, b)
    assert any("'Go'" in x for x in p) and any("certification" in x for x in p)
    a = {"keywords": [], "requirements": [], "responsibilities": ["Build internal tools that validate incoming records"]}

    class It:
        kind, metrics, technologies = "bullet", [], ["Python"]

        def __init__(self, text):
            self.text = text
    hit = It("Automated imports in Python, validating incoming records before reports")
    miss = It("Prototyped suggestions in Python from product descriptions")
    assert v4build.relevance(hit, a) > v4build.relevance(miss, a)


def test_v43_show_dont_list_and_distinct_areas_are_reviewer_findings(v4env):
    """Rules 80–81 (reviewer findings): a JD skill only in Skills while an unused bullet on the page shows it — but a
    specific tool in a bullet already shows its general term (PostgreSQL → SQL); a JD area with no bullet; 3+ bullets
    showing only one area."""
    b, s = build_bank(), load_settings()
    a = _job(v4env, "Requirements\n- Python and SQL\n- React front ends", job_id="v4-sdl",
             keywords={"coverage": 0, "required_hit": 0, "required_total": 0,
                       "keywords": [_kw("Python"), _kw("SQL"), _kw("Redux")]})
    c = pipeline.assign_ids(v4build.baseline(a, b, s))
    f = rules_v4.show_dont_list(c, b, a)
    assert not [x for x in f if x["detail"].startswith("SQL ")]                     # PostgreSQL bullets show SQL
    c.experience[0].bullets.pop(1)                                                  # the Redux dashboard bullet
    f = rules_v4.show_dont_list(c, b, a)
    assert any(x["detail"].startswith("Redux ") and "rule 80" in x["detail"] for x in f)
    assert "frontend" in rules_v4.jd_areas(a) and not [
        x for x in rules_v4.area_findings(c, a) if "no bullet shows frontend" in x["detail"]]
    for ex in c.experience:                                                         # no frontend bullet left
        ex.bullets = [x for x in ex.bullets if "frontend" not in rules_v4.areas_of(x.text)]
    c.projects = []
    found = [x["detail"] for x in rules_v4.area_findings(c, a)]
    assert any("no bullet shows frontend" in d for d in found)
    for x in c.experience[1].bullets:
        x.text = "Migrated messaging workflows to Kafka queues for Demo Bank's REST endpoints."
    assert any("show only backend / database" in d for d in [x["detail"] for x in rules_v4.area_findings(c, a)])


@pytest.mark.parametrize("phrase", ["Understanding of object-oriented design principles", "Experience with OOP",
                                    "object-oriented and functional design principles", "Object Oriented Design",
                                    "OO design patterns", "Solid object-oriented programming skills"])
def test_object_oriented_design_matches_jd_wording(phrase):
    """The extended skill 'Object-Oriented Design' counts as asked for whatever the JD calls it, in a requirement or
    only in its responsibilities."""
    for where in ("required", "responsibilities"):
        a = {"keywords": [], "requirements": [], "required": [], "preferred": [], "mentioned": [], "responsibilities": []}
        a[where] = [phrase]
        assert rules_v4.mentions(a, "Object-Oriented Design"), (phrase, where)


def test_object_oriented_design_not_matched_by_unrelated_text():
    a = {"keywords": [], "requirements": [], "required": ["Design and build REST APIs in Java"], "preferred": [],
         "mentioned": [], "responsibilities": ["Object storage on S3"]}
    assert not rules_v4.mentions(a, "Object-Oriented Design")
