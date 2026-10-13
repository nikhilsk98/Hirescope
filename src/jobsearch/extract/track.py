"""Classify each posting as Co-op/Intern, Full-time, or Unclear, and pull internship terms.

Signals, strongest first: title, source-stated employment type, JD wording.
Every result carries the evidence that decided it, so the dashboard can show why.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

COOP = "coop_intern"
FULL_TIME = "full_time"
UNCLEAR = "unclear"
TRACKS = (COOP, FULL_TIME, UNCLEAR)
TRACK_LABELS = {COOP: "Co-op/Intern", FULL_TIME: "Full-time", UNCLEAR: "Unclear"}

# "intern" must be a whole word: "Internal Tools" / "International" are not internships.
_COOP_TITLE = re.compile(
    r"\b(co-?ops?|interns?|internships?|apprentices?(hip)?|off[- ]cycle|off[- ]season|working student|"
    r"summer (analyst|associate)|placement student|student (developer|engineer|researcher|worker))\b", re.I)
_FT_TITLE = re.compile(
    r"\b(new grad(uate)?s?|university grad(uate)?|college grad(uate)?|early career|entry[- ]level|"
    r"graduate (software|engineer|developer|program|programme)|full[- ]time|rotational|class of 20\d\d)\b", re.I)
_COOP_JD = re.compile(
    r"\b(this|our|the|a|an)\s+(\d{1,2}[- ](week|month)\s+)?(paid\s+)?(co-?op|internship|intern program|"
    r"intern position|intern role)\b|\b\d{1,2}[- ](week|month) (co-?op|internship)\b|"
    r"\b(co-?op|internship) (term|program|position|opportunity)\b", re.I)
_FT_JD = re.compile(r"\b(full[- ]time (position|role|employee|opportunity)|permanent (position|role))\b", re.I)

_ET_COOP = {"intern", "internship", "co-op", "coop", "intern (fixed term)", "apprentice", "student"}
_ET_FT = {"full-time", "full time", "fulltime", "full_time", "regular", "permanent", "new_grad",
          "regular employee", "new college graduate"}
_ET_OTHER = {"contract", "contractor", "part-time", "part time", "parttime", "part_time", "temporary", "temp",
             "freelance", "seasonal"}


@dataclass
class TrackResult:
    track: str
    evidence: str
    confidence: str  # "stated" (title/employment type) | "jd" | "inferred"


def normalize_employment_type(value: str | None) -> str | None:
    """Map source values (Lever 'Intern', Ashby 'FullTime', Workday 'Full time') to a small vocabulary."""
    if not value:
        return None
    v = value.strip().lower()
    if re.search(r"\b(co-?op)\b", v):
        return "co-op"
    # whole words only: Lever's "International EOR" is not an internship
    if v in _ET_COOP or re.search(r"\b(intern|interns|internship|apprentice|student)\b", v):
        return "internship"
    if v in _ET_FT or "full" in v or "regular" in v or "permanent" in v:
        return "full_time"
    if "part" in v:
        return "part_time"
    if "contract" in v or "freelance" in v:
        return "contract"
    if "temp" in v or "seasonal" in v:
        return "temporary"
    return None


def classify_track(title: str, employment_type: str | None = None, text: str = "",
                   source_hint: str | None = None) -> TrackResult:
    """source_hint: a feed that only lists one kind of role (e.g. SimplifyJobs internships -> 'coop_intern')."""
    if m := _COOP_TITLE.search(title):
        return TrackResult(COOP, f"title: “{m.group(0)}”", "stated")
    et = normalize_employment_type(employment_type)
    if et in ("internship", "co-op"):
        return TrackResult(COOP, f"employment type: {employment_type}", "stated")
    if m := _FT_TITLE.search(title):
        return TrackResult(FULL_TIME, f"title: “{m.group(0)}”", "stated")
    if source_hint == COOP:
        return TrackResult(COOP, "listed in an internship feed", "stated")
    head = text[:3000]
    jd_coop = _COOP_JD.search(head)
    if jd_coop and not _FT_JD.search(head):
        return TrackResult(UNCLEAR, f"title looks full-time but JD says “{jd_coop.group(0)}”", "jd")
    if et == "full_time":
        return TrackResult(FULL_TIME, f"employment type: {employment_type}", "stated")
    if et in ("contract", "part_time", "temporary"):
        return TrackResult(UNCLEAR, f"employment type: {employment_type}", "stated")
    if source_hint == FULL_TIME:
        return TrackResult(FULL_TIME, "listed in a new-grad feed", "stated")
    if m := _FT_JD.search(head):
        return TrackResult(FULL_TIME, f"JD: “{m.group(0)}”", "jd")
    return TrackResult(FULL_TIME, "no intern/co-op signals in title, type or JD", "inferred")


_SEASON = re.compile(r"\b(spring|summer|fall|autumn|winter)\s*(?:'|’)?\s*(20\d\d|\d\d)\b", re.I)
_YEAR_SEASON = re.compile(r"\b(20\d\d)\s+(spring|summer|fall|autumn|winter)\b", re.I)   # "2027 Summer Intern"
_MONTH_START = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|"
    r"jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+(20\d\d)\s+(start|cohort|intake)\b|"
    r"\bstart(ing|s)?\s+(in|date:?)?\s*(january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\s+(20\d\d)\b", re.I)


_GRAD = re.compile(r"\b(graduat\w*|class of|degree (completion|conferral)|expected to (complete|finish))", re.I)
_CLAUSE_END = re.compile(r"[.;!?\n]|\s-\s")


def in_grad_phrase(text: str, start: int, end: int) -> bool:
    """Is text[start:end] part of a graduation phrase ("graduating between Fall 2027 to Summer 2028", "Spring 2027
    graduates")? Then it is a graduation window, not a work term."""
    before = text[max(0, start - 90):start]
    cut = max((m.end() for m in _CLAUSE_END.finditer(before)), default=0)
    after = text[end:end + 25]
    return bool(_GRAD.search(before[cut:])) or bool(re.match(r"\s*(\(?\w+\)?\s+)?graduat", after, re.I))


def without_grad_phrases(text: str) -> str:
    """The text with graduation clauses removed (for term / season checks)."""
    return " ".join(c for c in re.split(r"(?<=[.;!?\n])", text or "") if not _GRAD.search(c))


def parse_terms(text: str) -> list[str]:
    """Internship/co-op terms like 'Spring 2027', 'Summer 2027', 'January 2027 start' (deduped, in order).
    Seasons inside a graduation phrase ("graduating between Fall 2027 to Summer 2028") are not terms."""
    out: list[str] = []
    t = text or ""
    found = [(m.start(), m.group(1), m.group(2) if len(m.group(2)) == 4 else "20" + m.group(2))
             for m in _SEASON.finditer(t) if not in_grad_phrase(t, m.start(), m.end())]
    found += [(m.start(), m.group(2), m.group(1)) for m in _YEAR_SEASON.finditer(t) if not in_grad_phrase(t, m.start(), m.end())]
    for _, season, year in sorted(found):
        out.append(f"{season.capitalize().replace('Autumn', 'Fall')} {year}")
    for m in _MONTH_START.finditer(text or ""):
        month = (m.group(1) or m.group(6) or "").capitalize()
        year = m.group(2) or m.group(7)
        if month and year:
            out.append(f"{month[:3]} {year} start")
    return list(dict.fromkeys(out))
