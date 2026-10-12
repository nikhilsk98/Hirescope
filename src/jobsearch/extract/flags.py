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
    if m := _SPONSOR_NO.search(text):
        flags["sponsorship"] = {"value": "No sponsorship", "evidence": _evidence(text, m)}
    elif m := _SPONSOR_YES.search(text):
        flags["sponsorship"] = {"value": "Sponsorship available", "evidence": _evidence(text, m)}
    else:
        flags["sponsorship"] = {"value": NOT_STATED, "evidence": None}

    if m := _CITIZEN.search(text):
        flags["citizenship"] = {"value": "US citizenship required", "evidence": _evidence(text, m)}
    else:
        flags["citizenship"] = {"value": NOT_STATED, "evidence": None}

    if m := _CLEARANCE.search(text):
        flags["clearance"] = {"value": "Clearance required", "evidence": _evidence(text, m)}
    else:
        flags["clearance"] = {"value": NOT_STATED, "evidence": None}
    return flags
