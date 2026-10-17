"""Desktop / Downloads work in a child process with a timeout: a background job never hangs on a macOS permission
prompt (a quick check sat an hour in rename(); the worker and the launcher hung in open())."""
import json
import subprocess

import pytest

from jobsearch import launcher, paths
from jobsearch.ops import applications_excel, desktop_access
from jobsearch.storage import db


@pytest.fixture
def hanging_child(monkeypatch):
    """A child that never finishes, like one waiting for an unanswered macOS prompt (killed after 1 s)."""
    real = subprocess.run

    def run(args, **kw):
        if "jobsearch.ops.desktop_access" in args:
            args = [args[0], "-c", "import time; time.sleep(30)"]
            kw["timeout"] = 1
        return real(args, **kw)
    monkeypatch.setattr(desktop_access.subprocess, "run", run)


def test_excel_is_placed_by_a_child(tmp_path):
    conn = db.connect(paths.DB)
    assert applications_excel.update(conn) == ""
    assert applications_excel.settings()[1].exists()
    assert json.loads(db.get_meta(conn, "excel_applications"))["status"] == "ok"
    assert not desktop_access.pending_marker().exists()


def test_a_hanging_desktop_write_is_killed_and_reported(hanging_child):
    conn = db.connect(paths.DB)
    msg = applications_excel.update(conn)
    assert "macOS did not let" in msg and "waiting for macOS permission" in msg
    st = json.loads(db.get_meta(conn, "excel_applications"))
    assert st["status"] == "error" and "Privacy & Security" in st["message"]
    assert desktop_access.pending_marker().exists()                # the next sync tries again


def test_desktop_resume_copies_in_a_child(monkeypatch, hanging_child):
    from resume_builder import desktop
    monkeypatch.delenv("HIRESCOPE_DESKTOP_DIRECT")
    code, lines = desktop.sync_all()
    assert code == 1 and lines[0].startswith("Desktop copy skipped")


def test_desktop_sync_child_runs_for_real(monkeypatch):
    from resume_builder import desktop
    monkeypatch.delenv("HIRESCOPE_DESKTOP_DIRECT")
    code, lines = desktop.sync_all()
    assert code == 0 and "Desktop folder" in lines[0]


def test_downloads_read_check(tmp_path, hanging_child):
    ok, why = desktop_access.readable(tmp_path)
    assert not ok and "Downloads Folder" in why


def test_downloads_read_check_ok(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    assert desktop_access.readable(tmp_path, "a.json")[0]


def test_blocked_downloads_keeps_the_request_and_says_how(monkeypatch, cfg, hanging_child):
    from jobsearch import external_resume as ER
    d = ER.downloads_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "hirescope-external-job-X-1.json").write_text("{}")
    launcher.handle("hirescope://external?file=hirescope-external-job-X-1.json")
    conn = db.connect(paths.DB)
    out = ER.process_requests(conn, cfg)
    assert out[0]["blocked"] and len(ER.pending_requests()) == 1 and ER.pending_requests(include_blocked=False) == []
    last = json.loads(db.get_meta(conn, "launcher_last"))
    assert not last["ok"] and "Downloads Folder" in last["message"] and "external_resume.sh" in last["message"]


def test_sync_link_catches_up_and_never_loops(monkeypatch):
    """hirescope://sync runs the Desktop catch-up; a skipped write inside the launcher never re-sends the link."""
    sent = []
    monkeypatch.delenv("HIRESCOPE_NO_KICK")
    monkeypatch.setattr(desktop_access.subprocess, "run", _record_open(desktop_access.subprocess.run, sent))
    monkeypatch.setenv("HIRESCOPE_IN_LAUNCHER", "1")
    desktop_access.mark_pending("blocked")
    assert sent == []                                     # inside the launcher: no new link
    desktop_access.pending_marker().unlink()
    monkeypatch.delenv("HIRESCOPE_IN_LAUNCHER")
    desktop_access.mark_pending("blocked")
    desktop_access.mark_pending("blocked again")
    assert sent == [["open", "-g", "hirescope://sync"]]   # from a background job: once
    monkeypatch.setenv("HIRESCOPE_NO_KICK", "1")
    launcher.handle("hirescope://sync")
    assert not desktop_access.pending_marker().exists()
    with pytest.raises(launcher.LinkError):
        launcher.parse("hirescope://sync?x=1")


def _record_open(real, sent):
    def run(args, **kw):
        if args and args[0] == "open":
            sent.append(list(args))
            return subprocess.CompletedProcess(args, 0, "", "")
        return real(args, **kw)
    return run
