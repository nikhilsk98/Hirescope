"""Background resume queue + worker (stub builds: no Claude, no LaTeX), the hirescope:// launcher, the installer."""
import json
import os
import plistlib
import sys
import time
from pathlib import Path

import pytest

from jobsearch import launcher, live
from jobsearch import paths as hs_paths
from jobsearch.ops import launcher_install
from jobsearch.storage import db
from resume_builder import pipeline, queue

from .conftest import insert_job

STUB = """import json, os, sys, time
from datetime import datetime, timezone
job_dir, mode = sys.argv[1], sys.argv[2]
if mode == "sleep":
    time.sleep(60)
if mode == "fail":
    print("Error: DEMO build failed on purpose", flush=True)
    sys.exit(3)
n = 1
while os.path.isdir(os.path.join(job_dir, f"v{n}")):
    n += 1
v = os.path.join(job_dir, f"v{n}")
os.makedirs(v)
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
json.dump({"version": n, "status": "ready", "generated_at": now, "files": {"pdf": "r.pdf"}}, open(os.path.join(v, "manifest.json"), "w"))
open(os.path.join(v, "r.pdf"), "wb").write(b"%PDF-1.4 DEMO")
json.dump({"version": n, "status": "ready", "pdf": "r.pdf"}, open(os.path.join(job_dir, "current.json"), "w"))
print("DEMO build saved", v, flush=True)
"""


@pytest.fixture
def qenv(tmp_path, monkeypatch):
    data = hs_paths.DATA
    conn = db.connect(hs_paths.DB)
    a = insert_job(conn, data / "jd", company="DemoCorp", title="Backend Engineer Intern", job_id="q-1")
    b = insert_job(conn, data / "jd", company="Full Time Co", title="Software Engineer I", track="full_time",
                   job_id="q-2")
    stub = tmp_path / "stub_build.py"
    stub.write_text(STUB)

    def use(mode: str, pk: int):
        jd = pipeline.job_dir("DemoCorp" if pk == a else "Full Time Co", pk,
                              "Backend Engineer Intern" if pk == a else "Software Engineer I")
        jd.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HIRESCOPE_QUEUE_COMMAND", json.dumps([sys.executable, str(stub), str(jd), mode,
                                                                  "HIRESCOPE_FAKE_BUILD", "{job}"]))
        return jd

    class E:
        pass
    E.conn, E.a, E.b, E.use = conn, a, b, use
    yield E
    conn.close()


def live_payload() -> dict:
    lv = (hs_paths.DASHBOARD / "live.js").read_text()
    return json.loads(lv.split("})(", 1)[1].rsplit(");", 1)[0])


def run_worker(**kw):
    return queue.Worker(poll=0.05, once=True, **kw).run()


def test_add_skips_duplicates_and_ready_resumes(qenv):
    out = queue.add(qenv.conn, [qenv.a, qenv.a, qenv.b])
    assert [o["status"] for o in out] == ["queued", "already", "queued"]
    assert queue.add(qenv.conn, [qenv.a], regenerate=True)[0]["status"] == "already"        # upgrades the waiting item
    assert queue.active_item(qenv.conn, qenv.a)["regenerate"] == 1
    st = qenv.conn.execute("SELECT status FROM resume_status WHERE job_pk=?", (qenv.a,)).fetchone()
    assert st["status"] == "queued"
    assert queue.add(qenv.conn, [999999])[0]["status"] == "error"


def test_worker_builds_in_order_and_saves(qenv):
    jd = qenv.use("ok", qenv.a)
    queue.add(qenv.conn, [qenv.a])
    assert run_worker() == 0
    it = qenv.conn.execute("SELECT * FROM resume_queue WHERE job_pk=?", (qenv.a,)).fetchone()
    assert it["status"] == "done" and it["result"] == "ready" and it["attempts"] == 1
    assert (jd / "v1" / "manifest.json").exists()
    row = qenv.conn.execute("SELECT status, current_version FROM resume_status WHERE job_pk=?", (qenv.a,)).fetchone()
    assert row["status"] == "ready" and row["current_version"] == 1
    assert (hs_paths.DASHBOARD / "resumes.js").exists() and (hs_paths.DASHBOARD / "live.js").exists()
    assert (hs_paths.LOG_DIR / "queue").is_dir()
    # a current READY resume: queued again without regenerate → skipped, never built twice
    assert queue.add(qenv.conn, [qenv.a])[0]["status"] == "skipped"
    queue.add(qenv.conn, [qenv.a], regenerate=True)
    run_worker()
    assert (jd / "v2").exists()


def test_failed_build_retries_once_then_fails_with_reason(qenv):
    qenv.use("fail", qenv.a)
    queue.add(qenv.conn, [qenv.a])
    run_worker()
    it = qenv.conn.execute("SELECT * FROM resume_queue WHERE job_pk=?", (qenv.a,)).fetchone()
    assert it["status"] == "failed" and it["attempts"] == 2
    assert "DEMO build failed on purpose" in it["message"] and "queue item" not in it["message"]
    st = qenv.conn.execute("SELECT status, message FROM resume_status WHERE job_pk=?", (qenv.a,)).fetchone()
    assert st["status"] == "failed" and "DEMO" in st["message"]
    logs = sorted((hs_paths.LOG_DIR / "queue").glob(f"job{qenv.a}-*.log"))
    assert len(logs) == 2                                                           # attempt 1 and the retry


def test_cancel_queued_and_running(qenv):
    queue.add(qenv.conn, [qenv.a, qenv.b])
    assert queue.cancel(qenv.conn, qenv.b)["status"] == "cancelled"
    assert queue.cancel(qenv.conn, qenv.b)["status"] == "none"
    assert qenv.conn.execute("SELECT 1 FROM resume_status WHERE job_pk=?", (qenv.b,)).fetchone() is None
    qenv.use("sleep", qenv.a)
    w = queue.Worker(poll=0.05)
    w.tick(qenv.conn)                                      # starts job a
    it = queue.active_item(qenv.conn, qenv.a)
    assert it["status"] == "generating" and queue._alive(it["pid"])
    queue.cancel(qenv.conn, qenv.a)
    time.sleep(0.3)
    w.tick(qenv.conn)
    assert not w.running and not queue._alive(it["pid"])
    assert qenv.conn.execute("SELECT status FROM resume_queue WHERE id=?", (it["id"],)).fetchone()[0] == "cancelled"


def test_restart_resumes_the_interrupted_build(qenv):
    qenv.use("sleep", qenv.a)
    queue.add(qenv.conn, [qenv.a, qenv.b])
    w = queue.Worker(poll=0.05)
    w.tick(qenv.conn)
    pid = queue.active_item(qenv.conn, qenv.a)["pid"]
    # the worker dies without cleaning up (launchd restart): its build keeps running until the next worker starts
    w.running.clear()
    assert queue.recover(qenv.conn) == 1
    assert not queue._alive(pid)
    rows = qenv.conn.execute("SELECT job_pk, status, attempts FROM resume_queue ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(qenv.a, "queued", 0), (qenv.b, "queued", 0)]     # same order, no attempt used
    qenv.use("ok", qenv.a)
    run_worker()
    assert queue.active_item(qenv.conn, qenv.a) is None


def test_concurrency_limit_and_one_build_per_job(qenv, monkeypatch):
    qenv.use("sleep", qenv.a)
    queue.add(qenv.conn, [qenv.a, qenv.b])
    w = queue.Worker(poll=0.05)
    w.s["concurrency"] = 1
    w.tick(qenv.conn)
    assert len(w.running) == 1
    w.s["concurrency"] = 2
    w.tick(qenv.conn)
    assert len(w.running) == 2
    assert queue.settings()["concurrency"] <= 2
    monkeypatch.setattr(queue.rc, "load_settings", lambda: type("S", (), {"raw": {"resume_queue": {"concurrency": 9}}})())
    assert queue.settings()["concurrency"] == 2
    for r in list(w.running.values()):
        queue.stop_group(r["proc"].pid)


def test_queue_pauses_while_a_run_holds_the_lock(qenv):
    from jobsearch.ops.lock import RunLock
    qenv.use("ok", qenv.a)
    queue.add(qenv.conn, [qenv.a])
    lock = RunLock(hs_paths.STATE_DIR / "run.lock", what="run")
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text(json.dumps({"pid": os.getppid(), "started": "2099-01-01T00:00:00+00:00", "what": "run"}))
    w = queue.Worker(poll=0.05)
    w.tick(qenv.conn)
    assert not w.running and w.paused and "run" in w.paused
    lock.path.unlink()
    w.tick(qenv.conn)
    assert len(w.running) == 1


def test_command_is_non_interactive_and_allowlisted(qenv, monkeypatch):
    monkeypatch.delenv("HIRESCOPE_QUEUE_COMMAND", raising=False)
    cmd = queue.command({"job_pk": 42, "regenerate": 1}, queue.settings())
    assert cmd[1:3] == ["-p", "/resume-builder 42 --queue --regenerate"]
    assert "--permission-prompts" in cmd and cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert any(a.startswith("Bash(") and "-m resume_builder" in a for a in cmd)
    assert not any(a in ("Bash", "Bash(*)") for a in cmd)
    assert "--append-system-prompt" not in cmd
    retry = queue.command({"job_pk": 42, "regenerate": 0, "message": "retrying (attempt 2 of 2): bullet_length: "
                           "bullets over 2 lines: 3 lines: Designing the MLOps lifecycle"}, queue.settings())
    note = retry[retry.index("--append-system-prompt") + 1]
    assert "Designing the MLOps lifecycle" in note and "tighten" in note


# --- launcher -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "hirescope://queue?job=12", "hirescope://queue?job=12&regenerate=1", "hirescope://queue?job=1,2,3",
    "hirescope://queue?list=bucket", "hirescope://queue?list=bucket&regenerate=1", "hirescope://cancel?job=7",
    "hirescope://interactive?job=7", "HIRESCOPE://queue/?job=5"])
def test_launcher_accepts_known_links(url):
    action, p = launcher.parse(url)
    assert action in launcher.ACTIONS


@pytest.mark.parametrize("url,why", [
    ("hirescope://queue?job=12a", "numeric"), ("hirescope://queue?job=-1", "numeric"),
    ("hirescope://queue?job=1;rm -rf ~", "numeric"), ("hirescope://queue?job=$(id)", "numeric"),
    ("hirescope://queue?list=shortlisted", "unknown value"), ("hirescope://queue?job=1&list=bucket", "either"),
    ("hirescope://queue?job=1&job=2", "twice"), ("hirescope://queue?job=1&evil=2", "unknown parameter"),
    ("hirescope://queue?job=1&regenerate=yes", "unknown value"), ("hirescope://launch?job=1", "unknown action"),
    ("https://queue?job=1", "not a hirescope"), ("hirescope://cancel?job=1,2", "numeric"),
    ("hirescope://queue?job=1#x", "fragment"), ("hirescope://queue/../x?job=1", "path"),
    ("hirescope://queue?job=1%0a", "control"), ("hirescope://cancel", "needs job"), ("", "no link"),
    ("hirescope://queue?job=" + ",".join(["1"] * 101), "numeric")])
def test_launcher_rejects_bad_links(url, why):
    with pytest.raises(launcher.LinkError, match=why):
        launcher.parse(url)


def test_launcher_queue_cancel_and_unknown_job(qenv, capsys):
    assert "queued 1" in launcher.handle(f"hirescope://queue?job={qenv.a}")
    assert queue.active_item(qenv.conn, qenv.a)["origin"] == "dashboard"
    assert "cancelled" in launcher.handle(f"hirescope://cancel?job={qenv.a}")
    assert launcher.main(["hirescope://queue?job=99999999"]) == 2
    assert "no HireScope job" in capsys.readouterr().err
    assert launcher.main(["hirescope://queue?job=1;id"]) == 2
    log = (hs_paths.LOG_DIR / "launcher.log").read_text().splitlines()
    assert any('"ok": false' in x for x in log) and any('"ok": true' in x for x in log)
    lv = (hs_paths.DASHBOARD / "live.js").read_text()
    assert "window.__hsLive" in lv and live_payload()["launcher"]["ok"]


def test_launcher_is_silent_except_interactive(qenv, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: calls.append(a[0]) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr(queue.subprocess, "run", lambda *a, **k: calls.append(a[0]) or type("R", (), {"returncode": 0, "stdout": ""})())
    for url in (f"hirescope://queue?job={qenv.a}", f"hirescope://cancel?job={qenv.a}"):
        launcher.handle(url)
    assert not any("Terminal" in " ".join(map(str, c)) for c in calls)
    launcher.handle(f"hirescope://interactive?job={qenv.a}")
    script = calls[-1]
    assert script[0] == "osascript" and 'tell application "Terminal"' in script[2]
    assert f'claude \\"/resume-builder {qenv.a}\\"' in script[2] and str(hs_paths.ROOT) in script[2]


# --- installer ----------------------------------------------------------------------------------------------------------
def test_installer_renders_app_and_worker(tmp_path):
    src = launcher_install.applescript()
    assert "on open location theURL" in src and "quoted form of theURL" in src and "{{REPO}}" not in src
    assert "on idle" in src and "quit" in src and "property " not in src     # quits when idle; no saved state
    code = "\n".join(x for x in src.split("on run")[0].splitlines() if not x.strip().startswith("--"))
    assert "Terminal" not in code and "do shell script" in code           # links run silently: no Terminal window
    pl = plistlib.loads(launcher_install.worker_plist().encode())
    assert pl["Label"] == launcher_install.WORKER_LABEL and pl["RunAtLoad"] is True
    assert pl["KeepAlive"] == {"SuccessfulExit": False}
    assert pl["ProgramArguments"][-1].endswith("scripts/queue_worker.sh")
    assert "/opt/homebrew/bin" in pl["EnvironmentVariables"]["PATH"] and ".local/bin" in pl["EnvironmentVariables"]["PATH"]
    app = tmp_path / "X.app" / "Contents"
    app.mkdir(parents=True)
    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.apple.ScriptEditor.id.x"}))
    d = launcher_install.patch_info_plist(tmp_path / "X.app")
    assert d["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["hirescope"] and d["LSUIElement"] is True
    out = launcher_install.install(dry=True)
    assert any("hirescope://" in x for x in out)


def test_install_and_uninstall_steps(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "osacompile":
            assert "-s" in cmd                                   # stay-open: links arriving together queue up
            app = Path(cmd[cmd.index("-o") + 1]) / "Contents"
            app.mkdir(parents=True)
            (app / "Info.plist").write_bytes(plistlib.dumps({}))
        return type("R", (), {"returncode": 0, "stderr": ""})()
    lines = launcher_install.install(apps=tmp_path / "Apps", agents=tmp_path / "Agents", run=fake_run)
    assert (tmp_path / "Apps" / launcher_install.APP_NAME / "Contents" / "Info.plist").exists()
    assert (tmp_path / "Agents" / f"{launcher_install.WORKER_LABEL}.plist").exists()
    assert [c[0] for c in calls][:2] == ["osacompile", "codesign"] and any(c[0].endswith("lsregister") for c in calls)
    assert any(c[:2] == ["launchctl", "bootstrap"] for c in calls)
    assert "installed\": true" in (hs_paths.DASHBOARD / "launcher.js").read_text()
    assert any("Always allow" in x for x in lines)
    launcher_install.uninstall(apps=tmp_path / "Apps", agents=tmp_path / "Agents", run=fake_run)
    assert not (tmp_path / "Apps" / launcher_install.APP_NAME).exists()
    assert "installed\": false" in (hs_paths.DASHBOARD / "launcher.js").read_text()


def test_install_replaces_an_old_app_and_reports_launchd_errors(tmp_path):
    def fake_run(cmd, **kw):
        if cmd[0] == "osacompile":
            app = Path(cmd[cmd.index("-o") + 1]) / "Contents"
            app.mkdir(parents=True)
            (app / "Info.plist").write_bytes(plistlib.dumps({}))
            (app / "NEW").write_text("DEMO")
        rc_ = 5 if cmd[:2] == ["launchctl", "bootstrap"] else 0
        return type("R", (), {"returncode": rc_, "stderr": "Bootstrap failed: 5: Input/output error\n"})()
    old = tmp_path / "Apps" / launcher_install.APP_NAME
    (old / "Contents").mkdir(parents=True)
    (old / "Contents" / "OLD").write_text("DEMO")
    with pytest.raises(RuntimeError, match="launchctl bootstrap failed.*Input/output error"):
        launcher_install.install(apps=tmp_path / "Apps", agents=tmp_path / "Agents", run=fake_run)
    assert (old / "Contents" / "NEW").exists() and not (old / "Contents" / "OLD").exists()   # replaced, not merged


def test_applescript_refuses_a_repo_path_it_cannot_quote(tmp_path):
    for bad in ('/tmp/my "repo"', "/tmp/back\\slash"):
        with pytest.raises(ValueError, match="quotes or backslashes"):
            launcher_install.applescript(Path(bad))


def test_installer_status_and_main(tmp_path, monkeypatch, capsys):
    apps, agents = tmp_path / "Apps", tmp_path / "Agents"
    ran = []
    monkeypatch.setattr(launcher_install.subprocess, "run",
                        lambda cmd, **k: ran.append(cmd) or type("R", (), {"returncode": 113})())
    out = launcher_install.status(apps, agents)
    assert out[0].startswith("launcher app: not installed") and out[1].startswith("resume worker: not loaded")
    assert ran[0][:2] == ["launchctl", "print"]
    (apps / launcher_install.APP_NAME).mkdir(parents=True)
    monkeypatch.setattr(launcher_install.subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 0})())
    out = launcher_install.status(apps, agents)
    assert out[0].startswith("launcher app: installed") and out[1].startswith("resume worker: loaded")
    # main: real installs are faked (never the real ~/Applications or launchd)
    called = []
    monkeypatch.setattr(launcher_install, "install", lambda dry=False: called.append(("install", dry)) or ["DEMO ok"])
    monkeypatch.setattr(launcher_install, "uninstall", lambda: called.append(("uninstall",)) or ["DEMO removed"])
    monkeypatch.setattr(launcher_install, "status", lambda: called.append(("status",)) or ["DEMO status"])
    monkeypatch.setattr(launcher_install.sys, "platform", "darwin")
    assert launcher_install.main(["install", "--dry-run"]) == 0 and "DEMO ok" in capsys.readouterr().out
    assert launcher_install.main(["uninstall"]) == 0 and launcher_install.main(["status"]) == 0
    assert called == [("install", True), ("uninstall",), ("status",)]
    monkeypatch.setattr(launcher_install, "status", lambda: (_ for _ in ()).throw(RuntimeError("DEMO launchd down")))
    assert launcher_install.main(["status"]) == 1 and "install_launcher: DEMO launchd down" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        launcher_install.main(["reinstall"])                              # unknown command
    monkeypatch.setattr(launcher_install.sys, "platform", "linux")
    assert launcher_install.main(["install"]) == 2 and "macOS only" in capsys.readouterr().err
    assert len(called) == 3                                               # nothing ran on another OS

def test_live_files(qenv):
    live.ensure_files(qenv.conn)
    assert (hs_paths.DASHBOARD / "launcher.js").exists() and (hs_paths.DASHBOARD / "live.js").exists()


def test_skill_documents_queue_mode():
    skill = (hs_paths.ROOT / ".claude" / "skills" / "resume-builder" / "SKILL.md").read_text()
    sec = skill.split("## Queue mode", 1)[1].split("\n## ", 1)[0]
    assert "Never ask anything" in sec and "prepare <job_id> --queue" in sec
    assert "Never run `add-evidence`" in sec


def test_finished_builds_notify_and_refresh_everything(qenv, monkeypatch):
    sent = []
    monkeypatch.setattr(queue, "notify", lambda t, m: sent.append((t, m)))
    qenv.use("ok", qenv.a)
    queue.add(qenv.conn, [qenv.a])
    run_worker()
    assert sent and sent[0][0] == "Resume ready" and "DemoCorp" in sent[0][1]
    assert (hs_paths.DASHBOARD / "sync.js").exists() and (hs_paths.DASHBOARD / "resumes.js").exists()
    assert live_payload()["queue"][0]["status"] == "done"
    qenv.use("fail", qenv.b)
    queue.add(qenv.conn, [qenv.b])
    run_worker()
    assert sent[-1][0] == "Resume failed" and "DEMO build failed" in sent[-1][1]


def test_ready_with_questions_pending(qenv, monkeypatch):
    qenv.conn.execute("INSERT INTO resume_questions (job_pk, kind, keyword, question, asked_at) VALUES (?,?,?,?,?)",
                      (qenv.a, "keyword", "Kafka", "Have you used Kafka?", "2026-10-17T00:00:00+00:00"))
    qenv.conn.commit()
    qenv.use("ok", qenv.a)
    queue.add(qenv.conn, [qenv.a])
    run_worker()
    it = qenv.conn.execute("SELECT result, message FROM resume_queue WHERE job_pk=?", (qenv.a,)).fetchone()
    assert it["result"] == "ready_questions" and "1 question(s) for you" in it["message"]


def test_retry_on_database_is_locked():
    import sqlite3
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"
    assert queue.retry_locked(flaky) == "ok" and len(calls) == 3
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        queue.retry_locked(lambda: (_ for _ in ()).throw(sqlite3.OperationalError("no such table: x")))


# --- worker edge cases: settings, bookkeeping, processes ------------------------------------------------------------
def item_row(conn, pk):
    return conn.execute("SELECT * FROM resume_queue WHERE job_pk=? ORDER BY id DESC LIMIT 1", (pk,)).fetchone()


def ready_version(jd: Path, n: int = 1, generated_at: str = "2026-10-17T01:00:00+00:00", manifest: bool = True):
    """A DEMO READY version n in the job folder (current.json points at it)."""
    v = jd / f"v{n}"
    v.mkdir(parents=True, exist_ok=True)
    if manifest:
        (v / "manifest.json").write_text(json.dumps({"version": n, "status": "ready", "generated_at": generated_at}))
    (jd / "current.json").write_text(json.dumps({"version": n, "status": "ready", "pdf": "r.pdf"}))


def test_broken_settings_fall_back_to_defaults_and_are_clamped(monkeypatch):
    def boom():
        raise ValueError("DEMO: resume_builder.yaml is not valid YAML")
    monkeypatch.setattr(queue.rc, "load_settings", boom)
    assert queue.settings() == queue.DEFAULTS
    monkeypatch.setattr(queue.rc, "load_settings", lambda: type("S", (), {"raw": {"resume_queue": {
        "concurrency": 0, "retries": 7, "timeout_minutes": None}}})())
    s = queue.settings()
    assert s["concurrency"] == 1 and s["retries"] == 3 and s["timeout_minutes"] == 45     # None keeps the default


def test_retry_locked_retries_busy_without_waiting_for_real(monkeypatch):
    import sqlite3
    waits = []
    monkeypatch.setattr(queue.time, "sleep", waits.append)
    calls = []

    def busy():
        calls.append(1)
        raise sqlite3.OperationalError("database is busy")
    with pytest.raises(sqlite3.OperationalError, match="busy"):
        queue.retry_locked(busy, tries=3)
    assert len(calls) == 3 and waits == [0.25, 0.5]                    # backs off, gives up after the last try
    assert queue.retry_locked(lambda: "never", tries=0) is None


def test_unknown_job_has_no_ready_resume(qenv):
    assert queue.current_ready(qenv.conn, 999999) is None


def test_add_keeps_the_item_when_the_status_update_fails(qenv, monkeypatch):
    from resume_builder import hirescope_bridge

    def boom(*a, **k):
        raise RuntimeError("DEMO resumes.js not writable")
    monkeypatch.setattr(hirescope_bridge, "set_status", boom)
    out = queue.add(qenv.conn, [qenv.a])
    assert out[0]["status"] == "queued" and queue.active_item(qenv.conn, qenv.a)["status"] == "queued"


def test_bucket_jobs_skip_ready_unknown_and_removed(qenv):
    from jobsearch import bucket
    bucket.set_in(qenv.conn, qenv.a, True, "2026-10-17T01:00:00+00:00")
    bucket.set_in(qenv.conn, qenv.b, True, "2026-10-17T02:00:00+00:00")
    qenv.conn.execute("INSERT INTO bucket (job_pk, job_key, in_bucket, added_at, updated_at, snapshot) VALUES "
                      "(999999, 'Gone::x::1', 1, '2026-10-17T00:00:00+00:00', '2026-10-17T00:00:00+00:00', '{}')")
    qenv.conn.commit()
    assert queue.bucket_jobs(qenv.conn) == [qenv.a, qenv.b]               # a job no longer in the DB is left out
    ready_version(qenv.use("ok", qenv.a))
    assert queue.bucket_jobs(qenv.conn) == [qenv.b]                       # a has a current READY resume
    assert queue.bucket_jobs(qenv.conn, regenerate=True) == [qenv.a, qenv.b]
    bucket.set_in(qenv.conn, qenv.b, False)
    assert queue.bucket_jobs(qenv.conn) == []


def test_cancel_still_cancels_when_the_status_reset_fails(qenv, monkeypatch):
    from resume_builder import hirescope_bridge
    queue.add(qenv.conn, [qenv.a])
    monkeypatch.setattr(hirescope_bridge, "clear_in_flight", lambda *a, **k: (_ for _ in ()).throw(OSError("DEMO")))
    assert queue.cancel(qenv.conn, qenv.a)["status"] == "cancelled"
    assert item_row(qenv.conn, qenv.a)["status"] == "cancelled"


def test_alive_and_is_build_checks(monkeypatch):
    assert queue._alive(None) is False and queue._alive(0) is False
    assert queue._alive(os.getpid()) is True

    def denied(pid, sig):
        raise PermissionError("DEMO: another user's process")
    monkeypatch.setattr(queue.os, "kill", denied)
    assert queue._alive(4242) is True                                    # exists, just not ours to signal
    monkeypatch.setattr(queue.subprocess, "run", lambda *a, **k: type("R", (), {
        "stdout": "/usr/bin/python -m resume_builder prepare 7"})())
    assert queue._is_build(4242) is True
    monkeypatch.setattr(queue.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "/usr/bin/vim notes"})())
    assert queue._is_build(4242) is False                                 # a recycled pid: never killed

    def no_ps(*a, **k):
        raise OSError("DEMO: ps missing")
    monkeypatch.setattr(queue.subprocess, "run", no_ps)
    assert queue._is_build(4242) is False


def test_stop_group_terms_then_kills_only_what_is_left(monkeypatch):
    sent = []
    monkeypatch.setattr(queue.time, "sleep", lambda s: None)
    monkeypatch.setattr(queue.os, "getpgid", lambda pid: 777)
    monkeypatch.setattr(queue.os, "killpg", lambda pg, sig: sent.append((pg, sig)))

    def not_our_child(pid, opts):
        raise ChildProcessError
    monkeypatch.setattr(queue.os, "waitpid", not_our_child)
    alive = iter([True, False, False])
    monkeypatch.setattr(queue, "_alive", lambda pid: next(alive))
    queue.stop_group(4242, grace=5)
    assert sent == [(777, queue.signal.SIGTERM)]                         # it stopped after SIGTERM: no SIGKILL
    sent.clear()
    monkeypatch.setattr(queue, "_alive", lambda pid: True)
    queue.stop_group(4242, grace=0)
    assert sent == [(777, queue.signal.SIGTERM), (777, queue.signal.SIGKILL)]
    sent.clear()

    def gone(pg, sig):
        raise ProcessLookupError
    monkeypatch.setattr(queue.os, "killpg", gone)
    queue.stop_group(4242)                                               # the group exited in between: no error
    monkeypatch.setattr(queue.os, "getpgid", lambda pid: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(queue.os, "killpg", lambda pg, sig: sent.append(sig))
    queue.stop_group(4242)
    assert sent == []                                                    # no process: nothing signalled


def test_abandon_stale_work_only_for_this_queue_or_old_generations(qenv):
    from datetime import datetime, timedelta, timezone
    from resume_builder import pipeline as pl
    work = qenv.use("ok", qenv.a) / pl.WORK
    work.mkdir(parents=True)
    st = work / "state.json"

    def state(status="generating", age_s=60, queue_item=None):
        at = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat(timespec="seconds")
        st.write_text(json.dumps({"status": status, "prepared_at": at, "queue_item": queue_item}))

    item = {"job_pk": qenv.a, "id": 5, "attempts": 0}
    state()                                                              # someone else's recent generation
    queue._abandon_stale_work(qenv.conn, item)
    assert json.loads(st.read_text())["status"] == "generating"
    state(queue_item=5)
    queue._abandon_stale_work(qenv.conn, item)
    d = json.loads(st.read_text())
    assert d["status"] == "abandoned" and d["abandoned_by"] == "queue item 5"
    state(age_s=pl.IN_PROGRESS_S + 60)
    queue._abandon_stale_work(qenv.conn, item)
    assert json.loads(st.read_text())["status"] == "abandoned"
    state()
    queue._abandon_stale_work(qenv.conn, {**item, "attempts": 1})        # our own earlier attempt left it
    assert json.loads(st.read_text())["status"] == "abandoned"
    state(status="ready")
    queue._abandon_stale_work(qenv.conn, {**item, "attempts": 1})
    assert json.loads(st.read_text())["status"] == "ready"               # a finished generation is left alone
    queue._abandon_stale_work(qenv.conn, {**item, "job_pk": 999999})     # unknown job: nothing to do


def test_outcome_reasons_in_order_of_trust(qenv, tmp_path):
    from resume_builder import hirescope_bridge
    jd = qenv.use("ok", qenv.a)
    item, started = {"job_pk": qenv.a}, "2026-10-17T00:00:00+00:00"
    log = tmp_path / "build.log"
    # current.json says READY but its manifest is gone: only a REUSED build counts
    ready_version(jd, manifest=False)
    log.write_text("# header\nREUSED v1 (inputs unchanged)\n")
    assert queue.outcome(qenv.conn, item, started, 0, log) == (True, "v1 ready", 1)
    (jd / "current.json").unlink()
    # a failed version saved after the item started: its first three reasons
    (jd / "v1" / "manifest.json").write_text(json.dumps({"status": "failed", "generated_at": "2026-10-17T01:00:00+00:00",
                                                         "failure_reasons": ["page_count: 2 pages", "b", "c", "d"]}))
    ok, why, v = queue.outcome(qenv.conn, item, started, 1, log)
    assert not ok and v is None and why == "page_count: 2 pages; b; c"
    # unreadable newest manifest → the resume status message
    (jd / "v1" / "manifest.json").write_text("{not json")
    hirescope_bridge.set_status(qenv.a, "failed", "DEMO status says why", db_path=hs_paths.DB)
    assert queue.outcome(qenv.conn, item, started, 1, log)[1] == "DEMO status says why"
    # no status → the last lines of the log, without the worker's own header
    qenv.conn.execute("DELETE FROM resume_status WHERE job_pk=?", (qenv.a,))
    qenv.conn.commit()
    log.write_text("# 2026 queue item 1 job 1 attempt 1\nstep one\nstep two\nError: DEMO tectonic missing\n")
    why = queue.outcome(qenv.conn, item, started, 1, log)[1]
    assert why == "step one step two Error: DEMO tectonic missing"
    # nothing at all → the exit code
    assert queue.outcome(qenv.conn, item, started, 9, tmp_path / "missing.log")[1] == \
        "the build stopped without saving a version (exit code 9)"


def test_after_build_never_raises(qenv, monkeypatch, caplog):
    import jobsearch
    from jobsearch import hooks
    from resume_builder import hirescope_bridge
    calls = []

    def boom(what):
        def f(*a, **k):
            calls.append(what)
            raise RuntimeError(f"DEMO {what} broke")
        return f
    monkeypatch.setattr(hirescope_bridge, "update_job", boom("status"))
    monkeypatch.setattr(hooks, "after_change", boom("sync"))
    queue.after_build(qenv.conn, qenv.a)
    assert calls == ["status", "sync"]                                   # one failing step doesn't stop the next
    assert sum("after build" in r.getMessage() for r in caplog.records) == 2
    calls.clear()
    monkeypatch.delattr(jobsearch, "hooks")                              # an install without the hooks module
    monkeypatch.setitem(sys.modules, "jobsearch.hooks", None)
    queue.after_build(qenv.conn, qenv.a)
    assert calls == ["status"]


def test_publish_dashboard_waits_for_a_run_and_clears_the_flag(qenv, monkeypatch):
    from jobsearch import add_url, config, run
    from jobsearch.extract import resume as res
    lock = hs_paths.STATE_DIR / "run.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getppid(), "started": queue.now(), "what": "run"}))
    assert queue.publish_dashboard() == "skipped"                         # a run holds the lock: left pending
    assert lock.exists()
    lock.unlink()
    seen = []
    monkeypatch.setattr(config, "load_config", lambda p: type("C", (), {"synonyms": {}})())
    monkeypatch.setattr(res, "parse_resume", lambda p, syn: "DEMO resume")
    monkeypatch.setattr(run, "setup_logging", lambda verbose: "DEMO log")
    monkeypatch.setattr(add_url, "rescore_and_publish", lambda conn, cfg, resume, lg: seen.append((resume, lg)))
    db.set_meta(qenv.conn, "publish_pending", "1")
    qenv.conn.commit()
    assert queue.publish_dashboard() == "published"
    assert seen == [("DEMO resume", "DEMO log")]
    assert not db.get_meta(qenv.conn, "publish_pending") and not lock.exists()   # flag cleared, lock released


def test_notify_never_raises(monkeypatch):
    from jobsearch.ops import notify as n
    shown = []
    monkeypatch.setattr(n, "notify", lambda t, m: shown.append((t, m)) or True)
    queue.notify("Resume ready", "DemoCorp — v1")
    assert shown == [("Resume ready", "DemoCorp — v1")]
    monkeypatch.setattr(n, "notify", lambda t, m: (_ for _ in ()).throw(OSError("DEMO osascript missing")))
    queue.notify("Resume ready", "DemoCorp — v1")


def test_live_heartbeat_survives_a_live_js_failure(qenv, monkeypatch):
    from jobsearch import live
    monkeypatch.setattr(live, "write", lambda conn: (_ for _ in ()).throw(OSError("DEMO dashboard read-only")))
    w = queue.Worker(poll=0.05)
    w._write_live(qenv.conn, "running")
    hb = json.loads(db.get_meta(qenv.conn, "queue_worker"))
    assert hb["state"] == "running" and hb["pid"] == os.getpid()


def test_build_that_cannot_start_fails_at_once(qenv, monkeypatch, tmp_path):
    monkeypatch.setenv("HIRESCOPE_QUEUE_COMMAND", json.dumps([str(tmp_path / "no-such-claude"), "{job}"]))
    queue.add(qenv.conn, [qenv.a])
    w = queue.Worker(poll=0.05)
    w.tick(qenv.conn)
    it = item_row(qenv.conn, qenv.a)
    assert not w.running and it["status"] == "failed" and it["attempts"] == 1
    assert it["message"].startswith("could not start the build")
    st = qenv.conn.execute("SELECT status, message FROM resume_status WHERE job_pk=?", (qenv.a,)).fetchone()
    assert st["status"] == "failed" and "could not start" in st["message"]


def test_status_update_failures_never_stop_a_build(qenv, monkeypatch):
    from resume_builder import hirescope_bridge
    sent = []
    monkeypatch.setattr(queue, "notify", lambda t, m: sent.append(t))
    qenv.use("fail", qenv.a)
    queue.add(qenv.conn, [qenv.a])
    monkeypatch.setattr(hirescope_bridge, "set_status", lambda *a, **k: (_ for _ in ()).throw(OSError("DEMO")))
    w = queue.Worker(poll=0.05)
    w.s["retries"] = 0
    w.tick(qenv.conn)
    assert len(w.running) == 1 and item_row(qenv.conn, qenv.a)["status"] == "generating"
    next(iter(w.running.values()))["proc"].wait(timeout=30)
    w.tick(qenv.conn)
    it = item_row(qenv.conn, qenv.a)
    assert it["status"] == "failed" and "DEMO build failed" in it["message"] and sent == ["Resume failed"]


def test_timed_out_build_is_stopped_and_failed(qenv, monkeypatch):
    sent = []
    monkeypatch.setattr(queue, "notify", lambda t, m: sent.append((t, m)))
    qenv.use("sleep", qenv.a)
    queue.add(qenv.conn, [qenv.a])
    w = queue.Worker(poll=0.05)
    w.s.update(timeout_minutes=0, retries=0)
    w.tick(qenv.conn)
    r = next(iter(w.running.values()))
    r["t0"] -= 10
    w.tick(qenv.conn)
    assert not w.running and r["proc"].returncode is not None
    it = item_row(qenv.conn, qenv.a)
    assert it["status"] == "failed" and it["message"] == "timed out after 0 minutes" and it["pid"] is None
    assert sent and sent[0][0] == "Resume failed" and "timed out" in sent[0][1]


def test_cancel_seen_by_the_worker_stops_the_build(qenv):
    """The item was cancelled elsewhere (the row only): the worker stops the build itself and keeps "cancelled"."""
    qenv.use("sleep", qenv.a)
    queue.add(qenv.conn, [qenv.a])
    w = queue.Worker(poll=0.05)
    w.tick(qenv.conn)
    it = queue.active_item(qenv.conn, qenv.a)
    with qenv.conn:
        qenv.conn.execute("UPDATE resume_queue SET status='cancelled' WHERE id=?", (it["id"],))
    w.tick(qenv.conn)
    assert not w.running and not queue._alive(it["pid"])
    assert item_row(qenv.conn, qenv.a)["status"] == "cancelled"
    st = qenv.conn.execute("SELECT status FROM resume_status WHERE job_pk=?", (qenv.a,)).fetchone()
    assert st is None or st["status"] not in ("queued", "generating")


def test_tick_applies_spooled_links_imports_and_publishes_before_builds(qenv, monkeypatch):
    from jobsearch import external_resume
    calls = []
    monkeypatch.setattr(launcher, "spooled", lambda: [Path("DEMO.link")])
    monkeypatch.setattr(launcher, "drain_spool", lambda db_path=None: calls.append("links") or 1)
    monkeypatch.setattr(external_resume, "pending_requests", lambda include_blocked=True: [Path("DEMO.json")])
    monkeypatch.setattr(external_resume, "process_requests", lambda conn, cfg=None: calls.append("import") or [])
    monkeypatch.setattr(queue, "publish_dashboard", lambda: calls.append("publish") or "skipped")
    db.set_meta(qenv.conn, "publish_pending", "1")
    qenv.conn.commit()
    qenv.use("ok", qenv.a)
    queue.add(qenv.conn, [qenv.a])
    w = queue.Worker(poll=0.05)
    w.tick(qenv.conn)
    assert calls == ["links", "import", "publish"] and not w.running      # builds wait for the publish
    assert json.loads(db.get_meta(qenv.conn, "queue_worker"))["state"] == "publishing"

    def broken():
        raise RuntimeError("DEMO scoring failed")
    monkeypatch.setattr(queue, "publish_dashboard", broken)
    w.tick(qenv.conn)
    assert len(w.running) == 1 and not db.get_meta(qenv.conn, "publish_pending")   # builds run anyway
    next(iter(w.running.values()))["proc"].wait(timeout=30)
    w.tick(qenv.conn)
    assert item_row(qenv.conn, qenv.a)["status"] == "done"


def test_tick_survives_spool_and_import_errors(qenv, monkeypatch, caplog):
    from jobsearch import external_resume

    def boom(*a, **k):
        raise OSError("DEMO state dir unreadable")
    monkeypatch.setattr(launcher, "spooled", boom)
    monkeypatch.setattr(external_resume, "pending_requests", boom)
    qenv.use("ok", qenv.a)
    queue.add(qenv.conn, [qenv.a])
    w = queue.Worker(poll=0.05)
    w.tick(qenv.conn)
    assert len(w.running) == 1
    msgs = [r.getMessage() for r in caplog.records]
    assert "spooled links not applied" in msgs and "external jobs not imported" in msgs
    next(iter(w.running.values()))["proc"].wait(timeout=30)
    w.tick(qenv.conn)


def test_concurrency_one_starts_only_the_first_waiting_item(qenv):
    qenv.use("ok", qenv.a)
    queue.add(qenv.conn, [qenv.a, qenv.b])
    w = queue.Worker(poll=0.05)
    w.s["concurrency"] = 1
    w.tick(qenv.conn)
    assert [r["item"]["job_pk"] for r in w.running.values()] == [qenv.a]
    assert item_row(qenv.conn, qenv.b)["status"] == "queued"
    for r in list(w.running.values()):
        r["proc"].wait(timeout=30)
    w.tick(qenv.conn)


def test_never_two_builds_of_one_job_at_once(qenv):
    """Two waiting rows for one job (e.g. left by an older version): the second waits even with a free slot."""
    qenv.use("ok", qenv.a)
    with qenv.conn:
        for _ in range(2):
            qenv.conn.execute("INSERT INTO resume_queue (job_pk, regenerate, status, origin, added_at) "
                              "VALUES (?, 0, 'queued', 'dashboard', ?)", (qenv.a, queue.now()))
    w = queue.Worker(poll=0.05)
    w.s["concurrency"] = 2
    w.tick(qenv.conn)
    assert len(w.running) == 1
    assert [r["status"] for r in qenv.conn.execute("SELECT status FROM resume_queue ORDER BY id")] == \
        ["generating", "queued"]
    next(iter(w.running.values()))["proc"].wait(timeout=30)
    w.tick(qenv.conn)                                                     # the first finishes, the second starts
    assert [r["item"]["job_pk"] for r in w.running.values()] == [qenv.a]
    next(iter(w.running.values()))["proc"].wait(timeout=30)
    w.tick(qenv.conn)


def test_second_worker_exits_when_one_is_running(qenv, capsys):
    lock = hs_paths.STATE_DIR / "queue_worker.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getppid(), "started": queue.now(), "what": "resume queue worker"}))
    queue.add(qenv.conn, [qenv.a])
    assert queue.Worker(poll=0.05).run() == 0
    assert "nothing to do" in capsys.readouterr().err
    assert item_row(qenv.conn, qenv.a)["status"] == "queued" and lock.exists()     # untouched


def test_worker_recovers_builds_then_exits_when_idle(qenv):
    qenv.use("ok", qenv.a)
    with qenv.conn:
        qenv.conn.execute("INSERT INTO resume_queue (job_pk, regenerate, status, origin, added_at, attempts, pid) "
                          "VALUES (?, 0, 'generating', 'dashboard', ?, 1, NULL)", (qenv.a, queue.now()))
    w = queue.Worker(poll=0.02)
    w.s["idle_exit_seconds"] = 0
    assert w.run() == 0
    it = item_row(qenv.conn, qenv.a)
    assert it["status"] == "done" and it["attempts"] == 1                 # the interruption didn't use an attempt
    assert json.loads(db.get_meta(qenv.conn, "queue_worker"))["state"] == "stopped"
    assert not (hs_paths.STATE_DIR / "queue_worker.lock").exists()


def test_worker_that_dies_stops_its_builds_and_releases_the_lock(qenv):
    qenv.use("sleep", qenv.a)
    queue.add(qenv.conn, [qenv.a])

    class Dies(queue.Worker):
        def _write_live(self, conn, state):
            raise RuntimeError(f"DEMO crash while writing {state}")
    with pytest.raises(RuntimeError, match="DEMO crash while writing running"):
        Dies(poll=0.02).run()
    it = item_row(qenv.conn, qenv.a)
    assert it["status"] == "generating" and not queue._alive(it["pid"])    # left for the next worker to resume
    assert not (hs_paths.STATE_DIR / "queue_worker.lock").exists()
    assert queue.recover(qenv.conn) == 1 and item_row(qenv.conn, qenv.a)["status"] == "queued"


def test_kick_writes_the_trigger_and_asks_launchd(monkeypatch):
    trig = hs_paths.STATE_DIR / "queue.trigger"
    assert queue.kick() is False and trig.exists()                        # HIRESCOPE_NO_KICK (tests): trigger only
    monkeypatch.delenv("HIRESCOPE_NO_KICK")
    calls = []
    monkeypatch.setattr(queue.subprocess, "run", lambda cmd, **k: calls.append(cmd) or type("R", (), {"returncode": 0})())
    assert queue.kick() is True
    assert calls == [["launchctl", "kickstart", f"gui/{os.getuid()}/{queue.WORKER_LABEL}"]]
    monkeypatch.setattr(queue.subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 113})())
    assert queue.kick() is False                                          # worker not installed

    def no_launchctl(*a, **k):
        raise FileNotFoundError("DEMO launchctl")
    monkeypatch.setattr(queue.subprocess, "run", no_launchctl)
    assert queue.kick() is False
    blocker = hs_paths.DATA / "not-a-dir"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("x")
    monkeypatch.setattr(hs_paths, "STATE_DIR", blocker / "state")
    monkeypatch.setenv("HIRESCOPE_NO_KICK", "1")
    assert queue.kick() is False                                          # an unwritable trigger is not an error


def test_worker_main_installs_sigterm_and_runs(monkeypatch):
    from jobsearch import run
    handlers, made, basic = {}, [], []
    monkeypatch.setattr(queue.signal, "signal", lambda sig, fn: handlers.setdefault(sig, fn))
    monkeypatch.setattr(run, "setup_logging", lambda v: (_ for _ in ()).throw(OSError("DEMO log dir")))
    monkeypatch.setattr(queue.logging, "basicConfig", lambda **k: basic.append(k))

    class FakeWorker:
        def __init__(self, once=False):
            made.append(once)

        def run(self):
            return 0
    monkeypatch.setattr(queue, "Worker", FakeWorker)
    assert queue.worker_main(once=True) == 0
    assert made == [True] and basic and handlers[queue.signal.SIGTERM] is queue._sigterm
    with pytest.raises(SystemExit) as e:
        queue._sigterm(15, None)
    assert e.value.code == 0
