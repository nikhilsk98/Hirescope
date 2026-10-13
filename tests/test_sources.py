"""Adapters (mocked HTTP), watchlist CSV, ATS detection + fallback order, failure reporting, cross-source
dedupe, partial listings, 304 handling, tier scheduling, add-by-URL, dead links. (All DEMO data.)"""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from jobsearch.adapters.amazon import AmazonAdapter
from jobsearch.adapters.ashby import AshbyAdapter
from jobsearch.adapters.base import FetchContext
from jobsearch.adapters.detect import Detector, directory_from_simplify, from_url, slugs
from jobsearch.adapters.jsonld import CareersPageAdapter, NetflixAdapter, jobpostings_in_html
from jobsearch.adapters.lever import LeverAdapter
from jobsearch.adapters.simplify_github import SimplifyAdapter, strip_tracking
from jobsearch.adapters.smartrecruiters import SmartRecruitersAdapter
from jobsearch.adapters.workday import WorkdayAdapter, _find_us_facet, _posted_on
from jobsearch.add_url import add_job
from jobsearch.config import Company
from jobsearch.output.links import classify
from jobsearch.pipeline import plan, run
from jobsearch.storage import db
from jobsearch.watchlist import WatchItem, WatchlistError, parse_watchlist

from .conftest import mock_client

REQ = "<h3>Requirements</h3><ul><li>Python and SQL</li><li>3+ years of software engineering experience</li></ul>"


def router(routes: dict):
    """routes: (METHOD, url-substring) -> JSON payload | int status | callable(request)."""
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, str(request.url)))
        for (method, frag), resp in routes.items():
            if request.method == method and frag in str(request.url):
                if callable(resp):
                    return resp(request)
                if isinstance(resp, int):
                    return httpx.Response(resp, json={})
                if isinstance(resp, str):
                    return httpx.Response(200, text=resp, headers={"content-type": "text/html"})
                return httpx.Response(200, json=resp)
        return httpx.Response(404, json={})
    handler.calls = calls
    return handler


# --- adapters -----------------------------------------------------------------------------------
LEVER = [{"id": "11111111-1111-1111-1111-111111111111", "text": "Software Engineer Intern (DEMO)",
          "categories": {"commitment": "Intern", "location": "Boston, MA", "allLocations": ["Boston, MA"]},
          "createdAt": 1791676800000, "description": "<p>DEMO lever posting</p>",
          "lists": [{"text": "Requirements", "content": "<li>Python</li><li>Spring 2027 availability</li>"}],
          "additional": "", "hostedUrl": "https://jobs.lever.co/democo/11111111-1111-1111-1111-111111111111",
          "workplaceType": "onsite"}]


def test_lever_adapter():
    res = LeverAdapter(mock_client(router({("GET", "api.lever.co"): LEVER}))).fetch(
        Company(name="DemoCo", source="lever", board="democo"), FetchContext())
    j = res.jobs[0]
    assert j.employment_type == "Intern" and j.canonical_key.startswith("lever:")
    assert "- Python" in j.description and j.posted_date.year == 2026


ASHBY = {"jobs": [{"id": "22222222-2222-2222-2222-222222222222", "title": "Backend Engineer (DEMO)",
                   "location": "New York, NY", "secondaryLocations": [{"location": "Remote (US)"}], "isRemote": True,
                   "employmentType": "FullTime", "publishedAt": "2026-10-10T12:00:00Z", "isListed": True,
                   "jobUrl": "https://jobs.ashbyhq.com/democo/22222222-2222-2222-2222-222222222222",
                   "descriptionHtml": "<p>DEMO</p>" + REQ,
                   "compensation": {"compensationTierSummary": "$120K – $150K"}},
                  {"id": "33333333-3333-3333-3333-333333333333", "title": "Unlisted (DEMO)", "isListed": False}]}


def test_ashby_adapter_skips_unlisted_and_keeps_compensation():
    res = AshbyAdapter(mock_client(router({("GET", "api.ashbyhq.com"): ASHBY}))).fetch(
        Company(name="DemoCo", source="ashby", board="democo"), FetchContext())
    assert len(res.jobs) == 1
    j = res.jobs[0]
    assert "Remote (US)" in j.location and "$120K" in j.description and j.employment_type == "FullTime"


def test_conditional_get_unchanged():
    def not_modified(request):
        assert request.headers.get("if-none-match") == 'W/"abc"'
        return httpx.Response(304)
    ctx = FetchContext(validators={"https://api.ashbyhq.com/posting-api/job-board/democo?includeCompensation=true":
                                   {"etag": 'W/"abc"'}})
    res = AshbyAdapter(mock_client(router({("GET", "api.ashbyhq.com"): not_modified}))).fetch(
        Company(name="DemoCo", source="ashby", board="democo"), ctx)
    assert res.unchanged and not res.jobs


WD_LIST = {"total": 2, "jobPostings": [
    {"title": "Software Engineer I (DEMO)", "externalPath": "/job/US-CA-Santa-Clara/Software-Engineer-I_JR1",
     "locationsText": "US, CA, Santa Clara", "postedOn": "Posted 3 Days Ago", "bulletFields": ["JR1"]},
    {"title": "Account Executive (DEMO)", "externalPath": "/job/US-NY/Account-Executive_JR2",
     "locationsText": "US, NY, New York", "postedOn": "Posted Today", "bulletFields": ["JR2"]}],
    "facets": [{"facetParameter": "locationMainGroup", "values": [
        {"facetParameter": "locationHierarchy1", "values": [{"descriptor": "Canada", "id": "c1"},
                                                            {"descriptor": "United States of America", "id": "us1"}]}]}]}
WD_DETAIL = {"jobPostingInfo": {"title": "Software Engineer I (DEMO)", "jobDescription": "<p>DEMO</p>" + REQ,
                                "location": "US, CA, Santa Clara", "startDate": "2026-10-09", "timeType": "Full time",
                                "externalUrl": "https://democo.wd5.myworkdayjobs.com/Ext/job/US-CA-Santa-Clara/Software-Engineer-I_JR1"}}


def test_workday_adapter_us_facet_prefilter_and_details(cfg):
    from jobsearch.match.rules import quick_relevant
    bodies = []

    def jobs(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=WD_LIST)
    h = router({("POST", "/wday/cxs/democo/Ext/jobs"): jobs, ("GET", "/wday/cxs/democo/Ext/job/"): WD_DETAIL})
    ctx = FetchContext(want=lambda t, loc: quick_relevant(t, loc, cfg.prefs, cfg.title_rules))
    res = WorkdayAdapter(mock_client(h)).fetch(
        Company(name="DemoCo", source="workday", host="democo.wd5.myworkdayjobs.com", board="democo", site="Ext"), ctx)
    assert bodies[1]["appliedFacets"] == {"locationHierarchy1": ["us1"]}       # US facet applied
    swe = next(j for j in res.jobs if j.job_id == "software-engineer-i_jr1")
    ae = next(j for j in res.jobs if j.job_id == "account-executive_jr2")
    assert "- Python and SQL" in swe.description and swe.posted_date.day == 9
    assert ae.description == ""                                               # not relevant: no detail fetch
    assert sum(1 for m, u in h.calls if m == "GET") == 1
    assert swe.canonical_key == "workday:democo:software-engineer-i_jr1"


def test_workday_known_job_keeps_stored_jd():
    h = router({("POST", "/jobs"): WD_LIST, ("GET", "/job/"): WD_DETAIL})
    fresh = datetime.now(timezone.utc).isoformat()
    ctx = FetchContext(known={"software-engineer-i_jr1": fresh, "account-executive_jr2": None})
    res = WorkdayAdapter(mock_client(h)).fetch(
        Company(name="DemoCo", source="workday", host="democo.wd5.myworkdayjobs.com", board="democo", site="Ext"), ctx)
    assert all(j.description is None for j in res.jobs if j.job_id == "software-engineer-i_jr1")
    assert not any(m == "GET" and "Software-Engineer-I" in u for m, u in h.calls)


def test_workday_early_career_facets_replace_fuzzy_coop_search():
    from jobsearch.adapters.workday import _early_career_facets
    facets = [{"facetParameter": "workerSubType", "values": [{"descriptor": "Regular", "id": "r"},
                                                              {"descriptor": "Intern (Fixed Term)", "id": "i"},
                                                              {"descriptor": "New College Graduate", "id": "g"}]},
              {"facetParameter": "locationMainGroup", "values": [{"facetParameter": "locations", "values": [
                  {"descriptor": "US, CA, University Park", "id": "loc"}]}]}]
    assert _early_career_facets(facets) == [("workerSubType", "i", "Intern (Fixed Term)"),
                                            ("workerSubType", "g", "New College Graduate")]
    queries = FetchContext().queries["workday"]          # config/title_rules.yaml -> search_queries
    assert "co-op" not in queries and "intern" not in queries and "software" in queries


def test_workday_helpers():
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert _posted_on("Posted Today", now)[0] == now
    assert _posted_on("Posted 3 Days Ago", now) == (now - timedelta(days=3), True)
    assert _posted_on("Posted 30+ Days Ago", now)[1] is False
    assert _find_us_facet(WD_LIST["facets"]) == ("locationHierarchy1", "us1")


SR_LIST = {"totalFound": 1, "content": [{"id": "744000000000001", "name": "Software Engineer (DEMO)",
                                          "releasedDate": "2026-10-11T00:00:00Z",
                                          "location": {"city": "Austin", "region": "TX", "country": "us"},
                                          "typeOfEmployment": {"label": "Full-time"}}]}
SR_DETAIL = {"postingUrl": "https://jobs.smartrecruiters.com/DemoCo/744000000000001-software-engineer",
             "jobAd": {"sections": {"jobDescription": {"title": "Job", "text": "<p>DEMO</p>"},
                                    "qualifications": {"title": "Qualifications", "text": REQ}}}}


def test_smartrecruiters_adapter():
    h = router({("GET", "/postings?"): SR_LIST, ("GET", "/postings/744000000000001"): SR_DETAIL})
    res = SmartRecruitersAdapter(mock_client(h)).fetch(Company(name="DemoCo", source="smartrecruiters",
                                                               board="DemoCo"), FetchContext())
    j = res.jobs[0]
    assert j.apply_url.startswith("https://jobs.smartrecruiters.com/") and "Python and SQL" in j.description
    assert j.location == "Austin, TX, US"


AMAZON = {"hits": 1, "error": None, "jobs": [{"id_icims": "10000001", "title": "Software Development Engineer I (DEMO)",
                                              "job_path": "/en/jobs/10000001/sde-i", "normalized_location": "Seattle, Washington, USA",
                                              "posted_date": "October 10, 2026", "description": "DEMO",
                                              "basic_qualifications": "- Python<br/>- 1+ years of software development experience",
                                              "preferred_qualifications": "- AWS", "job_schedule_type": "full-time"}]}


def test_amazon_adapter_requirements_split():
    from jobsearch.extract.requirements import split_requirements
    res = AmazonAdapter(mock_client(router({("GET", "search.json"): AMAZON}))).fetch(
        Company(name="Amazon", source="amazon"), FetchContext())
    j = res.jobs[0]
    assert j.apply_url == "https://www.amazon.jobs/en/jobs/10000001/sde-i" and j.canonical_key == "amazon:10000001"
    reqs = split_requirements(j.description)
    assert "Python" in reqs.minimum and "AWS" in reqs.preferred


JOBPAGE = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"Software Engineer (DEMO)","datePosted":"2026-10-10","description":"<p>DEMO</p><ul><li>Python</li></ul>",
"employmentType":"FULL_TIME","identifier":{"value":"JP-1"},"validThrough":"2026-11-30",
"jobLocation":{"@type":"Place","address":{"addressLocality":"Boston","addressRegion":"MA","addressCountry":"US"}}}
</script></head></html>"""


def test_jsonld_parsing_and_careers_page_links():
    assert jobpostings_in_html(JOBPAGE)[0]["title"] == "Software Engineer (DEMO)"
    listing = '<a href="/jobs/123-software-engineer">SWE</a><a href="https://other.com/jobs/9">x</a>'
    h = router({("GET", "robots.txt"): 404, ("GET", "/careers"): listing, ("GET", "/jobs/123"): JOBPAGE})
    res = CareersPageAdapter(mock_client(h)).fetch(
        Company(name="DemoCo", source="jsonld", careers_url="https://democo.example/careers"), FetchContext())
    j = res.jobs[0]
    assert j.location == "Boston, MA, US" and j.extra["deadline"] == "2026-11-30"
    assert not any("other.com" in u for _, u in h.calls)          # never leaves the site


def test_careers_page_respects_robots():
    h = router({("GET", "robots.txt"): "User-agent: *\nDisallow: /careers\n", ("GET", "/careers"): JOBPAGE})
    with pytest.raises(Exception, match="robots"):
        CareersPageAdapter(mock_client(h)).fetch(
            Company(name="DemoCo", source="jsonld", careers_url="https://democo.example/careers"), FetchContext())


SITEMAP = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://explore.jobs.netflix.net/careers/job/790300000001-software-engineer-l4-los-gatos-california</loc>
<lastmod>2026-10-10T00:00:00Z</lastmod></url>
<url><loc>https://explore.jobs.netflix.net/careers/job/790300000002-executive-producer-games</loc>
<lastmod>2026-10-10T00:00:00Z</lastmod></url></urlset>"""


def test_netflix_sitemap_prefilters_by_slug_and_caches_pages(cfg):
    from jobsearch.match.rules import quick_relevant
    h = router({("GET", "sitemap"): SITEMAP, ("GET", "robots.txt"): "User-agent: *\nDisallow: /\nAllow: /careers\n",
                ("GET", "/careers/job/790300000001"): JOBPAGE})
    ctx = FetchContext(want=lambda t, loc: quick_relevant(t, loc, cfg.prefs, cfg.title_rules))
    res = NetflixAdapter(mock_client(h)).fetch(Company(name="Netflix", source="netflix",
                                                       board="https://x/sitemap.xml"), ctx)
    assert [j.job_id for j in res.jobs] == ["790300000001"]
    assert res.seen_ids == {"790300000001", "790300000002"}      # both listed -> neither marked missing
    assert not any("790300000002" in u for _, u in h.calls)      # irrelevant page never fetched
    # second run with the page cache: no page fetch at all
    h2 = router({("GET", "sitemap"): SITEMAP})
    ctx2 = FetchContext(want=ctx.want, pages=res.pages)
    res2 = NetflixAdapter(mock_client(h2)).fetch(Company(name="Netflix", source="netflix",
                                                         board="https://x/sitemap.xml"), ctx2)
    assert [j.title for j in res2.jobs] == ["Software Engineer (DEMO)"]
    assert not any("/careers/job/" in u for _, u in h2.calls)


SIMPLIFY = [
    {"id": "s1", "company_name": "DemoCo", "title": "Software Engineering Co-op (DEMO)", "active": True, "is_visible": True,
     "url": "https://jobs.lever.co/democo/11111111-1111-1111-1111-111111111111?utm_source=Simplify&ref=Simplify",
     "locations": ["Boston, MA"], "date_posted": 1791676800, "terms": ["Spring 2027"], "sponsorship": "Other"},
    {"id": "s2", "company_name": "OtherCo", "title": "Marketing Intern (DEMO)", "active": True, "is_visible": True,
     "url": "https://otherco.example/jobs/2", "locations": ["Austin, TX"], "date_posted": 1791676800, "terms": []},
    {"id": "s3", "company_name": "GoneCo", "title": "SWE Intern (DEMO)", "active": False, "url": "https://x"},
]


def test_simplify_adapter_enriches_from_employer_ats(cfg):
    from jobsearch.match.rules import quick_relevant
    h = router({("GET", "listings.json"): SIMPLIFY, ("GET", "api.lever.co/v0/postings/democo/"): LEVER[0]})
    ctx = FetchContext(want=lambda t, loc: quick_relevant(t, loc, cfg.prefs, cfg.title_rules))
    res = SimplifyAdapter(mock_client(h)).fetch(Company(name="SimplifyJobs Internships", source="simplify",
                                                        board="https://raw/listings.json", site="coop_intern"), ctx)
    by = {j.job_id: j for j in res.jobs}
    assert set(by) == {"s1", "s2"}                                  # inactive skipped
    assert by["s1"].apply_url == "https://jobs.lever.co/democo/11111111-1111-1111-1111-111111111111"
    assert by["s1"].canonical_key == "lever:11111111-1111-1111-1111-111111111111"
    assert "- Python" in by["s1"].description and by["s1"].terms == ["Spring 2027"]
    assert by["s1"].source == "simplify_internships" and by["s1"].track_hint == "coop_intern"
    assert by["s2"].description == ""                               # not relevant -> not enriched
    assert res.directory["DemoCo"] == {"source": "lever", "board": "democo"}


def test_strip_tracking():
    assert strip_tracking("https://a.com/j?utm_source=Simplify&ref=Simplify&id=3") == "https://a.com/j?id=3"


# --- cross-source dedupe through the pipeline ---------------------------------------------------
def test_simplify_then_official_ats_is_one_job(conn, cfg, resume, embedder, tmp_path):
    h = router({("GET", "listings.json"): SIMPLIFY, ("GET", "api.lever.co/v0/postings/democo/"): LEVER[0],
                ("GET", "api.lever.co/v0/postings/democo?"): LEVER})
    feed = Company(name="SimplifyJobs Internships", source="simplify", board="https://raw/listings.json",
                   site="coop_intern", origin="feed")
    lever = Company(name="DemoCo", source="lever", board="democo", tier=1)
    run(conn, cfg, resume, [feed], mock_client(h), embedder, tmp_path, workers=1)
    run(conn, cfg, resume, [lever], mock_client(h), embedder, tmp_path, workers=1)
    rows = [r for r in db.open_jobs(conn) if r["company"] == "DemoCo"]
    assert len(rows) == 1
    r = rows[0]
    assert r["source"] == "lever" and r["source_official"] == 1          # official took over
    assert r["track"] == "coop_intern" and "Spring 2027" in r["terms"]
    srcs = {x[0] for x in conn.execute("SELECT source FROM job_sources WHERE job_pk=?", (r["id"],))}
    assert srcs == {"simplify_internships", "lever"}


def test_partial_listing_never_closes_jobs(conn, cfg, resume, embedder, tmp_path, monkeypatch):
    from jobsearch.adapters.base import FetchResult, RawJob
    from jobsearch.adapters import registry
    co = Company(name="DemoCo", source="lever", board="democo")
    run(conn, cfg, resume, [co], mock_client(router({("GET", "api.lever.co"): LEVER})), embedder, tmp_path, workers=1)
    monkeypatch.setattr(registry.ADAPTERS["lever"], "fetch",
                        lambda self, c, ctx: FetchResult(jobs=[], partial="capped at 0 of 1"))
    for _ in range(3):
        s = run(conn, cfg, resume, [co], mock_client(router({})), embedder, tmp_path, workers=1).as_dict()
    assert len(db.open_jobs(conn)) == 1 and s["sources_partial"] == 1


def test_304_touches_listings(conn, cfg, resume, embedder, tmp_path):
    co = Company(name="DemoCo", source="lever", board="democo")
    resp = {"n": 0}

    def lever(request):
        resp["n"] += 1
        if resp["n"] == 1:
            return httpx.Response(200, json=LEVER, headers={"etag": 'W/"v1"'})
        assert request.headers.get("if-none-match") == 'W/"v1"'
        return httpx.Response(304)
    h = router({("GET", "api.lever.co"): lever})
    run(conn, cfg, resume, [co], mock_client(h), embedder, tmp_path, workers=1)
    for _ in range(3):  # unchanged three times: still open, no misses
        s = run(conn, cfg, resume, [co], mock_client(h), embedder, tmp_path, workers=1).as_dict()
    assert len(db.open_jobs(conn)) == 1 and s["per_source"][0]["unchanged"]


# --- watchlist + detection + failure reporting --------------------------------------------------
def test_watchlist_csv_parsing():
    items = parse_watchlist("company,careers_url,notes\n# comment\nStripe,,\nRamp,jobs.ashbyhq.com/ramp,x\n"
                            "stripe,,dup\n\n")
    assert [(i.company, i.careers_url) for i in items] == [("Stripe", None), ("Ramp", "https://jobs.ashbyhq.com/ramp")]
    assert [i.company for i in parse_watchlist("Acme,https://acme.example/careers\n")] == ["Acme"]  # no header
    with pytest.raises(WatchlistError):
        parse_watchlist("company,careers_url\n,https://x\n")


@pytest.mark.parametrize("url,fields", [
    ("https://boards.greenhouse.io/figma", {"source": "greenhouse", "board": "figma"}),
    ("https://job-boards.greenhouse.io/embed/job_board?for=stripe", {"source": "greenhouse", "board": "stripe"}),
    ("https://jobs.lever.co/palantir", {"source": "lever", "board": "palantir"}),
    ("https://jobs.ashbyhq.com/ramp", {"source": "ashby", "board": "ramp"}),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
     {"source": "workday", "host": "nvidia.wd5.myworkdayjobs.com", "board": "nvidia", "site": "NVIDIAExternalCareerSite"}),
    ("https://jobs.smartrecruiters.com/ServiceNow", {"source": "smartrecruiters", "board": "ServiceNow"}),
    ("https://www.amazon.jobs/en/", {"source": "amazon"}),
    ("https://careers.example.com", None),
])
def test_ats_detection_from_url(url, fields):
    assert from_url(url) == fields


def test_slugs():
    assert slugs("Scale AI") == ["scaleai", "scale-ai"]
    assert slugs("Acme Technologies, Inc.")[0] == "acme"


def test_detection_fallback_order(cfg):
    seed = [Company(name="SeedCo", source="greenhouse", board="seedco", tier=2)]
    directory = {"DirCo": {"source": "lever", "board": "dirco"}}
    page = '<a href="https://jobs.ashbyhq.com/pageco">Jobs</a>'
    h = router({("GET", "robots.txt"): 404, ("GET", "pageco.example"): page,
                ("GET", "boards-api.greenhouse.io/v1/boards/probeco/jobs"): {"jobs": []},
                ("GET", "jsonldco.example"): JOBPAGE})
    d = Detector(mock_client(h), seed, directory)
    cached = {"source": "ashby", "board": "cached", "fail_count": 0, "detected_via": "name probe"}
    assert d.detect("Anything", None, cached).via == "cache"
    assert d.detect("UrlCo", "https://jobs.lever.co/urlco").via == "careers_url"
    r = d.detect("SeedCo")
    assert r.via == "seed list" and r.company.tier == 1 and r.company.origin == "my_companies"
    assert d.detect("DirCo").company.board == "dirco"
    assert d.detect("PageCo", "https://pageco.example/careers").company.board == "pageco"
    assert d.detect("ProbeCo").company.source == "greenhouse"
    assert d.detect("JsonLdCo", "https://jsonldco.example/careers").company.source == "jsonld"
    m = d.detect("NoWhereCo")
    assert m.company is None and m.via == "manual"
    # a detection that failed twice is ignored and re-run
    assert d.detect("UrlCo", "https://jobs.lever.co/urlco", {**cached, "fail_count": 2}).via == "careers_url"


def test_directory_from_simplify():
    d = directory_from_simplify([{"company_name": "A", "url": "https://jobs.lever.co/a/1"},
                                 {"company_name": "A", "url": "https://jobs.lever.co/a/2"},
                                 {"company_name": "A", "url": "https://boards.greenhouse.io/a2/jobs/3"}])
    assert d == {"A": {"source": "lever", "board": "a"}}


def test_my_companies_failure_reporting_and_manual(conn, cfg, resume, embedder, tmp_path):
    watch = [WatchItem("GoodCo", "https://jobs.lever.co/goodco"), WatchItem("BrokenCo", "https://jobs.lever.co/broken"),
             WatchItem("NoWhereCo")]
    h = router({("GET", "api.lever.co/v0/postings/goodco"): LEVER, ("GET", "api.lever.co/v0/postings/broken"): 500})
    cfg2 = cfg.model_copy(update={"companies": []})
    s = run(conn, cfg2, resume, None, mock_client(h), embedder, tmp_path, watch=watch, workers=1).as_dict()
    st = {o["company"]: o["status"] for o in s["per_source"]}
    assert st == {"GoodCo": "ok", "BrokenCo": "failed", "NoWhereCo": "manual"}
    assert not s["complete"] and s["manual"][0]["company"] == "NoWhereCo"
    assert db.get_company_source(conn, "GoodCo")["detected_via"] == "careers_url"
    assert db.get_company_source(conn, "BrokenCo")["fail_count"] == 1


def test_tier_schedule(conn, cfg):
    now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
    cos = [Company(name="T1", source="lever", board="t1", tier=1), Company(name="T2", source="lever", board="t2", tier=2),
           Company(name="T3", source="lever", board="t3", tier=3)]
    cfg2 = cfg.model_copy(update={"companies": cos})
    planned, skipped = plan(conn, cfg2, [], now=now)
    assert {p.company.name for p in planned} == {"T1", "T2", "T3"}      # never checked -> all due
    for c in cos:
        db.set_source_status(conn, c.name, "lever", "ok", None, 1, (now - timedelta(hours=10)).isoformat())
    planned, skipped = plan(conn, cfg2, [], now=now)
    assert {p.company.name for p in planned} == {"T1"} and {s.company for s in skipped} == {"T2", "T3"}
    planned, _ = plan(conn, cfg2, [], now=now + timedelta(hours=11))
    assert {p.company.name for p in planned} == {"T1", "T2"}
    planned, _ = plan(conn, cfg2, [WatchItem("T3")], now=now)          # My Companies: every run
    assert {(p.company.name if p.company else p.watch.company) for p in planned} == {"T1", "T3"}
    assert {p.company.name for p in plan(conn, cfg2, [], force_all=True, now=now)[0]} == {"T1", "T2", "T3"}


# --- add by URL / dead links ---------------------------------------------------------------------
def test_add_job_by_url_adapter_and_manual(conn, cfg, tmp_path):
    h = router({("GET", "api.lever.co/v0/postings/democo/"): LEVER[0]})
    pk, status, how = add_job(conn, cfg, LEVER[0]["hostedUrl"], mock_client(h), jd_dir=tmp_path)
    row = db.decode_row(conn.execute("SELECT * FROM jobs WHERE id=?", (pk,)).fetchone())
    assert status == "new" and how == "lever API" and row["track"] == "coop_intern"
    with pytest.raises(ValueError, match="jd-file"):
        add_job(conn, cfg, "https://school.example/coop/1", mock_client(router({("GET", "robots.txt"): 404})),
                jd_dir=tmp_path)
    pk2, _, how2 = add_job(conn, cfg, "https://school.example/coop/1", mock_client(router({})), company="SchoolCo",
                           title="Software Co-op (DEMO)", jd_text="- Python\n- Spring 2027", jd_dir=tmp_path,
                           track="coop_intern")
    row2 = db.decode_row(conn.execute("SELECT * FROM jobs WHERE id=?", (pk2,)).fetchone())
    assert how2 == "manual JD" and row2["track_override"] == "coop_intern" and row2["source"] == "added_url"


@pytest.mark.parametrize("status,final,ok", [(200, "https://jobs.lever.co/x/1", True), (404, None, False),
                                             (410, None, False), (403, None, None), (None, None, None),
                                             (200, "https://boards.greenhouse.io/x?error=true", False)])
def test_dead_link_classification(status, final, ok):
    assert classify(status, final)[0] is ok


def test_capped_listing_verifies_missing_jobs(conn, cfg, resume, embedder, tmp_path, monkeypatch):
    from jobsearch.adapters import registry
    from jobsearch.adapters.base import FetchResult
    co = Company(name="DemoCo", source="lever", board="democo")
    two = [LEVER[0], {**LEVER[0], "id": "44444444-4444-4444-4444-444444444444",
                      "hostedUrl": "https://jobs.lever.co/democo/44444444-4444-4444-4444-444444444444"}]
    run(conn, cfg, resume, [co], mock_client(router({("GET", "api.lever.co"): two})), embedder, tmp_path, workers=1)
    assert len([j for j in db.open_jobs(conn) if not j["filtered"]]) == 2
    states = {"11111111-1111-1111-1111-111111111111": True, "44444444-4444-4444-4444-444444444444": False}
    monkeypatch.setattr(registry.ADAPTERS["lever"], "fetch", lambda self, c, ctx: FetchResult(jobs=[], partial="capped"))
    monkeypatch.setattr(registry.ADAPTERS["lever"], "check_open", lambda self, c, jid, url: states[jid])
    for _ in range(2):
        run(conn, cfg, resume, [co], mock_client(router({})), embedder, tmp_path, workers=1)
    open_ids = {j["job_id"] for j in db.open_jobs(conn)}
    assert open_ids == {"11111111-1111-1111-1111-111111111111"}   # verified gone twice -> closed; other kept


def test_query_based_source_never_closes_relevant_job_on_a_guess(conn, cfg, resume, embedder, tmp_path, monkeypatch):
    from jobsearch.adapters import registry
    from jobsearch.adapters.base import FetchResult
    co = Company(name="DemoCo", source="workday", host="democo.wd5.myworkdayjobs.com", board="democo", site="Ext")
    h = router({("POST", "/jobs"): WD_LIST, ("GET", "/job/"): WD_DETAIL})
    run(conn, cfg, resume, [co], mock_client(h), embedder, tmp_path, workers=1)
    assert any(j["job_id"] == "software-engineer-i_jr1" and not j["filtered"] for j in db.open_jobs(conn))
    # search scope changes: the job is no longer returned, and its detail check can't tell (None)
    monkeypatch.setattr(registry.ADAPTERS["workday"], "fetch", lambda self, c, ctx: FetchResult(jobs=[]))
    monkeypatch.setattr(registry.ADAPTERS["workday"], "check_open", lambda self, c, jid, url: None)
    for _ in range(3):
        run(conn, cfg, resume, [co], mock_client(router({})), embedder, tmp_path, workers=1)
    assert any(j["job_id"] == "software-engineer-i_jr1" for j in db.open_jobs(conn))
    # once the posting itself is verified gone, it closes after the usual two misses
    monkeypatch.setattr(registry.ADAPTERS["workday"], "check_open", lambda self, c, jid, url: False)
    for _ in range(2):
        run(conn, cfg, resume, [co], mock_client(router({})), embedder, tmp_path, workers=1)
    assert not any(j["job_id"] == "software-engineer-i_jr1" for j in db.open_jobs(conn))


def test_watchlist_brand_resolution_and_merging(tmp_path):
    from jobsearch.watchlist import resolve
    aliases = tmp_path / "aliases.yaml"
    aliases.write_text("aliases:\n  \"ITA Software LLC\": Google\n  \"N-able Technologies, Inc.\": N-able\n"
                       "manual:\n  \"GetGo Investment LLC\": \"unknown\"\nlegal_suffixes: [inc, llc, corporation]\n")
    items = parse_watchlist("company,careers_url,notes\n\"Toast, Inc.\",,Boston\n\"N-able, Inc.\",,a\n"
                            "\"N-able Technologies, Inc.\",https://x.example/careers,b\nITA Software LLC,,c\nGoogle,,d\n"
                            "GetGo Investment LLC,,e\n\"Sapient Corporation / Publicis Sapient\",,f\nImmersive (formerly Immersive Labs),,g\n")
    out = {w.company: w for w in resolve(items, aliases)}
    assert set(out) == {"Toast", "N-able", "Google", "GetGo Investment LLC", "Publicis Sapient", "Immersive"}
    assert out["N-able"].names == ["N-able, Inc.", "N-able Technologies, Inc."] and out["N-able"].careers_url
    assert out["Google"].notes == "c; d"
    assert out["GetGo Investment LLC"].manual_reason == "unknown"


def test_manual_names_never_probed(cfg):
    h = router({})
    d = Detector(mock_client(h), [], {})
    r = d.detect("GetGo Investment LLC", None, None, manual_reason="Could not identify")
    assert r.company is None and r.via == "manual" and not h.calls


def test_name_probe_rejects_board_owned_by_another_company():
    h = router({("GET", "boards-api.greenhouse.io/v1/boards/thrive/jobs"): {"jobs": [{"company_name": "Thrive Market"}]},
                ("GET", "boards-api.greenhouse.io/v1/boards/acme/jobs"): {"jobs": [{"company_name": "Acme, Inc."}]}})
    d = Detector(mock_client(h), [], {})
    assert d.detect("Thrive").company is None or d.detect("Thrive").company.board != "thrive"
    assert d.detect("Acme").company.board == "acme"


def test_seed_lookup_ignores_legal_suffixes():
    d = Detector(mock_client(router({})), [Company(name="Akamai Technologies", source="workday", host="h", board="b", site="s")], {})
    assert d.detect("Akamai").company.source == "workday"


def test_workday_canonical_key_ignores_repost_suffix():
    from jobsearch.adapters.canonical import canonical_key
    a = canonical_key("https://demo.wd5.myworkdayjobs.com/en-US/Ext/job/Boston-MA/Software-Developer---Early-Career---15_R260021450")
    b = canonical_key("https://demo.wd5.myworkdayjobs.com/en-US/Ext/job/Boston-MA/Software-Developer---Early-Career---15_R260021450-1")
    assert a == b == "workday:demo:software-developer---early-career---15_r260021450"
    assert canonical_key("https://demo.wd1.myworkdayjobs.com/x/job/y/Engineer_JR2015623") == "workday:demo:engineer_jr2015623"
    assert canonical_key("https://demo.wd1.myworkdayjobs.com/x/job/y/Specialist-Software-Engineer_R-255470") == \
        "workday:demo:specialist-software-engineer_r-255470"


# --- Rippling / Jobvite / Eightfold -------------------------------------------------------------
RIP_UUID = "8c392f73-4918-4ccf-bbff-4fa6f0ee2506"
RIP_LIST = [{"uuid": RIP_UUID, "name": "Software Engineer (DEMO)", "department": {"label": "Engineering"},
             "url": f"https://ats.rippling.com/democo/jobs/{RIP_UUID}", "workLocation": {"label": "Boston, MA"}},
            {"uuid": "00000000-0000-0000-0000-000000000002", "name": "Account Executive (DEMO)",
             "url": "https://ats.rippling.com/democo/jobs/00000000-0000-0000-0000-000000000002",
             "workLocation": {"label": "Remote (United States)"}}]
RIP_DETAIL = {"uuid": RIP_UUID, "name": "Software Engineer (DEMO)", "createdOn": "2026-10-10T09:00:00-07:00",
              "description": {"company": "<p>About DemoCo</p>", "role": "<p>DEMO role</p>" + REQ},
              "workLocations": ["Boston, MA"], "employmentType": {"label": "SALARIED_FT", "id": "Salaried, full-time"},
              "url": f"https://ats.rippling.com/democo/jobs/{RIP_UUID}", "companyName": "DemoCo Inc"}


def test_rippling_adapter_prefilters_details(cfg):
    from jobsearch.adapters.rippling import RipplingAdapter
    from jobsearch.match.rules import quick_relevant
    h = router({("GET", f"/board/democo/jobs/{RIP_UUID}"): RIP_DETAIL, ("GET", "/board/democo/jobs"): RIP_LIST})
    ctx = FetchContext(want=lambda t, loc: quick_relevant(t, loc, cfg.prefs, cfg.title_rules))
    res = RipplingAdapter(mock_client(h)).fetch(Company(name="DemoCo", source="rippling", board="democo"), ctx)
    swe = next(j for j in res.jobs if j.job_id == RIP_UUID)
    ae = next(j for j in res.jobs if j.job_id != RIP_UUID)
    assert "- Python and SQL" in swe.description and "About DemoCo" in swe.description
    assert swe.posted_date.day == 10 and swe.location == "Boston, MA" and swe.canonical_key == f"rippling:{RIP_UUID}"
    assert swe.employment_type == "Salaried, full-time"
    assert ae.description == "" and ae.location == "Remote (United States)"       # not relevant: no detail
    assert sum(1 for m, u in h.calls if RIP_UUID in u) == 1


def test_rippling_unknown_board_is_source_error():
    from jobsearch.adapters.base import SourceError
    from jobsearch.adapters.rippling import RipplingAdapter
    with pytest.raises(SourceError):
        RipplingAdapter(mock_client(router({}))).fetch(Company(name="X", source="rippling", board="nope"), FetchContext())


JV_PAGE0 = """<table class="jv-job-list"><tbody>
<tr><td class="jv-job-list-name"><a href="/democo/job/oAAAA1">Software Engineer (DEMO)</a></td>
    <td class="jv-job-list-location">  Boston,
        Massachusetts </td></tr>
<tr><td class="jv-job-list-name"><a href="/democo/job/oBBBB2">Account Executive (DEMO)</a></td>
    <td class="jv-job-list-location">Chicago, Illinois</td></tr>
</tbody></table><div class="jv-pagination"><a href="/democo/search/?p=1" class="jv-pagination-next">Next</a></div>"""
JV_PAGE1 = """<a href="/democo/job/oCCCC3"><div class="title">Software Engineering Intern (DEMO)</div>
<div class="jv-job-list-location">United States</div><div class="arrow"><i></i></div></a>"""
JV_DETAIL = """<html><script type="application/ld+json">{"@context": "http://schema.org", "@type": "JobPosting",
"title": "Software Engineer (DEMO)", "datePosted": "2026-09-24", "employmentType": "Full-time",
"identifier": "oAAAA1", "hiringOrganization": "DemoCo", "description": "<p>DEMO</p>%s",
"jobLocation": [{"@type": "Place", "address": {"addressLocality": "Boston", "addressRegion": "MA",
"addressCountry": "United States"}}]}</script></html>""" % REQ.replace('"', '\\"')


def test_jobvite_listing_parser_handles_table_and_card_themes():
    from jobsearch.adapters.jobvite import parse_listing
    assert parse_listing(JV_PAGE0, "democo") == {"oAAAA1": ("Software Engineer (DEMO)", "Boston, Massachusetts"),
                                                 "oBBBB2": ("Account Executive (DEMO)", "Chicago, Illinois")}
    assert parse_listing(JV_PAGE1, "democo") == {"oCCCC3": ("Software Engineering Intern (DEMO)", "United States")}


def test_jobvite_adapter_paginates_and_reads_jsonld_details(cfg):
    from jobsearch.adapters.jobvite import JobviteAdapter
    from jobsearch.match.rules import quick_relevant
    h = router({("GET", "robots.txt"): 404, ("GET", "/democo/search?nl=1&p=0"): JV_PAGE0,
                ("GET", "/democo/search?nl=1&p=1"): JV_PAGE1, ("GET", "/democo/job/oAAAA1"): JV_DETAIL,
                ("GET", "/democo/job/oCCCC3"): "<html>no structured data</html>"})
    ctx = FetchContext(want=lambda t, loc: quick_relevant(t, loc, cfg.prefs, cfg.title_rules))
    res = JobviteAdapter(mock_client(h)).fetch(Company(name="DemoCo", source="jobvite", board="democo"), ctx)
    by_id = {j.job_id: j for j in res.jobs}
    assert set(by_id) == {"oAAAA1", "oBBBB2", "oCCCC3"} and res.partial is None
    swe = by_id["oAAAA1"]
    assert "- Python and SQL" in swe.description and swe.posted_date.day == 24
    assert swe.location == "Boston, MA, United States" and swe.apply_url == "https://jobs.jobvite.com/democo/job/oAAAA1"
    assert swe.canonical_key == "jobvite:democo:oAAAA1"
    assert by_id["oBBBB2"].description == ""                          # not relevant: no detail fetch
    assert by_id["oCCCC3"].description == "" and by_id["oCCCC3"].location == "United States"
    assert not any("p=2" in u for _, u in h.calls)                     # no "next" link on page 1


def test_jobvite_respects_robots():
    from jobsearch.adapters.base import SourceError
    from jobsearch.adapters.jobvite import JobviteAdapter
    h = router({("GET", "robots.txt"): "User-agent: *\nDisallow: /\n"})
    with pytest.raises(SourceError, match="robots.txt"):
        JobviteAdapter(mock_client(h)).fetch(Company(name="DemoCo", source="jobvite", board="democo"), FetchContext())
    assert not any("/democo/" in u for _, u in h.calls)


def _ef_pos(i, name, loc="United States, Massachusetts, Cambridge"):
    return {"id": 1000 + i, "displayJobId": f"R{i}", "name": name, "locations": [loc], "postedTs": 1791676800,
            "positionUrl": f"/careers/job/{1000 + i}", "department": "Engineering"}


def test_eightfold_small_site_lists_everything_with_details(cfg):
    from jobsearch.adapters.eightfold import EightfoldAdapter
    from jobsearch.match.rules import quick_relevant
    pages = {0: [_ef_pos(i, "Software Engineer (DEMO)" if i == 0 else f"Sales Lead {i} (DEMO)") for i in range(10)],
             10: [_ef_pos(10, "Data Engineer (DEMO)")]}

    def search(request):
        start = int(request.url.params["start"])
        assert request.url.params["location"] == "United States" and request.url.params["query"] == ""
        return httpx.Response(200, json={"status": 200, "data": {"count": 11, "positions": pages.get(start, [])}})
    detail = {"status": 200, "data": {"id": "1000", "jobDescription": "<p>DEMO</p>" + REQ,
                                      "publicUrl": "https://democo.eightfold.ai/careers/job/1000",
                                      "efcustomTextEmploymentType": ["Full-Time"]}}
    h = router({("GET", "/api/pcsx/search"): search, ("GET", "position_id=1000&"): detail,
                ("GET", "/api/pcsx/position_details"): {"status": 200, "data": {"id": "x", "jobDescription": ""}}})
    ctx = FetchContext(want=lambda t, loc: quick_relevant(t, loc, cfg.prefs, cfg.title_rules))
    res = EightfoldAdapter(mock_client(h)).fetch(
        Company(name="DemoCo", source="eightfold", host="democo.eightfold.ai", board="democo.com"), ctx)
    assert len(res.jobs) == 11 and res.partial is None
    swe = next(j for j in res.jobs if j.job_id == "1000")
    assert "- Python and SQL" in swe.description and swe.employment_type == "Full-Time"
    assert swe.apply_url == "https://democo.eightfold.ai/careers/job/1000" and swe.posted_date.year == 2026
    assert swe.canonical_key == "eightfold:democo.com:1000"
    assert next(j for j in res.jobs if j.job_id == "1001").description == ""     # not relevant: no detail
    assert sum(1 for _, u in h.calls if "/api/pcsx/search" in u) == 2           # first page reused


def test_eightfold_big_site_searches_role_keywords_and_reports_caps():
    from jobsearch.adapters.eightfold import EightfoldAdapter
    queries = []

    def search(request):
        q = request.url.params["query"]
        queries.append(q)
        count = 5000 if q == "" else 30
        return httpx.Response(200, json={"data": {"count": count, "positions": [
            _ef_pos(hash(q) % 1000 + int(request.url.params["start"]), f"{q or 'x'} role")]}})
    h = router({("GET", "/api/pcsx/search"): search})
    ctx = FetchContext(full=False, max_pages=2, queries={"eightfold": ["software engineer"]})
    res = EightfoldAdapter(mock_client(h)).fetch(
        Company(name="BigCo", source="eightfold", host="careers.bigco.example", board="bigco.com"), ctx)
    assert set(queries) == {"", "software engineer"} and len(res.jobs) == 2
    assert "capped at 20 of 30" in res.partial


def test_eightfold_pcsx_not_enabled_is_source_error():
    from jobsearch.adapters.base import SourceError
    from jobsearch.adapters.eightfold import EightfoldAdapter
    h = router({("GET", "/api/pcsx/search"): lambda r: httpx.Response(403, json={"message": "Not authorized for PCSX"})})
    with pytest.raises(SourceError):
        EightfoldAdapter(mock_client(h)).fetch(
            Company(name="OldCo", source="eightfold", host="oldco.eightfold.ai", board="oldco.com"), FetchContext())


@pytest.mark.parametrize("url,fields", [
    ("https://ats.rippling.com/tive-careers/jobs", {"source": "rippling", "board": "tive-careers"}),
    (f"https://ats.rippling.com/en-GB/democo/jobs/{RIP_UUID}", {"source": "rippling", "board": "democo"}),
    ("https://jobs.jobvite.com/careers/imprivata/jobs", {"source": "jobvite", "board": "imprivata"}),
    ("https://jobs.jobvite.com/democo/job/oAAAA1", {"source": "jobvite", "board": "democo"}),
    ("https://apply.careers.microsoft.com/careers?domain=microsoft.com",
     {"source": "eightfold", "host": "apply.careers.microsoft.com", "board": "microsoft.com"}),
    ("https://democo.eightfold.ai/careers?domain=democo.com",
     {"source": "eightfold", "host": "democo.eightfold.ai", "board": "democo.com"}),
    ("https://explore.jobs.netflix.net/careers?domain=netflix.com", {"source": "netflix"}),
])
def test_new_ats_detection_from_url(url, fields):
    assert from_url(url) == fields


def test_careers_page_embeds_new_ats():
    page = '<iframe src="https://jobs.jobvite.com/__assets__/x.js"></iframe><a href="https://ats.rippling.com/democo/jobs">Jobs</a>'
    h = router({("GET", "robots.txt"): 404, ("GET", "democo.example"): page})
    r = Detector(mock_client(h), [], {}).detect("DemoCo", "https://democo.example/careers")
    assert r.via == "careers page" and r.company.source == "rippling" and r.company.board == "democo"


def test_changed_careers_url_redetects_cached_company():
    d = Detector(mock_client(router({})), [], {})
    cached = {"source": "jsonld", "careers_url": "https://old.example/careers", "fail_count": 0, "detected_via": "json-ld"}
    assert d.detect("DemoCo", "https://old.example/careers", cached).via == "cache"
    r = d.detect("DemoCo", "https://jobs.jobvite.com/democo/jobs", cached)
    assert r.via == "careers_url" and r.company.source == "jobvite"
    # unchanged URL, but it is a board: it beats an old cached guess (Imprivata was cached as jsonld)
    stale = {**cached, "careers_url": "https://jobs.jobvite.com/careers/democo/jobs"}
    assert d.detect("DemoCo", "https://jobs.jobvite.com/careers/democo/jobs", stale).company.source == "jobvite"


def test_manual_by_brand_survives_alias_merge(tmp_path):
    from jobsearch.watchlist import resolve
    aliases = tmp_path / "aliases.yaml"
    aliases.write_text("aliases:\n  \"ITA Software LLC\": Google\nmanual:\n  Google: \"custom site\"\n")
    items = parse_watchlist("company,careers_url,notes\nITA Software LLC,,a\nGoogle,https://careers.google.example,b\n")
    (g,) = resolve(items, aliases)
    assert g.company == "Google" and g.manual_reason == "custom site" and g.careers_url == "https://careers.google.example"


def test_eightfold_respects_robots():
    from jobsearch.adapters.base import SourceError
    from jobsearch.adapters.eightfold import EightfoldAdapter
    allow = router({("GET", "robots.txt"): "User-agent: *\nDisallow: /\nAllow: /api/pcsx\n",
                    ("GET", "/api/pcsx/search"): {"data": {"count": 0, "positions": []}}})
    co = Company(name="DemoCo", source="eightfold", host="democo.eightfold.ai", board="democo.com")
    assert EightfoldAdapter(mock_client(allow)).fetch(co, FetchContext()).jobs == []
    deny = router({("GET", "robots.txt"): "User-agent: *\nDisallow: /\n"})
    with pytest.raises(SourceError, match="robots.txt"):
        EightfoldAdapter(mock_client(deny)).fetch(co, FetchContext())
    assert not any("/api/pcsx" in u for _, u in deny.calls)


# --- conditional (ETag / 304) listings: SmartRecruiters, Amazon ------------------------------------
def _etag_server(bodies: dict[str, dict], etag='W/"v1"'):
    """frag -> JSON body; answers 304 to If-None-Match == etag."""
    calls = []

    def handler(request: httpx.Request):
        calls.append((str(request.url), request.headers.get("if-none-match")))
        for frag, body in bodies.items():
            if frag in str(request.url):
                if request.headers.get("if-none-match") == etag:
                    return httpx.Response(304)
                return httpx.Response(200, json=body, headers={"etag": etag})
        return httpx.Response(404, json={})
    handler.calls = calls
    return handler


def test_smartrecruiters_unchanged_listing_is_304_only():
    q = {"smartrecruiters": ["software", "intern"]}
    co = Company(name="DemoCo", source="smartrecruiters", board="DemoCo")
    h = _etag_server({"q=software": SR_LIST, "q=intern": {"totalFound": 0, "content": []}, "/postings/744": SR_DETAIL})
    first = SmartRecruitersAdapter(mock_client(h)).fetch(co, FetchContext(queries=q))
    assert len(first.jobs) == 1 and len(first.validators) == 2
    # next run: both pages answer 304 -> the whole listing is unchanged, no detail requests
    h2 = _etag_server({"q=software": SR_LIST, "q=intern": {"totalFound": 0, "content": []}, "/postings/744": SR_DETAIL})
    res = SmartRecruitersAdapter(mock_client(h2)).fetch(co, FetchContext(queries=q, validators=first.validators))
    assert res.unchanged and all(inm for _, inm in h2.calls) and not any("/postings/744" in u for u, _ in h2.calls)


def test_smartrecruiters_partly_changed_listing_refetches_304_pages():
    q = {"smartrecruiters": ["software", "intern"]}
    co = Company(name="DemoCo", source="smartrecruiters", board="DemoCo")
    intern = {"totalFound": 1, "content": [{**SR_LIST["content"][0], "id": "744000000000002", "name": "Software Engineering Intern (DEMO)"}]}

    def handler(request: httpx.Request):
        u, inm = str(request.url), request.headers.get("if-none-match")
        if "q=software" in u:
            return httpx.Response(304) if inm == 'W/"v1"' else httpx.Response(200, json=SR_LIST, headers={"etag": 'W/"v1"'})
        if "q=intern" in u:
            return httpx.Response(200, json=intern, headers={"etag": 'W/"v2"'})     # changed since last run
        if "/postings/" in u:
            return httpx.Response(200, json=SR_DETAIL)
        return httpx.Response(404, json={})
    url_sw = "https://api.smartrecruiters.com/v1/companies/DemoCo/postings?limit=100&offset=0&country=us&q=software"
    url_in = "https://api.smartrecruiters.com/v1/companies/DemoCo/postings?limit=100&offset=0&country=us&q=intern"
    known = {url_sw: {"etag": 'W/"v1"'}, url_in: {"etag": 'W/"old"'}}
    res = SmartRecruitersAdapter(mock_client(handler)).fetch(co, FetchContext(queries=q, validators=known))
    assert not res.unchanged and {j.job_id for j in res.jobs} == {"744000000000001", "744000000000002"}
    assert res.validators[url_in]["etag"] == 'W/"v2"' and res.validators[url_sw]["etag"] == 'W/"v1"'


def test_amazon_unchanged_listing_is_304_only():
    from jobsearch.adapters.amazon import AmazonAdapter
    body = {"hits": 0, "jobs": []}
    q = {"amazon": [{"base_query": "intern"}]}
    h = _etag_server({"search.json": body})
    first = AmazonAdapter(mock_client(h)).fetch(Company(name="Amazon", source="amazon"), FetchContext(queries=q))
    assert not first.unchanged and first.validators
    h2 = _etag_server({"search.json": body})
    res = AmazonAdapter(mock_client(h2)).fetch(Company(name="Amazon", source="amazon"), FetchContext(queries=q, validators=first.validators))
    assert res.unchanged
