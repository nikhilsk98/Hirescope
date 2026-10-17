"""Background resume queue: the dashboard queues builds (through the hirescope:// launcher), a worker runs them.

Statuses per item: queued → generating → done (Ready / Ready with questions pending) | failed | cancelled | skipped
(the job already had a current READY resume and no regeneration was asked for). Items live in SQLite (resume_queue),
so the queue survives restarts: a worker that stops mid-build leaves its item "generating"; the next worker kills
what is left of that build and queues it again (an interruption is not a failed attempt).

The worker (`python -m resume_builder queue worker`, started by launchd: com.nikhil.hirescope.worker) runs
`claude -p "/resume-builder <id> --queue"` per job, one at a time by default (config/resume_builder.yaml →
resume_queue.concurrency, at most 2, never the same job twice at once), with stdin closed so a build can never wait for
an answer. It pauses while a HireScope run holds the run lock, retries a failed build once (resume_queue.retries), then
marks it Failed with the reason. Every finished build is saved by the build itself (version folder, Desktop copy);
the worker then refreshes HireScope's resume status, the dashboard files, the applications Excel file, and sends a
macOS notification.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from jobsearch import paths as hs_paths
from jobsearch.storage import db

from . import config as rc

log = logging.getLogger("resume_builder.queue")
ACTIVE = ("queued", "generating")
DONE = ("done", "failed", "cancelled", "skipped")
DEFAULTS = {"concurrency": 1, "retries": 1, "timeout_minutes": 45, "batch_warn": 5, "claude": "~/.local/bin/claude",
            "idle_exit_seconds": 30}
WORKER_LABEL = "com.nikhil.hirescope.worker"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def settings() -> dict:
    try:
        raw = rc.load_settings().raw.get("resume_queue") or {}
    except Exception:          # noqa: BLE001 — broken settings never stop the queue; the build reports them itself
        raw = {}
    s = {**DEFAULTS, **{k: v for k, v in raw.items() if v is not None}}
    s["concurrency"] = max(1, min(2, int(s["concurrency"])))
    s["retries"] = max(0, min(3, int(s["retries"])))
    return s


def retry_locked(fn, *a, tries: int = 12, **kw):
    """Run fn, retrying while SQLite says "database is locked" (a HireScope run or a build writing at the same time)."""
    for i in range(tries):
        try:
            return fn(*a, **kw)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) and "busy" not in str(e) or i == tries - 1:
                raise
            time.sleep(min(5.0, 0.25 * 2 ** i))
    return None


def connect(db_path: Path | None = None):
    return db.connect(db_path or hs_paths.DB)


# --- the queue ---------------------------------------------------------------------------------------------------
def _job(conn, pk: int):
    return conn.execute("SELECT id, company, title, job_status FROM jobs WHERE id=?", (int(pk),)).fetchone()


def current_ready(conn, pk: int) -> int | None:
    """The version number of the job's current READY (or reviewed) resume, from its folder (authoritative), else None."""
    from . import pipeline, versions
    j = _job(conn, pk)
    if not j:
        return None
    jd = pipeline.job_dir(j["company"], pk, j["title"])
    cur = versions.current(jd) if jd.exists() else None
    return cur["version"] if cur and cur.get("status") in ("ready", "reviewed") else None


def active_item(conn, pk: int):
    return conn.execute("SELECT * FROM resume_queue WHERE job_pk=? AND status IN ('queued','generating') "
                        "ORDER BY id LIMIT 1", (int(pk),)).fetchone()


def add(conn, job_pks, regenerate: bool = False, origin: str = "dashboard", batch: str | None = None) -> list[dict]:
    """Queue builds. A job already queued or generating isn't queued twice (a regenerate request upgrades a waiting
    item); a job with a current READY resume is skipped unless regenerate. Returns one outcome per job."""
    from . import hirescope_bridge
    out = []
    for pk in job_pks:
        pk = int(pk)
        j = _job(conn, pk)
        if not j:
            out.append({"job_pk": pk, "status": "error", "message": f"job {pk} is not in the HireScope database"})
            continue
        cur = active_item(conn, pk)
        if cur:
            if regenerate and not cur["regenerate"] and cur["status"] == "queued":
                retry_locked(_exec, conn, "UPDATE resume_queue SET regenerate=1 WHERE id=?", (cur["id"],))
            out.append({"job_pk": pk, "id": cur["id"], "status": "already", "message": f"{j['company']}: already "
                        f"{'generating' if cur['status'] == 'generating' else 'in the queue'}"})
            continue
        ready = None if regenerate else current_ready(conn, pk)
        t = now()
        if ready:
            iid = retry_locked(_exec, conn, "INSERT INTO resume_queue (job_pk, regenerate, status, batch, origin, "
                               "added_at, finished_at, message, result) VALUES (?,?,?,?,?,?,?,?,?)",
                               (pk, 0, "skipped", batch, origin, t, t,
                                f"already has a READY resume (v{ready}); Regenerate builds a new version", "ready"))
            out.append({"job_pk": pk, "id": iid, "status": "skipped",
                        "message": f"{j['company']}: already has a READY resume (v{ready})"})
            continue
        iid = retry_locked(_exec, conn, "INSERT INTO resume_queue (job_pk, regenerate, status, batch, origin, added_at)"
                           " VALUES (?,?,?,?,?,?)", (pk, int(bool(regenerate)), "queued", batch, origin, t))
        try:
            hirescope_bridge.set_status(pk, "queued", "waiting in the resume queue", db_path=_db_path(conn))
        except Exception as e:  # noqa: BLE001 — the queue item is what counts; resumes.js catches up on refresh
            log.warning("resume status not set", extra={"job": pk, "error": str(e)[:200]})
        out.append({"job_pk": pk, "id": iid, "status": "queued", "message": f"{j['company']}: queued"})
    return out


def _db_path(conn) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row and row[2] else None


def _exec(conn, sql: str, args=()) -> int:
    with conn:
        cur = conn.execute(sql, args)
    return cur.lastrowid


def bucket_jobs(conn, regenerate: bool = False) -> list[int]:
    """Bucket jobs that need a build: every job in the Bucket without a current READY resume (all of them with
    regenerate)."""
    pks = [r[0] for r in conn.execute("SELECT job_pk FROM bucket WHERE in_bucket=1 ORDER BY added_at, job_pk")]
    pks = [pk for pk in pks if _job(conn, pk)]
    return pks if regenerate else [pk for pk in pks if not current_ready(conn, pk)]


def cancel(conn, pk: int) -> dict:
    """Cancel the job's queued or running build. A running build's process group is stopped right away (the worker
    also notices); the job's resume status goes back to what its versions say."""
    from . import hirescope_bridge
    it = active_item(conn, pk)
    if not it:
        return {"job_pk": pk, "status": "none", "message": f"job {pk} has no queued or running build"}
    retry_locked(_exec, conn, "UPDATE resume_queue SET status='cancelled', finished_at=?, message=? WHERE id=?",
                 (now(), "cancelled from the dashboard", it["id"]))
    if it["status"] == "generating" and it["pid"]:
        stop_group(it["pid"])
    try:
        hirescope_bridge.clear_in_flight(pk, db_path=_db_path(conn))
    except Exception as e:  # noqa: BLE001
        log.warning("resume status not reset after cancel", extra={"job": pk, "error": str(e)[:200]})
    return {"job_pk": pk, "id": it["id"], "status": "cancelled", "message": "cancelled"}


def items(conn, recent_hours: int = 24) -> list[dict]:
    """Active items in order, then the ones that finished in the last `recent_hours` (newest first)."""
    since = datetime.fromtimestamp(time.time() - recent_hours * 3600, timezone.utc).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT q.*, j.company, j.title FROM resume_queue q LEFT JOIN jobs j ON j.id = q.job_pk WHERE q.status IN "
        "('queued','generating') OR q.finished_at >= ? ORDER BY CASE q.status WHEN 'generating' THEN 0 WHEN 'queued' "
        "THEN 1 ELSE 2 END, CASE WHEN q.status IN ('queued','generating') THEN q.id ELSE -julianday(q.finished_at) END",
        (since,)).fetchall()
    return [dict(r) for r in rows]


# --- processes ---------------------------------------------------------------------------------------------------
def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_build(pid: int) -> bool:
    """The pid is (still) one of our builds, not a recycled pid of something else."""
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(int(pid))], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "resume-builder" in cmd or "resume_builder" in cmd or "HIRESCOPE_FAKE_BUILD" in cmd


def stop_group(pid: int, grace: float = 8.0) -> None:
    """SIGTERM the build's process group (claude and the commands it started), SIGKILL whatever is left."""
    try:
        pgid = os.getpgid(int(pid))
    except (ProcessLookupError, PermissionError, OSError):
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        end = time.time() + grace
        while time.time() < end and _alive(pid):
            time.sleep(0.2)
            try:
                os.waitpid(int(pid), os.WNOHANG)
            except ChildProcessError:
                pass
        if not _alive(pid):
            return


def recover(conn) -> int:
    """Items left "generating" by a worker that stopped: what is left of their build is stopped, and they wait again
    (attempts unchanged: an interruption is not a failure)."""
    n = 0
    for it in conn.execute("SELECT * FROM resume_queue WHERE status='generating'").fetchall():
        if it["pid"] and _alive(it["pid"]) and _is_build(it["pid"]):
            stop_group(it["pid"])
        retry_locked(_exec, conn, "UPDATE resume_queue SET status='queued', pid=NULL, started_at=NULL, "
                     "attempts=MAX(attempts-1, 0), message=? WHERE id=?",
                     ("resumed after the worker restarted", it["id"]))
        n += 1
    return n


def command(item: dict, s: dict) -> list[str]:
    """claude -p "/resume-builder <id> --queue" with only the Resume Builder's own commands allowed; nothing can
    prompt (anything that would ask is denied). HIRESCOPE_QUEUE_COMMAND (a JSON list with {job} / {regenerate}) replaces
    it in tests."""
    flags = " --regenerate" if item["regenerate"] else ""
    override = os.environ.get("HIRESCOPE_QUEUE_COMMAND")
    if override:
        return [str(a).replace("{job}", str(item["job_pk"])).replace("{regenerate}", "1" if item["regenerate"] else "0")
                for a in json.loads(override)]
    py_home = "~/.venvs/hirescope/bin/python"
    allowed = [f"Bash({py_home} -m resume_builder *)", f"Bash({sys.executable} -m resume_builder *)",
               f"Bash({os.path.expanduser(py_home)} -m resume_builder *)", "Read", "Write", "Edit", "Glob", "Grep"]
    cmd = [os.path.expanduser(str(s["claude"])), "-p", f"/resume-builder {item['job_pk']} --queue{flags}",
           "--permission-mode", "acceptEdits", "--permission-prompts", "none",
           "--allowedTools", *dict.fromkeys(allowed), "--output-format", "text", "--no-session-persistence"]
    note = retry_note(item)
    return cmd + (["--append-system-prompt", note] if note else [])


def retry_note(item: dict) -> str | None:
    """A retry starts a new session that knows nothing of the failed one: tell it what failed, so it fixes that in the
    draft instead of repeating it (e.g. a required bullet that ran to 3 lines needs tighter wording; the build's
    automatic trimming can't reword)."""
    msg = str(item.get("message") or "")
    if not msg.startswith("retrying") or ":" not in msg:
        return None
    why = msg.split(":", 1)[1].strip()[:600]
    return ("Resume queue retry: the previous attempt of this generation failed with: " + why + ". Fix exactly that "
            "in your draft before you build (a bullet over 2 lines: tighten that bullet's wording by a few words, "
            "keeping its facts and numbers; over a page: keep the baseline's bullet count and drop JD-irrelevant "
            "skills first). Never drop a bullet the master's notes require.")


def log_dir() -> Path:
    return hs_paths.LOG_DIR / "queue"


def run_lock_held() -> str | None:
    """A HireScope run (or import) holding the run lock: the queue waits. Returns what holds it."""
    from jobsearch.ops.lock import RunLock, _alive as lock_alive
    info = RunLock(hs_paths.STATE_DIR / "run.lock").info() or {}
    if info.get("pid") and lock_alive(int(info["pid"])) and int(info["pid"]) != os.getpid():
        return f"a HireScope {info.get('what', 'run')} is writing (started {info.get('started', '?')})"
    return None


def _abandon_stale_work(conn, item: dict) -> None:
    """A previous attempt of this item (interrupted or failed) left _work/ "generating": that generation is abandoned,
    so prepare can start over. A generation someone else is running (no queue item, recent) is left alone."""
    from . import pipeline
    j = _job(conn, item["job_pk"])
    if not j:
        return
    work = pipeline.job_dir(j["company"], item["job_pk"], j["title"]) / pipeline.WORK
    st = pipeline._state(work)
    if not st or st.get("status") not in ("generating", "validating"):
        return
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(st["prepared_at"])).total_seconds()
    if st.get("queue_item") == item["id"] or age >= pipeline.IN_PROGRESS_S or item["attempts"] > 0:
        st["status"] = "abandoned"
        st["abandoned_by"] = f"queue item {item['id']}"
        (work / "state.json").write_text(json.dumps(st, indent=1), encoding="utf-8")


def outcome(conn, item: dict, started: str, rc_: int | None, log_file: Path | None) -> tuple[bool, str, int | None]:
    """(success, reason / summary, version) for a finished build: success = the job's current version is READY and
    was built after the item started (or prepare reused a still-valid version)."""
    from . import pipeline, versions
    j = _job(conn, item["job_pk"])
    jd = pipeline.job_dir(j["company"], item["job_pk"], j["title"]) if j else None
    cur = versions.current(jd) if jd and jd.exists() else None
    text = ""
    if log_file and log_file.exists():
        text = log_file.read_text(encoding="utf-8", errors="replace")[-20000:]
    if cur and cur.get("status") in ("ready", "reviewed"):
        try:
            m = versions.read_manifest(jd / f"v{cur['version']}")
        except (OSError, ValueError):
            m = {}
        if (m.get("generated_at") or "") >= started or "REUSED" in text:
            return True, f"v{cur['version']} ready", cur["version"]
    reason = ""
    if jd and jd.exists():
        vs = versions.version_dirs(jd)
        if vs:
            try:
                m = versions.read_manifest(vs[-1])
                if m.get("status") == "failed" and (m.get("generated_at") or "") >= started:
                    reason = "; ".join((m.get("failure_reasons") or [])[:3])
            except (OSError, ValueError):
                pass
    if not reason:
        row = conn.execute("SELECT status, message FROM resume_status WHERE job_pk=?", (item["job_pk"],)).fetchone()
        if row and row["status"] == "failed" and row["message"]:
            reason = row["message"]
    if not reason and text:
        lines = [x.strip() for x in text.strip().splitlines() if x.strip()]
        reason = " ".join(lines[-3:])[:400] if lines else ""
    if not reason:
        reason = f"the build stopped without saving a version (exit code {rc_})"
    return False, reason[:500], None


def after_build(conn, pk: int) -> None:
    """Everything that should reflect a finished build: HireScope resume status + resumes.js, applications (external
    jobs → "Ready to apply"), sync.js, the applications Excel file. Never raises."""
    from . import hirescope_bridge
    steps = [("resume status", lambda: hirescope_bridge.update_job(pk, db_path=_db_path(conn)))]
    try:
        from jobsearch import hooks
        steps.append(("dashboard sync", lambda: hooks.after_change(conn, reason="queue build")))
    except ImportError:
        pass
    for what, fn in steps:
        try:
            retry_locked(fn)
        except Exception as e:  # noqa: BLE001
            log.warning("after build: %s failed", what, extra={"job": pk, "error": str(e)[:200]})


def notify(title: str, message: str) -> None:
    try:
        from jobsearch.ops.notify import notify as _n
        _n(title, message)
    except Exception:  # noqa: BLE001
        pass


class Worker:
    """Runs queued builds until the queue is empty (plus idle_exit_seconds), then exits 0."""

    def __init__(self, db_path: Path | None = None, poll: float = 2.0, once: bool = False):
        self.db_path, self.poll, self.once = db_path, poll, once
        self.s = settings()
        self.running: dict[int, dict] = {}     # item id → {proc, item, started, log, t0}
        self.paused: str | None = None

    def _conn(self):
        return connect(self.db_path)

    def _write_live(self, conn, state: str) -> None:
        db.set_meta(conn, "queue_worker", json.dumps({"pid": os.getpid(), "state": state, "paused": self.paused,
                                                       "heartbeat": now()}))
        conn.commit()
        try:
            from jobsearch import live
            live.write(conn)
        except Exception as e:  # noqa: BLE001
            log.warning("live.js not written", extra={"error": str(e)[:200]})

    def start(self, conn, item: dict) -> None:
        from . import hirescope_bridge
        _abandon_stale_work(conn, item)
        log_dir().mkdir(parents=True, exist_ok=True)
        lf = log_dir() / f"job{item['job_pk']}-item{item['id']}-attempt{item['attempts'] + 1}.log"
        env = {**os.environ, "HIRESCOPE_QUEUE": "1", "HIRESCOPE_QUEUE_ITEM": str(item["id"]),
               "PYTHONUNBUFFERED": "1"}
        with open(lf, "w", encoding="utf-8") as out:
            out.write(f"# {now()} queue item {item['id']} job {item['job_pk']} attempt {item['attempts'] + 1}\n")
            out.flush()
            try:
                proc = subprocess.Popen(command(item, self.s), cwd=str(rc.ROOT), stdin=subprocess.DEVNULL,
                                        stdout=out, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            except OSError as e:
                retry_locked(_exec, conn, "UPDATE resume_queue SET status='failed', attempts=attempts+1, "
                             "finished_at=?, message=? WHERE id=?", (now(), f"could not start the build: {e}",
                                                                      item["id"]))
                hirescope_bridge.set_status(item["job_pk"], "failed", f"could not start the build: {e}",
                                            db_path=_db_path(conn))
                return
        t = now()
        retry_locked(_exec, conn, "UPDATE resume_queue SET status='generating', started_at=?, pid=?, "
                     "attempts=attempts+1, message=? WHERE id=?", (t, proc.pid, "generating", item["id"]))
        try:
            hirescope_bridge.set_status(item["job_pk"], "generating", "building in the background queue",
                                        db_path=_db_path(conn))
        except Exception:  # noqa: BLE001
            pass
        self.running[item["id"]] = {"proc": proc, "item": {**item, "attempts": item["attempts"] + 1},
                                    "started": t, "log": lf, "t0": time.time()}
        log.info("build started", extra={"job": item["job_pk"], "item": item["id"], "pid": proc.pid})

    def finish(self, conn, iid: int, rc_: int | None, forced: str | None = None) -> None:
        from . import hirescope_bridge, questions
        r = self.running.pop(iid)
        item, pk = r["item"], r["item"]["job_pk"]
        row = conn.execute("SELECT status FROM resume_queue WHERE id=?", (iid,)).fetchone()
        if row and row["status"] == "cancelled":
            hirescope_bridge.clear_in_flight(pk, db_path=_db_path(conn))
            after_build(conn, pk)
            return
        ok, why, version = (False, forced, None) if forced else outcome(conn, item, r["started"], rc_, r["log"])
        j = _job(conn, pk)
        name = f"{j['company']} — {j['title']}" if j else f"job {pk}"
        if ok:
            pending = questions.open_count(conn, pk)
            result = "ready_questions" if pending else "ready"
            retry_locked(_exec, conn, "UPDATE resume_queue SET status='done', finished_at=?, pid=NULL, result=?, "
                         "message=? WHERE id=?", (now(), result, why + (f"; {pending} question(s) for you"
                                                                       if pending else ""), iid))
            hirescope_bridge.clear_in_flight(pk, db_path=_db_path(conn))     # the versions say "ready" now
            after_build(conn, pk)
            notify("Resume ready" + (" (questions pending)" if pending else ""), f"{name} — v{version}")
            return
        if item["attempts"] <= self.s["retries"]:
            retry_locked(_exec, conn, "UPDATE resume_queue SET status='queued', pid=NULL, message=? WHERE id=?",
                         (f"retrying (attempt {item['attempts'] + 1} of {self.s['retries'] + 1}): {why}"[:500], iid))
            log.info("build failed, retrying", extra={"job": pk, "item": iid, "reason": why[:200]})
            return
        retry_locked(_exec, conn, "UPDATE resume_queue SET status='failed', finished_at=?, pid=NULL, result='failed', "
                     "message=? WHERE id=?", (now(), why, iid))
        try:
            hirescope_bridge.set_status(pk, "failed", why, db_path=_db_path(conn))
        except Exception:  # noqa: BLE001
            pass
        after_build(conn, pk)
        notify("Resume failed", f"{name}: {why[:150]}")

    def tick(self, conn) -> None:
        timeout = float(self.s["timeout_minutes"]) * 60
        for iid, r in list(self.running.items()):
            row = conn.execute("SELECT status FROM resume_queue WHERE id=?", (iid,)).fetchone()
            if row and row["status"] == "cancelled" and r["proc"].poll() is None:
                stop_group(r["proc"].pid)
            rc_ = r["proc"].poll()
            if rc_ is None and time.time() - r["t0"] > timeout:
                stop_group(r["proc"].pid)
                r["proc"].wait()
                self.finish(conn, iid, None, forced=f"timed out after {self.s['timeout_minutes']} minutes")
                continue
            if rc_ is not None:
                self.finish(conn, iid, rc_)
        self.paused = run_lock_held()
        if self.paused:
            return
        busy = {r["item"]["job_pk"] for r in self.running.values()}
        free = self.s["concurrency"] - len(self.running)
        if free <= 0:
            return
        for it in conn.execute("SELECT * FROM resume_queue WHERE status='queued' ORDER BY id").fetchall():
            if free <= 0:
                break
            if it["job_pk"] in busy:
                continue
            self.start(conn, dict(it))
            busy.add(it["job_pk"])
            free -= 1

    def run(self) -> int:
        from jobsearch.ops.lock import Locked, RunLock
        lock = RunLock(hs_paths.STATE_DIR / "queue_worker.lock", stale_hours=24, what="resume queue worker")
        try:
            lock.acquire()
        except Locked as e:
            print(f"queue worker: {e}; nothing to do", file=sys.stderr)
            return 0
        conn = self._conn()
        idle_since = None
        try:
            n = recover(conn)
            if n:
                log.info("queue recovered after a restart", extra={"items": n})
            while True:
                retry_locked(self.tick, conn)
                queued = conn.execute("SELECT COUNT(*) FROM resume_queue WHERE status='queued'").fetchone()[0]
                state = "paused" if self.paused and queued else "running" if self.running or queued else "idle"
                retry_locked(self._write_live, conn, state)
                if not self.running and not queued:
                    if self.once:
                        break
                    idle_since = idle_since or time.time()
                    if time.time() - idle_since >= float(self.s["idle_exit_seconds"]):
                        break
                else:
                    idle_since = None
                time.sleep(self.poll)
        finally:
            for iid in list(self.running):        # stopped (SIGTERM / Ctrl-C): the next worker resumes these
                stop_group(self.running[iid]["proc"].pid)
            try:
                retry_locked(self._write_live, conn, "stopped")
            except Exception:  # noqa: BLE001
                pass
            conn.close()
            lock.release()
        return 0


def kick() -> bool:
    """Start the launchd worker now (it is also started by its WatchPaths trigger). HIRESCOPE_NO_KICK turns this off
    (tests)."""
    trig = hs_paths.STATE_DIR / "queue.trigger"
    try:
        trig.parent.mkdir(parents=True, exist_ok=True)
        trig.write_text(now(), encoding="utf-8")
    except OSError:
        pass
    if os.environ.get("HIRESCOPE_NO_KICK"):
        return False
    try:
        r = subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/{WORKER_LABEL}"], capture_output=True,
                           text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _sigterm(signum, frame):            # launchd stop / kickstart -k: unwind through Worker.run's finally
    raise SystemExit(0)


def worker_main(once: bool = False) -> int:
    signal.signal(signal.SIGTERM, _sigterm)
    from jobsearch.run import setup_logging
    try:
        setup_logging(False)
    except Exception:  # noqa: BLE001
        logging.basicConfig(level=logging.INFO)
    return Worker(once=once).run()

