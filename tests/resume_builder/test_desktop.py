"""Desktop copy of READY resume PDFs (desktop.py, desktop_copy setting): current + older READY versions, never failed
builds or build files, rollback, the default resume, a blocked Desktop, backfill and the sync command."""
import errno
import json
import os
from pathlib import Path

import yaml

from resume_builder import __main__ as cli
from resume_builder import config as rc
from resume_builder import desktop, versions

PDF = "Demo_Candidate_Resume_Acme.pdf"


def _desk() -> Path:
    return Path(os.environ["JOBSEARCH_DESKTOP_DIR"])


def _version(job: Path, n: int, status: str, pdf: str = PDF) -> Path:
    v = job / f"v{n}"
    v.mkdir(parents=True)
    (v / pdf).write_bytes(f"%PDF-1.5 v{n}".encode())
    (v / "main.tex").write_text("tex")
    (v / "manifest.json").write_text(json.dumps({"version": n, "status": status, "files": {"pdf": pdf},
                                                 "generated_at": f"2026-10-1{n}T00:00:00+00:00"}))
    return v


def _job(company="Acme", role="Cloud-Developer-Intern_123") -> Path:
    j = versions.resumes_root() / company / role
    j.mkdir(parents=True)
    return j


def test_current_and_older_ready_versions_only(rb_env, capsys):
    j = _job()
    _version(j, 1, "ready")
    _version(j, 2, "failed")
    _version(j, 3, "ready")
    versions.set_current(j, 1, "first")
    versions.set_current(j, 3, "new build")
    d = _desk() / "Acme" / "Cloud-Developer-Intern_123"
    assert sorted(p.name for p in d.iterdir()) == [PDF, "Demo_Candidate_Resume_Acme_v1.pdf"]   # no v2, no .tex
    assert (d / PDF).read_bytes() == b"%PDF-1.5 v3"
    assert "Desktop copy:" in capsys.readouterr().out


def test_rollback_and_reviewed_change_the_current_copy(rb_env):
    j = _job()
    _version(j, 1, "ready")
    _version(j, 2, "ready")
    versions.set_current(j, 2, "new build")
    d = _desk() / "Acme" / "Cloud-Developer-Intern_123"
    (d / "my notes.txt").write_text("mine")                       # not ours: left alone
    versions.rollback(j, 1)
    assert sorted(p.name for p in d.iterdir()) == [PDF, "Demo_Candidate_Resume_Acme_v2.pdf", "my notes.txt"]
    assert (d / PDF).read_bytes() == b"%PDF-1.5 v1"


def test_default_resume_goes_to_default_folder(rb_env):
    j = versions.resumes_root() / desktop.DEFAULT_JOB
    _version(j, 1, "ready", pdf="Demo_Candidate_Resume.pdf")
    versions.set_current(j, 1, "default")
    assert (_desk() / "Default" / "Demo_Candidate_Resume.pdf").read_bytes() == b"%PDF-1.5 v1"


def test_disabled_and_moved_by_setting(rb_env, monkeypatch, tmp_path):
    cfg = yaml.safe_load(rc.CONFIG_FILE.read_text())
    before = rc.load_settings().hash()
    cfg["desktop_copy"] = {"enabled": False, "dir": str(tmp_path / "elsewhere")}
    rc.CONFIG_FILE.write_text(yaml.safe_dump(cfg))
    assert rc.load_settings().hash() == before                    # never forces a rebuild
    monkeypatch.delenv("JOBSEARCH_DESKTOP_DIR")
    j = _job()
    _version(j, 1, "ready")
    versions.set_current(j, 1, "new build")
    assert not (tmp_path / "elsewhere").exists()
    cfg["desktop_copy"]["enabled"] = True
    rc.CONFIG_FILE.write_text(yaml.safe_dump(cfg))
    versions.set_current(j, 1, "again")
    assert (tmp_path / "elsewhere" / "Acme" / "Cloud-Developer-Intern_123" / PDF).exists()


def test_blocked_desktop_never_fails_the_build(rb_env, monkeypatch, capsys):
    def denied(*_a, **_k):
        raise PermissionError(errno.EPERM, "Operation not permitted")
    monkeypatch.setattr(desktop, "sync_job", denied)
    j = _job()
    _version(j, 1, "ready")
    cur = versions.set_current(j, 1, "new build")                 # no exception
    assert cur["version"] == 1 and versions.current(j)["version"] == 1
    out = capsys.readouterr().out
    assert "macOS did not allow" in out and "sync_desktop_resumes.sh" in out and "data.nosync" in out
    code, lines = desktop.sync_all()
    assert code == 1 and "macOS did not allow" in lines[0]


def test_backfill_and_sync_command(rb_env, capsys):
    a = _job()
    _version(a, 1, "ready")
    (a / "current.json").write_text(json.dumps({"version": 1, "status": "ready", "pdf": PDF}))  # built before the copy
    b = _job("Bose", "Engineer_9")
    _version(b, 1, "failed", pdf="Demo_Candidate_Resume_Bose.pdf")                          # no current: skipped
    dflt = versions.resumes_root() / "_default"
    _version(dflt, 1, "ready", pdf="Demo_Candidate_Resume.pdf")
    (dflt / "current.json").write_text(json.dumps({"version": 1, "status": "ready"}))
    (versions.resumes_root() / "_cache").mkdir()
    assert cli.main(["sync-desktop"]) == 0
    out = capsys.readouterr().out
    assert "2 copied" in out
    assert (_desk() / "Acme" / "Cloud-Developer-Intern_123" / PDF).exists()
    assert (_desk() / "Default" / "Demo_Candidate_Resume.pdf").exists() and not (_desk() / "Bose").exists()
    assert cli.main(["sync-desktop"]) == 0 and "0 copied" in capsys.readouterr().out          # idempotent


def test_two_jobs_same_company_never_share_a_folder_and_stray_copies_go(rb_env):
    a = _job("Hewlett-Packard-Enterprise", "Cloud-Developer-Intern_34877")
    b = _job("Hewlett-Packard-Enterprise", "Cloud-Engineer-Intern_67496")
    pa = _version(a, 1, "ready", pdf="Demo_Candidate_Resume_HPE.pdf")
    _version(b, 1, "ready", pdf="Demo_Candidate_Resume_HPE.pdf")
    (b / "v1" / "Demo_Candidate_Resume_HPE.pdf").write_bytes(b"%PDF-1.5 engineer")
    versions.set_current(a, 1, "build")
    versions.set_current(b, 1, "build")
    hpe = _desk() / "Hewlett-Packard-Enterprise"
    da, db_ = hpe / "Cloud-Developer-Intern_34877", hpe / "Cloud-Engineer-Intern_67496"
    assert (da / "Demo_Candidate_Resume_HPE.pdf").read_bytes() == b"%PDF-1.5 v1"
    assert (db_ / "Demo_Candidate_Resume_HPE.pdf").read_bytes() == b"%PDF-1.5 engineer"
    # stray copies: hash-suffixed / "(1)" in a job folder, an identical loose copy, and a unique file
    for n in ("Demo_Candidate_Resume_HPE_19fc85.pdf", "Demo_Candidate_Resume_HPE (1).pdf"):
        (da / n).write_bytes(pa.joinpath("Demo_Candidate_Resume_HPE.pdf").read_bytes())
    (hpe / "Demo_Candidate_Resume_HPE_6c3c2d.pdf").write_bytes(b"%PDF-1.5 v1")
    (hpe / "Demo_Candidate_Resume_HPE_unique.pdf").write_bytes(b"%PDF-1.5 something else")
    code, lines = desktop.sync_all()
    assert code == 0
    assert sorted(p.name for p in da.iterdir()) == ["Demo_Candidate_Resume_HPE.pdf"]
    assert not (hpe / "Demo_Candidate_Resume_HPE_6c3c2d.pdf").exists()
    assert (hpe / "Demo_Candidate_Resume_HPE_unique.pdf").exists()                  # unique content: kept
    assert any("duplicate of" in ln for ln in lines)
