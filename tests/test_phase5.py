"""Phase 5 (DEMO data): LinkedIn alert emails, Hacker News Who's Hiring, optional aggregator, secrets handling."""
import json
from email.message import EmailMessage

import httpx
import pytest

from jobsearch import secrets
from jobsearch.adapters import hn_hiring as hn
from jobsearch.adapters.aggregator import AggregatorAdapter
from jobsearch.adapters.base import FetchContext, safe_url
from jobsearch.adapters.linkedin_alerts import LinkedInAlertsAdapter, parse_alert
from jobsearch.config import Company
from jobsearch.pipeline import plan, run
from jobsearch.storage import db

from .conftest import mock_client


# --- LinkedIn job-alert emails ---------------------------------------------------------------------
def alert(text=None, html=None, sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
          date="Mon, 12 Oct 2026 08:00:00 -0400"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = sender, "demo@example.com", "DEMO: 3 new jobs", date
    if text:
        m.set_content(text)
    if html:
        if text:
            m.add_alternative(html, subtype="html")
        else:
            m.set_content(html, subtype="html")
    return m


TEXT_ALERT = """Your job alert for software engineer in Boston, MA
3 new jobs match your preferences.

Software Engineer, New Grad (DEMO)
DemoCo
Boston, MA
View job: https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc%3D&refId=x

Backend Engineer I (DEMO)
Acme Robotics
Cambridge, MA (Hybrid)
Actively recruiting
View job: https://www.linkedin.com/comm/jobs/view/4012345679/?trackingId=def

----------------------------------------
See all jobs: https://www.linkedin.com/comm/jobs/search/?keywords=software
Unsubscribe: https://www.linkedin.com/comm/psettings/email-unsubscribe
"""

HTML_ALERT = """<html><body><table>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4099999001/?trackingId=1"><img alt="DemoCo logo"></a></td>
<td><a href="https://www.linkedin.com/comm/jobs/view/4099999001/?trackingId=1">Software Engineer Co-op, Spring 2027 (DEMO)</a>
<p>DemoCo &amp; Sons</p><p>Seattle, WA</p><p>Be an early applicant</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4099999002/?trackingId=2">Junior Developer (DEMO)</a>
<p>Beta Labs</p><p>Remote</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/search/">See all jobs</a></td></tr>
</table></body></html>"""


def test_parse_plain_text_alert():
    jobs = parse_alert(alert(text=TEXT_ALERT))
    assert [(j["job_id"], j["title"], j["company"], j["location"]) for j in jobs] == [
        ("4012345678", "Software Engineer, New Grad (DEMO)", "DemoCo", "Boston, MA"),
        ("4012345679", "Backend Engineer I (DEMO)", "Acme Robotics", "Cambridge, MA (Hybrid)")]
    assert jobs[0]["url"] == "https://www.linkedin.com/jobs/view/4012345678/"   # tracking stripped


def test_parse_html_alert_prefers_html_part():
    jobs = parse_alert(alert(text="View in browser", html=HTML_ALERT))
    assert [(j["title"], j["company"], j["location"]) for j in jobs] == [
        ("Software Engineer Co-op, Spring 2027 (DEMO)", "DemoCo & Sons", "Seattle, WA"),
        ("Junior Developer (DEMO)", "Beta Labs", "Remote")]


def test_linkedin_adapter_ignores_other_senders_and_dedupes():
    mails = [alert(text=TEXT_ALERT, date="Sun, 11 Oct 2026 08:00:00 -0400"), alert(text=TEXT_ALERT),
             alert(text=TEXT_ALERT, sender="Phisher <jobalerts-noreply@linkedln.com.evil>")]
    res = LinkedInAlertsAdapter(None).fetch(Company(name="LinkedIn Job Alerts", source="linkedin_alerts", board="21"),
                                            FetchContext(), mailbox=[m.as_bytes() for m in mails])
    assert len(res.jobs) == 2 and "2 alert emails" in res.notes[0]
    assert res.jobs[0].posted_date.day == 11       # earliest alert wins
    assert res.jobs[0].description == "" and res.jobs[0].source == "linkedin_alerts"


def test_linkedin_unavailable_without_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets.paths, "ROOT", tmp_path)
    secrets._dotenv.cache_clear()
    for k in ("IMAP_USER", "IMAP_APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    c = Company(name="LinkedIn Job Alerts", source="linkedin_alerts")
    assert "IMAP_USER, IMAP_APP_PASSWORD" in LinkedInAlertsAdapter.unavailable(c)
    (tmp_path / ".env").write_text('IMAP_USER=demo@example.com\nexport IMAP_APP_PASSWORD="abcd efgh"\n')
    secrets._dotenv.cache_clear()
    assert LinkedInAlertsAdapter.unavailable(c) is None
    assert secrets.get("IMAP_APP_PASSWORD") == "abcd efgh"
    assert secrets.redact("login failed for abcd efgh", "IMAP_APP_PASSWORD") == "login failed for ***"
    monkeypatch.setattr(LinkedInAlertsAdapter, "disabled", True)
    assert "--no-email" in LinkedInAlertsAdapter.unavailable(c)
    secrets._dotenv.cache_clear()


def test_unavailable_sources_are_skipped_not_failed(conn, cfg):
    cfg.companies.append(Company(name="LinkedIn Job Alerts", source="linkedin_alerts", origin="feed", tier=1))
    LinkedInAlertsAdapter.disabled = True
    try:
        planned, skipped = plan(conn, cfg, [])
    finally:
        LinkedInAlertsAdapter.disabled = False
    assert all(p.company.source != "linkedin_alerts" for p in planned)
    assert any(s.source == "linkedin_alerts" and s.status == "skipped" and "--no-email" in s.notes[0] for s in skipped)


# --- Hacker News Who's Hiring ----------------------------------------------------------------------
POSTS = {
    101: {"id": 101, "by": "demo", "time": 1790000000, "text":
          "DemoCo (YC W24) | Software Engineer, New Grad | Boston, MA | ONSITE | $120k-$150k<p>We build DEMO things. "
          "0-2 years of experience with Python.<p>Apply: <a href=\"https:&#x2F;&#x2F;boards.greenhouse.io&#x2F;democo&#x2F;jobs&#x2F;555\">"
          "https:&#x2F;&#x2F;boards.greenhouse.io&#x2F;democo&#x2F;jobs&#x2F;555</a>"},
    102: {"id": 102, "by": "demo", "time": 1790000100, "text":
          "Acme | Account Executive | NYC | Onsite<p>Sales role (DEMO)."},
    103: {"id": 103, "by": "demo", "time": 1790000200, "text":
          "Beta Labs | Backend Engineer | Frontend Engineer | REMOTE (US) | Full-time<p>DEMO. "
          "<a href=\"https:&#x2F;&#x2F;betalabs.example&#x2F;careers\">careers</a>"},
    104: {"id": 104, "deleted": True},
    105: {"id": 105, "by": "demo", "time": 1790000300, "text": "Just a comment without the header format (DEMO)."},
}


def hn_handler(posts=POSTS, calls=None):
    def handler(request: httpx.Request):
        path = request.url.path
        if calls is not None:
            calls.append(path)
        if path.endswith("/user/whoishiring.json"):
            return httpx.Response(200, json={"submitted": [900, 901]})
        if path.endswith("/item/900.json"):
            return httpx.Response(200, json={"id": 900, "title": "Ask HN: Who wants to be hired? (October 2026)"})
        if path.endswith("/item/901.json"):
            return httpx.Response(200, json={"id": 901, "title": "Ask HN: Who is hiring? (October 2026)",
                                             "time": 1789990000, "kids": sorted(posts)})
        pid = int(path.rsplit("/", 1)[1].split(".")[0])
        return httpx.Response(200, json=posts.get(pid))
    return handler


def test_hn_parse_post():
    p = hn.parse_post(POSTS[101])
    assert p["company"] == "DemoCo" and p["title"] == "Software Engineer, New Grad"
    assert p["location"] == "Boston, MA" and p["apply_url"] == "https://boards.greenhouse.io/democo/jobs/555"
    assert "0-2 years" in p["text"]
    b = hn.parse_post(POSTS[103])
    assert b["title"] == "Backend Engineer, Frontend Engineer" and b["location"] == "Remote (US)"
    assert b["apply_url"] == "https://betalabs.example/careers"
    assert hn.parse_post(POSTS[102]) is None and hn.parse_post(POSTS[104]) is None and hn.parse_post(POSTS[105]) is None


def test_hn_adapter_fetches_only_new_posts():
    calls = []
    a = hn.HNHiringAdapter(mock_client(hn_handler(calls=calls)))
    co = Company(name="Hacker News Who's Hiring", source="hn_hiring", origin="feed")
    res = a.fetch(co, FetchContext())
    assert [j.job_id for j in res.jobs] == ["101", "103"] and res.seen_ids == {"101", "103"}
    assert res.jobs[0].canonical_key == "greenhouse:555" and res.jobs[0].source_url.endswith("item?id=101")
    skipped_cache = next(iter(res.pages.values()))["payload"]
    calls.clear()
    res2 = a.fetch(co, FetchContext(known={"101": "x", "103": "x"},
                                    pages={"hn_hiring:skipped:901": {"payload": skipped_cache}}))
    assert res2.jobs == [] and res2.seen_ids == {"101", "103"}
    assert not [c for c in calls if "/item/10" in c]         # nothing re-fetched


def test_hn_end_to_end_scored_and_touched(conn, cfg, resume, embedder, tmp_path):
    co = Company(name="Hacker News Who's Hiring", source="hn_hiring", origin="feed", tier=1)
    run(conn, cfg, resume, [co], mock_client(hn_handler()), embedder, tmp_path, workers=1)
    rows = {r["company"]: r for r in conn.execute("SELECT * FROM jobs")}
    assert set(rows) == {"DemoCo", "Beta Labs"} and rows["DemoCo"]["source_official"] == 0
    s = run(conn, cfg, resume, [co], mock_client(hn_handler()), embedder, tmp_path, workers=1).as_dict()
    assert s["new"] == 0 and conn.execute("SELECT COUNT(*) FROM jobs WHERE job_status='seen'").fetchone()[0] == 2
    # next month's thread: last month's posts age out (closed after 2 misses)
    fresh = {201: dict(POSTS[101], id=201)}
    for _ in range(2):
        run(conn, cfg, resume, [co], mock_client(hn_handler(fresh)), embedder, tmp_path, workers=1)
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE job_status='closed'").fetchone()[0] >= 1


# --- aggregator ------------------------------------------------------------------------------------
JSEARCH = {"status": "OK", "data": [
    {"job_id": "js1", "employer_name": "DemoCo", "job_title": "Software Engineer I (DEMO)", "job_city": "Boston",
     "job_state": "MA", "job_country": "US", "job_is_remote": False, "job_description": "DEMO JD. Python.",
     "job_apply_link": "https://jobs.lever.co/democo/0f6a0d2e-1111-2222-3333-444455556666?utm_source=jsearch",
     "job_posted_at_datetime_utc": "2026-10-11T12:00:00.000Z", "job_employment_type": "FULLTIME", "job_publisher": "LinkedIn"},
    {"job_id": "bad"}]}
SERP = {"jobs_results": [{"job_id": "eyJ...DEMO", "title": "Junior Developer (DEMO)", "company_name": "Beta Labs",
                          "location": "Atlanta, GA", "description": "DEMO", "via": "Indeed",
                          "detected_extensions": {"posted_at": "3 days ago", "schedule_type": "Full-time"},
                          "apply_options": [{"title": "Beta Labs", "link": "https://betalabs.example/jobs/1"}]}]}


def test_aggregator_off_by_default_and_needs_key(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets.paths, "ROOT", tmp_path)
    secrets._dotenv.cache_clear()
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    c = Company(name="Aggregator (jsearch)", source="aggregator", board="jsearch")
    assert "off" in AggregatorAdapter.unavailable(c)
    monkeypatch.setattr(AggregatorAdapter, "enabled", True)
    assert "JSEARCH_API_KEY" in AggregatorAdapter.unavailable(c)
    monkeypatch.setenv("JSEARCH_API_KEY", "demo-key")
    assert AggregatorAdapter.unavailable(c) is None
    secrets._dotenv.cache_clear()


def test_aggregator_jsearch_and_serpapi(monkeypatch):
    monkeypatch.setenv("JSEARCH_API_KEY", "demo-key-123")
    monkeypatch.setenv("SERPAPI_API_KEY", "serp-key-456")
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        if request.url.host == "jsearch.p.rapidapi.com":
            assert request.headers["X-RapidAPI-Key"] == "demo-key-123"
            return httpx.Response(200, json=JSEARCH)
        return httpx.Response(200, json=SERP)
    a = AggregatorAdapter(mock_client(handler))
    res = a.fetch(Company(name="Aggregator (jsearch)", source="aggregator", board="jsearch", queries=["swe new grad"]),
                  FetchContext(known={"old": "2020-01-01T00:00:00+00:00", "recent": "2099-01-01T00:00:00+00:00"}))
    assert [j.title for j in res.jobs] == ["Software Engineer I (DEMO)"]
    j = res.jobs[0]
    assert j.source == "aggregator_jsearch" and j.canonical_key == "lever:0f6a0d2e-1111-2222-3333-444455556666"
    assert "utm_source" not in j.apply_url and j.location == "Boston, MA, US"
    assert res.protected == {"recent"}            # search results: recent ones never closed on a miss
    assert seen[0].url.params["query"] == "swe new grad" and "resume" not in str(seen[0].url).lower()
    r2 = a.fetch(Company(name="Aggregator (serpapi)", source="aggregator", board="serpapi", queries=["junior developer"]),
                 FetchContext())
    assert r2.jobs[0].company == "Beta Labs" and r2.jobs[0].posted_date is not None


def test_safe_url_masks_keys():
    assert safe_url("https://serpapi.com/search.json?engine=google_jobs&api_key=SECRET123&q=x") == \
        "https://serpapi.com/search.json?engine=google_jobs&api_key=***&q=x"
    assert "SECRET" not in safe_url("HTTP 500 for url 'https://x/?token=SECRET&a=1'")


def test_real_seed_list_loads_and_every_source_has_an_adapter():
    from jobsearch.adapters.registry import ADAPTERS
    from jobsearch.config import load_companies
    cos = load_companies()
    assert len(cos) >= 300
    assert {c.source for c in cos} <= set(ADAPTERS)
    names = [c.name.casefold() for c in cos]
    assert len(names) == len(set(names))


def test_add_url_never_fetches_linkedin_apple_meta():
    from jobsearch.add_url import resolve

    def handler(request):
        raise AssertionError(f"fetched {request.url}")
    for u in ("https://www.linkedin.com/jobs/view/4012345678/", "https://jobs.apple.com/en-us/details/200000-0000/swe",
              "https://www.metacareers.com/profile/job_details/1092606640401919/"):
        raw, how = resolve(u, mock_client(handler), None)
        assert raw is None and "paste the JD" in how
