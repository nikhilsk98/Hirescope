"""Resume Builder Phase 4: HireScope bridge (statuses, versions, dashboard data), applications, reviews, batch.
All data is DEMO."""
import json

import pytest

from jobsearch import paths as hs_paths
from jobsearch.storage import db
from jobsearch.tracker import statuses as hs_statuses
from resume_builder import __main__ as cli
from resume_builder import batch, hirescope_bridge as bridge, pipeline, versions

from .conftest import needs_tex, reviewed_flow

pytestmark = needs_tex()


def _resumes_js(env) -> dict:
    t = (env.root / "dashboard" / "resumes.js").read_text()
    return json.loads(t.split("window.RESUMES = ", 1)[1].rstrip().rstrip(";"))


def _cli_generation(env, job=None, edit=None, regenerate=False):
    job = job or env.job
    args = ["prepare", str(job)] + (["--regenerate"] if regenerate else [])
    assert cli.main(args) == 0
    conn = db.connect(env.db_path)
    j = dict(conn.execute("SELECT company FROM jobs WHERE id=?", (job,)).fetchone())
    status = conn.execute("SELECT status FROM resume_status WHERE job_pk=?", (job,)).fetchone()[0]
    conn.close()
    jd = pipeline.job_dir(j["company"], job)
    if (jd / "work" / "state.json").exists() and json.loads((jd / "work" / "state.json").read_text())["status"] == "generating":
        reviewed_flow(env, jd / "work", edit=edit, job=job)
    return jd, status, cli.main(["build", str(job)])


def test_statuses_flow_and_dashboard_data(rb_env, capsys):
    jd, status_after_prepare, code = _cli_generation(rb_env)
    assert status_after_prepare == "generating" and code == 0
    out = capsys.readouterr().out
    assert "READY: v1" in out and "HireScope updated" in out
    conn = db.connect(rb_env.db_path)
    s = dict(conn.execute("SELECT * FROM resume_status WHERE job_pk=?", (rb_env.job,)).fetchone())
    b = dict(conn.execute("SELECT * FROM resume_builds WHERE job_pk=?", (rb_env.job,)).fetchone())
    conn.close()
    assert s["status"] == "ready" and s["current_version"] == 1 and b["status"] == "ready"
    data = _resumes_js(rb_env)["jobs"][str(rb_env.job)]
    assert data["command"] == f'claude "/resume-builder {rb_env.job}"' and data["key"] == "DemoCorp::greenhouse::demo-1"
    cur = data["current"]
    assert not cur["pdf"].startswith("/") and cur["pdf"].endswith("Demo_Candidate_Resume_DemoCorp.pdf")
    for k in ("pdf", "tex", "preview", "report"):
        assert (rb_env.root / "dashboard" / cur[k]).resolve().exists(), k          # relative file:// links resolve
    assert cur["truth_unsupported"] == 0 and cur["pages"] == 1 and cur["coverage_pct"] is not None


def test_failed_build_keeps_last_good_version_and_says_so(rb_env, capsys):
    _cli_generation(rb_env)

    def invent(c):
        c.experience[0].bullets[0].text = "Led a 9-person team building Java REST APIs"
    jd, _s, code = _cli_generation(rb_env, edit=invent, regenerate=True)
    assert code == 1
    out = capsys.readouterr().out
    assert "FAILED: v2" in out and "current version stays v1" in out
    data = _resumes_js(rb_env)["jobs"][str(rb_env.job)]
    assert data["status"] == "failed" and data["current"]["version"] == 1 and data["latest"]["version"] == 2
    assert "v1 is still current" in data["message"] and data["latest"]["reasons"]


def test_compile_failure_keeps_last_good_version(rb_env, capsys, monkeypatch):
    _cli_generation(rb_env)
    from resume_builder import compile as comp
    real = comp.compile_tex

    def broken(build_dir, main="main.tex", timeout_s=120):
        (build_dir / "content.tex").write_text("\\undefinedcommandxyz\n")
        return real(build_dir, main, timeout_s)
    monkeypatch.setattr(comp, "compile_tex", broken)
    _jd, _s, code = _cli_generation(rb_env, regenerate=True)
    assert code == 1
    out = capsys.readouterr().out
    assert "compile_errors" in out and "current version stays v1" in out
    assert versions.current(pipeline.job_dir("DemoCorp", rb_env.job))["version"] == 1


def test_hirescope_update_failure_is_reported_and_resume_kept(rb_env, capsys, monkeypatch):
    pipeline.prepare(str(rb_env.job))
    jd = pipeline.job_dir("DemoCorp", rb_env.job)
    reviewed_flow(rb_env, jd / "work")

    def boom(*a, **k):
        raise OSError("database is locked (DEMO)")
    with pytest.MonkeyPatch.context() as mp:      # only this patch; the DEMO sandbox paths stay patched
        mp.setattr(bridge, "sync_job", boom)
        code = cli.main(["build", str(rb_env.job)])
    err = capsys.readouterr().err
    assert code == 1 and "the resume is saved, but updating HireScope failed" in err and "refresh" in err
    assert versions.current(jd)["version"] == 1 and (jd / "v1" / "Demo_Candidate_Resume_DemoCorp.pdf").exists()
    assert hs_paths.DB == rb_env.db_path
    assert cli.main(["refresh"]) == 0
    assert _resumes_js(rb_env)["jobs"][str(rb_env.job)]["status"] == "ready"


def test_review_from_dashboard_export_and_application_resume(rb_env):
    _cli_generation(rb_env)
    key = "DemoCorp::greenhouse::demo-1"
    export = {"exported_at": "2026-10-13T12:00:00Z", "format": 5, "statuses": {},
              "resume_reviews": {str(rb_env.job): {"version": 1, "reviewed_at": "2026-10-13T11:00:00Z"},
                                 "999999": {"version": 1}},
              "applications": {"app_demo_1": {"id": "app_demo_1", "job_key": key, "company": "DemoCorp",
                                              "title": "Backend Engineer Intern", "applied_date": "2026-10-13",
                                              "stage": "applied", "updated_at": "2026-10-13T11:30:00Z",
                                              "job": {"source": "greenhouse", "resume": {
                                                  "job_pk": rb_env.job, "version": 1, "pdf_name": "x.pdf",
                                                  "pdf_sha": "abc", "pdf": "../data/resumes/x.pdf"}}}}}
    conn = db.connect(rb_env.db_path)
    res = hs_statuses.merge(conn, export, None)
    assert res.resume_reviews_applied == 1 and any("999999" in e for e in res.errors)
    jd = pipeline.job_dir("DemoCorp", rb_env.job)
    assert versions.read_manifest(jd / "v1")["status"] == "reviewed"
    r = json.loads(conn.execute("SELECT resume FROM applications WHERE id='app_demo_1'").fetchone()[0])
    assert r["version"] == 1 and r["linked"] == "applied"
    payload = hs_statuses.sync_payload(conn)
    assert payload["applications"][0]["job"]["resume"]["version"] == 1          # back to the dashboard
    hs_statuses.merge(conn, {**export, "exported_at": "2026-10-14T12:00:00Z", "resume_reviews": {},
                             "applications": {"app_demo_1": {**export["applications"]["app_demo_1"],
                                                             "updated_at": "2026-10-14T00:00:00Z", "job": {}}}}, None)
    assert json.loads(conn.execute("SELECT resume FROM applications WHERE id='app_demo_1'").fetchone()[0])["version"] == 1
    conn.close()


def test_applications_without_a_recorded_resume_are_linked_by_date(rb_env):
    _cli_generation(rb_env)
    _cli_generation(rb_env, regenerate=True)
    conn = db.connect(rb_env.db_path)
    v1_at = conn.execute("SELECT created_at FROM resume_builds WHERE job_pk=? AND version=1", (rb_env.job,)).fetchone()[0]
    conn.execute("UPDATE resume_builds SET created_at='2026-01-01T00:00:00+00:00' WHERE job_pk=? AND version=1",
                 (rb_env.job,))
    for app_id, day in (("a_old", "2026-02-01"), ("a_before", "2025-12-01")):
        conn.execute("INSERT INTO applications (id, job_pk, company, company_key, title, applied_date, created_at, "
                     "updated_at) VALUES (?,?,?,?,?,?,?,?)", (app_id, rb_env.job, "DemoCorp", "democorp", "DEMO", day,
                                                               "x", "x"))
    conn.commit()
    assert bridge.link_applications(conn) == 1
    got = json.loads(conn.execute("SELECT resume FROM applications WHERE id='a_old'").fetchone()[0])
    assert got["version"] == 1 and got["linked"] == "auto"                       # the version current back then
    assert conn.execute("SELECT resume FROM applications WHERE id='a_before'").fetchone()[0] is None
    assert v1_at
    conn.close()


def test_batch_shortlisted_needs_confirmation_and_queues(rb_env, capsys):
    assert cli.main(["batch", "shortlisted"]) == 0
    assert "Nothing to do" in capsys.readouterr().out
    conn = db.connect(rb_env.db_path)
    conn.execute("UPDATE jobs SET user_status='shortlisted', user_status_at='2026-10-13T00:00:00+00:00'")
    conn.commit()
    conn.close()
    assert cli.main(["batch", "shortlisted"]) == batch.EXIT_NEEDS_CONFIRMATION
    out = capsys.readouterr().out
    assert "2 shortlisted job(s)" in out and "⚠ Usage" in out and "Not started" in out
    conn = db.connect(rb_env.db_path)
    assert conn.execute("SELECT COUNT(*) FROM resume_status").fetchone()[0] == 0     # nothing queued yet
    conn.close()
    assert cli.main(["batch", "shortlisted", "--yes", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert "Queued 1 job(s)" in out and "over the batch cap of 1" in out
    data = _resumes_js(rb_env)["jobs"]
    assert [v["status"] for v in data.values()] == ["queued"]
    q = json.loads((rb_env.data / "resumes" / "batch_queue.json").read_text())
    assert len(q["jobs"]) == 1


def test_batch_reused_job_leaves_queued(rb_env):
    _cli_generation(rb_env)
    conn = db.connect(rb_env.db_path)
    conn.execute("UPDATE jobs SET user_status='shortlisted' WHERE id=?", (rb_env.job,))
    conn.commit()
    conn.close()
    assert cli.main(["batch", "shortlisted", "--yes"]) == 0
    assert _resumes_js(rb_env)["jobs"][str(rb_env.job)]["status"] == "queued"
    assert cli.main(["prepare", str(rb_env.job)]) == 0                    # REUSED
    assert _resumes_js(rb_env)["jobs"][str(rb_env.job)]["status"] == "ready"


def test_set_status_validates_and_hirescope_run_refreshes(rb_env):
    with pytest.raises(ValueError):
        bridge.set_status(rb_env.job, "approved-by-ai")
    _cli_generation(rb_env)
    (rb_env.root / "dashboard" / "resumes.js").unlink()
    conn = db.connect(rb_env.db_path)
    conn.execute("DELETE FROM resume_status")          # e.g. a database restored from before the Resume Builder
    conn.commit()
    conn.close()
    assert bridge.refresh_all(rb_env.db_path) == 1
    assert _resumes_js(rb_env)["jobs"][str(rb_env.job)]["status"] == "ready"


def test_application_resume_reference_survives_marking_reviewed(rb_env):
    """The exact-file reference (PDF hash) stays valid after the version is marked reviewed (manifest rewritten)."""
    _cli_generation(rb_env)
    from resume_builder.config import sha256_file
    jd = pipeline.job_dir("DemoCorp", rb_env.job)
    pdf = jd / "v1" / "Demo_Candidate_Resume_DemoCorp.pdf"
    data = _resumes_js(rb_env)["jobs"][str(rb_env.job)]
    assert data["current"]["pdf_sha"] == sha256_file(pdf)
    assert cli.main(["mark-reviewed", str(rb_env.job)]) == 0
    conn = db.connect(rb_env.db_path)
    ref = bridge.resume_ref(conn, rb_env.job)
    conn.close()
    assert ref["pdf_sha"] == sha256_file(pdf) and _resumes_js(rb_env)["jobs"][str(rb_env.job)]["current"]["pdf_sha"] == ref["pdf_sha"]
