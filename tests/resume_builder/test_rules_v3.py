"""resume_rules.md v3 + tool_checklist.md: status tags (rule 1), metric flags (3), section order by level (15–17),
bullet / project budgets (22–24, 36–38), overflow order (53, see test_core), smart keyword matching (61), project
placement (62), category & tool mapping (63), and the evidence_additions.md format. All data is DEMO."""
import json

import pytest

from resume_builder import config as rc
from resume_builder import draft, interview, jd_analysis, keywords, pipeline, review, tools, verify
from resume_builder.config import InputError, load_settings, rules_budget
from resume_builder.content_schema import Bullet, ExperienceEntry
from resume_builder.evidence import build_bank, parse_additions, parse_master, split_tags, status_report

from .conftest import insert_job

CHECKLIST = """# Tool Checklist (DEMO)

## CI/CD
- [x] Jenkins | Proficiency: intermediate | Where: Demo Systems | Level: production
- [ ] GitHub Actions | Proficiency: | Where: | Level:
- [ ] CircleCI | Proficiency: | Where: | Level:

## Containers & orchestration
- [x] Kubernetes | Proficiency: intermediate | Where: Multiple roles | Level: production
- [x] Docker | Proficiency: intermediate | Where: Multiple roles | Level: production

## Databases
- [x] PostgreSQL | Proficiency: intermediate | Where: Multiple roles | Level: production
- [ ] Redis | Proficiency: | Where: | Level:

## Data & ML
- [x] TensorFlow | Proficiency: beginner | Where: Demo ML Notebook | Level: coursework

## Monitoring & logging
- [x] Kubernetes | Proficiency: intermediate | Where: Multiple roles | Level: production

## To confirm (unticked until confirmed)
- [ ] ECS (heard as "GCS"; tick under Cloud (AWS) if you meant ECS)

## Other
- [ ]  | Where: | Level:
"""

RULES_V3 = """# DEMO rules v3
22. Default total: **8–11 bullets**. Adjust after rendering.
23. Primary relevant role: 4 (up to 5) · second relevant role: 3 (up to 4) · relevant internship: 2 (up to 3) · older or less relevant role: 0–1.
24. Starting allocations (Demo Systems / Example Labs / Tiny Startup):
    - Java/backend: 3 / 2 / 1
    - React/full-stack: 2 / 3 / 1
36. Default: **2 projects**, 2 bullets each.
37. **3 projects** only for co-op roles where the third fills a real JD gap.
38. **1 project** for mid-level roles.
"""


def _kw(term, section="required", weight=6.0, category="tools", status="missing"):
    return {"canonical": term, "term": term, "section": section, "count": 1, "weight": weight, "category": category,
            "status": status, "resume_term": None, "evidence": None, "evidence_section": None}


def _job(rb_env, **kw):
    from jobsearch.storage import db
    conn = db.connect(rb_env.db_path)
    level = kw.pop("level", None)
    yoe = kw.pop("yoe", None)
    pk = insert_job(conn, rb_env.jd_dir, **kw)
    if level or yoe is not None:
        conn.execute("UPDATE jobs SET level=COALESCE(?, level), yoe_required=? WHERE id=?", (level, yoe, pk))
        conn.commit()
    conn.close()
    return pk


def _analysis(pk):
    job, pkg = jd_analysis.load_analysis_inputs(str(pk))
    return jd_analysis.analyze(job, pkg, build_bank(), load_settings())


# --- rule 1: status tags --------------------------------------------------------------------------------------------
def test_status_tags_inherit_and_only_confirmed_is_used(rb_env):
    assert split_tags("- Built X [needs confirmation] [client work: Freelance]") == ("- Built X", "needs confirmation",
                                                                                   "Freelance")
    m = rc.MASTER_RESUME
    text = m.read_text()
    text = text.replace("- Reduced PostgreSQL query latency by 35% by adding indexes and rewriting slow joins.",
                        "- Reduced PostgreSQL query latency by 35% by adding indexes and rewriting slow joins. [needs confirmation]")
    text = text.replace("### Web Developer Intern — Tiny Startup, Austin TX (Jan 2020 – May 2020)",
                        "### Web Developer Intern — Tiny Startup, Austin TX (Jan 2020 – May 2020) [demo]")
    text = text.replace("- Libraries/Tools: scikit-learn, Socket.io", "- Libraries/Tools: scikit-learn, Socket.io, Kotlin [demo]")
    text = text.replace("- Databases: PostgreSQL, MongoDB", "- Databases [needs confirmation]: PostgreSQL, MongoDB")
    text = text.replace("## Coursework [confirmed]", "## Coursework")              # untagged section
    m.write_text(text)
    b = build_bank()
    texts = [i.text for i in b.items.values()]
    assert not any("query latency" in t for t in texts)                             # needs confirmation: out
    assert "exp-tiny" not in b.entries and not any(i.entry_id == "exp-tiny" for i in b.items.values())   # demo entry
    assert "Kotlin" not in texts and "Socket.io" in texts                           # per-item tag in a skills list
    assert "MongoDB" not in [i.text for i in b.items.values() if i.section == "skill"]   # line tag after the category
    assert any(i.section == "coursework" for i in b.items.values())                 # untagged = usable (v4)
    assert "exp-demo-b3" in b.items                     # ids stay stable around the excluded exp-demo-b2
    st = {x["id"]: x["status"] for x in b.excluded}
    assert st["exp-demo-b2"] == "needs confirmation" and st["exp-tiny-h"] == "demo" and st["skill-kotlin"] == "demo"
    rep = status_report()
    assert rep["counts"]["untagged"] == 3 and rep["counts"]["demo"] >= 3
    # a resume citing an unconfirmed item is refused, with the reason
    a = _analysis(rb_env.job)
    c = pipeline.assign_ids(draft.baseline(a, b, load_settings()))
    c.experience[0].bullets[0].evidence_ids = ["exp-demo-b2"]
    assert any("not confirmed (needs confirmation" in p for p in pipeline.structure_problems(c, b))
    m.write_text(m.read_text().replace(" [confirmed]", ""))
    assert "exp-demo" in build_bank().entries          # the [confirmed] tag requirement is gone (v4)


# --- rule 3: metric flags -------------------------------------------------------------------------------------------
def test_inconsistent_metrics_are_flagged_never_changed(rb_env):
    m = rc.MASTER_RESUME
    m.write_text(m.read_text().replace("for internal metrics.", "for internal metrics, cutting review time by 35%."))
    b = build_bank()
    f = next(x for x in b.metric_flags if x["metric"] == "35%")
    assert set(f["items"]) == {"exp-demo-b2", "exp-example-b1"} and "never changed" in f["detail"]
    a = _analysis(rb_env.job)
    c = pipeline.assign_ids(draft.baseline(a, b, load_settings()))
    c.experience[0].bullets.append(Bullet(text=b.items["exp-demo-b2"].text, evidence_ids=["exp-demo-b2"]))
    c.experience[1].bullets.append(Bullet(text=b.items["exp-example-b1"].text, evidence_ids=["exp-example-b1"]))
    lint = review.lint(c, b, load_settings(), a)
    assert any(x["rule"] == "inconsistent metric" for x in lint)
    assert "35%" in b.items["exp-demo-b2"].text and "35%" in b.items["exp-example-b1"].text


# --- rules 15–17: section order by level ----------------------------------------------------------------------------
def test_section_order_by_track_and_level(rb_env):
    s = load_settings()
    assert s.audience("coop_intern", "senior") == "student"
    assert s.audience("full_time", "entry") == "student" and s.audience("full_time", "intern") == "student"
    assert s.audience("full_time", "mid") == "experienced" and s.audience("full_time", "senior") == "experienced"
    assert s.audience("full_time", "unknown", 3) == "experienced" and s.audience("full_time", "unknown", 1) == "student"
    mid = _job(rb_env, company="Mid Co", title="Software Engineer II", track="full_time", job_id="demo-mid", level="mid")
    a = _analysis(mid)
    c = pipeline.assign_ids(draft.baseline(a, build_bank(), s))
    assert c.section_order == ["education", "skills", "experience", "projects"]           # rules 24–25 (v4.1)
    a2 = _analysis(rb_env.job)
    c2 = pipeline.assign_ids(draft.baseline(a2, build_bank(), s))
    assert c2.section_order == ["education", "skills", "experience", "projects"]          # every level
    c2.section_order = ["education", "skills", "projects", "experience"]                  # v4.1: no swap either
    assert any("section order" in p for p in pipeline.structure_problems(c2, build_bank(), s, a2))
    c2.section_order = ["skills", "education", "experience", "projects"]
    assert any("section order" in p for p in pipeline.structure_problems(c2, build_bank(), s, a2))


# --- rules 22–24, 36–38: budgets ------------------------------------------------------------------------------------
def test_rules_budget_is_read_from_the_rules_file(rb_env):
    rc.RULES.write_text(RULES_V3)
    b = rules_budget(load_settings())
    assert b["total"] == {"min": 8, "max": 11}
    assert b["roles"]["primary"] == {"start": 4, "max": 5} and b["roles"]["other"]["max"] == 1
    assert b["allocations"]["backend"] == {"Demo Systems": 3, "Example Labs": 2, "Tiny Startup": 1}
    assert b["allocations"]["fullstack"]["Example Labs"] == 3
    assert b["projects"] == {"student": {"count": 2, "max": 3}, "experienced": {"count": 1, "max": 1}}
    rc.RULES.write_text("# no numbers here\n")
    assert rules_budget(load_settings())["allocations"] == {}                    # config defaults, no names


def test_bullet_allocation_projects_and_caps(rb_env):
    rc.RULES.write_text(RULES_V3)
    s = load_settings()
    a = _analysis(rb_env.job)                                  # profile backend (Java / Spring Boot JD)
    assert a["profile"]["picked"] == "backend"
    c = pipeline.assign_ids(draft.baseline(a, build_bank(), s))
    per = {e.entry_id: len(e.bullets) for e in c.experience}
    assert per["exp-demo"] == 3 and per["exp-example"] == 2                # rule 24's starting allocation
    assert sum(per.values()) <= 11 and any("rule 24" in n for n in c.notes.selection)
    assert len(c.projects) == 2 and all(len(p.bullets) <= 2 for p in c.projects)       # rule 36
    mid = _job(rb_env, company="Mid Co", title="Software Engineer II", track="full_time", job_id="demo-mid", level="mid")
    am = _analysis(mid)
    cm = pipeline.assign_ids(draft.baseline(am, build_bank(), s))
    assert len(cm.projects) == 1                                                     # rule 38
    cm.projects = c.projects
    assert any("projects: 2" in p for p in pipeline.structure_problems(cm, build_bank(), s, am))
    c.experience[0].bullets = [Bullet(text=f"Built DEMO item {i} with Java and Spring Boot", evidence_ids=["exp-demo-b1"])
                               for i in range(6)]
    probs = pipeline.structure_problems(c, build_bank(), s, a)
    assert any("rule 23" in p for p in probs)


def test_third_project_only_for_coop_when_it_fills_a_gap(rb_env):
    m = rc.MASTER_RESUME
    m.write_text(m.read_text().replace("## Coursework [confirmed]", "### Demo Infra Lab | Kubernetes, GCP\n"
                                       "- Deployed a DEMO service to GCP with Kubernetes manifests.\n\n## Coursework [confirmed]"))
    s = load_settings()
    a = _analysis(rb_env.job)                       # JD asks for GCP or Kubernetes; nothing else shows them
    c = pipeline.assign_ids(draft.baseline(a, build_bank(), s))
    b = build_bank()
    assert "proj-demo-infra-lab" in [p.entry_id for p in c.projects] and len(c.projects) == 2   # rule 36: 2
    from resume_builder.content_schema import ProjectEntry
    ml = ProjectEntry(entry_id="proj-demo-ml-notebook", bullets=[Bullet(text=b.items["proj-demo-ml-notebook-b1"].text,
                                                                        evidence_ids=["proj-demo-ml-notebook-b1"])])
    c3 = c.model_copy(deep=True)
    c3.projects.append(ml)                           # a 3rd that adds no JD need: refused (rule 37)
    assert any("rule 37" in p for p in pipeline.structure_problems(c3, b, s, a))
    c4 = c.model_copy(deep=True)
    infra = next(p for p in c4.projects if p.entry_id == "proj-demo-infra-lab")
    c4.projects = [p for p in c4.projects if p is not infra] + [ml.model_copy(), infra]   # infra 3rd: fills the GCP gap
    assert not [p for p in pipeline.structure_problems(c4, b, s, a) if "projects" in p]
    ft = _job(rb_env, company="FT Co", title="Software Engineer", track="full_time", job_id="demo-ft3")
    af = _analysis(ft)
    assert any("projects: 3" in p for p in pipeline.structure_problems(c4, b, s, af))   # not co-op: 3 refused


# --- rule 62: project placement -------------------------------------------------------------------------------------
def test_project_work_stays_under_projects_unless_client_work(rb_env):
    b = build_bank()
    a = _analysis(rb_env.job)
    s = load_settings()
    c = pipeline.assign_ids(draft.baseline(a, b, s))
    c.experience[0].bullets.append(Bullet(text=b.items["proj-demo-chat-app-b1"].text,
                                          evidence_ids=["proj-demo-chat-app-b1"]))
    assert any("rule 62" in p for p in pipeline.structure_problems(c, b))
    m = rc.MASTER_RESUME
    m.write_text(m.read_text().replace("### Demo Chat App | Node.js, Socket.io, MongoDB",
                                       "### Demo Chat App | Node.js, Socket.io, MongoDB [client work: Demo Systems]"))
    b2 = build_bank()
    assert b2.entries["proj-demo-chat-app"].client_of == "exp-demo"
    c2 = pipeline.assign_ids(draft.baseline(a, b2, s))
    assert "proj-demo-chat-app" not in [p.entry_id for p in c2.projects]           # it goes under its role
    c2.experience[0].bullets.append(Bullet(text=b2.items["proj-demo-chat-app-b1"].text,
                                           evidence_ids=["proj-demo-chat-app-b1"]))
    assert not [p for p in pipeline.structure_problems(c2, b2) if "rule 62" in p]
    c2.experience[1].bullets.append(Bullet(text=b2.items["proj-demo-chat-app-b2"].text,
                                           evidence_ids=["proj-demo-chat-app-b2"]))
    assert any("rule 62" in p for p in pipeline.structure_problems(c2, b2))       # not under another role


# --- rule 63: tool checklist ----------------------------------------------------------------------------------------
def test_tool_checklist_parse_and_bank(rb_env):
    tl, warn = tools.parse(CHECKLIST)
    assert [t.name for t in tools.ticked(tl)] == ["Jenkins", "Kubernetes", "Docker", "PostgreSQL", "TensorFlow"]
    assert tools.find(tl, "ECS").ticked is False and tools.find(tl, "Jenkins").where == "Demo Systems"
    rc.TOOL_CHECKLIST.write_text(CHECKLIST)
    b = build_bank()
    tl_items = {i.text: i for i in b.items.values() if i.section == "tool"}
    assert set(tl_items) == {"Jenkins", "Kubernetes", "Docker", "PostgreSQL", "TensorFlow"}   # duplicate Kubernetes once
    assert tl_items["Jenkins"].where_entries == ["exp-demo"] and tl_items["Kubernetes"].where_entries == []
    assert b.tool_index["redis"]["ticked"] is False
    a = _analysis(rb_env.job)            # "Experience with GCP or Kubernetes": Kubernetes is now confirmed
    req = next(r for r in a["requirements"] if "Kubernetes" in r["text"])
    assert "Kubernetes" not in req.get("missing_terms", [])
    c = pipeline.assign_ids(draft.baseline(a, b, load_settings()))
    skill_names = [x.name for cat in c.skills for x in cat.items]
    assert "Kubernetes" in skill_names and "Redis" not in skill_names and "GitHub Actions" not in skill_names
    assert any("tool_checklist.md" in n for n in c.notes.selection)


def test_tool_citations_follow_where_and_level(rb_env):
    rc.TOOL_CHECKLIST.write_text(CHECKLIST)
    b = build_bank()
    a = _analysis(rb_env.job)
    c = pipeline.assign_ids(draft.baseline(a, b, load_settings()))
    kube = next(i.id for i in b.items.values() if i.section == "tool" and i.text == "Kubernetes")
    jenk = next(i.id for i in b.items.values() if i.section == "tool" and i.text == "Jenkins")
    tf = next(i.id for i in b.items.values() if i.section == "tool" and i.text == "TensorFlow")
    c.experience[0].bullets[0].evidence_ids.append(kube)          # "Multiple roles": Skills only
    assert any("doesn't place in this role" in p for p in pipeline.structure_problems(c, b))
    c.experience[0].bullets[0].evidence_ids[-1] = jenk            # Where: Demo Systems → allowed there
    assert not [p for p in pipeline.structure_problems(c, b) if "tool" in p]
    c.experience[1].bullets[0].evidence_ids.append(jenk)          # … but not in another role
    assert any("Jenkins" in p for p in pipeline.structure_problems(c, b))
    c.experience[1].bullets[0].evidence_ids.pop()
    proj = next(p for p in c.projects if p.entry_id == "proj-demo-ml-notebook")
    proj.bullets[0].evidence_ids.append(tf)                       # coursework tool in its project: fine
    assert not [p for p in pipeline.structure_problems(c, b) if "TensorFlow" in p]


def test_specific_to_general_only(rb_env):
    rc.TOOL_CHECKLIST.write_text(CHECKLIST)
    b = build_bank()
    jd_analysis.set_category_terms(load_settings())
    ev = b.items["exp-demo-b3"]                                    # "… Docker and Jenkins pipelines."
    ok = verify.technology_problems("Built CI/CD pipelines and containerization", ev.text, set(ev.technologies))
    assert ok == []                                                # Jenkins → CI/CD, Docker → containerization
    bad = verify.technology_problems("Built GitHub Actions workflows", ev.text, set(ev.technologies))
    assert bad                                                     # never general → specific
    assert jd_analysis._supported("CI/CD", {"Jenkins"})[0] and not jd_analysis._supported("Jenkins", {"CI/CD"})[0]


# --- rule 61: smart keyword matching --------------------------------------------------------------------------------
def test_keyword_plan_questions_gaps_wording_and_learn_it(rb_env):
    rc.TOOL_CHECKLIST.write_text(CHECKLIST)
    kws = {"coverage": 40.0, "required_hit": 0, "required_total": 4, "keywords": [
        _kw("Travis CI"), _kw("CircleCI"), _kw("Redis", category="databases"), _kw("Kafka"),
        _kw("MySQL", category="databases"),
        _kw("Java", status="matched", category="skills")]}
    pk = _job(rb_env, company="KW Co", title="Backend Engineer Intern", job_id="demo-kw", keywords=kws)
    a = _analysis(pk)
    plan = keywords.plan(a, build_bank(), load_settings())
    q = {x["keyword"]: x for x in plan["questions"]}
    assert q["Travis CI"]["kind"] == "tool_family" and "Which of these have you actually used" in \
        q["Travis CI"]["question"] and "Jenkins" in q["Travis CI"]["question"]
    assert "CircleCI" not in q["Travis CI"]["options"]         # unticked in the checklist: not offered again
    assert any(g.startswith("CircleCI: unticked") for g in plan["known_gaps"])
    assert q["Kafka"]["kind"] == "keyword" and q["Kafka"]["question"].startswith("Have you used Kafka?")
    assert "Redis" not in q and any(g.startswith("Redis: unticked") for g in plan["known_gaps"])
    assert any(w.startswith("MySQL: write") and "PostgreSQL" in w for w in plan["wording"])
    assert "Java" not in plan["missing"]
    keywords.record_answer("Kafka", "no")                           # asked once: a "no" is remembered
    plan2 = keywords.plan(a, build_bank(), load_settings())
    assert "Kafka" not in {x["keyword"] for x in plan2["questions"]} and any("Kafka" in g for g in plan2["known_gaps"])
    # prepare puts the questions in the brief and records the job's missing keywords for the learn-it list
    out = pipeline.prepare(str(pk))
    brief = (pipeline.job_dir("KW Co", pk, "Backend Engineer Intern") / pipeline.WORK / "brief.md").read_text()
    assert "Ask Nikhil before using these JD keywords" in brief and "Travis CI" in brief
    pk2 = _job(rb_env, company="KW Two", title="Backend Engineer Intern", job_id="demo-kw2", keywords=kws)
    pipeline.prepare(str(pk2))
    rows = {r["keyword"]: r for r in keywords.learn_it(build_bank())}
    assert rows["Travis CI"]["jobs"] == 2 and rows["Kafka"]["declined"] is True
    assert "Travis CI" in (rc.resumes_dir() / "learn_it.md").read_text()
    from resume_builder import hirescope_bridge
    p = hirescope_bridge.write_dashboard_data()
    data = json.loads(p.read_text().split("window.RESUMES = ", 1)[1].rstrip().rstrip(";"))
    assert any(r["keyword"] == "Travis CI" for r in data["learn_it"])
    assert out["state"]["status"] == "generating"


# --- evidence_additions.md format + tool routing --------------------------------------------------------------------
def test_additions_format_status_demo_and_tool_routing(rb_env):
    rc.TOOL_CHECKLIST.write_text(CHECKLIST)
    b = build_bank()
    p = interview.save_answer("exp-demo", "Have you used Spring Batch? If yes, where?",
                              "Wrote nightly Spring Batch jobs for the payments service", True, b, day="2026-10-14",
                              keyword="Spring Batch")
    text = p.read_text()
    assert p == rc.ADDITIONS and "## 2026-10-14 — exp-demo\n- Keyword: Spring Batch\n- Question: Have you used Spring Batch?" in text
    assert "- Status: confirmed\n- Role/project: Software Engineer — Demo Systems Inc, Boston MA" in text
    # a tool answer ticks the checklist instead of duplicating it here
    tp = interview.save_answer("exp-demo", "Which CI/CD tools?", "GitHub Actions for the payments repo", True, b,
                               keyword="GitHub Actions", level="production")
    assert tp == rc.TOOL_CHECKLIST and "GitHub Actions" not in rc.ADDITIONS.read_text()
    t = tools.find(tools.parse(rc.TOOL_CHECKLIST.read_text())[0], "GitHub Actions")
    assert t.ticked and t.level == "production" and "Demo Systems" in t.where
    with pytest.raises(InputError, match="level"):
        interview.save_answer("exp-demo", "Q", "Redis cache for sessions", True, b, keyword="Redis")
    assert tools.confirm(rc.TOOL_CHECKLIST, "Bun", "Demo Chat App", "project") == "added"
    assert "- [x] Bun | Proficiency: intermediate | Where: Demo Chat App | Level: project" in rc.TOOL_CHECKLIST.read_text()
    # statuses and DEMO markers in the additions file
    entries, items, _c, _w = parse_master(rc.MASTER_RESUME.read_text())
    more = (text + "\n## 2026-10-14 — exp-demo\n- Question: Q\n- Answer: Ran load tests on 3 services\n"
            "- Status: needs confirmation\n\n## 2026-10-14 — exp-demo\n- Question: Q\n- Answer: DEMO entry for "
            "testing\n- Status: confirmed\n\n## exp-example — 2026-10-01 — confirmed\n- Q: old form\n- A: Shipped the "
            "dashboard to 2 teams\n")
    adds, warn = parse_additions(interview.HEADER + more, entries, items)   # the header's example is not an entry
    assert [x.text for x in adds] == ["Wrote nightly Spring Batch jobs for the payments service",
                                      "Shipped the dashboard to 2 teams"]
    assert any("needs confirmation" in w for w in warn) and any("DEMO" in w for w in warn)
    assert not any("YYYY-MM-DD — <entry id, e.g." in w for w in warn)
