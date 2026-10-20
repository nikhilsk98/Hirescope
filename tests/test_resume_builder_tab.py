"""Resume Builder tab: a saved external job → imported (dedupe) and its resume queued in one link; the Terminal
fallback; "Ready to apply" applications and the auto_mark_applied_external setting. All data is DEMO."""
import base64
import json
import os
import subprocess

import pytest

from jobsearch import external_resume as ER
from jobsearch import launcher, paths
from jobsearch.adapters.base import RawJob
from jobsearch.storage import db
from jobsearch.tracker import applications as A
from jobsearch.tracker import ready_apps, statuses
from resume_builder import queue

from .test_external import JD, _docx


@pytest.fixture
def env(cfg, monkeypatch):
    conn = db.connect(paths.DB)
    downloads = ER.downloads_dir()
    downloads.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("jobsearch.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(queue, "publish_dashboard", lambda: "published")
    yield conn, downloads
    conn.close()


def external_link(conn, name: str) -> str:
    """hirescope://external as it runs for real: the launcher only names the file; the worker imports it."""
    first = launcher.handle(f"hirescope://external?file={name}")
    assert "imports it in the background" in first
    out = ER.process_requests(conn)
    assert out, "the worker found no request"
    return out[-1]["message"]


def saved(downloads, name="hirescope-external-job-DemoCorp-20261017T120000-ab12.json", **fields):
    payload = {"kind": "hirescope_external_job", "format": 1, "origin": "resume_builder", "created_at": "2026-10-17T12:00:00Z",
               "company": "DemoCorp", "role": "Software Engineer Co-op (DEMO)", "location": "Boston, MA", "url": "",
               "track": "coop_intern", "jd_text": JD, "force": False, **fields}
    p = downloads / name
    p.write_text(json.dumps(payload))
    return p


def test_allowed_paths_only_downloads_and_the_drop_folder(env, tmp_path):
    conn, downloads = env
    ok = saved(downloads)
    assert ER.allowed_path(ok.name) == ok.resolve()
    assert ER.allowed_path(str(ok)) == ok.resolve()
    paths.EXTERNAL_INBOX.mkdir(parents=True, exist_ok=True)
    (paths.EXTERNAL_INBOX / "Acme - Engineer.md").write_text(JD)
    assert ER.allowed_path("Acme - Engineer.md").parent == paths.EXTERNAL_INBOX.resolve()
    outside = tmp_path / "elsewhere" / "x.json"
    outside.parent.mkdir()
    outside.write_text("{}")
    link = downloads / "link.json"
    os.symlink(outside, link)
    for bad, why in ((str(outside), "only from"), ("../elsewhere/x.json", "absolute path"), (str(link), "only from"),
                     ("notes.exe", "only .json"), ("a;rm -rf.json", "unexpected characters"), ("", "no file"),
                     (str(downloads / "sub" / "x.json"), "only from")):
        with pytest.raises(ER.FileRefused, match=why):
            ER.allowed_path(bad)


def test_external_link_imports_and_queues_in_one_click(env):
    conn, downloads = env
    p = saved(downloads)
    msg = external_link(conn, p.name)
    assert "imported DemoCorp — Software Engineer Co-op (DEMO)" in msg and "resume queued" in msg
    pk = conn.execute("SELECT id FROM jobs WHERE source='external'").fetchone()[0]
    it = queue.active_item(conn, pk)
    assert it and it["origin"] == "resume_builder"
    ex = json.loads(conn.execute("SELECT extra FROM jobs WHERE id=?", (pk,)).fetchone()[0])
    assert ex["external"]["origin"] == "resume_builder"
    assert db.get_meta(conn, "publish_pending")                       # the worker republishes before building
    assert not p.exists()                                             # archived (data.nosync/imports/external/)
    assert list((paths.IMPORTS_DIR / "external").glob(f"*{p.name}"))
    # the same job again (a new file): dedupe → the existing job's ID, no second job, no second build
    p2 = saved(downloads, name="hirescope-external-job-DemoCorp-20261017T130000-cd34.json")
    msg = external_link(conn, p2.name)
    assert "linked to existing job" in msg and f"job {pk}" in msg
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE source='external'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM resume_queue WHERE job_pk=? AND status='queued'", (pk,)).fetchone()[0] == 1


def test_uploaded_file_and_missing_file_falls_back_to_newest_pending(env, cfg):
    conn, downloads = env
    p = saved(downloads, name="hirescope-external-job-DemoAir-20261017T120000-zz99.json", company="DemoAir",
              role="Platform Engineer (DEMO)", jd_text="",
              file={"name": "jd.docx", "type": "", "b64": base64.b64encode(_docx(JD.replace("DemoCorp", "DemoAir").splitlines())).decode()})
    out = ER.import_and_queue(conn, cfg, "hirescope-external-job-renamed (1).json", wait=0.1)
    assert out["note"].startswith("hirescope-external-job-renamed (1).json not found; used the newest pending external job")
    assert p.name in out["note"] and out["imported"] == "new"
    with pytest.raises(ER.FileRefused, match="no external job is waiting"):
        ER.import_and_queue(conn, cfg, "hirescope-external-job-gone.json", wait=0.1)


def test_url_only_uses_add_job_by_url(env, monkeypatch):
    conn, downloads = env
    calls = []

    def fake_resolve(url, client, company):
        calls.append(url)
        return RawJob(company="DemoUrl", job_id="u1", title="Backend Co-op (DEMO)", source="careers_page",
                      source_url=url, apply_url=url, description=JD.replace("DemoCorp", "DemoUrl")), "JSON-LD on the page"
    monkeypatch.setattr("jobsearch.add_url.resolve", fake_resolve)
    p = saved(downloads, name="hirescope-external-job-demo-url-20261017T120000-q1.json", company="", role="",
              jd_text="", url="https://jobs.demo.example/postings/42")
    msg = external_link(conn, p.name)
    assert calls == ["https://jobs.demo.example/postings/42"] and "imported DemoUrl" in msg and "resume queued" in msg
    pk = conn.execute("SELECT id FROM jobs WHERE source='added_url'").fetchone()[0]
    assert queue.active_item(conn, pk)


def test_external_resume_script_terminal_fallback(env, capsys):
    conn, downloads = env
    p = saved(downloads)
    assert ER.main([p.name]) == 0
    assert "imported DemoCorp" in capsys.readouterr().out
    assert ER.main(["hirescope-external-job-nothing.json"]) == 2
    assert "no external job is waiting" in capsys.readouterr().err
    assert (paths.ROOT / "scripts" / "external_resume.sh").stat().st_mode & 0o111
    out = subprocess.run(["zsh", "-n", str(paths.ROOT / "scripts" / "external_resume.sh")], capture_output=True)
    assert out.returncode == 0


def test_bad_external_links_rejected():
    for bad in ("hirescope://external", "hirescope://external?file=/etc/passwd", "hirescope://external?file=../../x.json",
                "hirescope://external?file=x.sh", "hirescope://external?file=a.json&job=1"):
        with pytest.raises(launcher.LinkError):
            launcher.parse(bad)


# --- Ready to apply ---------------------------------------------------------------------------------------------------
def _external_ready(conn, downloads, version=1):
    external_link(conn, saved(downloads).name)
    pk = conn.execute("SELECT id FROM jobs WHERE source='external'").fetchone()[0]
    queue.cancel(conn, pk)
    conn.execute("INSERT INTO resume_builds (job_pk, version, status, pdf_path, manifest_path, created_at, summary) "
                 "VALUES (?,?,?,?,?,?,?)", (pk, version, "ready", f"/x/v{version}/R.pdf", f"/x/v{version}/manifest.json",
                                           "2026-10-17T00:00:00+00:00", "{}"))
    conn.execute("INSERT OR REPLACE INTO resume_status (job_pk, status, current_version, latest_version, updated_at) "
                 "VALUES (?,?,?,?,?)", (pk, "ready", version, version, "2026-10-17T00:00:00+00:00"))
    conn.commit()
    return pk


def test_ready_resume_lands_in_my_applications_as_ready_to_apply(env):
    conn, downloads = env
    pk = _external_ready(conn, downloads)
    assert "Ready to apply: DemoCorp" in ready_apps.ensure(conn, auto_applied=False)
    a = conn.execute("SELECT * FROM applications WHERE job_pk=?", (pk,)).fetchone()
    assert a["stage"] == "ready" and a["applied_date"] is None and a["source"] == "External"
    assert json.loads(a["resume"])["version"] == 1
    assert ready_apps.ensure(conn, auto_applied=False) == ""                   # once
    # Mark Applied → Applied, dated today, with the exact version; Undo → back to Ready to apply
    launcher.handle(f"hirescope://status?job={pk}&stage=applied&app={a['id']}&date=2026-10-18&v=1")
    a2 = conn.execute("SELECT * FROM applications WHERE id=?", (a["id"],)).fetchone()
    assert a2["stage"] == "applied" and a2["applied_date"] == "2026-10-18" and json.loads(a2["resume"])["linked"] == "applied"
    launcher.handle(f"hirescope://status?job={pk}&stage=undo&app={a['id']}")
    a3 = conn.execute("SELECT * FROM applications WHERE id=?", (a["id"],)).fetchone()
    assert a3["stage"] == "ready" and a3["applied_date"] is None and not a3["deleted"]
    # a removed Ready-to-apply application is never re-created
    conn.execute("UPDATE applications SET deleted=1 WHERE id=?", (a["id"],))
    conn.commit()
    assert ready_apps.ensure(conn, auto_applied=False) == ""


def test_auto_mark_applied_external_setting(env, cfg):
    conn, downloads = env
    assert cfg.prefs.applications.auto_mark_applied_external is False        # the default
    pk = _external_ready(conn, downloads, version=2)
    assert ready_apps.ensure(conn, auto_applied=True).startswith("Applied:")
    a = conn.execute("SELECT * FROM applications WHERE job_pk=?", (pk,)).fetchone()
    assert a["stage"] == "applied" and a["applied_date"] and json.loads(a["resume"])["version"] == 2


def test_ready_stage_is_not_an_application_in_stats():
    apps = [{"id": "r", "company": "DemoCorp", "title": "X", "stage": "ready", "applied_date": None, "events": []},
            {"id": "a", "company": "DemoBank", "title": "Y", "stage": "applied", "applied_date": "2026-10-10", "events": []}]
    from datetime import date
    assert A.track_stats(apps, date(2026, 10, 17))["total"] == 1
    assert [r["company"] for r in A.applied_companies(apps)] == ["DemoBank"]
    assert A.STAGES[0] == "ready" and A.STAGE_LABELS["ready"] == "Ready to apply"
    core = json.dumps(str(paths.ROOT / "dashboard" / "core.js"))
    js = f"const C = require({core}); console.log(JSON.stringify([C.trackStats({json.dumps(apps)}, '2026-10-17').total, C.appliedCompanies({json.dumps(apps)}).length, C.STAGES[0]]))"
    assert json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout) == [1, 1, "ready"]
    with pytest.raises(ValueError):
        statuses.move_stage(db.connect(":memory:"), "nope", "ready")


def test_worker_publishes_new_external_jobs_before_building(env, monkeypatch):
    conn, downloads = env
    calls = []
    monkeypatch.setattr(queue, "publish_dashboard", lambda: calls.append("publish") or db.set_meta(conn, "publish_pending", "") or conn.commit() or "published")
    external_link(conn, saved(downloads).name)
    started = []
    monkeypatch.setattr(queue.Worker, "start", lambda self, c, item: started.append(item["job_pk"]))
    queue.Worker(poll=0.01).tick(conn)
    assert calls == ["publish"] and started


def test_launcher_never_opens_the_downloads_folder(env):
    """macOS asks before an app may read ~/Downloads, and the launcher app can't show that question (it would hang,
    found in the end-to-end run): the launcher checks the file by name only; the worker reads it."""
    conn, downloads = env
    p = saved(downloads)
    os.chmod(downloads, 0)                                    # unreadable to everyone, like an unanswered prompt
    try:
        msg = launcher.handle(f"hirescope://external?file={p.name}")
        assert "imports it in the background" in msg and len(ER.pending_requests()) == 1
    finally:
        os.chmod(downloads, 0o755)
    assert "imported DemoCorp" in ER.process_requests(conn)[0]["message"] and not ER.pending_requests()
    assert json.loads(db.get_meta(conn, "launcher_last"))["action"] == "external"



def _data_link(payload: dict) -> str:
    import base64
    return "hirescope://external?data=" + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def test_pasted_jd_inside_the_link_needs_no_file(env):
    """A pasted JD travels inside the link (macOS delivers 100 KB links): nothing in ~/Downloads, no folder permission."""
    conn, downloads = env
    payload = {"kind": "hirescope_external_job", "format": 1, "origin": "resume_builder", "company": "DemoInline",
               "role": "Platform Engineer Co-op (DEMO)", "location": "Remote", "url": "", "track": "coop_intern",
               "jd_text": JD.replace("DemoCorp", "DemoInline") * 20, "force": False}
    link = _data_link(payload)
    assert 8000 < len(link) < launcher.MAX_DATA_URL
    assert "imports it in the background" in launcher.handle(link)
    out = ER.process_requests(conn)
    assert "imported DemoInline" in out[0]["message"] and not list(downloads.iterdir())
    pk = conn.execute("SELECT id FROM jobs WHERE company='DemoInline'").fetchone()[0]
    assert queue.active_item(conn, pk)


def test_bad_data_links_rejected():
    for bad, why in ((_data_link({"kind": "other"}), "not a HireScope"), ("hirescope://external?data=%%%%", "base64url"),
                     (_data_link({"kind": "hirescope_external_job", "company": "X", "jd_text": ""}), "neither"),
                     (_data_link({"kind": "hirescope_external_job", "jd_text": "x", "file": {"name": "a.pdf"}}), "saved file"),
                     ("hirescope://external?data=" + "A" * 130000, "too long"),
                     (_data_link({"kind": "hirescope_external_job", "jd_text": "a JD"}) + "&file=a.json", "file=")):
        with pytest.raises(launcher.LinkError, match=why):
            launcher.parse(bad)
