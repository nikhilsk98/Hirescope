"""Resume Builder submissions that can't stay "Importing…": every request gets an answer (imported / linked to the job
HireScope already has / failed with the reason) in live.js; a URL that matches a known posting links to it and queues
its resume; an unreadable page (blocked, JavaScript-only, gone) fails with "Paste JD instead". All data is DEMO."""
import base64
import json

import httpx
import pytest

from jobsearch import external as X
from jobsearch import external_resume as ER
from jobsearch import launcher, live, paths
from jobsearch.adapters.base import RawJob
from jobsearch.add_url import UnreadableURL, resolve
from jobsearch.pipeline import to_record
from jobsearch.storage import db
from resume_builder import queue

from .conftest import mock_client
from .test_external import JD


@pytest.fixture
def env(cfg, monkeypatch):
    conn = db.connect(paths.DB)
    monkeypatch.setattr("jobsearch.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(queue, "publish_dashboard", lambda: "published")
    yield conn
    conn.close()


def regular_job(conn, cfg, company, title, source, job_id, url, ck=None):
    """A job found by one of HireScope's own sources."""
    raw = RawJob(company=company, job_id=job_id, title=title, source=source, source_url=url, apply_url=url,
                 description=JD.replace("DemoCorp", company), canonical_key=ck)
    pk, _ = db.upsert_job(conn, to_record(raw, cfg, None))
    conn.commit()
    return pk


def submit(conn, **payload) -> dict:
    """The Resume Builder tab's link as it runs for real (launcher → request → worker); returns the outcome."""
    pl = {"kind": "hirescope_external_job", "format": 1, "origin": "resume_builder", "company": "", "role": "",
          "url": "", "jd_text": "", "created_at": "2026-10-20T00:00:00.000Z", **payload}
    data = base64.urlsafe_b64encode(json.dumps(pl).encode()).decode().rstrip("=")
    assert "imports it in the background" in launcher.handle(f"hirescope://external?data={data}")
    ER.process_requests(conn)
    assert not ER.pending_requests(), "every request is answered and removed"
    rid = pl.get("rid") or pl["created_at"]
    return next(o for o in ER.outcomes(conn) if o["rid"] == rid)


def no_fetch(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a duplicate found from the URL alone must not fetch the page")
    monkeypatch.setattr("jobsearch.add_url.resolve", boom)


# --- duplicates: link to the existing job, queue its resume ------------------------------------------------------
def test_jibe_urls_from_three_places_are_one_posting(env, cfg, monkeypatch):
    conn = env
    pk = regular_job(conn, cfg, "DemoChips", "Software Engineer Intern/Co-op (DEMO)", "jibe", "91180",
                     "https://careers.demochips.example/careers-home/jobs/91180?lang=en-us",
                     "jibe:careers.demochips.example:91180")
    no_fetch(monkeypatch)
    o = submit(conn, rid="rb-1", url="https://careers.demochips.example/careers-home/jobs/91180?lang=en-us&iis=Job%20Board&iisn=Linkedin")
    assert o["status"] == "linked" and o["job_pk"] == pk and o["queue"] == "queued"
    assert queue.active_item(conn, pk)["origin"] == "resume_builder"
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    # SimplifyJobs' form of the same link (?icims=1) is the same posting too
    assert X.canonical_key("https://careers.demochips.example/jobs/91180?icims=1") == "jibe:careers.demochips.example:91180"


def test_company_page_vs_ats_by_posting_number_and_brand(env, cfg, monkeypatch):
    """Cisco-style: the careers page names posting 2026923, the Workday copy has it in its slug."""
    conn = env
    pk = regular_job(conn, cfg, "DemoNet", "Software Engineer II (Co-op) (DEMO)", "workday",
                     "software-engineer-ii--co-op_2026923",
                     "https://demonet.wd5.myworkdayjobs.com/Careers/job/Boston/Software-Engineer-II--Co-op_2026923",
                     "workday:demonet:software-engineer-ii--co-op_2026923")
    no_fetch(monkeypatch)
    o = submit(conn, rid="rb-2", company="DemoNet, Inc.", url="https://careers.demonet.example/global/en/job/2026923")
    assert o["status"] == "linked" and o["job_pk"] == pk and "posting number 2026923" in o["message"]
    # not the same company: no link from a number alone (the page is read instead)
    called = []
    monkeypatch.setattr("jobsearch.add_url.resolve", lambda url, c, co: (called.append(url), (None, "blocked: test"))[1])
    o2 = submit(conn, rid="rb-3", company="OtherCo", url="https://careers.other.example/job/2026923")
    assert called and o2["status"] == "failed"


def test_linked_after_reading_the_page_and_ready_resume_not_rebuilt(env, cfg, monkeypatch):
    """The URL alone doesn't match; what the page says (company by brand + role + JD) does."""
    conn = env
    pk = regular_job(conn, cfg, "AMD", "Software Engineer Intern/Co-op", "simplify_internships", "s1",
                     "https://jobs.example/simplify/s1")
    monkeypatch.setattr(queue, "current_ready", lambda conn_, pk_: 1 if pk_ == pk else None)   # its folder: v1 READY
    monkeypatch.setattr("jobsearch.add_url.resolve", lambda url, c, co: (RawJob(
        company="Advanced Micro Devices, Inc", job_id="p1", title="Software Engineer Intern/Co-op", source="careers_page",
        source_url=url, apply_url=url, description=JD.replace("DemoCorp", "AMD")), "JSON-LD on the page"))
    o = submit(conn, rid="rb-4", url="https://careers.amd-demo.example/jobs/x/abc")
    assert o["status"] == "linked" and o["job_pk"] == pk
    assert o["queue"] == "skipped" and not queue.active_item(conn, pk)        # it already has a READY resume
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_brand_key():
    assert X.brand_key("Advanced Micro Devices, Inc") == X.brand_key("AMD") == "amd"
    assert X.brand_key(" Keurig Dr Pepper ") == "keurig dr pepper"
    assert X.brand_key("Tive Inc") == "tive"


def test_posting_ids():
    assert X.posting_ids("https://nvidia.wd5.myworkdayjobs.com/x/job/y/NVIDIA-Intern_JR2023499") == {"2023499"}
    assert X.posting_ids("https://careers.example/JobDetail?jobId=128506&src=SN_LinkedIn") == {"128506"}
    assert X.posting_ids("software-engineer-ii--co-op----united-states_2026923") == {"2026923"}
    assert X.posting_ids("https://example.com/jobs/2026") == set()            # a year is not a posting number


# --- failures: reported with the reason, "Paste JD instead" ---------------------------------------------------------
def test_unreadable_url_fails_with_reason_and_paste_offer(env, monkeypatch):
    conn = env
    monkeypatch.setattr("jobsearch.add_url.resolve", lambda url, c, co: (
        None, "blocked: the site answered with an empty bot-check page (HTTP 202)"))
    o = submit(conn, rid="rb-5", company="DemoBlue", role="Software Developer Co-op (DEMO)",
               url="https://careers.demoblue.example/JobDetail?jobId=1")
    assert o["status"] == "failed" and o["paste"] is True and "bot-check" in o["reason"]
    assert o["company"] == "DemoBlue" and o["url"].endswith("jobId=1")       # what the form is filled in with
    last = json.loads(db.get_meta(conn, "launcher_last"))
    assert last["ok"] is False and last["rid"] == "rb-5"
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    # then the JD is pasted: imported and queued
    o2 = submit(conn, rid="rb-6", company="DemoBlue", role="Software Developer Co-op (DEMO)",
                url="https://careers.demoblue.example/JobDetail?jobId=1", jd_text=JD.replace("DemoCorp", "DemoBlue"))
    assert o2["status"] == "imported" and o2["queue"] == "queued"


def test_any_error_is_an_answer_never_a_stuck_request(env, monkeypatch):
    conn = env
    def crash(*a, **k):
        raise RuntimeError("adapter exploded")
    monkeypatch.setattr("jobsearch.add_url.resolve", crash)
    o = submit(conn, rid="rb-7", url="https://jobs.demo.example/p/9")
    assert o["status"] == "failed" and "RuntimeError: adapter exploded" in o["reason"]


def test_older_pages_are_answered_by_created_at_and_live_js_has_outcomes(env, monkeypatch):
    conn = env
    monkeypatch.setattr("jobsearch.add_url.resolve", lambda url, c, co: (None, "the posting is gone (HTTP 404)"))
    o = submit(conn, created_at="2026-10-20T00:43:26.230Z", url="https://jobs.demo.example/p/1")   # no rid
    assert o["rid"] == "2026-10-20T00:43:26.230Z"
    p = live.write(conn, paths.DASHBOARD / "live.js")
    data = json.loads(p.read_text().split("(window.__hsLive || function (d) { window.LIVE = d; })(", 1)[1].rsplit(");", 1)[0])
    assert [x["rid"] for x in data["external"]] == ["2026-10-20T00:43:26.230Z"] and data["external"][0]["status"] == "failed"


def test_outcomes_keep_a_week_and_replace_by_id(env):
    conn = env
    ER.record_outcome(conn, "a", status="failed")
    ER.record_outcome(conn, "a", status="imported")
    cur = json.loads(db.get_meta(conn, ER.OUTCOMES_KEY))
    cur.append({"rid": "old", "at": "2020-01-01T00:00:00+00:00", "status": "failed"})
    db.set_meta(conn, ER.OUTCOMES_KEY, json.dumps(cur))
    ER.record_outcome(conn, "b", status="linked")
    assert [(x["rid"], x["status"]) for x in ER.outcomes(conn)] == [("a", "imported"), ("b", "linked")]


def test_rid_is_checked_by_the_launcher():
    pl = {"kind": "hirescope_external_job", "format": 1, "url": "https://jobs.demo.example/p/1", "rid": "x; rm -rf /"}
    data = base64.urlsafe_b64encode(json.dumps(pl).encode()).decode().rstrip("=")
    with pytest.raises(launcher.LinkError, match="rid must be"):
        launcher.handle(f"hirescope://external?data={data}")


# --- reading a page: honest reasons ---------------------------------------------------------------------------------
@pytest.mark.parametrize("status,body,want", [
    (202, "", "empty bot-check page"),
    (403, "<html>Forbidden</html>", "refused automated reading (HTTP 403)"),
    (410, "gone", "posting is gone"),
    (200, "<html><head><script src=a.js></script><script>boot()</script></head><body><div id=app></div></body></html>",
     "builds itself with JavaScript"),
    (200, "<html><body>" + "Careers at Demo. " * 100 + "</body></html>", "no job data"),
    (200, "<html><body>Please verify you are human</body></html>", "bot check"),
])
def test_resolve_reasons(status, body, want):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(status, text=body)
    raw, why = resolve("https://careers.demo.example/job/1", mock_client(handler), None)
    assert raw is None and want in why, why


def test_resolve_never_reads_no_fetch_sites():
    raw, why = resolve("https://www.linkedin.com/jobs/view/1", mock_client(lambda r: httpx.Response(500)), None)
    assert raw is None and "terms" in why
    with pytest.raises(UnreadableURL) as e:
        raise UnreadableURL("https://x.example", why)
    assert e.value.reason == why and "Paste the job description" in str(e.value)


# --- merging records of one posting keeps what you did ---------------------------------------------------------------
def test_merge_keeps_the_applied_job_and_moves_bucket_and_questions(env, cfg):
    conn = env
    a = regular_job(conn, cfg, "DemoChips", "SWE Intern (DEMO)", "jibe", "5", "https://c.demo.example/careers-home/jobs/55555",
                    "jibe:c.demo.example:55555")
    b = regular_job(conn, cfg, "DemoChips", "Software Engineer Co-op - Masters", "simplify_internships", "s5",
                    "https://elsewhere.example/x")
    c = regular_job(conn, cfg, "Demo Chips Inc", "SWE Intern", "added_url", "u5", "https://other.example/y")
    assert len({a, b, c}) == 3
    conn.execute("UPDATE jobs SET apply_url='https://c.demo.example/jobs/55555?icims=1' WHERE id=?", (b,))
    conn.execute("UPDATE jobs SET apply_url='https://c.demo.example/careers-home/jobs/55555?iis=LinkedIn' WHERE id=?", (c,))
    conn.execute("INSERT INTO applications (id, job_pk, job_key, company, company_key, title, stage, created_at, updated_at) "
                 "VALUES ('ext-1', ?, 'k', 'Demo Chips Inc', 'demo chips inc', 'SWE Intern', 'applied', '2026-10-20', '2026-10-20')", (c,))
    conn.execute("INSERT INTO bucket (job_pk, job_key, in_bucket, updated_at) VALUES (?, 'k', 1, '2026-10-20')", (a,))
    conn.execute("INSERT INTO resume_questions (job_pk, kind, keyword, question, asked_at) VALUES "
                 "(?, 'keyword', 'Go', 'Go?', '2026-10-20')", (b,))
    conn.commit()
    assert db.merge_canonical_duplicates(conn, X.canonical_key) == 2
    assert [r[0] for r in conn.execute("SELECT id FROM jobs")] == [c]
    assert conn.execute("SELECT job_pk FROM bucket").fetchone()[0] == c
    assert conn.execute("SELECT job_pk FROM resume_questions").fetchone()[0] == c
    assert {r[0] for r in conn.execute("SELECT source FROM job_sources WHERE job_pk=?", (c,))} == \
        {"jibe", "simplify_internships", "added_url"}


def test_workday_adapter_key_matches_links_to_a_reposted_req():
    """A re-posted requisition (…_2642693-1) and a SimplifyJobs link to …_2642693 are one posting."""
    from jobsearch.adapters.canonical import workday_key
    assert workday_key("kla", "Engr--Software-1_2642693-1") == X.canonical_key(
        "https://kla.wd1.myworkdayjobs.com/Search/job/USA-NY-Remote/Engr--Software-1_2642693") == "workday:kla:engr--software-1_2642693"


def test_every_run_merges_duplicates_found_since(conn, cfg, resume, embedder):
    """Not only when extraction changes: a SimplifyJobs copy of a Jibe posting is merged on the next run."""
    from jobsearch.pipeline import run
    a = regular_job(conn, cfg, "DemoChips", "SWE Intern (DEMO)", "jibe", "7", "https://c.demo.example/careers-home/jobs/77777",
                    "jibe:c.demo.example:77777")
    b = regular_job(conn, cfg, "DemoChips", "Software Engineer Co-op - Masters", "simplify_internships", "s7",
                    "https://c.demo.example/jobs/77777?icims=1")
    assert a != b
    run(conn, cfg, resume, [], mock_client(lambda r: httpx.Response(404)), embedder, None)
    assert [r[0] for r in conn.execute("SELECT id FROM jobs")] == [a]
