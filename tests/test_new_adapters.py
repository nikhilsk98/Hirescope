"""Job systems added in the coverage audit (2026-10-20): Oracle Recruiting Cloud, BambooHR, JazzHR, Paylocity, Avature,
SuccessFactors (sitemap + schema.org microdata, RSS sitemaps), Radancy job URLs; Workday's relevance stop and 429
handling. Mock HTTP only; all data is DEMO."""
import json

import httpx

from jobsearch.adapters.base import FetchContext
from jobsearch.adapters.canonical import canonical_key
from jobsearch.adapters.detect import from_url
from jobsearch.adapters.jsonld import SitemapJsonLdAdapter, _slug_title, jobpostings_in_html
from jobsearch.adapters.oracle_cloud import OracleCloudAdapter
from jobsearch.adapters.smallats import AvatureAdapter, BambooHRAdapter, JazzHRAdapter, PaylocityAdapter
from jobsearch.adapters.workday import WorkdayAdapter
from jobsearch.config import Company

from .conftest import mock_client

ROBOTS_OK = httpx.Response(404)
want_swe = lambda t, loc: "software" in (t or "").lower() or "engineer" in (t or "").lower()  # noqa: E731


def routed(routes: dict):
    """path substring -> response (or callable(request) -> response); robots.txt is absent (allowed)."""
    def handler(req: httpx.Request):
        if req.url.path == "/robots.txt":
            return ROBOTS_OK
        for key, resp in routes.items():
            if key in str(req.url):
                return resp(req) if callable(resp) else resp
        return httpx.Response(404)
    return handler


# --- Oracle Recruiting Cloud -----------------------------------------------------------------------------------------
def test_oracle_cloud_us_facet_search_and_detail():
    seen = []

    def search(req):
        seen.append(str(req.url))
        if "facetsList" in str(req.url) and "limit=1," in str(req.url):
            return httpx.Response(200, json={"items": [{"TotalJobsCount": 2, "locationsFacet": [
                {"Id": 111, "Name": "United Kingdom"}, {"Id": 300, "Name": "United States"}]}]})
        return httpx.Response(200, json={"items": [{"TotalJobsCount": 2, "requisitionList": [
            {"Id": "77", "Title": "Software Engineer I (DEMO)", "PostedDate": "2026-10-14", "PrimaryLocation": "Boston, MA, United States"},
            {"Id": "78", "Title": "Payroll Specialist (DEMO)", "PostedDate": "2026-10-14", "PrimaryLocation": "Boston, MA, United States"}]}]})
    detail = httpx.Response(200, json={"items": [{"Title": "Software Engineer I (DEMO)", "ExternalDescriptionStr": "<p>Build APIs.</p>",
                                                  "ExternalQualificationsStr": "<ul><li>Python</li></ul>",
                                                  "ExternalPostedStartDate": "2026-10-14T22:16:04+00:00", "JobSchedule": "Full time"}]})
    c = mock_client(routed({"recruitingCEJobRequisitionDetails": detail, "recruitingCEJobRequisitions": search}))
    co = Company(name="DemoBank", source="oracle_cloud", host="demo.fa.us2.oraclecloud.com", site="CX_1")
    res = OracleCloudAdapter(c).fetch(co, FetchContext(want=want_swe, queries={"oracle_cloud": ["software"]}))
    by = {j.job_id: j for j in res.jobs}
    assert set(by) == {"77", "78"} and "Build APIs." in by["77"].description and "Python" in by["77"].description
    assert by["78"].description == ""                         # not relevant: no detail request
    assert by["77"].apply_url == "https://demo.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/77"
    assert any("locationId=300" in u for u in seen)            # the site's own United States facet
    assert canonical_key(by["77"].apply_url) == by["77"].canonical_key == "oracle_cloud:demo:77"
    assert from_url(by["77"].apply_url) == {"source": "oracle_cloud", "host": "demo.fa.us2.oraclecloud.com", "site": "CX_1"}


def test_oracle_cloud_stops_when_relevance_runs_out():
    calls = []

    def search(req):
        calls.append(1)
        n = len(calls)
        title = "Software Engineer" if n <= 2 else "Branch Teller"
        return httpx.Response(200, json={"items": [{"TotalJobsCount": 5000, "requisitionList": [
            {"Id": f"{n}-{i}", "Title": f"{title} {i}", "PrimaryLocation": "NY, United States"} for i in range(25)]}]})
    c = mock_client(routed({"recruitingCEJobRequisitions": search}))
    co = Company(name="DemoBank", source="oracle_cloud", host="demo.fa.us2.oraclecloud.com", site="CX_1")
    res = OracleCloudAdapter(c).fetch(co, FetchContext(want=want_swe, max_details=0, queries={"oracle_cloud": ["x"]}))
    assert len(calls) == 1 + 1 + 3 and res.partial is None        # facets, one relevant page, then 3 stale pages


# --- BambooHR / JazzHR / Paylocity -----------------------------------------------------------------------------------
def test_bamboohr_list_and_detail():
    c = mock_client(routed({
        "/careers/list": httpx.Response(200, json={"result": [{"id": "5", "jobOpeningName": "Software Engineer (DEMO)",
                                                               "location": {"city": "Boston", "state": "Massachusetts"}},
                                                              {"id": "6", "jobOpeningName": "Office Manager", "location": {}}]}),
        "/careers/5/detail": httpx.Response(200, json={"result": {"jobOpening": {
            "jobOpeningName": "Software Engineer (DEMO)", "description": "<p>Go and AWS.</p>", "datePosted": "2026-10-01",
            "location": {"city": "Boston", "state": "Massachusetts"}}}})}))
    res = BambooHRAdapter(c).fetch(Company(name="DemoCo", source="bamboohr", board="democo"), FetchContext(want=want_swe))
    j = {x.job_id: x for x in res.jobs}
    assert j["5"].description == "Go and AWS." and j["5"].location == "Boston, Massachusetts" and j["6"].description == ""
    assert j["5"].apply_url == "https://democo.bamboohr.com/careers/5" and canonical_key(j["5"].apply_url) == j["5"].canonical_key


LD = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Backend Engineer (DEMO)", "datePosted": '
      '"2026-09-02", "description": "<p>Python services.</p>", "jobLocation": {"@type": "Place", "address": '
      '{"addressLocality": "Boston", "addressRegion": "MA"}}}</script>')


def test_jazzhr_listing_links_and_json_ld_pages_on_its_own_domain():
    page = ('<a href="https://careers.democo.example/apply/AbC123xyz9/Backend-Engineer">x</a>'
            '<a href="https://other.applytojob.com/apply/ZZZZZZZZ/Elsewhere">y</a>')
    c = mock_client(routed({"/apply/AbC123xyz9": httpx.Response(200, text=LD), "/apply": httpx.Response(200, text=page)}))
    res = JazzHRAdapter(c).fetch(Company(name="DemoCo", source="jazzhr", board="careers.democo.example"), FetchContext(want=want_swe))
    assert [(j.job_id, j.title, j.location) for j in res.jobs] == [("AbC123xyz9", "Backend Engineer (DEMO)", "Boston, MA")]
    assert from_url("https://democo.applytojob.com/apply") == {"source": "jazzhr", "board": "democo"}


def test_paylocity_page_data_then_json_ld():
    html = ('<script>window.pageData = {"Jobs":[{"JobId":4590421,"JobTitle":"Embedded Software Engineer","LocationName":'
            '"BOSTON OFFICE","PublishedDate":"2026-10-15T15:20:00"}]};</script>')
    c = mock_client(routed({"/Jobs/Details/4590421": httpx.Response(200, text=LD), "/jobs/All/": httpx.Response(200, text=html)}))
    co = Company(name="DemoGrid", source="paylocity",
                 careers_url="https://recruiting.paylocity.com/recruiting/jobs/All/e85e8980-8325-4e41-9404-be920804d97d/Demo")
    res = PaylocityAdapter(c).fetch(co, FetchContext(want=want_swe))
    assert [(j.job_id, j.description) for j in res.jobs] == [("4590421", "Python services.")]
    assert res.jobs[0].canonical_key == canonical_key("https://recruiting.paylocity.com/Recruiting/Jobs/Details/4590421")


# --- Avature -----------------------------------------------------------------------------------------------------------
def test_avature_follows_the_portals_own_pagination():
    def listing(req):
        off = int(httpx.URL(str(req.url)).params.get("folderOffset", 0))
        items = [(off + i, f"Software Engineer {off + i}") for i in range(2)] if off < 4 else []
        body = "".join(f'<a class="link" href="https://jobs.demo.example/en_US/ext/JobDetail/{i}">{t}</a>'
                       f'<span class="list-item-location">Boston, Massachusetts, United States</span>' for i, t in items)
        nxt = f'<a href="https://jobs.demo.example/en_US/ext/SearchJobs/Demo?folderRecordsPerPage=2&amp;folderOffset={off + 2}">Next</a>'
        return httpx.Response(200, text=body + nxt)
    detail = httpx.Response(200, text='<div class="article__content">Location(s)\n<p>- Boston - Massachusetts - United States</p>'
                                      '<p>Build Java services.</p></div><footer>')
    c = mock_client(routed({"/JobDetail/": detail, "/SearchJobs": listing}))
    co = Company(name="DemoInd", source="avature", careers_url="https://jobs.demo.example/en_US/ext/SearchJobs/?search=Demo")
    res = AvatureAdapter(c).fetch(co, FetchContext(want=want_swe, max_details=1))
    assert sorted(j.job_id for j in res.jobs) == ["0", "1", "2", "3"]
    assert sum(bool(j.description) for j in res.jobs) == 1 and all(j.location for j in res.jobs)
    assert canonical_key(res.jobs[0].apply_url) == res.jobs[0].canonical_key


# --- SuccessFactors / Radancy sitemaps ---------------------------------------------------------------------------------
MICRO = ('<div itemscope itemtype="http://schema.org/JobPosting"><h1><span itemprop="title">Software Engineer - Early '
         'Career</span></h1><meta itemprop="datePosted" content="Thu Oct 15 00:00:00 UTC 2026">'
         '<span itemprop="jobLocation" itemscope><span itemprop="address" itemscope><meta itemprop="addressLocality" '
         'content="Boston"><meta itemprop="addressRegion" content="MA"><meta itemprop="addressCountry" content="US"></span>'
         '</span><span itemprop="description"><p>Build <b>cloud</b> services.</p><br><p>Java.</p></span></div>')


def test_microdata_job_posting():
    d = jobpostings_in_html(MICRO)[0]
    assert d["title"] == "Software Engineer - Early Career" and "cloud" in d["description"] and "Java." in d["description"]
    assert d["jobLocation"]["address"]["addressLocality"] == "Boston"


def test_successfactors_sitemap_google_namespace_and_id_ending_urls():
    sm = ('<?xml version="1.0"?><?xml-stylesheet type="text/xsl" href="x.xsl"?><urlset xmlns="http://www.google.com/'
          'schemas/sitemap/0.9"><url><loc>https://jobs.demo.example/job/Boston-Software-Engineer-Early-Career/1440363800/'
          '</loc><lastmod>2026-10-15</lastmod></url><url><loc>https://jobs.demo.example/job/Boston-Payroll-Clerk/1440363801/'
          '</loc></url></urlset>')
    c = mock_client(routed({"/sitemap.xml": httpx.Response(200, text=sm), "/job/Boston-Software": httpx.Response(200, text=MICRO)}))
    res = SitemapJsonLdAdapter(c).fetch(Company(name="DemoSF", source="jsonld_sitemap", board="https://jobs.demo.example/sitemap.xml",
                                                site="/job/"), FetchContext(want=want_swe))
    assert [(j.job_id, j.location) for j in res.jobs] == [("1440363800", "Boston, MA, US")] and len(res.seen_ids) == 2
    assert res.jobs[0].posted_date.date().isoformat() == "2026-10-15"


def test_rss_sitemap_items_are_postings():
    rss = ('<?xml version="1.0"?><rss version="2.0"><channel><item><title>Cloud Engineer (Boston, MA, US)</title><link>'
           'https://careers.demo.example/job/Boston-Cloud-Engineer/1320264500/</link><description><![CDATA[&lt;p&gt;AWS and '
           'Terraform.&lt;/p&gt;]]></description></item></channel></rss>')
    c = mock_client(routed({"/sitemap.xml": httpx.Response(200, text=rss)}))
    res = SitemapJsonLdAdapter(c).fetch(Company(name="DemoData", source="jsonld_sitemap", board="https://careers.demo.example/sitemap.xml",
                                                site="/job/"), FetchContext(want=want_swe))
    assert [(j.title, j.location, j.description) for j in res.jobs] == [("Cloud Engineer", "Boston, MA, US", "AWS and Terraform.")]


def test_slug_title_skips_trailing_ids():
    assert _slug_title("https://jobs.demo.example/job/boston/software-engineer/27595/102189634112") == "software engineer"
    assert _slug_title("https://jobs.demo.example/job/Boston-Software-Engineer/1440363800/") == "Boston Software Engineer"


# --- Workday: relevance stop, tier pages, 429 keeps what was found -----------------------------------------------------
def _workday_client(pages_title, status=None):
    calls = []

    def jobs(req):
        body = json.loads(req.content or b"{}")
        calls.append(body)
        if body.get("limit") == 1:
            return httpx.Response(200, json={"total": 1, "jobPostings": [], "facets": []})
        if status and len(calls) > 3:
            return httpx.Response(status)
        off = body["offset"]
        return httpx.Response(200, json={"total": 5000, "jobPostings": [
            {"title": pages_title(off // 20), "externalPath": f"/job/x/Job_{off + i}", "locationsText": "Boston, MA"}
            for i in range(20)]})
    return mock_client(routed({"/jobs": jobs})), calls


def test_workday_keyword_search_stops_after_irrelevant_pages_and_uses_tier_pages():
    c, calls = _workday_client(lambda page: "Software Engineer" if page < 15 else "Nurse")
    co = Company(name="DemoW", source="workday", host="demo.wd1.myworkdayjobs.com", board="demo", site="Ext")
    res = WorkdayAdapter(c).fetch(co, FetchContext(want=want_swe, full=False, max_pages=25, max_details=0,
                                                   queries={"workday": ["software"]}))
    searched = [x for x in calls if x.get("limit") == 20]
    assert len(searched) == 15 + 3 and res.partial is None     # past the old 10-page cap; stops 3 pages after the last hit


def test_workday_rate_limit_keeps_results_and_says_so():
    c, calls = _workday_client(lambda page: "Software Engineer", status=429)
    co = Company(name="DemoW", source="workday", host="demo.wd1.myworkdayjobs.com", board="demo", site="Ext")
    res = WorkdayAdapter(c).fetch(co, FetchContext(want=want_swe, full=False, max_pages=25, max_details=0,
                                                   queries={"workday": ["software", "data engineer"]}))
    assert len(res.jobs) == 40 and "rate-limited (HTTP 429)" in res.partial
