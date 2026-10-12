"""Split a JD into minimum/preferred requirements and parse YOE, degree, graduation year."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_MIN_HEAD = re.compile(
    r"^(minimum|basic|required|requirements|qualifications|what you('|’)ll need|what we('|’)re looking for|"
    r"you (might|may|should) be a (good )?fit|you have|about you|who you are|what you bring|must have|"
    r"you('|’)ll thrive|you will thrive|skills and experience|your background|we('|’)re looking for|"
    r"we('|’)d love to hear from you|what we look for|what you('|’)ll bring|you should have|who we('|’)re looking for|"
    r"what we require|what we value|what we need to see|you might thrive|what you have|what you need|what you should have|"
    r"your skills|to thrive in this role|education and experience|knowledge and skills|knowledge, skills|"
    r"essential qualifications|experience$|what we are looking for|what makes you a good fit|we('|’)re excited about you|"
    r"who you (might )?be|skills you('|’)ll need|essential (skills|requirements)|key qualifications|job requirements|"
    r"what you('|’)ll need to succeed|skills and qualifications|the ideal candidate|qualifications and experience|"
    r"to succeed in this role|to be successful|you('|’)ll need|you will need)",
    re.IGNORECASE,
)
_PREF_HEAD = re.compile(
    r"^(preferred|nice to have|bonus|nice-to-have|pluses|it('|’)s a plus|strong candidates may also|"
    r"extra credit|ideally|additional qualifications|while not required|(it('|’)s )?an? (added )?plus|"
    r"bonus points|even better|we('|’)d (also )?love it if|ways to stand out|what will give you a competitive edge|"
    r"desirable|desired skills|the extras|what will set you apart|technologies we use|our (tech )?stack|tech stack|"
    r"good to have|would be a plus|additional skills)",
    re.IGNORECASE,
)
_OTHER_HEAD = re.compile(
    r"^(responsibilities|what you('|’)ll do|about the (role|team|company)|benefits|compensation|"
    r"the role|in this role|your role|perks|why join|how we|logistics|deadline|annual salary|"
    r"the (expected )?(base )?(salary|pay)|pay range|equal opportunity|location|what you('|’)ll be doing|"
    r"what you will (be )?do(ing)?|what you might do|core responsibilities|your core responsibilities|key responsibilities|"
    r"essential duties|you will$|how you('|’)ll make an impact|a typical day|our principles|you('|’)re excited about this|"
    r"what we offer|health|financial benefits|time off|work schedule|additional information|available locations|"
    r"to apply|see below for)",
    re.IGNORECASE,
)


def _is_heading(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith("- ") and len(s) <= 90 and (s.endswith(":") or len(s.split()) <= 9)


@dataclass
class Requirements:
    minimum: list[str] = field(default_factory=list)
    preferred: list[str] = field(default_factory=list)


def split_requirements(text: str) -> Requirements:
    """Group bullet lines under the nearest requirement-like heading. Unknown structure -> empty lists."""
    req = Requirements()
    bucket: list[str] | None = None
    for raw in text.splitlines():
        line = re.sub(r"^#{1,6}\s*", "", raw.strip()).strip()   # Markdown headings ("# Nice to have:")
        if not line or set(line) <= {"#", "*", "-", "_"}:
            continue
        if _is_heading(line):
            h = line.rstrip(":").strip()
            if _PREF_HEAD.match(h):
                bucket = req.preferred
                continue
            if _MIN_HEAD.match(h):
                bucket = req.minimum
                continue
            if _OTHER_HEAD.match(h) or (bucket and not line.startswith("- ")):
                # a known non-requirement heading, or any heading-like line once a list has started
                # ("Representative projects" after the preferred list), ends the section
                bucket = None
                continue
        if bucket is not None and line.startswith("- "):
            bucket.append(line[2:].strip())
    return req


_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10}
_N = r"(\d{1,2}(?:\.\d)?|one|two|three|four|five|six|seven|eight|nine|ten)"
_YOE_PATTERNS = [
    # "3-5 years", "3 to 5 years", "3–5+ years"
    re.compile(rf"\b{_N}\s*(?:\+\s*)?(?:-|–|—|to)\s*{_N}\s*\+?\s*years?", re.I),
    # "minimum of 2 years", "at least 3 years"
    re.compile(rf"\b(?:minimum(?: of)?|at least|min\.?)\s*{_N}\s*\+?\s*years?", re.I),
    # "3+ years", "5 years of"
    re.compile(rf"\b{_N}\s*\+?\s*(?:years?|yrs?)\b(?:\s+of)?", re.I),
]
_YOE_CONTEXT = re.compile(r"experience|industry|professional|working|engineering|building|developing|software", re.I)


def _n(s: str) -> float:
    return float(_NUM.get(s.lower(), s))


@dataclass
class YOE:
    min: float | None
    max: float | None
    evidence: str | None


def parse_yoe(lines: list[str] | str) -> YOE:
    """Smallest stated minimum years of experience across requirement lines.

    Only counts mentions that look like experience ("years of experience", "years building ...").
    Returns YOE(None, None, None) when nothing is stated.
    """
    if isinstance(lines, str):
        lines = [ln for ln in re.split(r"\n|(?<=[.;])\s+", lines) if ln.strip()]
    best: YOE | None = None
    for line in lines:
        if not _YOE_CONTEXT.search(line) or re.search(r"\byears? old\b|\bfounded\b|\bago\b", line, re.I):
            continue
        for i, pat in enumerate(_YOE_PATTERNS):
            m = pat.search(line)
            if not m:
                continue
            lo = _n(m.group(1))
            hi = _n(m.group(2)) if i == 0 else None
            if lo > 20:
                break
            if best is None or lo < best.min:
                best = YOE(lo, hi, line.strip()[:200])
            break
    return best or YOE(None, None, None)


_DEGREES = [
    ("PhD", r"\b(ph\.?\s?d|doctorate|doctoral)\b"),
    ("Master's", r"\b(master'?s|m\.?s\.?(?= |,)|msc|m\.eng)\b"),
    ("Bachelor's", r"\b(bachelor'?s|b\.?s\.?(?= |,)|bsc|b\.?a\.?(?= |,)|undergraduate degree|bs/ms|ba/bs)\b"),
]


def parse_degree(text: str) -> str | None:
    """Lowest degree level mentioned as a requirement (Bachelor's < Master's < PhD), or None."""
    found = [name for name, pat in _DEGREES if re.search(pat, text, re.I)]
    return found[-1] if found else None


_GRAD_RE = re.compile(
    r"(?:graduat\w*|new grad\w*|class of|summer|fall|winter|spring|degree|start\w*|\binterns?(?:hips?)?\b)"
    r"[^.\n]{0,40}?\b(20[2-3]\d)\b"
    r"|\b(20[2-3]\d)\s+(?:new grad\w*|graduat\w*|university grad\w*|intern\w*|summer|start)",
    re.I,
)


def parse_grad_years(text: str) -> list[int]:
    """Years tied to graduation/start language ("New Grad 2027", "graduating between Dec 2026 and Jun 2027")."""
    years: set[int] = set()
    for m in _GRAD_RE.finditer(text or ""):
        years.add(int(m.group(1) or m.group(2)))
        # "between Dec 2026 and Jun 2027" -> grab the second year too
        tail = text[m.end(): m.end() + 30]
        m2 = re.match(r"\s*(?:-|–|to|and|or)\s*(?:[A-Za-z]+\.?\s+)?(20[2-3]\d)\b", tail)
        if m2:
            years.add(int(m2.group(1)))
    return sorted(years)
