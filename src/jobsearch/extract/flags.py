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
# Export-control (ITAR/EAR) "U.S. person" requirements: citizens, permanent residents and protected individuals only.
_USP = r"[“\"']?u\.?\s?s\.?\s+persons?[”\"']?"
_US_PERSON = re.compile(
    rf"(must|shall|will need to)\s+(be|qualify as)\s+(a\s+)?{_USP}"
    rf"|requires?\s+(a\s+|being\s+a\s+|that\s+\w+\s+(be|is)\s+a\s+)?{_USP}"
    rf"|(only|limited to|restricted to)\s+{_USP}"
    rf"|{_USP}\s+(only|status\s+(is\s+)?required|(is\s+)?required|as\s+defined)"
    rf"|\b(itar|export control\w*|ear)\b[^.\n]{{0,120}}\b{_USP}",
    re.I,
)
# the statutes that define a U.S. person (22 CFR 120.62, formerly 120.15; 8 U.S.C. 1324b(a)(3) protected individual)
_CFR = re.compile(r"22\s*c\.?\s?f\.?\s?r\.?\s*(?:§\s*|part\s*)?120\.(?:62|15)|8\s*u\.?\s?s\.?\s?c\.?\s*(?:§\s*)?1324b", re.I)
_GREEN = re.compile(r"green[- ]card|permanent residen\w*|protected individual", re.I)
_EXPORT = re.compile(r"\b(?:itar|(?-i:EAR)|export[- ]controll?\w*|export administration regulations|"
                     r"international traffic in arms|export (?:requirements|regulations|laws|compliance|restrictions))\b",
                     re.I)
_REQ = re.compile(r"\b(must|requires?|required|only|need to be|subject to|limited to|restricted to)\b", re.I)
_EXPORT_ACCESS = re.compile(r"\b(access|eligib\w*|licen[cs]\w*|release)\b", re.I)
# "citizen, permanent resident, or otherwise authorized to work": open to anyone authorized, not a restriction
_OPEN_AUTH = re.compile(r"\botherwise\s+(legally\s+|lawfully\s+)?(authori[sz]ed|eligible)|"
                        r"\bor\s+(have|hold|possess)\s+(a\s+)?(valid\s+)?(work|employment)\s+authori[sz]ation|"
                        r"\bor\s+(be\s+)?(legally\s+|lawfully\s+)?authori[sz]ed to work|\bvisa holders?", re.I)
# export rules aimed at nationals of sanctioned countries, not a U.S.-person requirement: check, don't filter. (The ITAR
# boilerplate "… or eligible to obtain the required authorizations from the U.S. Department of State" stays a hard
# requirement: employers don't seek export licences for new hires.)
_SANCTIONS = re.compile(r"\bsanctioned|\bembargoed", re.I)
# "must not require sponsorship (e.g. H-1B or green card)": a sponsorship statement (the sponsorship flag), not a
# U.S.-person rule
_SPONSOR_CTX = re.compile(r"sponsor\w*|\bvisas?\b|\bH-?1B\b|nonimmigrant|\bimmigrants?\b", re.I)
_AUTH_ROUTE = re.compile(r"authori[sz]ations?|licen[cs]e", re.I)
# the requirement is about this job / its candidates, not the company's products in general
_ROLE = re.compile(r"\b(position|role|job|candidates?|applicants?|you|hires?|employees?|this opportunity)\b", re.I)
_SKILL = re.compile(r"\b(experience|familiarity|knowledge|understanding|expertise)\b", re.I)
_US_BASED = re.compile(r"\bif (this|the) (role|position|job) is (based|located) in the (united states|u\.?\s?s\.?),?\s*", re.I)
# EEO / anti-discrimination statements list citizenship status as a protected class: never a requirement
_EEO = re.compile(r"without regard to|regardless of|irrespective of|discriminat\w*|equal (employment )?opportunit\w*|"
                  r"\beeo\b|affirmative action|protected (veteran|class|characteristic|status)|on the basis of|"
                  r"\b(race|religion|national origin|sexual orientation)\b", re.I)
# Only this job, unconditionally: "positions requiring…" / "where required" scope it; "may" / "might" hedge it
_SCOPE = re.compile(r"\b(positions? (with|that|requiring)|roles? (with|that|requiring)|for (all|certain|some|select) "
                    r"(positions|roles)|if (the|this|a) (position|role)|where (required|applicable)|when required)\b", re.I)
_HEDGE = re.compile(r"\b(may|might|could|potentially|possibly)\b", re.I)
_ABBR_END = re.compile(r"(?:\b(?:[A-Za-z]\.){1,3}[A-Za-z]|\b[A-Z]|\b(?:e\.g|i\.e|etc|vs|no|inc|corp|co|jr|sr|st|dr|mr|ms))$",
                       re.I)
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


# Structured headers: "Clearance: Secret", "Security Clearance: Active TS/SCI", "CLEARANCE REQUIRED FOR START: Yes",
# "CLEARANCE TYPE: SCI". None / N/A / No / Public Trust (a background check, not a clearance) don't count.
_CLEARANCE_HEADER = re.compile(
    r"\b(?:security\s+)?clearance(?:\s+(?:type|level|required(?:\s+for\s+start)?))?\s*:\s*"
    r"(?!none\b|n/?a\b|no\b|not\b|public\s+trust\b)"
    r"(yes\b|(?:active\s+|current\s+|interim\s+)?(?:ts\s*/\s*sci|top\s+secret|secret|sci|confidential|q\b|l\b|dod|doe|"
    r"u\.?s\.?|eligib|must|candidates must|this position requires|active))", re.I)
# Titles: "(Active SCI)", "TS/SCI", "Top Secret", "Active Secret", "Secret Clearance", "w/ Poly", "Cleared"
_CLEARANCE_TITLE = re.compile(
    r"\b(ts\s*/\s*sci|top\s+secret|sci|polygraph|poly|(?:active|interim|current)\s+secret|secret\s+(?:clearance|cleared)|"
    r"clearance\s+required|cleared)\b", re.I)


def _evidence(text: str, m: re.Match) -> str:
    start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
    end_candidates = [i for i in (text.find(".", m.end()), text.find("\n", m.end())) if i != -1]
    end = min(end_candidates) if end_candidates else len(text)
    return text[start:end + 1].strip()[:240]


def _sentences(text: str):
    """Sentences and lines; a period inside "U.S." / "U.S.C." / "120.62" doesn't end one."""
    start = 0
    for m in re.finditer(r"[.!?](?=\s|$)|\n", text):
        if m.group(0) == "." and _ABBR_END.search(text[max(start, m.start() - 12):m.start()]):
            continue
        s = text[start:m.end()].strip()
        if s:
            yield s
        start = m.end()
    if text[start:].strip():
        yield text[start:].strip()


def us_person_kind(s: str) -> str | None:
    """One sentence → "US citizenship required" / "US person required (export control)" (hard), "possible" (export
    control mentioned, but scoped to some roles or hedged: check eligibility), or None (no requirement: EEO
    boilerplate, "or otherwise authorized to work", a skill mention)."""
    s = _US_BASED.sub("", s)                         # HireScope lists U.S. jobs: "if this role is based in the U.S."
    citizen, person, cite = _CITIZEN.search(s), _US_PERSON.search(s), _CFR.search(s)
    green = _GREEN.search(s) and _REQ.search(s)
    export = _EXPORT.search(s)
    if not (citizen or person or cite or green or export) or _EEO.search(s) or _OPTIONAL.search(s):
        return None
    if _OPEN_AUTH.search(s) and not person and not re.search(r"\bonly\b", s, re.I):
        return None
    personish = bool(person or cite or export or _PERSON_WORDS.search(s))
    if export and _SKILL.search(s) and not (person or cite or citizen or _PERSON_WORDS.search(s)):
        return None                                  # "experience with ITAR compliance": a skill
    if _SCOPE.search(s) or (personish and _SANCTIONS.search(s)) or (
            personish and _HEDGE.search(s) and re.search(r"\bexcept\b", s, re.I)):
        return "possible" if personish else None     # "… except U.S. persons may have to go through a licence review"
    only_green = green and not (person or cite or citizen or export)
    if only_green and _SPONSOR_CTX.search(s):
        return None
    if export and not (person or cite or citizen or _PERSON_WORDS.search(s)) and _AUTH_ROUTE.search(s):
        return "possible"                            # "should be eligible for any required authorizations"
    if person or cite:
        return "US person required (export control)"
    if citizen and not _HEDGE.search(s):
        return "US person required (export control)" if _PERSON_WORDS.search(s) else "US citizenship required"
    if _HEDGE.search(s):
        return "possible" if personish else None
    if green or (export and _REQ.search(s) and _ROLE.search(s)):
        return "US person required (export control)"
    if export and (_EXPORT_ACCESS.search(s) or _REQ.search(s)):
        return "possible"
    return None


def extract_flags(text: str, title: str = "") -> dict[str, dict]:
    """{'sponsorship': {'value', 'evidence'}, 'citizenship': {...}, 'clearance': {...}}. The title counts for the
    clearance flag ("Software Engineer (Active SCI)")."""
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

    # Hard citizenship / U.S. person requirements → "citizenship"; export control that only might apply (scoped to
    # some roles, or hedged with "may") → "export_control" (check eligibility, never filtered)
    flags["citizenship"] = {"value": NOT_STATED, "evidence": None}
    flags["export_control"] = {"value": NOT_STATED, "evidence": None}
    for sent in _sentences(text):
        kind = us_person_kind(sent)
        if kind == "possible":
            if flags["export_control"]["value"] == NOT_STATED:
                flags["export_control"] = {"value": "Possibly required (export control)", "evidence": sent[:240]}
        elif kind:
            flags["citizenship"] = {"value": kind, "evidence": sent[:240]}
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
    if flags["clearance"]["value"] != "Clearance required":
        if m := _CLEARANCE_TITLE.search(title or ""):
            flags["clearance"] = {"value": "Clearance required", "evidence": f"Title: {title}"}
        elif m := _CLEARANCE_HEADER.search(text):
            ev = _evidence(text, m)
            if not _OPTIONAL.search(ev):
                flags["clearance"] = {"value": "Clearance required", "evidence": ev}
    return flags
