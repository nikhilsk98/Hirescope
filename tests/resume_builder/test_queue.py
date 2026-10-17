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
