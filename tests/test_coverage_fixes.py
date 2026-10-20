"""Coverage-audit fixes (2026-10-20): SimplifyJobs listings get their JD after a capped run (and when Simplify edits
them), its sponsorship field when there is no JD; different requisitions are never fuzzy-merged and old wrong merges
come apart; Eightfold / myworkdaysite posting keys. All data is DEMO."""
import httpx

from jobsearch.adapters.base import FetchContext, RawJob
from jobsearch.adapters.canonical import canonical_key, normalized
from jobsearch.adapters.simplify_github import SimplifyAdapter
from jobsearch.config import Company
from jobsearch.pipeline import to_record
from jobsearch.storage import db

from .conftest import mock_client
from .test_sources import LEVER, SIMPLIFY, router

FEED = Company(name="SimplifyJobs Internships", source="simplify", board="https://raw/listings.json", site="coop_intern")


def _want(cfg):
    from jobsearch.match.rules import quick_relevant
    return lambda t, loc: quick_relevant(t, loc, cfg.prefs, cfg.title_rules)


def test_simplify_listing_left_out_by_a_capped_run_gets_its_jd_later(cfg):
    h = router({("GET", "listings.json"): SIMPLIFY, ("GET", "api.lever.co/v0/postings/democo/"): LEVER[0]})
    # known from an earlier run, but never enriched (no detail time): it is enriched now
    res = SimplifyAdapter(mock_client(h)).fetch(FEED, FetchContext(want=_want(cfg), known={"s1": None}))
    assert "- Python" in {j.job_id: j for j in res.jobs}["s1"].description
    # enriched yesterday: nothing to do (the stored JD stays: description None)
    h2 = router({("GET", "listings.json"): SIMPLIFY})
    res2 = SimplifyAdapter(mock_client(h2)).fetch(FEED, FetchContext(want=_want(cfg), known={"s1": "2026-10-19T00:00:00+00:00"}))
    assert {j.job_id: j for j in res2.jobs}["s1"].description is None and not any("lever" in u for _, u in h2.calls)


def test_simplify_edit_after_the_last_fetch_is_re_read(cfg):
    edited = [{**SIMPLIFY[0], "date_updated": 1792700000}, *SIMPLIFY[1:]]           # 2026-10-21, after the fetch
    h = router({("GET", "listings.json"): edited, ("GET", "api.lever.co/v0/postings/democo/"): LEVER[0]})
    res = SimplifyAdapter(mock_client(h)).fetch(FEED, FetchContext(want=_want(cfg), known={"s1": "2026-10-19T00:00:00+00:00"}))
    assert {j.job_id: j for j in res.jobs}["s1"].description


def test_simplify_links_to_other_pages_are_read_for_json_ld_but_not_unreadable_sites(cfg):
    page = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Software Engineer Intern (DEMO)", '
            '"description": "<p>C++ and Python.</p>"}</script>')
    feed = [{**SIMPLIFY[0], "id": "j1", "url": "https://careers.demochips.example/careers-home/jobs/91180"},
            {**SIMPLIFY[0], "id": "t1", "url": "https://www.tesla.com/careers/search/job/1"}]
    h = router({("GET", "listings.json"): feed, ("GET", "robots.txt"): "User-agent: *\nAllow: /\n",
                ("GET", "careers-home/jobs/91180"): page})
    res = SimplifyAdapter(mock_client(h)).fetch(FEED, FetchContext(want=_want(cfg)))
    by = {j.job_id: j for j in res.jobs}
    assert "C++ and Python." in by["j1"].description and by["t1"].description == ""
    assert not any("tesla.com" in u for _, u in h.calls)


def test_simplify_sponsorship_field_fills_an_unstated_flag(cfg):
    base = dict(company="DemoCo", job_id="x", title="Software Engineer Intern (DEMO)", source="simplify_internships",
                source_url="", apply_url=None)
    rec = to_record(RawJob(**base, description="", extra={"simplify_sponsorship": "U.S. Citizenship is Required"}), cfg, None)
    assert rec.flags["citizenship"]["value"] == "US citizenship required" and "SimplifyJobs" in rec.flags["citizenship"]["evidence"]
    rec = to_record(RawJob(**base, description="", extra={"simplify_sponsorship": "Does Not Offer Sponsorship"}), cfg, None)
    assert rec.flags["sponsorship"]["value"] == "No sponsorship"
    jd = "We sponsor visas: visa sponsorship is available for this role."    # the JD decides when it says something
    rec = to_record(RawJob(**base, description=jd, extra={"simplify_sponsorship": "Does Not Offer Sponsorship"}), cfg, None)
    assert rec.flags["sponsorship"]["value"] == "Sponsorship available"


def _rec(cfg, source, job_id, title, loc, url):
    return to_record(RawJob(company="DemoCo", job_id=job_id, title=title, source=source, source_url="", apply_url=url,
                            location=loc, description="Build services in Python.", canonical_key=canonical_key(url)), cfg, None)


def test_different_requisitions_are_not_fuzzy_merged(conn, cfg):
    a, _ = db.upsert_job(conn, _rec(cfg, "workday", "intern_r1", "Analytics Intern", "Cincinnati, OH",
                                    "https://demo.wd5.myworkdayjobs.com/Ext/job/Cincinnati/Analytics-Intern_R000157697"))
    b, _ = db.upsert_job(conn, _rec(cfg, "simplify_internships", "s9", "Analytics Intern", "Cincinnati, OH",
                                    "https://demo.wd5.myworkdayjobs.com/Ext/job/Cincinnati/Analytics-Intern_R000157700"))
    c, _ = db.upsert_job(conn, _rec(cfg, "simplify_newgrad", "s10", "Analytics Intern", "Cincinnati, OH",
                                    "https://demo.wd5.myworkdayjobs.com/Ext/job/Cincinnati/Analytics-Intern_R000157697-1"))
    assert a != b and c == a          # a different requisition stays apart; the same req re-posted (-1) joins
    # an unknown location matches only an identical title
    d, _ = db.upsert_job(conn, _rec(cfg, "linkedin_alerts", "l1", "Analytics Manager", None, None))
    assert d not in (a, b)


def test_wrong_merges_come_apart(conn, cfg):
    a, _ = db.upsert_job(conn, _rec(cfg, "smartrecruiters", "3743990015679386", "Data Intern", "Chicago, IL",
                                    "https://jobs.smartrecruiters.com/DemoCo/3743990015679386"))
    conn.execute("INSERT INTO job_sources (job_pk, source, company_key, source_job_id, first_seen, last_seen, canonical_key) "
                 "VALUES (?, 'simplify_internships', 'democo', 's1', 'x', 'x', 'smartrecruiters:3743990014697918')", (a,))
    conn.execute("INSERT INTO job_sources (job_pk, source, company_key, source_job_id, first_seen, last_seen, canonical_key) "
                 "VALUES (?, 'simplify_newgrad', 'democo', 's2', 'x', 'x', 'smartrecruiters:3743990015679386')", (a,))
    assert db.split_wrong_merges(conn) == 1
    assert {r[0] for r in conn.execute("SELECT source_job_id FROM job_sources WHERE job_pk=?", (a,))} == \
        {"3743990015679386", "s2"}
    assert normalized("workday:kla:engr--software-1_2642693-1") == "workday:kla:engr--software-1_2642693"


def test_eightfold_and_myworkdaysite_posting_keys():
    assert canonical_key("https://apply.careers.microsoft.com/careers/job/1970393556985571") == "eightfold:microsoft.com:1970393556985571"
    assert canonical_key("https://demo.eightfold.ai/careers/job/77?domain=demo.example") == "eightfold:demo.example:77"
    assert canonical_key("https://wd1.myworkdaysite.com/recruiting/baird/BairdCareers/job/Milwaukee/Intern_R20261024-1") == \
        "workday:baird:intern_r20261024"
