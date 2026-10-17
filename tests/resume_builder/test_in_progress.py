"""Hard rule (rules 50–51, v4.7): no "In Progress" (or a variant) on any resume. The build checks the rendered PDF
text; the in-progress DEMO project renders without a label (design verbs only)."""
import shutil

import pytest

from resume_builder import config as rc
from resume_builder import pipeline
from resume_builder.validate import in_progress_labels

from .conftest import needs_tex
from .test_master_v4 import FIX, _lines, v4env  # noqa: F401


@pytest.mark.parametrize("text", ["Beta | In Progress", "Beta (in progress)", "in-progress", "In–Progress", "Ongoing",
                                  "on-going project", "(WIP)", "WIP", "a work in progress", "under development"])
def test_every_variant_is_caught(text):
    assert in_progress_labels(text)


@pytest.mark.parametrize("text", ["Python, FastAPI, Redis | Demo University", "Expected May 2027",
                                  "Designing a progressive two-stage ranker", "wip-free code", "Ongo Labs"])
def test_ordinary_text_passes(text):
    assert in_progress_labels(text) == []


@needs_tex()
def test_in_progress_project_renders_without_a_label(v4env):  # noqa: F811
    out = pipeline.build_default()
    assert out["status"] == "ready"
    check = {x.name: x for x in out["report"].checks}["no_in_progress_label"]
    assert check.ok
    beta = next(x for x in _lines(out["pdf"]) if "Demo University" in x and "Redis" in x)
    assert beta.endswith("Demo University") and not in_progress_labels(" ".join(_lines(out["pdf"])))


@needs_tex()
def test_a_label_anywhere_fails_the_build_with_a_clear_message(v4env):  # noqa: F811
    t = (FIX / "demo_template_v4.tex").read_text()
    rc.TEMPLATE.write_text(t.replace("Python, FastAPI, Redis \\,\\textbar\\, Demo University",
                                     "Python, FastAPI, Redis \\,\\textbar\\, Demo University \\,\\textbar\\, In Progress"))
    m = (FIX / "demo_master_v4.md").read_text()
    shutil.copy(FIX / "demo_master_v4.md", rc.MASTER_RESUME)
    rc.MASTER_RESUME.write_text(m.replace("Python, FastAPI, Redis | Demo University", "Python, FastAPI, Redis | Demo University | In Progress"))
    out = pipeline.build_default()
    assert out["status"] != "ready"
    bad = {x.name: x for x in out["report"].errors()}
    assert "no_in_progress_label" in bad and "'In Progress'" in bad["no_in_progress_label"].detail
    assert "design verbs" in bad["no_in_progress_label"].detail
