"""Rules 46–47 and 67 (v4.6): every resume shows exactly 2 projects, for every track and level; a 3rd only for co-op
roles where it fills a JD gap; overflow trimming never removes a project."""
import shutil

import pytest

from resume_builder import config as rc
from resume_builder import pipeline, rules_v4, v4build, validate
from resume_builder.config import load_settings, rules_budget
from resume_builder.content_schema import Bullet, ProjectEntry
from resume_builder.evidence import load_bank as build_bank

from .test_master_v4 import _job, v4env  # noqa: F401  (fixture)

# as in the real rules file: inside rule 67's numbered list, with the next step on the following line
MINIMUMS = ("    5. Reduce Experience bullets. **Overflow minimums (Freelance / DemoCorp / Tiny Labs): 3 / 2 / 1**\n"
            "    6. Shorten project bullets: minimum 1 each.\n")
GAMMA = ("### Gamma: Cluster Lab\n- **Context:** Course project, Demo University. Completed.\n"
         "- **Repo:** github.com/demo-candidate/gamma\n- **Tech:** Kubernetes, GCP\n\n#### Bullet bank\n"
         "- Deployed a DEMO service to GCP with Kubernetes manifests and rolling updates.\n\n")


def _projects(c):
    return [p.entry_id for p in c.projects]


@pytest.mark.parametrize("title,track,level", [("Software Engineer Intern", "coop_intern", None),
                                               ("Software Engineer", "full_time", None),
                                               ("Software Engineer II", "full_time", "mid")])
def test_two_projects_on_coop_full_time_and_mid_level(v4env, title, track, level):  # noqa: F811
    s = load_settings()
    a = _job(v4env, "Requirements\n- 3+ years of Java and Spring Boot", title=title, track=track, level=level,
             job_id=f"two-{track}-{level}")
    c = pipeline.assign_ids(v4build.baseline(a, build_bank(), s))
    assert len(c.projects) == 2
    assert rules_v4.project_problems(c, build_bank(), s, a) == []
    assert rules_v4.audience_projects(build_bank(), s, a) == ((2, 3) if track == "coop_intern" else (2, 2))
    assert rules_budget(s)["projects_min"] == 2


def test_fewer_than_two_projects_fails_validation_with_a_clear_message(v4env):  # noqa: F811
    b, s = build_bank(), load_settings()
    a = _job(v4env, "Requirements\n- Java", title="Software Engineer II", level="mid", job_id="two-few")
    c = pipeline.assign_ids(v4build.baseline(a, b, s))
    c.projects = c.projects[:1]
    msgs = [p for p in pipeline.structure_problems(c, b, s, a) if p.startswith("projects:")]
    assert msgs and "every resume must show exactly 2 projects (rule 46)" in msgs[0]
    assert "proj-alpha" in msgs[0] and "proj-beta" in msgs[0]          # names what's available
    assert any("exactly 2 projects" in p for p in pipeline.structure_problems(c, b, s, a, after_repair=True))
    c.projects = []
    assert any("projects: 0 on the page" in p for p in rules_v4.problems(c, b, s, a))


@pytest.mark.skipif(shutil.which("tectonic") is None, reason="tectonic not installed")
def test_a_built_resume_with_one_project_is_not_ready(v4env):  # noqa: F811
    from resume_builder import versions
    from .conftest import reviewed_flow
    jid = _job(v4env, "Requirements\n- Java and Spring Boot", job_id="two-built")["job"]["pk"]
    pipeline.prepare(str(jid))
    jd = pipeline.job_dir("DemoCo V4", jid, "Software Engineer")
    reviewed_flow(v4env, jd / pipeline.WORK, job=jid)
    with versions.JobLock(jd):
        res = pipeline.build(str(jid))
        res["content"].projects = res["content"].projects[:1]          # as a careless repair would
        fin = pipeline.finalize(res)
    assert fin["status"] == "failed"
    assert any("rules_after_repair" in r and "exactly 2 projects" in r for r in fin["reasons"])


def test_overflow_keeps_two_projects_and_follows_the_new_order(v4env, monkeypatch):  # noqa: F811
    rc.RULES.write_text(rc.RULES.read_text() + "\n" + MINIMUMS)
    b, s = build_bank(), load_settings()
    a = _job(v4env, "Requirements\n- React and Java\n- Spring Boot", job_id="two-over")
    floors = rules_v4.overflow_minimums(b, s)
    assert floors == {"exp-freelance": 3, "exp-democorp": 2, "exp-tiny": 1}
    c = pipeline.assign_ids(v4build.baseline(a, b, s))
    c.education[0].course_ids = c.education[0].course_ids or [
        i.id for i in b.items.values() if i.section == "coursework"][:1]
    monkeypatch.setattr(pipeline, "SKILL_ROW_CHARS", 40)                # make the skill rows wrap
    gaps = {"entry": 3.0, "template_entry": 3.0, "leading": 12.0}
    big = validate.Report(pages=2, overflow_lines=80)
    f1 = pipeline.repair(c, big, 1, s, gaps, set(), floors, b, a)
    i_skill = min(i for i, x in enumerate(f1.actions) if "skills" in x or "skill row" in x)
    assert i_skill < f1.actions.index("removed optional coursework")   # skills → coursework
    assert not any("bullet" in x or "project" in x for x in f1.actions)
    f2 = pipeline.repair(f1.content, big, 2, s, f1.gaps, set(), floors, b, a)
    out = f2.content
    per = {e.entry_id: len(e.bullets) for e in out.experience}
    assert per == floors                                              # down to the minimums, never below
    assert _projects(out) == _projects(c) and all(len(p.bullets) == 1 for p in out.projects)
    first_proj = min(i for i, x in enumerate(f2.actions) if x.startswith("shortened project"))
    assert all(not x.startswith("removed") for x in f2.actions[first_proj:])   # project bullets come last
    assert not any("removed project" in x for x in f2.actions)
    f3 = pipeline.repair(out, big, 3, s, f2.gaps, set(), floors, b, a)
    assert len(f3.content.projects) == 2 and f3.gaps["entry"] == 2.0  # only spacing is left, within limits
    # the trimmed content passes the post-repair rules (minimums, not the drafting ranges) but not the drafting ones
    after = [p for p in rules_v4.problems(out, b, s, a, after_repair=True) if "bullets" in p or "projects" in p]
    assert after == []
    assert any("the master says" in p or "rule 32" in p for p in rules_v4.problems(out, b, s, a))


def test_third_project_only_for_coop_roles_with_a_jd_gap(v4env):  # noqa: F811
    m = rc.MASTER_RESUME
    m.write_text(m.read_text().replace("### Excluded", GAMMA + "### Excluded", 1))
    b, s = build_bank(), load_settings()
    assert "proj-gamma" in b.entries
    gamma = ProjectEntry(entry_id="proj-gamma", bullets=[Bullet(text=b.items[i].text, evidence_ids=[i])
                                                         for i in b.items if b.items[i].entry_id == "proj-gamma"
                                                         and b.items[i].kind == "bullet"])
    coop_gap = _job(v4env, "Requirements\n- Java\n- Kubernetes and GCP", title="Cloud Intern", track="coop_intern",
                    job_id="two-gap")
    c = pipeline.assign_ids(v4build.baseline(coop_gap, b, s))
    c3 = c.model_copy(deep=True)
    c3.projects.append(gamma)
    assert rules_v4.project_problems(c3, b, s, coop_gap) == []         # co-op + fills the Kubernetes / GCP gap
    coop_nogap = _job(v4env, "Requirements\n- Java and Spring Boot", title="SWE Intern", track="coop_intern",
                      job_id="two-nogap")
    assert any("fills no JD gap" in p for p in rules_v4.project_problems(c3, b, s, coop_nogap))
    ft = _job(v4env, "Requirements\n- Java\n- Kubernetes and GCP", title="Cloud Engineer", job_id="two-ft")
    assert any(p.startswith("projects: 3") for p in rules_v4.project_problems(c3, b, s, ft))
    mid = _job(v4env, "Requirements\n- Java\n- Kubernetes and GCP", title="Cloud Engineer II", level="mid",
               job_id="two-mid")
    assert any(p.startswith("projects: 3") for p in rules_v4.project_problems(c3, b, s, mid))
