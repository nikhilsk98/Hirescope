"""Parsing: HTML, requirements, YOE, grad year, degree, flags, salary, skills, titles, resume. (DEMO data)"""
import pytest

from jobsearch.extract.flags import extract_flags
from jobsearch.extract.normalize import employment_type_from_title, normalize_title, parse_salary, work_mode
from jobsearch.extract.requirements import parse_degree, parse_grad_years, parse_yoe, split_requirements
from jobsearch.extract.skills import extract_skills
from jobsearch.extract.text import html_to_text


def test_html_to_text_handles_escaped_html_and_li_p():
    raw = "&lt;h2&gt;Minimum&lt;/h2&gt;&lt;ul&gt;\n&lt;li&gt;\n&lt;p&gt;Python &amp;amp; Go&lt;/p&gt;\n&lt;/li&gt;&lt;/ul&gt;&lt;script&gt;alert(1)&lt;/script&gt;"
    text = html_to_text(raw)
    assert "- Python & Go" in text
    assert "alert" not in text


def test_split_requirements_min_and_pref():
    text = "About the role\n- Build things\nMinimum qualifications\n- Python\n- SQL\nPreferred qualifications\n- Kafka\nBenefits\n- Free lunch"
    r = split_requirements(text)
    assert r.minimum == ["Python", "SQL"]
    assert r.preferred == ["Kafka"]


def test_unknown_heading_after_list_ends_section():
    text = "Preferred qualifications\n- Kafka\nRepresentative projects\n- Build a sandbox"
    assert split_requirements(text).preferred == ["Kafka"]
    # ...but an intro line before the list starts does not
    assert split_requirements("Requirements\nWe value:\n- Python").minimum == ["Python"]


def test_split_requirements_figma_style_headings():
    text = "We’d love to hear from you if you have:\n- 3+ years of experience\nWhile not required, it’s an added plus if you also have:\n- Rust"
    r = split_requirements(text)
    assert r.minimum == ["3+ years of experience"] and r.preferred == ["Rust"]


@pytest.mark.parametrize("line,expected", [
    ("3+ years of software engineering experience", 3),
    ("Minimum of 2 years of professional experience", 2),
    ("At least two years building distributed systems", 2),
    ("2-4 years of industry experience", 2),
    ("5 yrs experience with Java", 5),
    ("Experience with Python", None),
    ("Founded 10 years ago, our company is experienced", None),
])
def test_parse_yoe(line, expected):
    assert parse_yoe([line]).min == expected


def test_parse_yoe_takes_smallest_minimum():
    y = parse_yoe(["5+ years of experience in backend", "or 3+ years of experience with a PhD"])
    assert y.min == 3 and "3+" in y.evidence


@pytest.mark.parametrize("text,years", [
    ("Software Engineer, New Grad 2027", [2027]),
    ("Software Engineer Intern (Summer 2027)", [2027]),
    ("graduating between December 2026 and June 2027", [2026, 2027]),
    ("Class of 2026 students", [2026]),
    ("We were founded in 2015", []),
])
def test_parse_grad_years(text, years):
    assert parse_grad_years(text) == years


def test_parse_degree():
    assert parse_degree("BS or MS in Computer Science") == "Bachelor's"   # lowest level that qualifies
    assert parse_degree("Bachelor's degree in CS") == "Bachelor's"
    assert parse_degree("PhD in Machine Learning") == "PhD"
    assert parse_degree("Strong coding skills") is None


@pytest.mark.parametrize("text,key,value", [
    ("We are unable to sponsor visas for this role.", "sponsorship", "No sponsorship"),
    ("Candidates must be authorized to work without the need for current or future visa sponsorship.", "sponsorship", "No sponsorship"),
    ("We do sponsor visas!", "sponsorship", "Sponsorship available"),
    ("Must be a US citizen.", "citizenship", "US citizenship required"),
    ("Active TS/SCI security clearance required.", "clearance", "Clearance required"),
])
def test_flags_explicit(text, key, value):
    f = extract_flags(text)
    assert f[key]["value"] == value
    assert f[key]["evidence"]


def test_flags_silence_is_not_stated():
    f = extract_flags("We build great software in Boston.")
    assert all(v["value"] == "Not stated" and v["evidence"] is None for v in f.values())


@pytest.mark.parametrize("text,lo,hi", [
    ("The base salary range is $120,000 - $150,000 USD.", 120000, 150000),
    ("Annual Salary: $320,000—$485,000 USD", 320000, 485000),
    ("$120K–$150K", 120000, 150000),
    ("$46—$46 USD per hour", 95680, 95680),
    ("We raised $5 - $10 to buy snacks", None, None),
    ("No pay info", None, None),
])
def test_parse_salary(text, lo, hi):
    s = parse_salary(text)
    assert (s.min, s.max) == (lo, hi)


def test_skills_synonyms_and_short_alias_rules(cfg):
    text = "Go above and beyond. We use Go, k8s, C++, node.js and github actions.\nR is not a skill here"
    found = extract_skills(text, cfg.synonyms)
    assert {"Go", "Kubernetes", "C++", "Node.js", "CI/CD"} <= set(found)
    assert "R" not in found
    # at a line start, a short alias counts only when it reads as a list item
    assert extract_skills("Go or Rust experience", cfg.synonyms) == ["Go", "Rust"]
    assert extract_skills("Go\nC, Java", cfg.synonyms) == ["Java", "Go", "C"]
    assert extract_skills("Go build the future", cfg.synonyms) == []


def test_normalize_title_and_seniority(cfg):
    n = normalize_title("Sr. SWE, ML Platform", cfg.title_rules)
    assert n == "senior software engineer machine learning platform"
    assert employment_type_from_title(normalize_title("SWE Intern", cfg.title_rules), cfg.title_rules) == "internship"
    assert employment_type_from_title(normalize_title("Software Engineer, New Grad", cfg.title_rules), cfg.title_rules) == "new_grad"


def test_location_and_work_mode():
    assert work_mode("Remote - US") == "remote"
    assert work_mode("Boston, MA", "This is a hybrid role") == "hybrid"
    assert work_mode("Boston, MA", "We run a hybrid cloud") == "onsite"
    assert work_mode(None) == "unknown"


def test_resume_parsing(resume):
    assert len(resume.sections["experience"]) == 2
    assert resume.sections["experience"][0].bullets[0].startswith("Built a Python FastAPI")
    assert {"Python", "Go", "Kubernetes", "PostgreSQL"} <= set(resume.listed_skills)
    assert "CI/CD" in resume.skills  # evidenced by "GitHub Actions" in a bullet, not listed
    assert resume.skill_evidence["Kubernetes"].startswith("Containerized")
    assert len(resume.version_hash) == 16
