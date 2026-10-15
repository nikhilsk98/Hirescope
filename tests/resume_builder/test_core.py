"""Resume Builder Phase 1: evidence bank, JD analysis, content schema, LaTeX rendering, Tectonic, PDF validation,
bounded repair. All data is DEMO."""
import json

import pytest

from resume_builder import config as rc
from resume_builder import draft, jd_analysis, pipeline, render, validate, versions
from resume_builder.config import InputError, load_settings
from resume_builder.content_schema import ContentError, dump, parse
from resume_builder.evidence import build_bank, load_bank, metrics_in, parse_additions, parse_master

from .conftest import insert_job, needs_tex, reviewed_flow


# --- evidence bank -------------------------------------------------------------------------------------------
def test_bank_parses_entries_items_and_metrics(rb_env):
    b = build_bank()
    exp = [e for e in b.entries.values() if e.section == "experience"]
    assert [e.organization for e in exp] == ["Demo Systems Inc", "Example Labs", "Tiny Startup"]
    se = b.entries["exp-demo"]
    assert (se.role, se.location, se.dates, se.start, se.end) == ("Software Engineer", "Boston MA",
                                                                  "Jan 2022 – Present", "2022-01", "present")
    # sections stay separate: projects / coursework / experience never mix
    assert {i.section for i in b.by_entry("proj-demo-chat-app")} == {"project"}
    assert {i.text for i in b.items.values() if i.section == "coursework"} == {"Distributed Systems", "Algorithms",
                                                                                "Databases"}
    b1 = b.items["exp-demo-b1"]
    assert b1.metrics == ["2,000+"] and "Java" in b1.technologies and b1.actions == ["Built"]
    assert b.items["exp-demo-b3"].metrics == []          # no metric stated → none recorded
    assert "Bachelor's Degree" in b.items["edu-sample-h"].technologies
    assert [c for c, _ in b.skill_categories][:2] == ["Languages", "Web/Frameworks"]


def test_metrics_are_only_numbers_written():
    assert metrics_in("Reduced latency by 35% across 2,000+ requests in 2021 for a 5-member team") == \
        ["35%", "2,000+", "5-member"]


def test_bank_rebuilds_when_inputs_change(rb_env):
    b1 = load_bank()
    again = load_bank()
    assert again.hashes == b1.hashes
    m = rc.MASTER_RESUME
    m.write_text(m.read_text().replace("for internal metrics.", "for internal metrics used by 3 teams."))
    b2 = load_bank()
    assert b2.hashes["master_resume"] != b1.hashes["master_resume"]
    assert "3" in b2.items["exp-example-b1"].metrics


@pytest.mark.parametrize("bad,msg", [
    ("## Experience\n### Engineer — Acme, Boston\n- did things\n## Skills\n- Languages: Java\n", "has no dates"),
    ("## Experience\n### Engineer at Acme (Jan 2020 – Feb 2021)\n- x\n## Skills\n- Languages: Java\n", "must be"),
    ("## Experience\n### Engineer — Acme (Mar 2021 – Jan 2020)\n- x\n## Skills\n- Languages: Java\n", "ends before"),
    ("## Experience\n### Engineer — Acme (Jan 2020 – Feb 2021)\n- x\n### Engineer — Acme (Jan 2019 – Feb 2021)\n- y\n"
     "## Skills\n- Languages: Java\n", "appears twice with different dates"),
    ("## Skills\n- Languages: Java\n", "no '## Experience'"),
    ("## Experience\n- orphan bullet\n## Skills\n- Languages: Java\n", "not under a '###' heading"),
])
def test_malformed_or_contradictory_master_resume(bad, msg):
    with pytest.raises(InputError, match=msg):
        parse_master(bad)


def test_missing_master_resume_is_a_clear_error(rb_env):
    rc.MASTER_RESUME.unlink()
    with pytest.raises(InputError, match="master_resume.md is missing"):
        pipeline.check_inputs()


def test_additions_only_confirmed_and_attributed(rb_env):
    entries, items, _, _ = parse_master(rc.MASTER_RESUME.read_text())
    text = ("# Evidence additions (DEMO)\n\n## exp-demo — 2026-10-13 — confirmed\n- Q: How many services?\n"
            "- A: The payments service was one of 6 services owned by the team.\n\n"
            "## exp-demo — 2026-10-13 — draft\n- A: not confirmed, never used\n")
    adds, warnings = parse_additions(text, entries, items)
    assert [a.text for a in adds] == ["The payments service was one of 6 services owned by the team."]
    assert adds[0].entry_id == "exp-demo" and adds[0].source == "additions" and adds[0].metrics == ["6"]
    assert warnings and "ignored" in warnings[0]
    with pytest.raises(InputError, match="unknown entry id"):
        parse_additions("## exp-nope — 2026-10-13 — confirmed\n- A: x\n", entries, items)


def test_template_bullets_missing_from_master_are_reported(rb_env):
    t = rc.TEMPLATE.read_text().replace("\\end{itemize}", "\\item Invented a fusion reactor (DEMO).\n\\end{itemize}")
    rc.TEMPLATE.write_text(t)
    b = build_bank()
    assert any("not in master_resume.md" in w and "fusion reactor" in w for w in b.warnings)


# --- JD analysis -----------------------------------------------------------------------------------------------
def _analysis(rb_env, ref=None, profile=None):
    job, pkg = jd_analysis.load_analysis_inputs(str(ref or rb_env.job))
    return jd_analysis.analyze(job, pkg, build_bank(), load_settings(), profile)


def test_requirement_mapping_categories(rb_env):
    a = _analysis(rb_env)
    by = {r["text"]: r for r in a["requirements"]}
    assert by["Experience with Java and Spring Boot"]["category"] == "direct"
    assert by["Experience with Java and Spring Boot"]["evidence_ids"]
    gcp = by["Experience with GCP or Kubernetes"]
    # AWS is related evidence, never the same thing as GCP
    assert gcp["category"] == "transferable" and gcp["related_terms"] == ["AWS", "Docker"] and gcp["missing_terms"] == ["GCP", "Kubernetes"]
    assert by["Strong communication skills"]["category"] == "needs_judgment"
    kw = {k["term"]: k for k in a["keywords"]}
    assert kw["Java"]["supported"] and not kw["GCP"]["supported"]
    assert a["track"] == "coop_intern" and a["profile"]["picked"] == "backend"
    assert a["responsibilities"][0].startswith("Build and operate REST APIs")


def test_profile_override_and_unknown_profile(rb_env):
    assert _analysis(rb_env, profile="ml")["profile"]["picked"] == "ml"
    with pytest.raises(InputError, match="unknown profile"):
        _analysis(rb_env, profile="astronaut")


@pytest.mark.parametrize("ref", ["../../etc/passwd", "1; rm -rf ~", "", "a" * 200, "job id"])
def test_job_ref_validation(rb_env, ref):
    with pytest.raises(InputError):
        jd_analysis.load_analysis_inputs(ref)


def test_unknown_and_ambiguous_job_refs(rb_env):
    from jobsearch.storage import db
    with pytest.raises(InputError, match="no HireScope job"):
        jd_analysis.load_analysis_inputs("999999")
    conn = db.connect(rb_env.db_path)
    insert_job(conn, rb_env.jd_dir, company="OtherCo", job_id="demo-1")
    conn.close()
    with pytest.raises(InputError, match="matches 2 jobs"):
        jd_analysis.load_analysis_inputs("demo-1")
    assert jd_analysis.load_analysis_inputs(str(rb_env.job))[0]["company"] == "DemoCorp"


# --- content schema ----------------------------------------------------------------------------------------------
def _baseline(rb_env, ref=None):
    a = _analysis(rb_env, ref)
    return a, draft.baseline(a, build_bank(), load_settings())


def test_baseline_selection_and_track_order(rb_env):
    a, c = _baseline(rb_env)
    assert c.section_order == ["education", "skills", "experience", "projects"]      # co-op: rule 15
    assert [e.entry_id for e in c.experience][0] == "exp-demo"                         # reverse chronological
    assert all(b.origin == "verbatim" for _s, _e, b in c.all_bullets())
    first = c.experience[0].bullets[0]
    assert "Java" in first.text                                                        # JD-relevant bullet first
    _a2, c2 = _baseline(rb_env, rb_env.ft_job)
    assert c2.section_order == ["education", "skills", "experience", "projects"]       # new grad (entry): rule 15
    sk = {s.category: [i.name for i in s.items] for s in c.skills}
    assert sk["Languages"][0] == "Java"                                                # JD-matched skills first


def test_schema_rejects_raw_latex_fields_and_bad_input(rb_env):
    _a, c = _baseline(rb_env)
    d = json.loads(dump(c))
    d["experience"][0]["latex"] = "\\input{/etc/passwd}"
    with pytest.raises(ContentError, match="Extra inputs are not permitted"):
        parse(d)
    d = json.loads(dump(c))
    d["experience"][0]["bullets"][0]["evidence_ids"] = []
    with pytest.raises(ContentError, match="evidence_ids"):
        parse(d)
    d = json.loads(dump(c))
    d["experience"][0]["bullets"][0]["text"] = "Built things\x07 quickly and well"
    with pytest.raises(ContentError, match="control characters"):
        parse(d)
    with pytest.raises(ContentError, match="not valid JSON"):
        parse("{nope")
    d = json.loads(dump(c))
    d["section_order"] = ["skills", "education"]
    with pytest.raises(ContentError, match="missing"):
        parse(d)


def test_structure_problems_block_misattribution(rb_env):
    _a, c = _baseline(rb_env)
    bank = build_bank()
    c.experience[0].bullets[0].evidence_ids = ["exp-example-b1"]      # another employer's fact
    c.experience[1].bullets[0].evidence_ids = ["nope-1"]
    probs = pipeline.structure_problems(c, bank)
    assert any("from another entry" in p for p in probs) and any("unknown evidence" in p for p in probs)
    c2 = _baseline(rb_env)[1]
    c2.experience = c2.experience[1:]
    assert any("most recent role" in p for p in pipeline.structure_problems(c2, bank))


# --- rendering -------------------------------------------------------------------------------------------------
def test_escape_every_special_character():
    s = r"50% & $5 #1 a_b {x} ~y ^z \cmd"
    e = render.escape(s)
    assert e == (r"50\% \& \$5 \#1 a\_b \{x\} \textasciitilde{}y \textasciicircum{}z \textbackslash{}cmd")
    assert "\\input" not in render.escape(r"\input{/etc/passwd}").replace("\\textbackslash{}input", "")


def test_links_are_validated_and_escaped():
    assert render.safe_url("javascript:alert(1)") is None
    assert render.safe_url("https://x.example/a b") is None
    assert render.href("javascript:alert(1)", "click") == "click"
    h = render.href("https://github.com/demo/repo_name#readme%20x", "https://github.com/demo/repo_name#readme%20x")
    assert h.startswith(r"\href{https://github.com/demo/repo_name\#readme\%20x}")


def test_template_is_split_not_modified_and_font_falls_back(rb_env):
    before = rc.TEMPLATE.read_bytes()
    parts = render.split_template(rc.TEMPLATE.read_text(), load_settings())
    assert rc.TEMPLATE.read_bytes() == before
    assert "Demo Candidate" in parts.preamble_header and "\\section{Experience}" not in parts.preamble_header
    assert r"\IfFontExistsTF{Calibri}" in parts.preamble_header and "Carlito" in parts.preamble_header
    assert render.contact_from_header(parts.header_text)["links"][0]["url"] == "mailto:demo.candidate@example.com"
    for bad in ("\\documentclass{article}\\begin{document}x", "\\documentclass{article}\\begin{document}\\end{document}",
                "\\documentclass{article}\\immediate\\write18{ls}\\begin{document}\\begin{center}\\end{center}\\end{document}"):
        with pytest.raises(InputError):
            render.split_template(bad, load_settings())


# --- compile + validate (real Tectonic) -----------------------------------------------------------------------
def _build(rb_env, c, tmp_name="b"):
    bank, s = build_bank(), load_settings()
    d = rb_env.root / tmp_name
    res, rep, parts = pipeline.compile_and_validate(c, bank, s, d, {"entry": 6, "education": 4})
    return res, rep


@needs_tex()
def test_special_characters_and_long_links_compile(rb_env):
    m = rc.MASTER_RESUME
    m.write_text(m.read_text().replace(
        "### Demo Chat App | Node.js, Socket.io, MongoDB",
        "### Demo Chat App (https://github.com/demo-candidate/a-very-long-repository-name_with_underscores-and-more) "
        "| Node.js, Socket.io, MongoDB").replace(
        "supporting group rooms.", "supporting group rooms & 100% of C# / C++ clients ($0 cost, #1 in tests, a_b {x} ~y ^z)."))
    _a, c = _baseline(rb_env)
    for p in c.projects:
        for b in p.bullets:
            if "group rooms" in b.text:
                b.priority = 1
    res, rep = _build(rb_env, c)
    assert res.ok, res.errors
    names = {ch.name: ch for ch in rep.checks}
    assert names["text_matches_content"].ok, names["text_matches_content"].detail
    assert names["links"].ok, names["links"].detail
    assert "100%" in rep.extracted_text and "C#" in rep.extracted_text and "$0" in rep.extracted_text


@needs_tex()
def test_demo_resume_passes_validation(rb_env):
    _a, c = _baseline(rb_env)
    res, rep = _build(rb_env, c)
    assert res.ok and rep.ok, [f"{x.name}: {x.detail}" for x in rep.errors()]
    assert rep.pages == 1 and rep.images and res.font_used in ("Calibri", "Carlito")
    assert "demo.candidate@example.com" not in res.log_text          # contact details never in logs


@needs_tex()
def test_garbled_or_missing_text_is_detected(rb_env):
    _a, c = _baseline(rb_env)
    res, _rep = _build(rb_env, c)
    bank, s = build_bank(), load_settings()
    expected = render.plain_lines(c, bank) + [("bullet", "A bullet that never made it into the PDF at all")]
    parts = render.split_template(rc.TEMPLATE.read_text(), s)
    contact = render.contact_from_header(parts.header_text)
    rep = validate.validate_pdf(res.pdf, expected, contact["links"], contact, s, {})
    names = {ch.name: ch for ch in rep.checks}
    assert not names["text_matches_content"].ok and not names["bullet_characters"].ok
    assert not rep.ok
    assert validate.key("Eﬃcient ﬁx – done") == validate.key("Efficient fix - done")


@needs_tex()
def test_garbled_extraction_flags_cid_glyphs(rb_env, monkeypatch):
    """pdftotext output with unmapped glyphs ((cid:N)) fails the garbled-text check."""
    _a, c = _baseline(rb_env)
    res, _rep = _build(rb_env, c)
    real = validate._run
    monkeypatch.setattr(validate, "_run", lambda cmd, timeout=60: (real(cmd, timeout).replace("Java", "(cid:12)(cid:7)")
                                                                   if cmd[0] == "pdftotext" else real(cmd, timeout)))
    s = load_settings()
    parts = render.split_template(rc.TEMPLATE.read_text(), s)
    contact = render.contact_from_header(parts.header_text)
    rep = validate.validate_pdf(res.pdf, render.plain_lines(c, build_bank()), contact["links"], contact, s, {})
    names = {ch.name: ch for ch in rep.checks}
    assert not names["garbled_text"].ok and not names["text_matches_content"].ok


@needs_tex()
def test_overflow_triggers_trimming_in_priority_order(rb_env):
    """DEMO content two pages long: the build loop removes priority-5 bullets first and ends on one page."""
    m = rc.MASTER_RESUME
    text = m.read_text()
    for anchor, tag in (("- Deployed services to AWS with Docker and Jenkins pipelines.\n", "A"),
                        ("- Wrote Python scripts to automate weekly report generation for a 4-person team.\n", "B"),
                        ("- Implemented landing pages in HTML, CSS and JavaScript.\n", "C"),
                        ("- Stored messages in MongoDB with TTL indexes for automatic cleanup.\n", "D"),
                        ("- Trained a scikit-learn classifier on a public dataset and reported 0.91 F1.\n", "E")):
        filler = "".join(f"- Filler {tag}{i} described at length: DEMO work on internal tooling, reporting pipelines, "
                         f"dashboards and on-call runbooks for the operations group across several quarters, with weekly "
                         f"reviews, written postmortems and long hand-offs to the support group.\n"
                         for i in range(1, 6))
        text = text.replace(anchor, anchor + filler)
    m.write_text(text)
    import yaml
    cfg = yaml.safe_load(rc.CONFIG_FILE.read_text())       # this test is about the trim order, not rule 22–23 caps
    cfg["experience_bullets"] = {"total": {"min": 1, "max": 30}, **{k: {"start": 4, "max": 6} for k in
                                                                     ("primary", "second", "internship", "other")}}
    rc.CONFIG_FILE.write_text(yaml.safe_dump(cfg))
    a = _analysis(rb_env)
    bank, s = build_bank(), load_settings()
    c = draft.baseline(a, bank, s)
    from resume_builder.content_schema import Bullet, ExperienceEntry
    for e in bank.entries.values():         # rule 23 may leave out an irrelevant old role; this test wants them all
        if e.section == "experience" and e.id not in {x.entry_id for x in c.experience}:
            first = next(i for i in bank.by_entry(e.id) if not i.id.endswith("-h"))
            c.experience.append(ExperienceEntry(entry_id=e.id, priority=4,
                                                bullets=[Bullet(text=first.text, evidence_ids=[first.id])]))
    for ex in c.experience:
        fill = [i for i in bank.by_entry(ex.entry_id) if i.text.startswith("Filler")]
        ex.bullets = ex.bullets[:1] + [Bullet(text=i.text, evidence_ids=[i.id], priority=5) for i in fill]
    for pr in c.projects:
        fill = [i for i in bank.by_entry(pr.entry_id) if i.text.startswith("Filler")]
        pr.bullets = pr.bullets[:1] + [Bullet(text=i.text, evidence_ids=[i.id], priority=5) for i in fill[:3]]
    work = pipeline.job_dir("DemoCorp", rb_env.job, "Backend Engineer Intern") / pipeline.WORK
    pipeline.prepare(str(rb_env.job))
    (work / "content.json").write_text(dump(pipeline.assign_ids(c)))
    res = pipeline.build(str(rb_env.job), require_review=False)
    assert res["attempts"][0]["pages"] >= 2 and res["report"].pages == 1, res["attempts"]
    kinds = ["repeated" if "repeated" in r else "priority 5" if "priority 5" in r else r for r in res["repairs"]]
    assert kinds and set(kinds) <= {"repeated", "priority 5"}
    assert kinds == sorted(kinds, key=lambda k: k != "repeated")      # repetition first, then lowest priority
    left = [b for _s, _e, b in res["content"].all_bullets()]
    assert all(b.priority < 5 for b in left if not b.text.startswith("Filler")) and len(res["attempts"]) <= 3
    assert all(len(ex.bullets) >= 1 for ex in res["content"].experience)


def test_repair_order_without_compiling(rb_env):
    """Rule 53: attempt 1 drops repetition, weak (priority 4–5) bullets, then optional coursework and the summary;
    attempt 2 reduces bullets, then projects, then an irrelevant old role; attempt 3 only shrinks entry spacing (never
    fonts/margins)."""
    _a, c = _baseline(rb_env)
    s = load_settings()
    from resume_builder.content_schema import Bullet, Summary
    c.experience[0].bullets.append(Bullet(text=c.experience[0].bullets[0].text + " ", evidence_ids=["exp-demo-b1"],
                                          priority=2))
    c.experience[0].bullets[1].priority = 5
    c.summary = Summary(text="DEMO software engineer with 3+ years building web services.",
                        evidence_ids=["summary-1"])
    pipeline.assign_ids(c)
    rep = validate.Report(pages=2, overflow_lines=2)
    f1 = pipeline.repair(c, rep, 1, s, {"entry": 6, "education": 4})
    assert f1.actions[0].startswith("removed repeated bullet")
    assert any("priority 5" in a for a in f1.actions)
    assert f1.gaps["entry"] == 6 and not any("lower-priority" in a or "project" in a for a in f1.actions)
    c.education[0].course_ids = [i.id for i in build_bank().items.values() if i.section == "coursework"][:2]
    big = pipeline.repair(c, validate.Report(pages=2, overflow_lines=40), 1, s, {"entry": 6, "education": 4})
    i_course, i_summary = big.actions.index("removed optional coursework"), big.actions.index("removed the summary")
    assert max(i for i, a in enumerate(big.actions) if "weak bullet" in a) < i_course < i_summary   # rule 53 order
    assert big.content.summary is None and not any(e.course_ids for e in big.content.education)
    rep2 = validate.Report(pages=2, overflow_lines=40)
    f2 = pipeline.repair(big.content, rep2, 2, s, big.gaps)
    assert any("lower-priority bullet" in a for a in f2.actions)
    f3 = pipeline.repair(f2.content, validate.Report(pages=2, overflow_lines=1), 3, s, f2.gaps)
    assert f3.actions == ["entry spacing 6pt → 2.0pt (layout.min_entry_gap_pt)"] and f3.gaps["entry"] == 2.0
    assert pipeline.repair(f3.content, validate.Report(pages=2, overflow_lines=1), 3, s, f3.gaps) is None
    assert pipeline.repair(c, validate.Report(pages=1), 1, s, {"entry": 6}) is None


# --- naming / paths -------------------------------------------------------------------------------------------
def test_employer_facing_name_and_paths(rb_env):
    s = load_settings()
    assert pipeline.pdf_name(s, "Clēnera") == "Demo_Candidate_Resume_Clenera.pdf"
    assert pipeline.pdf_name(s, "Hewlett Packard Enterprise") == "Demo_Candidate_Resume_Hewlett_Packard_Enterprise.pdf"
    assert pipeline.pdf_name(s, "../../etc") == "Demo_Candidate_Resume_etc.pdf"
    d = pipeline.job_dir("AT&T / Mobility", 42, "Sr. Engineer (Backend) – C++/Go!")
    assert (d.parent.name, d.name) == ("AT-T-Mobility", "Sr-Engineer-Backend-C-Go_42")
    assert d.parent.parent == rc.resumes_dir()
    d.mkdir(parents=True)                                       # found again by job ID even if the title changes
    assert pipeline.job_dir("AT&T", 42, "New title") == d
    for bad in ("../x", ".hidden", "a/b", "-rf", ""):
        with pytest.raises(InputError):
            pipeline.safe_child(d, bad)
    assert pipeline.safe_child(d, "v1").name == "v1"


def test_settings_validation(rb_env, tmp_path):
    import yaml
    raw = yaml.safe_load(rc.CONFIG_FILE.read_text())
    raw["section_order"]["coop_intern"] = ["skills", "skills", "education", "projects"]
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(InputError, match="section_order"):
        load_settings(p)
    raw = yaml.safe_load(rc.CONFIG_FILE.read_text())
    raw["repair"]["max_attempts"] = 9
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(InputError, match="capped at 3"):
        load_settings(p)
    assert (load_settings().first_name, load_settings().last_name) == ("Demo", "Candidate")
    rc.MASTER_RESUME.write_text("No heading here\n" + rc.MASTER_RESUME.read_text().split("\n", 1)[1])
    with pytest.raises(InputError, match="your name"):
        load_settings()
    raw = yaml.safe_load(rc.CONFIG_FILE.read_text())
    raw["naming"] = {"first_name": "Demo", "last_name": "../x"}
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(InputError, match="letters only"):
        load_settings(p)


@needs_tex()
def test_prepare_and_build_end_to_end(rb_env):
    out = pipeline.prepare(str(rb_env.job))
    work = pipeline.job_dir("DemoCorp", rb_env.job, "Backend Engineer Intern") / pipeline.WORK
    assert out["work"] == str(work) and (work / "brief.md").exists()
    brief = (work / "brief.md").read_text()
    assert "DATA" in brief and "exp-demo-b1" in brief
    reviewed_flow(rb_env, work)
    jd = pipeline.job_dir("DemoCorp", rb_env.job, "Backend Engineer Intern")
    with versions.JobLock(jd):
        res = pipeline.build(str(rb_env.job))
        fin = pipeline.finalize(res)
    assert fin["status"] == "ready", fin["reasons"]
    v1 = jd / "v1"
    assert (v1 / "Demo_Candidate_Resume_DemoCorp.pdf").exists() and (v1 / "source" / "main.tex").exists()
    m = json.loads((v1 / "manifest.json").read_text())
    assert m["job_pk"] == rb_env.job and m["status"] == "ready" and set(m["input_hashes"]) >= {
        "master_resume", "template", "rules", "config"}
    assert str(rb_env.job) not in (v1 / "Demo_Candidate_Resume_DemoCorp.pdf").name
    assert versions.current(jd)["version"] == 1


def test_reports_never_quote_contact_details():
    rep = validate.Report()
    rep.add("x", False, "line: Boston | demo.candidate@example.com | +1 (555) 010-0000")
    assert rep.checks[0].detail == "line: Boston | [email] | [phone]"


@needs_tex()
def test_stretched_lines_are_flagged_but_header_is_not(rb_env):
    m = rc.MASTER_RESUME
    m.write_text(m.read_text().replace(
        "- Implemented landing pages in HTML, CSS and JavaScript.",
        "- Implemented landing pages with responsive layouts in HTML and CSS3 and JavaScript-based "
        "internationalization-and-accessibility-compliance-verification utilities."))
    _a, c = _baseline(rb_env)
    for e in c.experience:
        for b in e.bullets:
            b.priority = 1
    res, rep = _build(rb_env, c)
    names = {ch.name: ch for ch in rep.checks}
    assert names["loose_lines"].severity == "warn"
    assert all("Boston" not in t for t in rep.loose_lines)
