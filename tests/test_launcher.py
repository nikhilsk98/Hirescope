"""The hirescope:// launcher: every link type, strict validation, nothing from a link ever reaches a shell, silent
except `interactive`. (Per-feature behavior is tested with each feature: queue, Bucket, status, Resume Builder tab.)"""
import json
import random
import string
import subprocess

import pytest

from jobsearch import launcher, paths
from jobsearch.storage import db

from .resume_builder.conftest import insert_job

VALID = [
    "hirescope://queue?job=12", "hirescope://queue?job=12&regenerate=1", "hirescope://queue?list=bucket",
    "hirescope://queue?list=bucket&regenerate=1", "hirescope://cancel?job=12", "hirescope://interactive?job=12",
    "hirescope://bucket?job=12&op=add", "hirescope://bucket?job=12,13&op=remove",
    "hirescope://status?job=12&stage=applied", "hirescope://status?job=12&stage=applied&app=aX1&date=2026-10-17&v=3",
    "hirescope://status?app=aX1&stage=interviewing", "hirescope://status?job=12&stage=undo&app=aX1",
    "hirescope://status?job=12&stage=ready",
    *[f"hirescope://status?app=a1&stage={s}" for s in ("oa", "offer", "rejected", "ghosted", "withdrawn")],
]

INVALID = [
    ("hirescope://queue?job=one", "numeric"), ("hirescope://queue?job=1.5", "numeric"), ("hirescope://queue?job=", "numeric"),
    ("hirescope://queue?job=9999999999", "numeric"), ("hirescope://queue?list=all", "unknown value"),
    ("hirescope://queue?list=bucket&regenerate=true", "unknown value"), ("hirescope://cancel?job=1&regenerate=1", "unknown parameter"),
    ("hirescope://status?job=1&stage=Applied", "unknown value"), ("hirescope://status?job=1&stage=hired", "unknown value"),
    ("hirescope://bucket?job=1&op=delete", "unknown value"), ("hirescope://bucket?job=1&op=add&op=remove", "twice"),
    ("hirescope://interactive?job=1;open -a Calculator", "numeric"), ("hirescope://interactive?job=1%20&&%20id", "malformed"),
    ("hirescope://external?file=/etc/hosts", "only"), ("hirescope://external?file=..%2F..%2F.ssh%2Fid_rsa", "absolute path"),
    ("hirescope://external?file=x.json%00.pdf", "control"), ("hirescope://external?file=~/Documents/x.json", "only from"),
    ("hirescope://shell?cmd=id", "unknown action"), ("hirescope:///queue?job=1", "unexpected path"),
    ("javascript:alert(1)", "not a hirescope"), ("hirescope://queue?job=1&" + "&".join(f"x{i}=1" for i in range(20)), "malformed"),
    ("hirescope://queue?" + "job=1" + "1" * 9000, "too long"),
]


@pytest.mark.parametrize("url", VALID)
def test_every_link_type_parses(url):
    action, p = launcher.parse(url)
    assert action in launcher.ACTIONS and p


@pytest.mark.parametrize("url,why", INVALID)
def test_bad_links_are_rejected_with_a_short_error(url, why):
    with pytest.raises(launcher.LinkError, match=why) as e:
        launcher.parse(url)
    assert len(str(e.value)) < 120


def test_fuzzed_links_never_crash_and_never_run_anything(monkeypatch):
    """Random junk: parse() either validates or raises LinkError, nothing else, and nothing is executed."""
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: ran.append(a))
    rnd = random.Random(7)
    alphabet = string.printable + "éß😀%&=?#/\\;|$`'\""
    actions = list(launcher.ACTIONS) + ["x", ""]
    for _ in range(3000):
        tail = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 60)))
        url = f"hirescope://{rnd.choice(actions)}?{tail}"
        try:
            launcher.parse(url)
        except launcher.LinkError:
            pass
    assert ran == []


def test_link_text_never_reaches_a_shell(monkeypatch):
    conn = db.connect(paths.DB)
    pk = insert_job(conn, paths.DATA / "jd", company="DemoCorp", title="Backend Engineer Intern", job_id="l-1")
    conn.close()
    calls = []

    def rec(args, **kw):
        assert not kw.get("shell"), "shell=True is never used"
        calls.append(args)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    monkeypatch.setattr(subprocess, "run", rec)
    for url in (f"hirescope://queue?job={pk}", f"hirescope://bucket?job={pk}&op=add", f"hirescope://cancel?job={pk}",
                f"hirescope://status?job={pk}&stage=applied&app=aL1"):
        launcher.handle(url)
    assert not any("Terminal" in " ".join(map(str, c)) for c in calls)             # silent
    launcher.handle(f"hirescope://interactive?job={pk}")
    osa = calls[-1]
    assert osa[:2] == ["osascript", "-e"]
    line = launcher.interactive_command(pk)
    assert line.startswith("cd ") and line.endswith(f'&& claude "/resume-builder {pk}"') and str(paths.ROOT) in line


def test_main_exit_codes(capsys):
    assert launcher.main([]) == 2 and "exactly one" in capsys.readouterr().err
    assert launcher.main(["hirescope://queue?job=abc"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("HireScope: ") and "numeric" in err


def test_launcher_app_passes_the_link_as_one_quoted_argument():
    src = (paths.ROOT / "scripts" / "launcher" / "HireScopeLauncher.applescript").read_text()
    assert 'quoted form of theURL' in src and 'do shell script "/bin/zsh " & quoted form of' in src
    sh = (paths.ROOT / "scripts" / "launcher.sh").read_text()
    assert 'jobsearch.launcher "$1"' in sh and "eval" not in sh


def test_busy_database_spools_links_and_applies_them_in_order(monkeypatch):
    """A HireScope run holding SQLite's write lock: links wait briefly, are saved, and apply in order afterwards."""
    from jobsearch import bucket
    conn = db.connect(paths.DB)
    a = insert_job(conn, paths.DATA / "jd", company="DemoCorp", title="Backend Engineer Intern", job_id="sp-1")
    conn.close()
    monkeypatch.setattr(launcher, "BUSY_MS", 50)
    monkeypatch.setattr(launcher, "BUSY_TRIES", 2)
    import sqlite3
    holder = sqlite3.connect(str(paths.DB), timeout=0.1)
    holder.execute("BEGIN IMMEDIATE")                         # what a run's write transaction looks like
    assert "saved" in launcher.handle(f"hirescope://bucket?job={a}&op=add")
    assert "saved" in launcher.handle(f"hirescope://bucket?job={a}&op=remove")
    assert len(launcher.spooled()) == 2
    holder.rollback()
    holder.close()
    assert launcher.drain_spool() == 2 and launcher.spooled() == []
    c = db.connect(paths.DB)
    assert not bucket.in_bucket(c, a)                         # add, then remove: the order held
    assert bucket.entries(c)[str(a)]["in"] is False
    c.close()


# --- link handling edge cases ---------------------------------------------------------------------------------------
@pytest.fixture
def lconn():
    c = db.connect(paths.DB)
    pk = insert_job(c, paths.DATA / "jd", company="DemoCorp", title="Backend Engineer Intern", job_id="le-1")
    yield c, pk
    c.close()


@pytest.mark.parametrize("url,why", [
    ("hirescope://note?job=1&text=hello%01there", "text contains control characters"),
    ("hirescope://note?job=1&text=" + "x" * 4001, "longer than 4000"),
    ("hirescope://external?data=" + "_" * 24, "not a saved external job"),           # base64url, not JSON
    ("hirescope://answer?q=1&a=yes&entry=bad%20id&text=x", "entry must be"),
])
def test_bad_text_and_data_values_are_rejected(url, why):
    with pytest.raises(launcher.LinkError, match=why):
        launcher.parse(url)


def test_queue_bucket_link_reports_an_empty_or_ready_bucket(lconn, monkeypatch):
    from jobsearch import bucket
    from resume_builder import queue
    conn, pk = lconn
    assert launcher.handle("hirescope://queue?list=bucket") == "Bucket: every job already has a current READY resume"
    assert launcher.handle("hirescope://queue?list=bucket&regenerate=1") == "The Bucket is empty"
    bucket.set_in(conn, pk, True)
    assert launcher.handle("hirescope://queue?list=bucket") == "queued 1 resume build(s)"
    it = queue.active_item(conn, pk)
    assert it["origin"] == "bucket" and it["batch"].startswith("bucket-")


def test_interactive_dry_run_opens_nothing(lconn, monkeypatch):
    conn, pk = lconn
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setenv("HIRESCOPE_LAUNCHER_DRY", "1")
    msg = launcher.handle(f"hirescope://interactive?job={pk}")
    assert msg == f"would open Terminal: {launcher.interactive_command(pk)}" and ran == []


def test_links_for_missing_applications_questions_and_refused_files(lconn, monkeypatch):
    from jobsearch import external_resume
    conn, pk = lconn
    with pytest.raises(launcher.LinkError, match=f"job {pk} has no application yet"):
        launcher.handle(f"hirescope://status?job={pk}&stage=interviewing")
    with pytest.raises(launcher.LinkError, match="no application aNope"):
        launcher.handle("hirescope://note?app=aNope&text=DEMO%20note")
    with pytest.raises(launcher.LinkError, match="no question 424242"):
        launcher.handle("hirescope://answer?q=424242&a=no")

    def refuse(file=None, data=None):
        raise external_resume.FileRefused("DEMO: file vanished")
    monkeypatch.setattr(external_resume, "request", refuse)
    with pytest.raises(launcher.LinkError, match="DEMO: file vanished"):
        launcher.handle("hirescope://external?file=demo_job.json")
    log = [json.loads(x) for x in (paths.LOG_DIR / "launcher.log").read_text().splitlines()]
    assert sum(not e["ok"] for e in log) >= 4                              # every refusal is logged


def test_spooled_link_that_no_longer_applies_is_dropped(lconn):
    conn, pk = lconn
    launcher.spool("hirescope://cancel?job=99999999")                     # the job is gone by now
    launcher.spool(f"hirescope://bucket?job={pk}&op=add")
    assert launcher.drain_spool() == 2 and launcher.spooled() == []
    from jobsearch import bucket
    assert bucket.in_bucket(conn, pk)                                     # the next link still applied


def test_handle_survives_live_js_and_log_failures(lconn, monkeypatch, caplog):
    from jobsearch import live
    conn, pk = lconn
    monkeypatch.setattr(live, "write", lambda c: (_ for _ in ()).throw(OSError("DEMO dashboard read-only")))
    assert "added 1" in launcher.handle(f"hirescope://bucket?job={pk}&op=add")
    assert any(r.getMessage() == "live.js not written" for r in caplog.records)
    blocker = paths.DATA / "logs-is-a-file"
    blocker.write_text("x")
    monkeypatch.setattr(paths, "LOG_DIR", blocker / "logs")
    assert "removed 1" in launcher.handle(f"hirescope://bucket?job={pk}&op=remove")   # no log: still done


def test_main_prints_the_result_or_a_short_error(lconn, monkeypatch, capsys):
    conn, pk = lconn
    assert launcher.main([f"hirescope://bucket?job={pk}&op=add"]) == 0
    assert "Bucket: added 1 job(s)" in capsys.readouterr().out

    def broken(conn, p):
        raise RuntimeError("DEMO disk full")
    monkeypatch.setitem(launcher.HANDLERS, "bucket", broken)
    assert launcher.main([f"hirescope://bucket?job={pk}&op=remove"]) == 1
    err = capsys.readouterr().err.strip().splitlines()                    # (a logging handler may print the trace)
    assert err[-1] == "HireScope: RuntimeError: DEMO disk full"


# --- hooks.after_change: every step independent ---------------------------------------------------------------------
def test_after_change_reports_a_failed_step_and_runs_the_rest(lconn, monkeypatch):
    from jobsearch import hooks, live
    from jobsearch.ops import applications_excel
    conn, pk = lconn
    wrote = []
    monkeypatch.setattr(applications_excel, "update", lambda c: (_ for _ in ()).throw(OSError("DEMO Desktop blocked")))
    monkeypatch.setattr(live, "write", lambda c: wrote.append("live"))
    lines = hooks.after_change(conn, reason="DEMO")
    assert lines == ["Warning: applications Excel not updated: DEMO Desktop blocked"]
    assert wrote == ["live"] and (paths.DASHBOARD / "sync.js").exists()


def test_after_change_without_optional_modules(lconn, monkeypatch):
    import sys
    import jobsearch.ops
    import jobsearch.tracker
    from jobsearch import hooks
    conn, pk = lconn
    for pkg, name in ((jobsearch.tracker, "ready_apps"), (jobsearch.ops, "applications_excel")):
        monkeypatch.delattr(pkg, name)
        monkeypatch.setitem(sys.modules, f"{pkg.__name__}.{name}", None)
    assert hooks.after_change(conn) == []
    assert (paths.DASHBOARD / "sync.js").exists() and (paths.DASHBOARD / "live.js").exists()


def test_desktop_catch_up_only_when_something_is_pending(monkeypatch):
    from jobsearch import hooks
    from jobsearch.ops import desktop_access
    calls = []
    monkeypatch.setattr(desktop_access, "sync_desktop", lambda: calls.append(1) or (True, "DEMO copied 2 PDFs"))
    assert hooks._desktop_catch_up() == "" and calls == []                # nothing pending, not forced
    m = desktop_access.pending_marker()
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text("DEMO")
    assert hooks._desktop_catch_up() == "DEMO copied 2 PDFs" and not m.exists()
    m.write_text("DEMO")
    monkeypatch.setattr(desktop_access, "sync_desktop", lambda: (False, "DEMO still blocked"))
    assert hooks._desktop_catch_up(force=True) == "DEMO still blocked" and m.exists()   # retried next time
