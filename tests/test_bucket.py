"""The Bucket (apply-list): add / remove, persistence through SQLite, export format 6, launcher links, Mark Applied
removes the job (with the exact resume version) and Undo restores it."""
import json

import pytest

from jobsearch import bucket, launcher
from jobsearch import paths
from jobsearch.storage import db
from jobsearch.tracker import statuses

from .resume_builder.conftest import insert_job


class Conn:
    """The sandbox database connection plus the two DEMO job ids (a: co-op, b: full-time)."""
    def __init__(self, c, a, b):
        self._c, self.a, self.b = c, a, b

    def __getattr__(self, k):
        return getattr(self._c, k)

    def __enter__(self):
        return self._c.__enter__()

    def __exit__(self, *exc):
        return self._c.__exit__(*exc)


@pytest.fixture
def bconn():
    c = db.connect(paths.DB)
    a = insert_job(c, paths.DATA / "jd", company="DemoCorp", title="Backend Engineer Intern", job_id="b-1")
    b = insert_job(c, paths.DATA / "jd", company="Full Time Co", title="Software Engineer I", track="full_time",
                   job_id="b-2")
    yield Conn(c, a, b)
    c.close()


def sync_js() -> dict:
    t = (paths.DASHBOARD / "sync.js").read_text()
    return json.loads(t.split("window.SYNC = ", 1)[1].rstrip().rstrip(";"))


def test_add_remove_snapshot_and_tombstone(bconn):
    assert bucket.set_in(bconn, bconn.a, True) and not bucket.set_in(bconn, bconn.a, True)
    assert bucket.jobs_in(bconn) == [bconn.a]
    e = bucket.entries(bconn)[str(bconn.a)]
    assert e["in"] and e["snap"]["title"] == "Backend Engineer Intern" and e["snap"]["track"] == "coop_intern"
    assert e["snap"]["key"] == "DemoCorp::greenhouse::b-1" and e["snap"]["apply_url"]
    bucket.set_in(bconn, bconn.a, False)
    assert bucket.jobs_in(bconn) == [] and bucket.entries(bconn)[str(bconn.a)]["in"] is False   # tombstone kept
    with pytest.raises(ValueError):
        bucket.set_in(bconn, 999999, True)


def test_closed_job_stays_with_its_status(bconn):
    bucket.set_in(bconn, bconn.a, True)
    bconn.execute("UPDATE jobs SET job_status='closed' WHERE id=?", (bconn.a,))
    assert bucket.entries(bconn)[str(bconn.a)]["snap"]["job_status"] == "closed"
    assert bucket.jobs_in(bconn) == [bconn.a]


def test_export_format_6_merge_newer_wins(bconn):
    payload = {"format": 6, "exported_at": "2026-10-17T12:00:00Z", "statuses": {},
               "bucket": {str(bconn.a): {"in": True, "added_at": "2026-10-17T10:00:00Z", "updated_at": "2026-10-17T10:00:00Z"},
                          str(bconn.b): {"in": True, "updated_at": "2026-10-17T10:00:00Z"},
                          "999999": {"in": True, "updated_at": "2026-10-17T10:00:00Z"}, "x;y": {"in": True}}}
    res = statuses.merge(bconn, payload, None)
    assert res.bucket_applied == 2 and any("malformed" in e for e in res.errors)
    # SQLite has a newer removal: an older export can't bring the job back
    bucket.set_in(bconn, bconn.a, False, "2026-10-17T11:00:00+00:00")
    assert statuses.merge(bconn, payload, None).bucket_applied == 0 and not bucket.in_bucket(bconn, bconn.a)
    assert str(bconn.b) in statuses.sync_payload(bconn)["bucket"]


def test_launcher_bucket_links_sync_instantly(bconn):
    assert "added 2" in launcher.handle(f"hirescope://bucket?job={bconn.a},{bconn.b}&op=add")
    s = sync_js()
    assert s["bucket"][str(bconn.a)]["in"] and s["bucket"][str(bconn.b)]["in"]
    assert "removed 1" in launcher.handle(f"hirescope://bucket?job={bconn.b}&op=remove")
    assert sync_js()["bucket"][str(bconn.b)]["in"] is False
    for bad in ("hirescope://bucket?job=1&op=toggle", "hirescope://bucket?job=1", "hirescope://bucket?op=add",
                "hirescope://bucket?job=abc&op=add"):
        with pytest.raises(launcher.LinkError):
            launcher.parse(bad)
    with pytest.raises(launcher.LinkError, match="no HireScope job"):
        launcher.handle("hirescope://bucket?job=999999&op=add")


def _ready_resume(conn, pk, version=2):
    conn.execute("INSERT INTO resume_builds (job_pk, version, status, pdf_path, manifest_path, created_at, summary) "
                 "VALUES (?,?,?,?,?,?,?)", (pk, version, "ready", f"/x/v{version}/Demo_Resume.pdf", f"/x/v{version}/manifest.json",
                                           "2026-10-17T00:00:00+00:00", json.dumps({"pdf_sha": "abc"})))
    conn.execute("INSERT INTO resume_status (job_pk, status, current_version, latest_version, updated_at) VALUES "
                 "(?,?,?,?,?)", (pk, "ready", version, version, "2026-10-17T00:00:00+00:00"))
    conn.commit()


def test_mark_applied_removes_from_bucket_records_version_and_undo(bconn):
    _ready_resume(bconn, bconn.a, 2)
    launcher.handle(f"hirescope://bucket?job={bconn.a}&op=add")
    msg = launcher.handle(f"hirescope://status?job={bconn.a}&stage=applied&app=aDash123&date=2026-10-17&v=2")
    assert "aDash123" in msg
    a = bconn.execute("SELECT * FROM applications WHERE id='aDash123'").fetchone()
    assert a and a["stage"] == "applied" and a["applied_date"] == "2026-10-17" and a["job_pk"] == bconn.a
    assert json.loads(a["resume"])["version"] == 2 and json.loads(a["resume"])["linked"] == "applied"
    assert not bucket.in_bucket(bconn, bconn.a)
    assert bconn.execute("SELECT user_status FROM jobs WHERE id=?", (bconn.a,)).fetchone()[0] == "applied"
    s = sync_js()
    assert any(x["id"] == "aDash123" and not x["deleted"] for x in s["applications"]) and not s["bucket"][str(bconn.a)]["in"]
    # Undo: application gone, status and Bucket back
    launcher.handle(f"hirescope://status?job={bconn.a}&stage=undo&app=aDash123")
    assert bconn.execute("SELECT deleted FROM applications WHERE id='aDash123'").fetchone()[0] == 1
    assert bucket.in_bucket(bconn, bconn.a)
    assert bconn.execute("SELECT user_status FROM jobs WHERE id=?", (bconn.a,)).fetchone()[0] in (None, "")
    with pytest.raises(launcher.LinkError, match="nothing to undo"):
        launcher.handle(f"hirescope://status?job={bconn.a}&stage=undo&app=aDash123")
    # applying again reuses the same record
    launcher.handle(f"hirescope://status?job={bconn.a}&stage=applied&app=aDash123")
    assert bconn.execute("SELECT deleted, stage FROM applications WHERE id='aDash123'").fetchone()[:] == (0, "applied")


def test_stage_changes_and_bad_status_links(bconn):
    launcher.handle(f"hirescope://status?job={bconn.b}&stage=applied&app=aB")
    launcher.handle("hirescope://status?app=aB&stage=interviewing")
    a = bconn.execute("SELECT stage FROM applications WHERE id='aB'").fetchone()
    assert a["stage"] == "interviewing"
    assert bconn.execute("SELECT user_status FROM jobs WHERE id=?", (bconn.b,)).fetchone()[0] == "interviewing"
    ev = [r["to_stage"] for r in bconn.execute("SELECT to_stage FROM application_events WHERE app_id='aB' ORDER BY at, id")]
    assert ev[-1] == "interviewing"
    launcher.handle(f"hirescope://status?job={bconn.b}&stage=offer")              # by job: its application
    assert bconn.execute("SELECT stage FROM applications WHERE id='aB'").fetchone()[0] == "offer"
    for bad, why in (("hirescope://status?job=1&stage=hired", "unknown value"), ("hirescope://status?stage=offer", "needs job"),
                     ("hirescope://status?app=a/b&stage=offer", "application id"),
                     ("hirescope://status?job=1&stage=applied&date=2026-13-40", "YYYY-MM-DD"),
                     ("hirescope://status?job=1&stage=offer&date=2026-10-17", "stage=applied only"),
                     ("hirescope://status?job=1&stage=undo", "needs job and app"),
                     ("hirescope://status?job=1&stage=applied&v=x", "version")):
        with pytest.raises(launcher.LinkError, match=why):
            launcher.parse(bad)
    with pytest.raises(launcher.LinkError, match="Export statuses"):
        launcher.handle("hirescope://status?app=nope&stage=offer")


def test_load_export_accepts_format_6(tmp_path):
    f = tmp_path / "jobsearch-statuses.json"
    f.write_text(json.dumps({"format": 6, "statuses": {}, "bucket": {}}))
    assert statuses.load_export(f)["format"] == 6
    f.write_text(json.dumps({"format": 7, "statuses": {}}))
    with pytest.raises(ValueError, match="unknown export format"):
        statuses.load_export(f)
