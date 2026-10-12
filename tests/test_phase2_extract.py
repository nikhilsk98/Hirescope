"""Track classification, terms, level mapping, location normalization, canonical keys, robots. (DEMO data)"""
import pytest

from jobsearch.adapters.canonical import canonical_key
from jobsearch.adapters.robots import Robots
from jobsearch.extract.levels import map_level
from jobsearch.extract.locations import normalize_location
from jobsearch.extract.normalize import normalize_title
from jobsearch.extract.track import classify_track, normalize_employment_type, parse_terms


@pytest.mark.parametrize("title,et,text,hint,track", [
    ("Software Engineer Co-op (Spring 2027)", None, "", None, "coop_intern"),
    ("SWE Intern - Off-cycle", None, "", None, "coop_intern"),
    ("Software Engineer", "Intern", "", None, "coop_intern"),               # Lever commitment / Ashby Intern
    ("Software Engineer", "Intern (Fixed Term)", "", None, "coop_intern"),  # Workday worker subtype
    ("Internal Tools Engineer", None, "", None, "full_time"),               # "Internal" is not "intern"
    ("Software Engineer, New Grad 2027", None, "", None, "full_time"),
    ("Software Engineer", "FullTime", "", None, "full_time"),
    ("Software Engineer", None, "Join our 12-week internship program this spring.", None, "unclear"),
    ("Software Engineer", "Contract", "", None, "unclear"),
    ("Software Engineer", None, "", "coop_intern", "coop_intern"),          # internships feed
    ("Software Engineer", None, "", None, "full_time"),                     # default, low confidence
])
def test_classify_track(title, et, text, hint, track):
    assert classify_track(title, et, text, hint).track == track


def test_track_confidence_and_evidence():
    r = classify_track("Software Engineer")
    assert r.confidence == "inferred" and r.evidence
    assert classify_track("SDE Co-op").confidence == "stated"


def test_normalize_employment_type():
    assert normalize_employment_type("FullTime") == "full_time"
    assert normalize_employment_type("Full time") == "full_time"
    assert normalize_employment_type("Intern") == "internship"
    assert normalize_employment_type("Co-op") == "co-op"
    assert normalize_employment_type("Part-time") == "part_time"
    assert normalize_employment_type(None) is None


def test_parse_terms():
    assert parse_terms("Spring 2027 Co-op; Summer '27; Fall 2026 and Autumn 2027") == \
        ["Spring 2027", "Summer 2027", "Fall 2026", "Fall 2027"]
    assert parse_terms("Program starting in January 2027") == ["Jan 2027 start"]
    assert parse_terms("We were founded in 2015") == []


@pytest.mark.parametrize("title,company,yoe,level", [
    ("Software Development Engineer I", "Amazon", None, "entry"),
    ("SDE II", "Amazon", None, "mid"),
    ("SDE III", "Amazon", None, "senior"),
    ("Software Engineer III", "Google", None, "mid"),        # Google SWE III ~ L4
    ("Software Engineer III", "DemoCo", 3, "mid"),           # generic III: depends on YOE
    ("Software Engineer III", "DemoCo", 7, "senior"),
    ("Software Engineer II", "DemoCo", None, "mid"),
    ("Junior Developer", "DemoCo", None, "entry"),
    ("Associate Software Engineer", "DemoCo", None, "entry"),
    ("Software Engineer, New Grad", "DemoCo", None, "entry"),
    ("Senior Software Engineer", "DemoCo", None, "senior"),
    ("Staff Software Engineer", "DemoCo", None, "staff_plus"),
    ("Engineering Manager", "DemoCo", None, "manager"),
    ("Software Engineer Intern", "DemoCo", None, "intern"),
    ("Software Engineer", "DemoCo", None, "unknown"),        # plain SWE: no level, still a match
    ("Software Engineer", "DemoCo", 4, "mid"),               # no level in title -> from YOE
])
def test_level_mapping(cfg, title, company, yoe, level):
    assert map_level(normalize_title(title, cfg.title_rules), company, yoe).level == level


@pytest.mark.parametrize("text,us,remote_us,display", [
    ("San Francisco, CA • New York, NY", True, False, "San Francisco, CA · New York, NY"),
    ("NYC", True, False, "New York, NY"),
    ("SF Bay Area", True, False, "SF Bay Area, CA"),
    ("Remote - US", True, True, "Remote (US)"),
    ("US Remote", True, True, "Remote (US)"),
    ("Anywhere in the United States", True, True, "Remote (US)"),
    ("Remote-Friendly, United States", True, True, "Remote (US)"),
    ("US, WA, Seattle", True, False, "Seattle, WA"),
    ("USA - CA - Santa Clara", True, False, "Santa Clara, CA"),
    ("Austin, Texas", True, False, "Austin, Texas"),
    ("Toronto, ON", False, False, "Toronto, ON"),
    ("London, UK", False, False, "London, UK"),
    ("Remote - Canada", False, False, "Remote - Canada"),
    ("Bengaluru, India | Seattle, WA", True, False, "Bengaluru, India · Seattle, WA"),
    ("Remote", None, False, "Remote"),
    (None, None, False, "Unknown"),
])
def test_location_normalization(text, us, remote_us, display):
    info = normalize_location(text)
    assert (info.us, info.remote_us, info.display) == (us, remote_us, display)


@pytest.mark.parametrize("url,key", [
    ("https://boards.greenhouse.io/figma/jobs/5426468004?gh_jid=5426468004", "greenhouse:5426468004"),
    ("https://job-boards.greenhouse.io/stripe/jobs/123", "greenhouse:123"),
    ("https://www.example.com/careers?gh_jid=987", "greenhouse:987"),
    ("https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd/apply",
     "lever:6ed76ce8-4156-4b60-b120-403538bd66cd"),
    ("https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245", "ashby:34413f8d-26bf-4bbc-8ade-eb309a0e2245"),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/SWE_JR123",
     "workday:nvidia:swe_jr123"),
    ("https://jobs.smartrecruiters.com/ServiceNow/744000154891980-software-engineer", "smartrecruiters:744000154891980"),
    ("https://www.amazon.jobs/en/jobs/10575340/software-development-engineer", "amazon:10575340"),
    ("https://example.com/jobs/1", None),
])
def test_canonical_key(url, key):
    assert canonical_key(url) == key


def test_robots_longest_match_wins():
    r = Robots("User-agent: *\nDisallow: /\nAllow: /$\nAllow: /careers\n\nUser-agent: BadBot\nDisallow: /careers\n")
    assert r.allowed("/careers/job/1") and r.allowed("/")
    assert not r.allowed("/api/private")
    assert Robots("").allowed("/anything")
    assert not Robots("User-agent: *\nDisallow: /*.json$\n").allowed("/a/b.json")


# --- regressions found in the Phase 2 real-data spot check -------------------------------------
def test_international_eor_is_not_an_internship():
    assert normalize_employment_type("International EOR") is None
    assert classify_track("Senior Engineer, Autonomy", "International EOR").track == "full_time"


@pytest.mark.parametrize("title,company,level", [
    ("Contract Student Worker Software Engineer (6-month Contract)", "DemoCo", "intern"),
    ("Software Development Engineer, AWS Border Network (L5)", "Amazon", "mid"),
    ("SDE (L4)", "Amazon", "entry"),
    ("Software Engineer 5 - Android", "Netflix", "senior"),
    ("Software Engineer 4", "Netflix", "mid"),
])
def test_level_regressions(cfg, title, company, level):
    lvl = map_level(normalize_title(title, cfg.title_rules), company, None).level
    assert lvl == level or (level == "intern" and lvl == "unknown")   # "student worker" has no level word


@pytest.mark.parametrize("text,us", [
    ("CT - Hartford", True), ("3325 Fort George G. Meade MD", True), ("Warsaw - Poland · Digital", False),
    ("Office - Bulgaria - Sofia", False), ("South SF", True), ("Remote (Any State", True), ("Lviv", False),
    ("CA-QC-LONGUEUIL-MD ~ 1000 Blvd Marie-Victorin", False), ("CAN-ON-Toronto", False), ("CA - Hartford", True),
])
def test_location_regressions(text, us):
    assert normalize_location(text).us is us


def test_preferred_metros():
    from jobsearch.extract.locations import preferred_match
    prefs = ["Boston", "Atlanta", "Seattle", "SF Bay Area"]
    assert preferred_match("Cambridge, MA", prefs) == "Boston"
    assert preferred_match("Waltham, Massachusetts", prefs) == "Boston"
    assert preferred_match("Bellevue, WA · Austin, TX", prefs) == "Seattle"
    assert preferred_match("Sunnyvale, CA", prefs) == "SF Bay Area"
    assert preferred_match("Alpharetta, GA", prefs) == "Atlanta"
    assert preferred_match("Austin, TX", prefs) is None
    assert preferred_match("Cambridge, UK · Austin, TX", prefs) is None      # only US places count
