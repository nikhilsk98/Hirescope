"""Sponsorship / citizenship / clearance flags. Only explicit statements count; silence -> 'Not stated'."""
from __future__ import annotations

import re

NOT_STATED = "Not stated"

_SPONSOR_NO = re.compile(
    r"(not|unable to|cannot|can't|won't|will not|does not|do not|don't)\s+(be able to\s+)?(offer|provide|sponsor|support)\w*"
    r"[^.\n]{0,60}(sponsor\w*|visa)"
    r"|(sponsor\w*|visa)[^.\n]{0,40}\b(is|are)\s+not\s+(available|offered|provided|possible)"
    r"|without\s+(the\s+)?(need\s+for\s+)?(current\s+or\s+future\s+)?(visa\s+)?sponsorship"
    r"|no\s+(visa\s+)?sponsorship",
    re.I,
)
_SPONSOR_YES = re.compile(
    r"(we|company)\s+(do|does|will|can)\s+(offer|provide|sponsor|support)\w*[^.\n]{0,40}(sponsor\w*|visa)"
    r"|sponsorship\s+(is\s+)?(available|offered|provided)"
    r"|(will|can)\s+sponsor\b",
    re.I,
)
_CITIZEN = re.compile(
    r"(u\.?s\.?|united states)\s+citizen(ship)?\s+(is\s+)?(required|only)"
    r"|must\s+be\s+(a\s+)?(u\.?s\.?|united states)\s+citizen"
    r"|requires?\s+(u\.?s\.?|united states)\s+citizenship"
    r"|only\s+(u\.?s\.?|united states)\s+citizens",
    re.I,
)
# Export-control (ITAR/EAR) "U.S. person" requirements: citizens or permanent residents only. Explicit only.
_US_PERSON = re.compile(
    r"must\s+(be|qualify as)\s+(a\s+)?u\.?s\.?\s+persons?"
    r"|(only|limited to)\s+u\.?s\.?\s+persons"
    r"|u\.?s\.?\s+persons?\s+(only|status\s+(is\s+)?required|as\s+defined)"
    r"|\b(itar|export control\w*|ear)\b[^.\n]{0,120}\bu\.?s\.?\s+persons?",
    re.I,
)
# A statement that only applies to *some* roles, or only might apply, is not this job's requirement.
_CONDITIONAL = re.compile(r"\b(positions? (with|that|requiring)|roles? (with|that|requiring)|for (all|certain|some|select) "
                          r"(positions|roles)|if (the|this|a) (position|role)|where (required|applicable)|when required|"
                          r"may (need|be required|require)|might (need|be required)|could be required|potentially|"
                          r"subject to a (security|background) investigation)\b", re.I)
_PERSON_WORDS = re.compile(r"permanent resident|green card|protected (individual|person)|asylee|refugee|u\.?s\.? national",
                           re.I)
_OPTIONAL = re.compile(r"\b(preferred|a plus|is a plus|nice to have|bonus|desired|desirable|beneficial|not required|"
                       r"advantageous|helpful)\b", re.I)
_CLEARANCE = re.compile(
    r"(active|current|ability to obtain|eligib\w+ (for|to obtain)|must (hold|have|obtain))[^.\n]{0,40}"
    r"(security\s+clearance|ts/sci|top secret|secret clearance)"
    r"|(security\s+clearance|ts/sci|top secret clearance)\s+(is\s+)?required",
    re.I,
)


def _evidence(text: str, m: re.Match) -> str:
    start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
    end_candidates = [i for i in (text.find(".", m.end()), text.find("\n", m.end())) if i != -1]
    end = min(end_candidates) if end_candidates else len(text)
    return text[start:end + 1].strip()[:240]


def extract_flags(text: str) -> dict[str, dict]:
    """{'sponsorship': {'value', 'evidence'}, 'citizenship': {...}, 'clearance': {...}}"""
    text = text or ""
    flags = {}
    no = None
    for m in _SPONSOR_NO.finditer(text):
        ev = _evidence(text, m)
        if re.search(r"export licen[cs]e", ev, re.I):   # "...without sponsorship for an export license" is not a visa
            continue
        if re.search(r"\bJ-?1\b", ev) and not re.search(r"\b(H-?1B|F-?1|OPT|CPT|any|all|employment visa|work visa)\b", ev, re.I):
            continue                                     # J-1-only statements say nothing about F-1/OPT/H-1B
        no = ev
        break
    if no:
        flags["sponsorship"] = {"value": "No sponsorship", "evidence": no}
    elif m := _SPONSOR_YES.search(text):
        flags["sponsorship"] = {"value": "Sponsorship available", "evidence": _evidence(text, m)}
    else:
        flags["sponsorship"] = {"value": NOT_STATED, "evidence": None}

    flags["citizenship"] = {"value": NOT_STATED, "evidence": None}
    for pat in (_CITIZEN, _US_PERSON):
        for m in pat.finditer(text):
            ev = _evidence(text, m)
            if _CONDITIONAL.search(ev):
                continue
            # "U.S. citizen, lawful permanent resident, or protected individual" is the export-control
            # "U.S. person" rule, not citizens-only
            value = "US person required (export control)" if pat is _US_PERSON or _PERSON_WORDS.search(ev) \
                else "US citizenship required"
            flags["citizenship"] = {"value": value, "evidence": ev}
            break
        if flags["citizenship"]["value"] != NOT_STATED:
            break

    flags["clearance"] = {"value": NOT_STATED, "evidence": None}
    for m in _CLEARANCE.finditer(text):
        ev = _evidence(text, m)
        if _OPTIONAL.search(ev) or _CONDITIONAL.search(ev):   # "a plus" / "may need" is not a requirement
            if flags["clearance"]["value"] == NOT_STATED:
                label = "Clearance preferred" if _OPTIONAL.search(ev) else "Clearance possibly required"
                flags["clearance"] = {"value": label, "evidence": ev}
            continue
        flags["clearance"] = {"value": "Clearance required", "evidence": ev}
        break
    return flags
