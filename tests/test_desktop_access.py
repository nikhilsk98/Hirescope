"""Desktop writes in background jobs never hang on a macOS permission prompt (a quick check sat an hour in rename())."""
import json
import time

from jobsearch import paths
from jobsearch.ops import applications_excel, desktop_access
from jobsearch.storage import db


def test_probe_ok_and_cached(tmp_path):
    desktop_access.forget()
    assert desktop_access.writable(tmp_path / "Desktop")[0]
    assert not list((tmp_path / "Desktop").iterdir())                 # leaves nothing behind
    assert str(tmp_path / "Desktop") in desktop_access._cache


def test_hanging_write_is_killed_and_reported(tmp_path, monkeypatch):
    desktop_access.forget()
    monkeypatch.setattr(desktop_access, "_PROBE", "import time; time.sleep(30)")
    t = time.time()
    ok, why = desktop_access.writable(tmp_path / "D", timeout=1)
    assert not ok and time.time() - t < 5 and "System Settings" in why and "waiting for permission" in why


def test_excel_skips_a_blocked_desktop_and_says_how_to_allow(tmp_path, monkeypatch):
    desktop_access.forget()
    monkeypatch.setattr(desktop_access, "_PROBE", "import sys; sys.exit('PermissionError: Operation not permitted')")
    conn = db.connect(paths.DB)
    msg = applications_excel.update(conn)
    assert "macOS did not let" in msg and not applications_excel.settings()[1].exists()
    st = json.loads(db.get_meta(conn, "excel_applications"))
    assert st["status"] == "error" and "Privacy & Security" in st["message"]
    desktop_access.forget()


def test_desktop_resume_copy_skips_a_blocked_desktop(tmp_path, monkeypatch):
    from resume_builder import desktop
    desktop_access.forget()
    monkeypatch.setattr(desktop_access, "_PROBE", "import sys; sys.exit(1)")
    assert desktop.after_current(tmp_path / "x").startswith("Desktop copy skipped")
    code, lines = desktop.sync_all()
    assert code == 1 and "Desktop copy skipped" in lines[0]
    desktop_access.forget()


def test_blocked_downloads_keeps_the_request_and_says_how(tmp_path, monkeypatch, cfg):
    from jobsearch import external_resume as ER
    from jobsearch import launcher
    desktop_access.forget()
    monkeypatch.setattr(desktop_access, "_READ", "import time; time.sleep(30)")
    monkeypatch.setattr(desktop_access.subprocess, "run", _short(desktop_access.subprocess.run))
    d = ER.downloads_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "hirescope-external-job-X-1.json").write_text("{}")
    launcher.handle("hirescope://external?file=hirescope-external-job-X-1.json")
    conn = db.connect(paths.DB)
    out = ER.process_requests(conn, cfg)
    assert out[0]["blocked"] and len(ER.pending_requests()) == 1 and ER.pending_requests(include_blocked=False) == []
    last = json.loads(db.get_meta(conn, "launcher_last"))
    assert not last["ok"] and "Downloads Folder" in last["message"] and "external_resume.sh" in last["message"]
    desktop_access.forget()


def _short(run):
    def wrapped(*a, **k):
        k["timeout"] = min(k.get("timeout", 1), 1)
        return run(*a, **k)
    return wrapped
