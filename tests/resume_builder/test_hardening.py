"""Resume Builder Phase 5: failure handling — missing tools and inputs, broken template, compile timeout and missing
font, corrupt state files, malformed content, privacy of logs. All data is DEMO."""
import json
import logging
import shutil

import pytest

from resume_builder import __main__ as cli
from resume_builder import compile as comp
from resume_builder import config as rc
from resume_builder import pipeline, versions
from resume_builder.compile import CompileError
from resume_builder.config import InputError

from .conftest import needs_tex, reviewed_flow


def test_missing_tools_are_named_with_the_install_command(rb_env, monkeypatch):
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name in ("tectonic", "pdftoppm") else real(name))
    with pytest.raises(InputError, match=r"tectonic \(install: brew install tectonic\)") as e:
        pipeline.prepare(str(rb_env.job))
    assert "pdftoppm (install: brew install poppler)" in str(e.value)
    with pytest.raises(CompileError, match="brew install tectonic"):
        comp.compile_tex(rb_env.root)
    assert cli.main(["check"]) == 2


@pytest.mark.parametrize("which,setup,msg", [
    ("template", lambda p: p.unlink(), "resume_template.tex is missing"),
    ("rules", lambda p: p.unlink(), "resume_rules.md is missing"),
    ("rules", lambda p: p.write_text("  \n"), "resume_rules.md is empty"),
    ("master", lambda p: p.write_text(""), "master_resume.md is empty"),
])
def test_missing_or_empty_inputs_stop_with_exact_file(rb_env, which, setup, msg):
    setup({"template": rc.TEMPLATE, "rules": rc.RULES, "master": rc.MASTER_RESUME}[which])
    with pytest.raises(InputError, match=msg):
        pipeline.check_inputs()


@needs_tex()
def test_broken_template_copy_fails_clearly_and_keeps_last_good(rb_env, capsys):
    pipeline.prepare(str(rb_env.job))
    jd = pipeline.job_dir("DemoCorp", rb_env.job)
    reviewed_flow(rb_env, jd / "work")
    assert cli.main(["build", str(rb_env.job)]) == 0
    original = rc.TEMPLATE.read_bytes()
    rc.TEMPLATE.write_text(rc.TEMPLATE.read_text().replace("\\end{center}", ""))   # header block broken
    assert cli.main(["prepare", str(rb_env.job), "--regenerate"]) == 0
    reviewed_flow(rb_env, jd / "work")
    assert cli.main(["build", str(rb_env.job)]) == 2
    err = capsys.readouterr().err
    assert "no header block" in err
    assert versions.current(jd)["version"] == 1
    from jobsearch.storage import db
    conn = db.connect(rb_env.db_path)
    st = dict(conn.execute("SELECT status, message FROM resume_status WHERE job_pk=?", (rb_env.job,)).fetchone())
    conn.close()
    assert st["status"] == "failed" and "v1 is still current" in st["message"]
    rc.TEMPLATE.write_bytes(original)


@needs_tex()
def test_compile_timeout_is_reported(rb_env):
    d = rb_env.root / "t"
    d.mkdir()
    (d / "main.tex").write_text("\\documentclass{article}\\begin{document}DEMO\\end{document}\n")
    res = comp.compile_tex(d, timeout_s=0.001)
    assert not res.ok and res.timed_out and any("timed out" in e for e in res.errors)


@needs_tex()
def test_missing_font_without_fallback_is_a_clear_compile_error(rb_env):
    d = rb_env.root / "f"
    d.mkdir()
    (d / "main.tex").write_text("\\documentclass{article}\\usepackage{fontspec}\\setmainfont{NoSuchFontDEMO}"
                                "\\begin{document}DEMO\\end{document}\n")
    res = comp.compile_tex(d, timeout_s=120)
    assert not res.ok and any("font" in e.lower() for e in res.errors)


def test_corrupt_state_files_never_crash_or_count_as_valid(rb_env):
    jd = pipeline.job_dir("DemoCorp", rb_env.job)
    (jd / "v1").mkdir(parents=True)
    (jd / "v1" / "manifest.json").write_text("{not json")
    (jd / "current.json").write_text("{nope")
    assert versions.current(jd) is None
    assert versions.versions_summary(jd) == []
    assert versions.reusable(jd, "any") is None
    with pytest.raises(ValueError):
        versions.rollback(jd, 1)


@needs_tex()
def test_malformed_content_json_is_rejected_with_reasons(rb_env, capsys):
    pipeline.prepare(str(rb_env.job))
    work = pipeline.job_dir("DemoCorp", rb_env.job) / "work"
    reviewed_flow(rb_env, work)
    c = json.loads((work / "content.json").read_text())
    c["experience"][0]["bullets"][0]["evidence_ids"] = []
    c["skills"][0]["latex"] = "\\input{/etc/passwd}"
    (work / "content.json").write_text(json.dumps(c))
    assert cli.main(["build", str(rb_env.job)]) == 2
    err = capsys.readouterr().err
    assert "content does not match the schema" in err and "evidence_ids" in err and "latex" in err
    (work / "content.json").write_text("{broken")
    assert cli.main(["build", str(rb_env.job)]) == 2
    assert "not valid JSON" in capsys.readouterr().err


@needs_tex()
def test_logs_and_reports_never_contain_contact_details(rb_env, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    pipeline.prepare(str(rb_env.job))
    jd = pipeline.job_dir("DemoCorp", rb_env.job)
    reviewed_flow(rb_env, jd / "work")
    assert cli.main(["build", str(rb_env.job)]) == 0
    out = capsys.readouterr()
    secrets = ("demo.candidate@example.com", "555) 010-0000", "+1-555-010-0000")
    for text in (out.out, out.err, caplog.text, (jd / "v1" / "compile.log").read_text(),
                 *[p.read_text() for p in (jd / "v1" / "reports").glob("*.md")],
                 (jd / "v1" / "manifest.json").read_text()):
        assert not any(s in text for s in secrets)


def test_lock_targets_and_job_refs_are_validated(rb_env):
    jd = pipeline.job_dir("DemoCorp", rb_env.job)
    jd.mkdir(parents=True)
    with pytest.raises(ValueError, match="no version yet"):
        versions.lock(jd, "../../etc/passwd")
    assert cli.main(["show", "../../etc"]) == 2
    assert cli.main(["build", "1;rm -rf"]) == 2


@needs_tex()
def test_every_cli_command(rb_env, capsys):
    """DEMO walk through the whole CLI: check, schema, prepare (reuse), review, verify, build, interview,
    add-evidence, show, lock/unlock, diff, rollback, mark-reviewed, refresh."""
    j = str(rb_env.job)
    assert cli.main(["check"]) == 0 and "OK: tools present" in capsys.readouterr().out
    assert cli.main(["schema"]) == 0 and "evidence_ids" in json.loads(capsys.readouterr().out)["$defs"]["Bullet"]["properties"]
    assert cli.main(["prepare", j]) == 0
    work = pipeline.job_dir("DemoCorp", rb_env.job) / "work"
    (work / "draft.json").write_text((work / "baseline.json").read_text())
    assert cli.main(["review", j]) == 0 and "Reviewer packet" in capsys.readouterr().out
    c = json.loads((work / "draft.json").read_text())
    c["notes"]["stage"] = "reviewed"
    (work / "content.json").write_text(json.dumps(c))
    (work / "review.json").write_text('{"critique": []}')
    assert cli.main(["verify", j]) == 0 and "SUPPORTED" in capsys.readouterr().out
    assert cli.main(["build", j]) == 0 and "READY: v1" in capsys.readouterr().out
    assert cli.main(["prepare", j]) == 0 and "REUSED: v1" in capsys.readouterr().out
    assert cli.main(["interview", j]) == 0
    capsys.readouterr()
    assert cli.main(["add-evidence", "--entry", "exp-demo", "--question", "Q?", "--answer", "DEMO answer"]) == 2
    assert "explicitly confirm" in capsys.readouterr().err
    assert cli.main(["add-evidence", "--entry", "exp-demo", "--question", "Q?", "--answer", "DEMO: 3 teams",
                     "--confirm"]) == 0
    capsys.readouterr()
    assert cli.main(["show", j, "--bullets"]) == 0
    shown = json.loads(capsys.readouterr().out)
    bid = shown["bullets"][0]["id"]
    assert cli.main(["lock", j, bid]) == 0 and cli.main(["unlock", j, bid]) == 0
    assert cli.main(["unlock", j, bid]) == 1
    assert cli.main(["diff", j]) == 2                     # one version only: a clear message, not a crash
    assert "nothing to compare" in capsys.readouterr().err
    assert cli.main(["prepare", j]) == 0                  # additions changed → new generation
    (work / "draft.json").write_text((work / "baseline.json").read_text())
    assert cli.main(["review", j]) == 0
    c = json.loads((work / "draft.json").read_text())
    c["notes"]["stage"] = "reviewed"
    (work / "content.json").write_text(json.dumps(c))
    (work / "review.json").write_text('{"critique": []}')
    assert cli.main(["build", j]) == 0
    capsys.readouterr()
    assert cli.main(["diff", j, "1", "2"]) == 0 and "# Diff v1 → v2" in capsys.readouterr().out
    assert cli.main(["rollback", j, "1"]) == 0 and "Current: v1" in capsys.readouterr().out
    assert cli.main(["mark-reviewed", j]) == 0 and "v1 marked reviewed" in capsys.readouterr().out
    assert cli.main(["refresh"]) == 0
    assert cli.main(["show", j]) == 0 and '"reviewed"' in capsys.readouterr().out
