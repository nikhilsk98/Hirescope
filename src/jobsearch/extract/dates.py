"""Graduation windows, start dates, application deadlines, and explicit new-grad experience restrictions.

Everything here is explicit-only: if a JD doesn't state it, nothing is returned (the scorer treats that as
uncertain, never as a match or a rejection). Each result carries the quoted evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"], start=1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MON = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|" \
       r"jun|jul|aug|sept|sep|oct|nov|dec)\.?"
# season -> (first month, last month) of a typical term; used for both graduation and start windows
SEASONS = {"winter": (12, 2), "spring": (1, 5), "summer": (5, 8), "fall": (8, 12), "autumn": (8, 12)}


def _ym(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _sentence(text: str, start: int, end: int) -> str:
    a = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    cands = [i for i in (text.find(".", end), text.find("\n", end)) if i != -1]
    b = min(cands) if cands else len(text)
    return text[a:b + 1].strip()[:240]


@dataclass
class Window:
    start: str          # YYYY-MM inclusive
    end: str            # YYYY-MM inclusive
    evidence: str

    def contains(self, ym: str, slack_months: int = 0) -> bool:
        return _add(self.start, -slack_months) <= ym <= _add(self.end, slack_months)

    def as_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "evidence": self.evidence}


def _add(ym: str, months: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    t = y * 12 + (m - 1) + months
    return _ym(t // 12, t % 12 + 1)


def _point(month_word: str | None, season: str | None, year: str, edge: str) -> str:
    y = int(year)
    if month_word:
        return _ym(y, _MONTHS[month_word.lower().rstrip(".")])
    if season:
        a, b = SEASONS[season.lower()]
        if edge == "start":
            return _ym(y - 1 if season.lower() == "winter" and a == 12 else y, a)
        return _ym(y, b)
    return _ym(y, 1 if edge == "start" else 12)


_WHEN = rf"(?:(?P<{{p}}m>{_MON})|(?P<{{p}}s>spring|summer|fall|autumn|winter))?\s*(?P<{{p}}y>20\d\d)"


def _when(prefix: str) -> str:
    return _WHEN.format(p=prefix)


_GRAD_CTX = r"(?:graduat\w*|graduation|degree (?:completion|conferral)|complet\w+ (?:your|a|their) degree|class of)"
_GRAD_BETWEEN = re.compile(
    rf"{_GRAD_CTX}[^.\n]{{0,60}}?between\s+{_when('a')}\s*(?:and|-|–|to)\s*{_when('b')}", re.I)
_GRAD_RANGE = re.compile(rf"{_GRAD_CTX}[^.\n]{{0,40}}?(?:from\s+)?{_when('a')}\s*(?:-|–|to|through)\s*{_when('b')}", re.I)
_GRAD_BY = re.compile(rf"{_GRAD_CTX}[^.\n]{{0,40}}?\b(?P<op>by|before|no later than|prior to)\s+{_when('a')}"
                      rf"(?:\s*,?\s*or\s+(?:by\s+)?{_when('b')})?", re.I)   # "by December 2026 or Summer 2027"
_GRAD_AFTER = re.compile(rf"{_GRAD_CTX}[^.\n]{{0,40}}?\b(?P<op>after|or later|on or after|no earlier than|"
                         rf"starting)\s+{_when('a')}|{_GRAD_CTX}[^.\n]{{0,40}}?{_when('c')}\s+or later", re.I)
_GRAD_IN = re.compile(rf"(?:{_GRAD_CTX}|new grad\w*)[^.\n]{{0,30}}?\b(?:in|of|date of|:)?\s*{_when('a')}\b", re.I)
_CLASS_OF = re.compile(r"\bclass of\s+(?P<y>20\d\d)\b", re.I)


def parse_grad_windows(text: str) -> list[Window]:
    """Graduation date windows a posting requires ("graduating between Dec 2026 and Jun 2027", "Class of 2027",
    "graduation by May 2027", "graduating in Spring 2027 or later")."""
    text = text or ""
    out: list[Window] = []
    seen_spans: list[tuple[int, int]] = []

    def add(m, a, b):
        if any(s <= m.start() < e for s, e in seen_spans):
            return
        seen_spans.append((m.start(), m.end()))
        if a <= b:
            out.append(Window(a, b, _sentence(text, m.start(), m.end())))

    for m in _GRAD_BETWEEN.finditer(text):
        add(m, _point(m.group("am"), m.group("as"), m.group("ay"), "start"),
            _point(m.group("bm"), m.group("bs"), m.group("by"), "end"))
    for m in _GRAD_RANGE.finditer(text):
        add(m, _point(m.group("am"), m.group("as"), m.group("ay"), "start"),
            _point(m.group("bm"), m.group("bs"), m.group("by"), "end"))
    for m in _GRAD_BY.finditer(text):
        end = _point(m.group("am"), m.group("as"), m.group("ay"), "end")
        if m.group("by") or m.group("bs") or m.group("bm"):     # an "or" alternative: the later one counts
            end = max(end, _point(m.group("bm"), m.group("bs"), m.group("by"), "end"))
        add(m, "2000-01", end)
    for m in _GRAD_AFTER.finditer(text):
        if m.group("ay"):
            add(m, _point(m.group("am"), m.group("as"), m.group("ay"), "start"), "2099-12")
        else:
            add(m, _point(m.group("cm"), m.group("cs"), m.group("cy"), "start"), "2099-12")
    for m in _CLASS_OF.finditer(text):
        y = int(m.group("y"))
        add(m, _ym(y - 1, 12), _ym(y, 8))   # "Class of 2027": Dec 2026 - Aug 2027 graduates
    for m in _GRAD_IN.finditer(text):
        if m.group("am") or m.group("as"):
            add(m, _point(m.group("am"), m.group("as"), m.group("ay"), "start"),
                _point(m.group("am"), m.group("as"), m.group("ay"), "end"))
    return out


_START_CTX = r"(?:start(?:ing|s)?(?: date)?|begin(?:ning|s)?|commenc\w+|available to start|internship (?:runs|dates)|" \
             r"co-?op (?:term|dates)|program (?:starts|begins))"
_START_AT = re.compile(rf"{_START_CTX}[^.\n]{{0,30}}?\b(?:in|on|:|by)?\s*{_when('a')}"
                       rf"(?:\s*(?:-|–|to|through)\s*{_when('b')})?", re.I)
# "January - June 2027" / "Jan–Aug 2027" (two months sharing one year) near co-op/internship/start wording
_MONTH_SPAN = re.compile(rf"(?:{_START_CTX}|co-?op|internship|term|runs?|program)[^.\n]{{0,40}}?\b(?P<a>{_MON})\s*"
                         rf"(?:-|–|to|through)\s*(?P<b>{_MON})\s*,?\s*(?P<y>20\d\d)", re.I)
_START_ASAP = re.compile(r"\b(start(?:ing)? (?:date )?(?:is )?(?:immediately|asap|as soon as possible))\b", re.I)


def parse_start_windows(text: str, terms: list[str] | None = None) -> list[Window]:
    """Stated start dates / term windows. `terms` like ['Spring 2027', 'Jan 2027 start'] add windows too."""
    text = text or ""
    out: list[Window] = []
    for m in _START_AT.finditer(text):
        a = _point(m.group("am"), m.group("as"), m.group("ay"), "start")
        b = _point(m.group("bm"), m.group("bs"), m.group("by"), "end") if m.group("by") else \
            _point(m.group("am"), m.group("as"), m.group("ay"), "end")
        if a <= b:
            out.append(Window(a, b, _sentence(text, m.start(), m.end())))
    for t in terms or []:
        if m := re.match(r"(Spring|Summer|Fall|Winter)\s+(20\d\d)$", t):
            a = _point(None, m.group(1), m.group(2), "start")
            out.append(Window(a, _point(None, m.group(1), m.group(2), "end"), f"term: {t}"))
        elif m := re.match(r"([A-Z][a-z]{2})\s+(20\d\d) start$", t):
            ym = _point(m.group(1), None, m.group(2), "start")
            out.append(Window(ym, ym, f"term: {t}"))
    for m in _MONTH_SPAN.finditer(text):
        a_m, b_m, y = _MONTHS[m.group("a").lower().rstrip(".")], _MONTHS[m.group("b").lower().rstrip(".")], int(m.group("y"))
        a_y = y - 1 if a_m > b_m else y            # "December - May 2027" starts Dec 2026
        out.append(Window(_ym(a_y, a_m), _ym(y, b_m), _sentence(text, m.start(), m.end())))
    if m := _START_ASAP.search(text):
        today = date.today()
        out.append(Window(_ym(today.year, today.month), _ym(today.year, today.month), _sentence(text, m.start(), m.end())))
    uniq: dict[tuple[str, str], Window] = {}
    for w in out:
        uniq.setdefault((w.start, w.end), w)
    ws = list(uniq.values())
    # "Co-op term: Jan–Aug 2027" also yields "Aug 2027" on its own: drop windows strictly inside a wider one
    # that came from the same sentence
    return [w for w in ws if not any(o is not w and o.evidence == w.evidence and o.start <= w.start and
                                     w.end <= o.end and (o.start, o.end) != (w.start, w.end) for o in ws)]


def term_window(term: str) -> Window | None:
    """A stated term as a window: "Summer 2026" → 2026-05..2026-08, "Winter 2026" → 2025-12..2026-02 (a winter term
    spans the new year), "Jan 2027 start" → 2027-01. Anything else (e.g. "Class of 2027") → None."""
    if m := re.match(r"(Spring|Summer|Fall|Autumn|Winter)\s+(20\d\d)$", term or "", re.I):
        return Window(_point(None, m.group(1), m.group(2), "start"), _point(None, m.group(1), m.group(2), "end"), term)
    m = re.match(r"([A-Za-z]{3,9})\.?\s+(20\d\d)(?: start)?$", term or "")
    if m and m.group(1).lower() in _MONTHS:
        ym = _point(m.group(1), None, m.group(2), "start")
        return Window(ym, ym, term)
    return None


def season_passed(terms: list[str], window_start: str | None, today: str | None = None) -> list[str]:
    """Co-op track: the stated season terms when every one of them is already over (ends before this month) or starts
    before your co-op window ("Summer 2026", "Fall 2026", "Winter 2026" with a window from 2026-12). Empty when no term
    is stated or any term still fits (Winter 2027, Spring 2027, Jan 2027 and later) — a job is never hidden for one
    past term among current ones."""
    today = today or _ym(date.today().year, date.today().month)
    wins = [w for w in (term_window(t) for t in terms or []) if w]
    if not wins:
        return []
    bad = [w for w in wins if w.end < today or (window_start and w.start < window_start)]
    return [w.evidence for w in bad] if len(bad) == len(wins) else []


def summer_only(terms: list[str], title: str, text: str) -> bool:
    """True when every stated term is a Summer term (and nothing says spring/winter/fall/off-cycle/co-op)."""
    seasons = {t.split()[0] for t in terms if re.match(r"(Spring|Summer|Fall|Winter) 20\d\d$", t)}
    if not seasons or seasons != {"Summer"}:
        return False
    from .track import without_grad_phrases   # "graduating between Fall 2027 …" is not a fall work term
    return _OTHER_TERM.search(f"{title}\n{without_grad_phrases(text[:3000])}") is None


# Signs an internship also runs outside summer. Seasons count only when used as a term ("Spring 2027", "spring
# semester", "winter or summer 2027", "during the fall"), not "Spring Boot" or "a 6-month period".
_OTHER_TERM = re.compile(
    r"\b(off[- ]cycle|off[- ]season|year[- ]round)\b"
    r"|\b(spring|winter|fall|autumn)\b(?!\s+(?:boot|framework|cloud|mvc|batch|security|data|webflux|integration|ai)\b)"
    r"(?=\s*(?:/|,|&|\band\b|\bor\b)?\s*(?:summer\s*)?(?:'|’)?\s*(?:20\d\d|\d\d\b|term|semester|quarter|session|co-?op|intern))"
    r"|\b(?:during|in|for|this|next)\s+(?:the\s+)?(?:spring|winter|fall|autumn)\b(?!\s+(?:boot|framework)\b)"
    r"|\b[468][- ]month\b(?!\s+period)", re.I)


_RESTRICT = re.compile(
    r"(no more than|not more than|less than|fewer than|under|maximum of|at most|up to)\s+(\d{1,2}|one|two|three)\s*\+?"
    r"\s*years?\s+of\s+(post[- ]graduation |prior |previous |relevant )?(full[- ]time|professional|industry|"
    r"post[- ]graduate|work)\s+(work )?experience"
    r"|\b(graduated|graduation)\s+within\s+the\s+(last|past)\s+(\d{1,2}|twelve|eighteen|twenty[- ]four)\s+months"
    r"|\b0\s*(?:-|–|to)\s*(1|2)\s+years?\s+of\s+(full[- ]time|professional|industry)\s+experience"
    r"|\bno\s+(prior\s+)?(full[- ]time|professional)\s+(work\s+)?experience\s+(required|necessary|expected)?",
    re.I)


def experience_restriction(text: str) -> str | None:
    """The quoted line when a JD explicitly caps prior experience ("no more than 1 year of full-time experience").
    Never inferred: returns None unless stated."""
    if m := _RESTRICT.search(text or ""):
        return _sentence(text, m.start(), m.end())
    return None


_DEADLINE = re.compile(
    rf"(?:apply by|applications? (?:are |will be )?(?:due|accepted until|close[sd]?|must be (?:received|submitted) by)|"
    rf"(?:application |submission )?deadline(?: to apply)?(?: is)?|posting (?:will )?(?:close[sd]?|expires?)(?: on)?|"
    rf"open until|accepting applications until)\s*:?\s*(?:on\s+)?"
    rf"(?:(?P<mon>{_MON})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>20\d\d)|"
    rf"(?P<m2>\d{{1,2}})/(?P<d2>\d{{1,2}})/(?P<y2>20\d\d|\d\d)|(?P<iso>20\d\d-\d\d-\d\d))",
    re.I)


def parse_deadline(text: str, explicit: str | None = None) -> tuple[str | None, str | None]:
    """(YYYY-MM-DD, evidence). `explicit` = a source field (Greenhouse application_deadline, JSON-LD validThrough)."""
    if explicit:
        try:
            return datetime.fromisoformat(str(explicit).replace("Z", "+00:00")).date().isoformat(), "posting's deadline field"
        except ValueError:
            pass
    for m in _DEADLINE.finditer(text or ""):
        try:
            if m.group("iso"):
                d = date.fromisoformat(m.group("iso"))
            elif m.group("mon"):
                d = date(int(m.group("year")), _MONTHS[m.group("mon").lower().rstrip(".")], int(m.group("day")))
            else:
                y = int(m.group("y2"))
                d = date(y + 2000 if y < 100 else y, int(m.group("m2")), int(m.group("d2")))
        except ValueError:
            continue
        return d.isoformat(), _sentence(text, m.start(), m.end())
    return None, None
