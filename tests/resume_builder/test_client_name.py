"""Hard rule (rule 39, v4.8): Accenture's client is never named on a resume — the build checks the rendered PDF text —
nor in anything an employer may see (job packages scrub resume lines). DEMO data with the one real name the rule
is about."""
import shutil

import pytest

from jobsearch.output.employer_facing import never_named, scrub
from jobsearch.output.packages import parse_package, render_package
from resume_builder import config as rc
from resume_builder import pipeline

from .conftest import needs_tex
from .test_master_v4 import FIX, _lines, v4env  # noqa: F401


@pytest.mark.parametrize("text", ["Client: Macquarie Bank", "for MACQUARIE", "macquarie's platform", "Macqua-\nrie Bank",
                                  "Macquarie Group Limited"])
def test_the_name_is_caught(text):
    assert never_named(text) == ["Macquarie"]


@pytest.mark.parametrize("text", ["for a banking client", "a banking client's legacy platform", "Associate Software Developer"])
def test_neutral_wording_passes(text):
    assert never_named(text) == []


@pytest.mark.parametrize("before,after", [
    ("Contributed to Macquarie Bank's legacy-platform modernization", "Contributed to a banking client's legacy-platform modernization"),
    ("modernize the Macquarie Bank platform", "modernize a banking client platform"),
    ("work for Macquarie, Australia", "work for a banking client"),
    ("no client here", "no client here"),
])
def test_scrub(before, after):
    assert scrub(before) == after and not never_named(scrub(before))


def _with_client_name(t: str) -> str:
    return t.replace("Client: Demo Bank", "Client: Macquarie Bank")


@needs_tex()
def test_the_name_anywhere_on_the_pdf_fails_the_build(v4env):  # noqa: F811
    rc.TEMPLATE.write_text(_with_client_name((FIX / "demo_template_v4.tex").read_text()))
    shutil.copy(FIX / "demo_master_v4.md", rc.MASTER_RESUME)
    rc.MASTER_RESUME.write_text(_with_client_name((FIX / "demo_master_v4.md").read_text()))
    out = pipeline.build_default()
    assert out["status"] != "ready"
    bad = {x.name: x for x in out["report"].errors()}
    assert "no_client_name" in bad and "'Macquarie'" in bad["no_client_name"].detail
    assert "a banking client" in bad["no_client_name"].detail


@needs_tex()
def test_without_the_name_the_check_passes(v4env):  # noqa: F811
    out = pipeline.build_default()
    assert out["status"] == "ready"
    assert {x.name: x for x in out["report"].checks}["no_client_name"].ok
    assert not never_named(" ".join(_lines(out["pdf"])))


def test_content_naming_the_client_is_a_problem_before_the_pdf(v4env):  # noqa: F811
    from resume_builder import evidence, rules_v4, v4build
    bank = evidence.load_bank()
    settings = rc.load_settings()
    c = v4build.baseline(None, bank, settings)
    assert not [p for p in rules_v4.problems(c, bank, settings, None) if "rule 39" in p]
    c.experience[1].bullets[0].text = "Contributed to Macquarie Bank's platform modernization with Java services."
    c.experience[1].title = "Software Engineer | Client: Macquarie Bank"
    probs = rules_v4.problems(c, bank, settings, None)
    assert any("names Accenture's client" in p for p in probs) and any("heading names" in p for p in probs)


def test_job_package_scrubs_resume_lines_but_keeps_the_jd():
    job = {"company": "DemoFin", "title": "Backend Co-op", "job_id": "1", "source": "external", "last_checked": "2026-10-20",
           "breakdown": {"evidence": [{"jd": "messaging", "section": "experience",
                                       "resume": "Contributed to Macquarie Bank's legacy-platform modernization"}],
                         "skill_evidence": {"Solace": "Migrated workflows for Macquarie Bank to Solace"}},
           "keywords": {"coverage": 50, "required_hit": 1, "required_total": 2, "keywords": [
               {"term": "Java", "section": "required", "status": "matched", "weight": 1, "evidence_section": "experience",
                "evidence": "Macquarie Bank's Java services"}]}}
    text = render_package(job, "DemoFin partners with Macquarie on payments (the employer's own JD).", "h")
    _, sec = parse_package(text)
    assert not never_named(sec["My matching evidence"]) and not never_named(sec["ATS keywords"])
    assert "a banking client's legacy-platform modernization" in sec["My matching evidence"]
    assert "Macquarie" in sec["Full JD"]                         # the employer's JD is theirs: unchanged
