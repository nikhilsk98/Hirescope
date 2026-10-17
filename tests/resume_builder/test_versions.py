"""Resume Builder Phase 3: manifests, dedupe, versions, diff, rollback, locks, hand edits, concurrency. DEMO data."""
import json
import multiprocessing
import time

import pytest

from resume_builder import config as rc
from resume_builder import pipeline, versions
from resume_builder.content_schema import dump, parse

from .conftest import needs_tex, reviewed_flow


def full_build(env, edit=None, regenerate=False, discard_edits=False):
    out = pipeline.prepare(str(env.job), regenerate=regenerate, discard_edits=discard_edits)
    if out.get("reused"):
        return {"reused": out["reused"]}
    jd = pipeline.job_dir("DemoCorp", env.job, "Backend Engineer Intern")
    reviewed_flow(env, jd / pipeline.WORK, edit=edit)
    with versions.JobLock(jd):
        res = pipeline.build(str(env.job))
        fin = pipeline.finalize(res)
    return {**fin, "prepare": out, "repairs": res["repairs"], "content": res["content"]}


def jd_of(env):
    return pipeline.job_dir("DemoCorp", env.job, "Backend Engineer Intern")


pytestmark = needs_tex()


def test_manifest_dedupe_regenerate_and_versions_kept(rb_env):
    v1 = full_build(rb_env)
    assert v1["status"] == "ready" and v1["version"] == 1
    m = json.loads((jd_of(rb_env) / "v1" / "manifest.json").read_text())
    for k in ("job_pk", "generated_at", "input_hashes", "template_version", "rules_version", "profile", "track",
              "validation", "dedupe_key", "file_hashes", "tools", "font_used"):
        assert m.get(k) not in (None, ""), k
    assert set(m["input_hashes"]) == {"master_resume", "template", "rules", "evidence_additions", "tool_checklist", "config"}
    again = full_build(rb_env)
    assert again["reused"]["version"] == 1                                   # duplicate request reuses
    assert not (jd_of(rb_env) / "v2").exists()
    v2 = full_build(rb_env, regenerate=True)
    assert v2["version"] == 2 and (jd_of(rb_env) / "v1" / "manifest.json").exists()   # prior version kept
    assert versions.current(jd_of(rb_env))["version"] == 2
    rc.RULES.write_text(rc.RULES.read_text() + "- DEMO: a new rule\n")       # an input changed → no reuse
    assert full_build(rb_env)["version"] == 3


def test_diff_and_rollback(rb_env):
    full_build(rb_env)

    def rewrite(c):
        c.experience[0].bullets[0].text = "Built Java and Spring Boot REST APIs serving 2,000+ daily payment requests"
    full_build(rb_env, edit=rewrite, regenerate=True)
    d = versions.diff(jd_of(rb_env))
    assert "# Diff v1 → v2" in d and "→ Built Java and Spring Boot REST APIs serving 2,000+" in d
    assert "## content.tex" in d
    cur = versions.rollback(jd_of(rb_env), 1)
    assert cur["version"] == 1 and versions.current(jd_of(rb_env))["version"] == 1
    hist = (jd_of(rb_env) / "history.jsonl").read_text()
    assert '"action": "rollback"' in hist
    with pytest.raises(ValueError, match="does not exist"):
        versions.rollback(jd_of(rb_env), 9)


def test_rollback_and_review_refuse_failed_versions(rb_env):
    full_build(rb_env)

    def invent(c):
        c.experience[0].bullets[0].text = "Led a 9-person team building Java REST APIs"
    bad = full_build(rb_env, edit=invent, regenerate=True)
    assert bad["status"] == "failed"
    with pytest.raises(ValueError, match="only a version that passed"):
        versions.rollback(jd_of(rb_env), bad["version"])
    with pytest.raises(ValueError, match="only a ready version"):
        versions.mark_reviewed(jd_of(rb_env), bad["version"])
    m = versions.mark_reviewed(jd_of(rb_env))
    assert m["status"] == "reviewed" and versions.current(jd_of(rb_env))["status"] == "reviewed"


def test_locked_bullet_entry_and_section_survive_regeneration(rb_env):
    full_build(rb_env)
    jd = jd_of(rb_env)
    c1 = versions._content_of(jd / "v1")
    bid = c1.experience[0].bullets[0].id
    old_text = c1.experience[0].bullets[0].text
    versions.lock(jd, bid)
    versions.lock(jd, "exp-example")
    versions.lock(jd, "section:skills")
    with pytest.raises(ValueError):
        versions.lock(jd, "section:experience")

    def drafter_ignores_locks(c):
        c.experience[0].bullets[0].text = "Built REST APIs in Java for payments"
        c.experience[1].bullets = c.experience[1].bullets[:1]
        c.experience[1].bullets[0].text = "Developed a React dashboard for metrics"
        c.skills = c.skills[:2]
    out = full_build(rb_env, edit=drafter_ignores_locks)   # locks changed → no reuse, a new generation
    assert out["version"] == 2 and out["status"] == "ready", out.get("reasons")
    c2 = versions._content_of(jd / "v2")
    assert c2.experience[0].bullets[0].text == old_text
    assert [b.text for b in c2.experience[1].bullets] == [b.text for b in c1.experience[1].bullets]
    assert [s.category for s in c2.skills] == [s.category for s in c1.skills]
    assert any("locked bullet" in r for r in out["repairs"]) and any("locked entry" in r for r in out["repairs"])
    assert bid in json.loads((jd / "v2" / "manifest.json").read_text())["locks"]
    assert versions.unlock(jd, bid) and not versions.unlock(jd, bid)


def test_locked_bullets_are_never_trimmed():
    from resume_builder.config import load_settings
    from resume_builder import validate
    from resume_builder.content_schema import Bullet, Content, EducationEntry, ExperienceEntry, SkillCategory, SkillItem
    s = load_settings(rc.CONFIG_FILE) if rc.CONFIG_FILE.exists() else None
    if s is None:
        pytest.skip("no config")
    c = Content(job_pk=1, profile="backend", track="full_time", section_order=["skills", "education", "experience",
                                                                                 "projects"],
                skills=[SkillCategory(category="Languages", items=[SkillItem(name="Java", evidence_ids=["x"])])],
                education=[EducationEntry(entry_id="edu-x")],
                experience=[ExperienceEntry(entry_id="exp-a", bullets=[
                    Bullet(id="exp-a:keep", text="A locked low-priority bullet text", evidence_ids=["a"], priority=5),
                    Bullet(id="exp-a:one", text="Another bullet with priority five", evidence_ids=["b"], priority=5),
                    Bullet(id="exp-a:two", text="A third bullet that may be trimmed", evidence_ids=["c"], priority=5)])])
    fix = pipeline.repair(c, validate.Report(pages=2, overflow_lines=30), 2, s, {"entry": 6}, {"exp-a:keep"})
    assert "exp-a:keep" in [b.id for b in fix.content.experience[0].bullets]


def test_hand_edits_to_content_json_are_preserved(rb_env):
    full_build(rb_env)
    jd = jd_of(rb_env)
    cj = jd / "v1" / "content.json"
    c = parse(cj.read_text())
    mine = "Built Java + Spring Boot REST APIs for a payments service (2,000+ daily requests)"
    c.experience[0].bullets[0].text = mine
    removed = c.experience[0].bullets.pop(2)
    cj.write_text(dump(c))
    ed = versions.detect_edits(jd / "v1")
    assert ed["json"] and list(ed["bullets"].values()) == [mine] and ed["removed"] == [removed.id]
    assert versions.reusable(jd, json.loads((jd / "v1" / "manifest.json").read_text())["dedupe_key"]) is None
    out = full_build(rb_env, edit=lambda c2: setattr(c2.experience[0].bullets[0], "text",
                                                     "Built REST APIs in Java and Spring Boot for payments"))
    assert out["status"] == "ready", out.get("reasons")
    c2 = versions._content_of(jd / "v2")
    texts = [b.text for b in c2.experience[0].bullets]
    assert mine in texts and removed.text not in texts                      # the edit wins over the drafter
    assert json.loads((jd / "v2" / "manifest.json").read_text())["hand_edits_carried"][0]["text"] == mine
    assert cj.read_text() == dump(c)                                         # the edited version is untouched


def test_hand_edits_to_content_tex_are_preserved(rb_env):
    full_build(rb_env)
    jd = jd_of(rb_env)
    ct = jd / "v1" / "content.tex"
    tex = ct.read_text()
    c = versions._content_of(jd / "v1")
    target = next(b for b in c.experience[0].bullets if b.evidence_ids == ["exp-demo-b2"])
    new = "Reduced PostgreSQL query latency 35\\% with new indexes and rewritten joins"
    from resume_builder.render import escape
    assert escape(target.text) in tex
    ct.write_text(tex.replace(escape(target.text), new))
    ed = versions.detect_edits(jd / "v1")
    assert ed["tex"] and ed["bullets"] == {target.id: "Reduced PostgreSQL query latency 35% with new indexes and "
                                                       "rewritten joins"}
    out = full_build(rb_env, regenerate=True)
    assert out["status"] == "ready", out.get("reasons")
    assert "Reduced PostgreSQL query latency 35% with new indexes and rewritten joins" in [
        b.text for b in versions._content_of(jd / "v2").experience[0].bullets]


def test_unmappable_or_conflicting_edits_are_flagged_not_overwritten(rb_env):
    full_build(rb_env)
    jd = jd_of(rb_env)
    ct = jd / "v1" / "content.tex"
    ct.write_text(ct.read_text().replace("\\end{itemize}", "\\item My own extra bullet\n\\end{itemize}", 1))
    out = full_build(rb_env, regenerate=True)
    assert out["status"] == "failed" and any(r.startswith("manual_edit_conflicts") for r in out["reasons"])
    assert versions.current(jd)["version"] == 1
    assert "My own extra bullet" in ct.read_text()                          # never overwritten
    brief = (jd / pipeline.WORK / "brief.md").read_text()
    assert "Hand-edit conflicts" in brief
    out2 = full_build(rb_env, regenerate=True, discard_edits=True)          # explicit choice to drop them
    assert out2["status"] == "ready" and out2["prepare"]["carried"]["discarded"]


def test_json_and_tex_edits_disagree_or_evidence_changed(rb_env):
    full_build(rb_env)
    jd = jd_of(rb_env)
    c = versions._content_of(jd / "v1")
    b = c.experience[0].bullets[0]
    from resume_builder.render import escape
    ct = jd / "v1" / "content.tex"
    ct.write_text(ct.read_text().replace(escape(b.text), "Text edited in the tex file for this bullet"))
    b.text = "Text edited in the json file for this bullet"
    (jd / "v1" / "content.json").write_text(dump(c))
    assert any("edited differently" in x for x in versions.detect_edits(jd / "v1")["conflicts"])
    # the evidence behind an edited bullet changed in the master resume since
    rc.MASTER_RESUME.write_text(rc.MASTER_RESUME.read_text().replace("2,000+ daily requests", "3,000+ daily requests"))
    out = pipeline.prepare(str(rb_env.job), regenerate=True)
    assert any("changed in your master resume" in x for x in out["carried"]["conflicts"])


def _hold_lock(path, ready, release):
    with versions.JobLock(path):
        ready.set()
        release.wait(10)


def test_concurrent_runs_on_one_job_never_overwrite(rb_env, tmp_path):
    jd = jd_of(rb_env)
    jd.mkdir(parents=True, exist_ok=True)
    ctx = multiprocessing.get_context("fork")
    ready, release = ctx.Event(), ctx.Event()
    p = ctx.Process(target=_hold_lock, args=(jd, ready, release))
    p.start()
    try:
        assert ready.wait(10)
        with pytest.raises(versions.LockedError, match="another Resume Builder run"):
            pipeline.prepare(str(rb_env.job))
    finally:
        release.set()
        p.join(10)
    full_build(rb_env)
    # a stale lock (dead process) is replaced
    (jd / ".lock").write_text(f"999999 {time.time()}")
    with versions.JobLock(jd):
        pass
    # a generation in progress (prepared, not built) blocks a second prepare unless --force
    pipeline.prepare(str(rb_env.job), regenerate=True)
    with pytest.raises(versions.LockedError, match="in progress"):
        pipeline.prepare(str(rb_env.job), regenerate=True)
    pipeline.prepare(str(rb_env.job), regenerate=True, force=True)


def test_version_numbers_are_unique_and_atomic(rb_env):
    full_build(rb_env)
    full_build(rb_env, regenerate=True)
    names = sorted(p.name for p in jd_of(rb_env).iterdir() if p.name.startswith("v") or p.name.startswith(".v"))
    assert names == ["v1", "v2"]
    m1 = json.loads((jd_of(rb_env) / "v1" / "manifest.json").read_text())
    from resume_builder.config import sha256_file
    assert m1["file_hashes"]["pdf"] == sha256_file(jd_of(rb_env) / "v1" / m1["files"]["pdf"])
    assert (jd_of(rb_env) / "v1" / "source" / "BUILD.md").exists()


def test_locked_bullet_replaces_its_regenerated_twin_instead_of_doubling(rb_env):
    full_build(rb_env)
    jd = jd_of(rb_env)
    c1 = versions._content_of(jd / "v1")
    b = c1.experience[0].bullets[0]

    def reword(c):
        c.experience[0].bullets[0].text = "Built Java and Spring Boot REST APIs serving 2,000+ daily payment requests"
    full_build(rb_env, edit=reword, regenerate=True)                       # v2: reworded (new text, same evidence)
    c2 = versions._content_of(jd / "v2")
    versions.lock(jd, c2.experience[0].bullets[0].id)
    out = full_build(rb_env, regenerate=True)                              # baseline has the verbatim twin
    c3 = versions._content_of(jd / f"v{out['version']}")
    same = [x for x in c3.experience[0].bullets if x.evidence_ids == b.evidence_ids]
    assert len(same) == 1 and same[0].text.startswith("Built Java and Spring Boot REST APIs serving")
