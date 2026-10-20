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
    # the same Jibe posting from its own site, SimplifyJobs (?icims=1) and a LinkedIn "Apply" link
    ("https://careers.amd.com/careers-home/jobs/91180?lang=en-us&iis=Job%20Board", "jibe:careers.amd.com:91180"),
    ("https://careers.amd.com/jobs/91180?icims=1", "jibe:careers.amd.com:91180"),
    ("https://careers.demo.example/jobs/12?lang=en-us", None),
    ("https://ats.rippling.com/demo-careers/jobs/c88ab3d3-a8e7-4639-b95b-d6c9b5290dd0",
     "rippling:c88ab3d3-a8e7-4639-b95b-d6c9b5290dd0"),
    ("https://apply.workable.com/demo/j/FD4C9770FF/apply", "workable:FD4C9770FF"),
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


def test_canadian_city_with_ca_country_code_is_not_california():
    from jobsearch.extract.locations import normalize_location
    assert normalize_location("Toronto, CA").us is False
    assert normalize_location("Vancouver, CA").us is False
    assert normalize_location("San Francisco, CA").us is True
    assert normalize_location("Toronto, CA · Seattle, WA").us is True     # any US place keeps it


def test_member_of_technical_staff_ladder():
    from jobsearch.config import load_title_rules
    from jobsearch.extract.levels import map_level
    from jobsearch.extract.normalize import normalize_title
    r = load_title_rules()
    lv = lambda t: map_level(normalize_title(t, r), "Salesforce").level  # noqa: E731
    assert lv("Software Engineering LMTS - Data (Remote)") == "staff_plus"
    assert lv("SMTS Software Engineer") == "senior"
    assert lv("AMTS, Software Engineering") == "entry"


def test_terms_with_year_before_season():
    from jobsearch.extract.track import parse_terms
    assert parse_terms("2027 Summer Intern, MS/PhD, Perception") == ["Summer 2027"]
    assert parse_terms("Spring 2027 co-op; also 2027 Fall") == ["Spring 2027", "Fall 2027"]


def test_member_of_technical_staff_is_not_a_staff_level():
    """At AI labs "Member of Technical Staff" is the plain engineer title: the "staff" inside it is not a level,
    but level words around it still count."""
    from jobsearch.config import load_title_rules
    from jobsearch.extract.levels import map_level
    from jobsearch.extract.normalize import normalize_title
    r = load_title_rules()
    lv = lambda t, co="Perplexity": map_level(normalize_title(t, r), co, None, non_level=tuple(r.non_level_phrases)).level  # noqa: E731
    assert lv("Member of Technical Staff (Software Engineer, Monetization)") == "unknown"
    assert lv("MTS, Backend") == "unknown"
    assert lv("Senior Member of Technical Staff", "Oracle") == "senior"
    assert lv("Member of Technical Staff - Staff Engineer") == "staff_plus"
    assert lv("Staff Software Engineer") == "staff_plus"


def test_graduation_phrases_are_not_work_terms():
    """'graduating between Fall 2027 to Summer 2028' is a graduation window: its seasons must not make a summer-only
    internship look like it also runs in the fall (DoorDash Labs, Summer 2027)."""
    from jobsearch.extract.dates import summer_only
    from jobsearch.extract.track import parse_terms
    title = "Software Engineer, Intern - Labs (Summer 2027) (DEMO)"
    jd = ("- You are pursuing a B.S. or M.S. in Computer Science and are graduating between Fall 2027 to Summer 2028.\n"
          "- You are available for a May or June 2027 start date")
    terms = parse_terms(f"{title}\n{jd}")
    assert terms == ["Summer 2027", "Jun 2027 start"] and summer_only(terms, title, jd)
    assert parse_terms("Fall 2026 graduates welcome; Summer 2027 internship") == ["Summer 2027"]
    assert parse_terms("Class of Spring 2027") == []
    assert parse_terms("Spring 2027 Co-op (January - June)") == ["Spring 2027"]           # real terms still count
    assert not summer_only(["Summer 2027"], "SWE Intern (DEMO)", "Summer or Fall 2027 internship")


def test_graduating_by_x_or_y_uses_the_later_date():
    """'graduating by December 2026 or Summer 2027' (Applied Intuition, New Grad 2027) includes an Aug 2027 graduate."""
    from jobsearch.extract.dates import parse_grad_windows
    (w,) = parse_grad_windows("Bachelor's degree in CS and graduating by December 2026 or Summer 2027. (DEMO)")
    assert (w.start, w.end) == ("2000-01", "2027-08")
    (w2,) = parse_grad_windows("graduating by May 2027 (DEMO)")
    assert w2.end == "2027-05"


@pytest.mark.parametrize("title,jd,terms,summer", [
    ("Software Engineering Co-op (Spring/Summer 2027) (DEMO)", "", ["Spring 2027", "Summer 2027"], False),
    ("Backend Intern 2027 (DEMO)", "available to work at HQ during winter or summer 2027", ["Winter 2027", "Summer 2027"], False),
    ("Data Engineer Intern (DEMO)", "Summer 2027. You may submit 3 applications within a 6-month period.", ["Summer 2027"], True),
    ("SWE Intern, Cloud (Summer 2027) (DEMO)", "Experience with Spring Boot and Spring Cloud.", ["Summer 2027"], True),
    ("SWE Intern - Summer 2027 (DEMO)", "Java (J2EE), JavaScript, Spring, etc.", ["Summer 2027"], True),
    ("SWE Intern (Summer 2027) (DEMO)", "This is a 6-month co-op.", ["Summer 2027"], False),
    ("SWE Intern (DEMO)", "Summer 2027; a spring semester term is also possible", ["Summer 2027"], False),
])
def test_summer_only_detection(title, jd, terms, summer):
    from jobsearch.extract.dates import summer_only
    from jobsearch.extract.track import parse_terms
    got = parse_terms(f"{title}\n{jd}")
    assert got == terms and summer_only(got, title, jd) is summer


def test_enrollment_phrases_are_not_work_terms():
    from jobsearch.extract.dates import summer_only
    from jobsearch.extract.track import parse_terms
    t, jd = "Software Developer Intern (DEMO)", "Summer 2027 internship. Must be enrolled at a university for Fall term 2027."
    terms = parse_terms(f"{t}\n{jd}")
    assert terms == ["Summer 2027"] and summer_only(terms, t, jd)
