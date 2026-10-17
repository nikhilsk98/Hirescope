"""resume_rules.md v4.3 checks for a master v4 resume (the bank's `v4` extras). `problems()` are errors (the build
refuses the content, naming the rule); `lint()` findings go to the reviewer.

Precedence (rule 2): the master's Builder notes set style and selection — bullets per role, courses, which bullets
overlap — and override the general rules there; they never relax a truthfulness rule (4–15), which is checked here
whatever a note says.
"""
from __future__ import annotations

import re
from functools import lru_cache

from rapidfuzz import fuzz

from .evidence import Bank, canonical, terms_in

ORDER = ["education", "skills", "experience", "projects"]                    # rule 24, every level
METRIC_KINDS = {                                                               # rule 8
    "latency / p95": re.compile(r"\bp95\b|\blatency\b|response time", re.I),
    "throughput": re.compile(r"throughput|messages/sec|msg/s|requests/sec|/sec\b", re.I),
    "deployment / pipeline duration": re.compile(r"(?:deploy\w*|pipeline)[^.;]*?\b\d+\s*(?:to|→)\s*\d+\s*min|"
                                                 r"(?:deployment|pipeline) (?:time|duration)", re.I),
    "query time": re.compile(r"query[^.;]*?\b\d+\s*ms\b|execution time", re.I),
}
COMPLIANCE = re.compile(r"\bHIPAA\b|\bPCI\b|\bcomplian\w*|\bregulator\w*|clinical[- ]records?", re.I)     # rule 13
PERF = re.compile(r"\d\s*%|\b\d+\s*ms\b|\b\d+(?:\.\d+)?\s*x\b|faster|\b\d+\s*(?:minutes?|seconds?|min)\b", re.I)
STUDENT = re.compile(r"\b(learned|gained exposure|assisted (?:with|in)|familiari[sz]ed|helped)\b", re.I)  # rule 78
INTERN_OWN = re.compile(r"\b(owned|led|architected|spearheaded|designed the system)\b", re.I)          # Rescho note
TEAM_OWN = re.compile(r"\b(designed the (?:architecture|system)|architected the|owned the kubernetes|"
                      r"ran production releases?|led the (?:migration|team|modernization))\b", re.I)       # rule 11
DESIGN_VERB = re.compile(r"^(Designing|Architecting|Planning|Prototyping|Specifying|Modeling|Researching)\b")
RESULT = re.compile(r"\b(reduc\w*|improv\w*|increas\w*|cut|achiev\w*|outperform\w*|boost\w*)\b[^.]*\d", re.I)
TDD = re.compile(r"\btest[- ]driven\b|\bTDD\b", re.I)                                                  # rule 82
# rule 81: the areas a resume should show with distinct evidence (bullet text or JD text → areas)
AREAS = {
    "frontend": re.compile(r"\b(React|Redux|Vue(?:\.js)?|Nuxt(?:\.js)?|Angular|TypeScript|front[- ]?ends?|UI|"
                           r"dashboards?|storefront|screens|views|interfaces?|components)\b", re.I),
    "backend / database": re.compile(r"\b(Spring Boot|Node\.js|Express|REST\w*|APIs?|endpoints?|back[- ]?ends?|"
                                     r"microservices?|SQL|PostgreSQL|MySQL|MongoDB|relational databases?|databases?)\b",
                                     re.I),
    "Python / data pipelines": re.compile(r"\b(Python|pandas|NumPy|data[- ](?:imports?|pipelines?|health|ingestion)|"
                                          r"ETL|scikit-learn|Lambda|imports?)\b", re.I),
    "testing": re.compile(r"\b(tests?|testing|JUnit|Mockito|PyTest|test-driven|TDD|differential|QA|regression)\b",
                          re.I),
    "delivery": re.compile(r"\b(CI/CD|GitHub Actions|Jenkins|Docker|deploy\w*|Terraform|Kubernetes|releases?|"
                           r"pipelines? duration)\b", re.I),
}
_STOP = set("""a an and the of to for in on with by from as at or into using use used via over under across their
its this that these those our your you we will be is are was were can able new build built design support team
software application applications develop developed tools data experience strong""".split())
SYNONYM_NOUNS = {"distributor": ("wholesaler",), "wholesaler": ("distributor",), "clinic": ("practice",)}


def v4(bank: Bank) -> dict:
    return bank.v4 or {}


def metric_kinds(text: str) -> list[str]:
    if not re.search(r"\d", text):
        return []
    return [k for k, rx in METRIC_KINDS.items() if rx.search(text)]


def bank_id(bank: Bank, item_id: str) -> str:
    """A snapshot line → the bank bullet it was written from (best match in the same entry), for overlap and
    per-client limits; a bank bullet is itself."""
    it = bank.items.get(item_id)
    if not it or it.kind != "snapshot":
        return item_id
    best, score = item_id, 0
    for c in bank.items.values():
        if c.kind == "bullet" and c.entry_id == it.entry_id:
            s = fuzz.token_set_ratio(c.text, it.text)
            if s > score:
                best, score = c.id, s
    return best if score >= 70 else item_id


def _group(bank: Bank, iid: str) -> str | None:
    it = bank.items.get(bank_id(bank, iid))
    return it.category if it else None


def jd_terms(analysis: dict | None) -> set[str]:
    if not analysis:
        return set()
    return {k["canonical"] for k in analysis.get("keywords", [])} | {
        t for r in analysis.get("requirements", []) for t in r.get("terms", [])}


def jd_text(analysis: dict | None) -> str:
    if not analysis:
        return ""
    return " ".join([analysis.get("role") or "", *analysis.get("required", []), *analysis.get("preferred", []),
                     *analysis.get("mentioned", []), *analysis.get("responsibilities", []),
                     *[k["term"] for k in analysis.get("keywords", [])]])


@lru_cache(maxsize=64)
def _text_terms(text: str) -> frozenset[str]:
    return frozenset(terms_in(text))


def mentions(analysis: dict | None, name: str) -> bool:
    """The JD mentions this skill (its canonical term, or the name itself as a word)."""
    if not analysis:
        return False
    base = re.sub(r"\s*\(.*\)$", "", name)
    if TDD.search(base):                 # "test-driven software development" in a JD asks for TDD (rule 82)
        return bool(TDD.search(jd_text(analysis)))
    c = canonical(base)
    if c in jd_terms(analysis) or c in _text_terms(jd_text(analysis)):    # incl. responsibilities lines
        return True
    return bool(re.search(rf"(?<![\w.+#]){re.escape(base)}(?![\w+#])", jd_text(analysis), re.I))


def _words(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z][a-z0-9+#.-]{3,}", (text or "").lower()) if w not in _STOP}


def responsibility_score(text: str, analysis: dict | None) -> float:
    """Rule 79: how many of the JD's responsibilities this accomplishment speaks to (two or more shared content
    words), 0.5 each, at most 1.5 — added to the technology score so ranking isn't by tool names alone."""
    if not analysis:
        return 0.0
    w = _words(text)
    n = sum(1 for r in analysis.get("responsibilities", []) if len(w & _words(r)) >= 2)
    return min(1.5, 0.5 * n)


def areas_of(text: str) -> list[str]:
    return [a for a, rx in AREAS.items() if rx.search(text or "")]


def jd_areas(analysis: dict | None) -> list[str]:
    return areas_of(jd_text(analysis)) if analysis else []


def jd_relevant_skill(analysis: dict | None, name: str) -> bool:
    """The JD names this skill, or a general term it asks for covers it (relational databases → MySQL, rule 77)."""
    if not analysis or mentions(analysis, name):
        return True
    from .evidence import vocab
    from .jd_analysis import _CATEGORY_TERMS
    c = canonical(re.sub(r"\s*\(.*\)$", "", name))
    v = vocab()
    terms = jd_terms(analysis) | _text_terms(jd_text(analysis))             # incl. the JD's own wording
    return any(c in (*v.implies.get(t, ()), *_CATEGORY_TERMS.get(t, ())) for t in terms)


def _clients(bank: Bank, eid: str) -> dict[str, tuple[str, set[str]]]:
    """Bullet-bank group → (client key, nouns a bullet may use to refer back to it). A group whose sector says "same"
    is the previous group's client (the distributor's two apps)."""
    groups = (v4(bank).get("roles", {}).get(eid) or {}).get("groups", {})
    raw, prev = {}, None
    for g, sector in groups.items():
        if len(g) != 1:
            continue
        s = re.sub(r"\(.*?\)", "", sector.split("—")[-1]).strip()
        key = prev if prev and re.search(r"\bsame\b", s, re.I) else g
        noun = (re.findall(r"[A-Za-z-]+", s) or [""])[-1].lower()
        raw[g] = (key, {noun, *SYNONYM_NOUNS.get(noun, ())} - {""})
        if key == g:
            prev = g
    merged: dict[str, set[str]] = {}
    for key, nouns in raw.values():
        merged.setdefault(key, set()).update(nouns)
    return {g: (key, merged[key]) for g, (key, _n) in raw.items()}


def backref_problems(c, bank: Bank) -> list[str]:
    """Rule 84: a bullet that refers back to a client ("for the distributor") needs an earlier bullet in the same role
    that introduces that client, unless it introduces the client itself ("for a packaging wholesaler")."""
    out = []
    for ex in c.experience:
        cl = _clients(bank, ex.entry_id)
        if not cl:
            continue
        seen: set[str] = set()
        for b in ex.bullets:
            g = next((x for x in (_group(bank, ev) for ev in b.evidence_ids) if x in cl), None)
            if not g:
                continue
            key, nouns = cl[g]
            if key not in seen:
                for n in sorted(nouns):
                    m = re.search(rf"\bthe\s+(?:[\w-]+\s+){{0,2}}{re.escape(n)}\b", b.text, re.I)
                    if m and not re.search(rf"\ban?\s+(?:[\w-]+\s+){{0,4}}{re.escape(n)}\b", b.text, re.I):
                        out.append(f"{b.id}: refers back to '{m.group(0)}' before any bullet introduces that client; "
                                   "introduce the client's sector first or reorder (rule 84)")
                        break
            seen.add(key)
    return out


def skill_trace_problems(c, bank: Bank) -> list[str]:
    """Rule 83: every Skills and certification item cites the master entry it comes from (a skill of any tier or a
    certification) under its own name; the JD only selects among them, it never adds one."""
    out = []
    I = bank.items
    certs = {x["id"]: x["name"] for x in v4(bank).get("certs", [])}

    def key(n: str) -> str:
        return canonical(re.sub(r"\s*\(.*\)$", "", n.replace("Certified: ", "")).strip()).casefold()
    for row in c.skills:
        is_cert = row.category.casefold() == "certifications"
        for s in row.items:
            if is_cert:
                ok = any(ev in certs and key(certs[ev]) == key(s.name) for ev in s.evidence_ids)
            else:
                ok = any(ev in I and I[ev].section == "skill" and I[ev].kind != "cert" and (
                    I[ev].text == s.name or key(I[ev].text) == key(s.name)) for ev in s.evidence_ids)
            if not ok:
                out.append(f"skills: '{s.name}' doesn't cite the master {'certification' if is_cert else 'skill'} it "
                           "comes from; every Skills item traces to the master, the JD never adds one (rule 83)")
    return out


ML_JD = re.compile(r"\bmachine[- ]learning\b|\bML\b|\bdeep learning\b", re.I)
CLOUD_JD = re.compile(r"\bcloud\b", re.I)


def skill_category(bank: Bank, name: str) -> str:
    cats = v4(bank).get("skills", {}).get("category", {})
    c = canonical(re.sub(r"\s*\(.*\)$", "", name)).casefold()
    return next((v for n, v in cats.items() if canonical(re.sub(r"\s*\(.*\)$", "", n)).casefold() == c), "") or ""


def named_in(text: str, name: str) -> bool:
    base = re.sub(r"\s*\(.*\)$", "", name)
    return bool(re.search(rf"(?<![\w.]){re.escape(base)}(?![\w])", text or "", re.I))


def extended_ok(analysis: dict | None, name: str, bank: Bank, bullets_text: str) -> bool:
    """Rule 53 + 85: an extended skill is listed when the JD names it (or an umbrella term it satisfies: the JD's
    "web-sockets" → Socket.IO, config/ats_keywords.yaml implies); an extended ML skill also when the JD asks for
    machine learning and a bullet on the page names it (rule 85); an extended cloud platform (GCP) also when the JD
    asks for cloud."""
    if not analysis:
        return False
    if jd_relevant_skill(analysis, name):    # named, or an umbrella term it satisfies (JD "web-sockets" → Socket.IO)
        return True
    cat = skill_category(bank, name).casefold()
    jt = jd_text(analysis)
    if cat in ("ml", "ai & ml") and (ML_JD.search(jt) or "Machine Learning" in jd_terms(analysis)) \
            and named_in(bullets_text, name):
        return True
    return cat == "cloud" and bool(CLOUD_JD.search(jt))


def tier_of(bank: Bank, name: str) -> str | None:
    tiers = v4(bank).get("skills", {}).get("tier", {})
    if name in tiers:
        return tiers[name]
    c = canonical(re.sub(r"\s*\(.*\)$", "", name))
    for n, t in tiers.items():
        if canonical(re.sub(r"\s*\(.*\)$", "", n)) == c:
            return t
    return None


def is_cloud(analysis: dict | None, profile: str | None) -> bool:
    return profile == "cloud" or bool(analysis and re.search(r"\b(cloud|aws|azure|gcp|devops)\b",
                                                              analysis.get("role") or "", re.I))


# rule 2: master "Builder:" notes that name a role type ("Use bullet 3 for data-engineering, MLOps, or cloud roles";
# "Swap D2 (…) in for C1 on cloud and backend-distributed roles") are applied when the JD is that type of role
NOTE_USE = re.compile(r"\buse bullet (\d+)\b[^.]*?\bfor ([^.]*?)\broles?\b", re.I)
NOTE_SWAP = re.compile(r"\bswap ([A-Z]\d+)\b[^.]*?\bin for ([A-Z]\d+)\b on ([^.]*?)\broles?\b", re.I)
ML_OPS = re.compile(r"\b(deploy\w*|monitor\w*|serving|retrain\w*|productioni[sz]\w*|operationali[sz]\w*|drift|"
                    r"model registry)\b", re.I)


def _jd_lines(analysis: dict) -> list[str]:
    return [*analysis.get("responsibilities", []), *analysis.get("required", []), *analysis.get("preferred", []),
            *analysis.get("mentioned", [])]


def role_type_match(analysis: dict | None, profile: str | None, kind: str) -> bool:
    """Does this JD fit a role type a master note names? MLOps: "MLOps" in the JD, or a responsibility / requirement
    about deploying, monitoring or retraining machine-learning models (one line names both)."""
    if not analysis:
        return False
    k = re.sub(r"[\s_]+", "-", kind.strip().casefold())
    role = analysis.get("role") or ""
    if k == "mlops":
        return bool(re.search(r"\bMLOps\b", jd_text(analysis), re.I)) or any(
            ML_JD.search(ln) and ML_OPS.search(ln) for ln in _jd_lines(analysis))
    if k == "cloud":
        return is_cloud(analysis, profile)
    if k in ("ml", "machine-learning"):
        return bool(ML_JD.search(jd_text(analysis)))
    if k in ("data-engineering", "data-engineer", "data"):
        return profile == "data" or bool(re.search(r"\bdata engineer|\bETL\b|\bdata pipelines?\b", jd_text(analysis), re.I))
    if k in ("backend-distributed", "distributed"):
        return bool(re.search(r"\bdistributed\b", jd_text(analysis), re.I)) and (
            profile in ("backend", "cloud") or bool(re.search(r"\bback[- ]?end\b", role, re.I)))
    if k in ("backend", "back-end"):
        return profile == "backend" or bool(re.search(r"\bback[- ]?end\b", role, re.I))
    if k in ("frontend", "front-end"):
        return profile == "fullstack" and bool(re.search(r"\bfront[- ]?end\b", role, re.I))
    if k in ("full-stack", "fullstack"):
        return profile == "fullstack" or bool(re.search(r"\bfull[- ]?stack\b", role, re.I))
    return False


def _kinds(text: str) -> list[str]:
    return [x for x in (y.strip(" ,") for y in re.split(r",|\bor\b|\band\b", text)) if x]


def note_swaps(bank: Bank, analysis: dict | None, profile: str | None) -> list[dict]:
    """The master's role-type notes that apply to this JD: {eid, add, replace, kinds, note} (bank ids)."""
    X = v4(bank)
    if not (X and analysis):
        return []
    out = []
    srcs = [(eid, p.get("notes", [])) for eid, p in X.get("projects", {}).items()] + \
           [(eid, r.get("notes", [])) for eid, r in X.get("roles", {}).items()]
    for eid, notes in srcs:
        for note in notes:
            for m in NOTE_USE.finditer(note):
                kinds = [k for k in _kinds(m.group(2)) if role_type_match(analysis, profile, k)]
                add = f"{eid}-b{m.group(1)}"
                if kinds and add in bank.items:
                    out.append({"eid": eid, "add": add, "replace": None, "kinds": kinds, "note": note})
            for m in NOTE_SWAP.finditer(note):
                kinds = [k for k in _kinds(m.group(3)) if role_type_match(analysis, profile, k)]
                add, rep = f"{eid}-{m.group(1).lower()}", f"{eid}-{m.group(2).lower()}"
                if kinds and add in bank.items:
                    out.append({"eid": eid, "add": add, "replace": rep if rep in bank.items else None,
                                "kinds": kinds, "note": note})
    return out


# rule 67: a shortened bullet keeps its evaluation / result clause while Technical Skills still has JD-irrelevant
# items to cut ("evaluated predictions on held-out labeled documents", "integrated … into the application")
EVAL = re.compile(r"\b(evaluat\w*|validat\w*|held[- ]out|benchmark\w*|verifi\w*|measur\w*|integrat\w*(?=[^,;.]*\binto\b))",
                  re.I)


def dropped_clauses(text: str, source: str) -> list[str]:
    """Evaluation / result phrases of the cited evidence that a reworded bullet no longer has."""
    low = text.casefold()
    out = []
    for m in EVAL.finditer(source):
        if m.group(1).casefold()[:6] not in low:
            out.append(source[m.start():m.start() + 60].split(";")[0].rstrip(" ,"))
    for m in RESULT.finditer(source):
        # a result is kept while one of its figures is: a reworded verb ("reducing" → "cutting") or one form of a
        # before/after figure ("by 68%" for "68%, from 25 to 8 minutes"; the master allows one form when space is tight)
        nums = re.findall(r"\d+(?:\.\d+)?", m.group(0))
        if m.group(1).casefold()[:6] not in low and not (nums and any(re.search(rf"(?<![\d.]){re.escape(x)}(?![\d])",
                                                                            text) for x in nums)):
            out.append(source[m.start():m.start() + 60].split(";")[0].rstrip(" ,"))
    return out


def irrelevant_skills(c, analysis: dict | None) -> list[str]:
    """Technical Skills items the JD doesn't ask for and no bullet shows (what rule 67 trims first)."""
    if not analysis:
        return []
    shown = " ".join(b.text for _s, _e, b in c.all_bullets())
    return [s.name for row in c.skills if row.category.casefold() != "certifications" for s in row.items
            if not jd_relevant_skill(analysis, s.name) and not named_in(shown, s.name)]


def wants_relocation(analysis: dict | None) -> bool:
    """Rule 31: remote or out-of-state jobs get the relocation line. A job listing Massachusetts among its locations
    is in-state; no stated location → the default line."""
    loc = ((analysis or {}).get("job") or {}).get("location") or ""
    if re.search(r"\bremote\b", loc, re.I):
        return True
    if not loc.strip():
        return False
    return not re.search(r"\bMA\b|massachusetts|\bboston\b|\bcambridge\b", loc, re.I)


def _intern(bank: Bank, eid: str) -> bool:
    e = bank.entries.get(eid)
    return bool(e and e.section == "experience" and "intern" in (e.role or "").casefold())


def audience_projects(bank: Bank, settings, analysis: dict | None) -> tuple[int, int]:
    """(default, max) projects: rules 46–48 — 2; 3 only for co-op roles that need it; 1 for mid-level."""
    if not analysis:
        return 2, 2
    aud = settings.audience(analysis.get("track"), analysis.get("seniority"),
                            (analysis.get("eligibility") or {}).get("yoe_required"))
    if aud == "experienced":
        return 1, 1
    return 2, 3 if analysis.get("track") == "coop_intern" else 2


def problems(c, bank: Bank, settings, analysis: dict | None) -> list[str]:
    X = v4(bank)
    if not X:
        return []
    out: list[str] = []
    I = bank.items
    roles = X.get("roles", {})
    order = [s for s in c.section_order]
    if order != ORDER:
        out.append(f"section order {order}: every resume uses {ORDER} (rules 24–25; never reorder)")
    if c.summary:
        out.append("summary: never included (rule 28)")
    for _s, eid, b in c.all_bullets():           # name every bullet in messages, even before ids are assigned
        if not b.id:
            b.id = f"{eid}:new{abs(hash(b.text)) % 10000}"
    bullets = list(c.all_bullets())

    # rule 4: usable evidence only; extended bank bullets only when the JD calls for them
    for _s, eid, b in bullets:
        for ev in b.evidence_ids:
            it = I.get(ev)
            if not it:
                continue
            if it.kind not in ("bullet", "snapshot", "own_words", "context", "metric") and it.section != "coursework":
                out.append(f"{b.id}: cites '{ev}' ({it.kind}), which is not bullet evidence (rule 4)")
            if getattr(it, "level", None) == "extended" and it.kind == "bullet":
                if not (set(it.technologies) & jd_terms(analysis)):
                    out.append(f"{b.id}: uses an extended-bank bullet ({ev}) the job description doesn't call for "
                               "(rule 4: extended only when the JD needs it, and prep it first)")
    # rule 8: one metric per kind
    kinds: dict[str, list[str]] = {}
    for _s, _e, b in bullets:
        for k in metric_kinds(b.text):
            kinds.setdefault(k, []).append(b.id)
    for k, ids in kinds.items():
        if len(ids) > 1:
            out.append(f"rule 8: {len(ids)} {k} figures on one resume ({', '.join(ids)}); keep the stronger one and "
                       "use a non-metric bullet from the other role")
    # rules 11–14, 45, 50, 78 per bullet
    excluded_skills = {x.casefold() for x in X.get("skills", {}).get("excluded", [])}
    for sec, eid, b in bullets:
        t = b.text
        r = roles.get(eid, {})
        for name in r.get("skill_only", []):
            if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", t):
                out.append(f"{b.id}: {name} is a skill-only fact — Technical Skills only, never inside a bullet (rule 12)")
        for name, where in (r.get("barred") or {}).items():
            if where == eid and re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", t):
                out.append(f"{b.id}: {name} stays out of this role's bullets until the master describes a component "
                           "built with it (rule 12)")
        if COMPLIANCE.search(t):
            out.append(f"{b.id}: implies regulatory-compliance work ('{COMPLIANCE.search(t).group(0)}') (rule 13)")
        if _intern(bank, eid):
            if PERF.search(t):
                out.append(f"{b.id}: performance numbers in an internship bullet ('{PERF.search(t).group(0)}') (rule 14)")
            if INTERN_OWN.search(t):
                out.append(f"{b.id}: '{INTERN_OWN.search(t).group(0)}' — keep internship claims intern-sized (master note)")
        if sec == "experience" and TEAM_OWN.search(t):
            out.append(f"{b.id}: '{TEAM_OWN.search(t).group(0)}' presents team-owned work as your own (rule 11)")
        if STUDENT.search(t):
            out.append(f"{b.id}: student-style phrasing '{STUDENT.search(t).group(0)}' (rule 78)")
        for x in excluded_skills:
            if re.search(rf"(?<![\w.]){re.escape(x)}(?![\w])", t, re.I) and not re.search(rf"{re.escape(x)}\s+SQL", t, re.I):
                out.append(f"{b.id}: names '{x}', which the master excludes")
        if sec == "projects" and X.get("projects", {}).get(eid, {}).get("in_progress"):
            if not DESIGN_VERB.match(t):
                out.append(f"{b.id}: in-progress project: start with a design verb ('Designing', 'Architecting'), "
                           "never 'Building' or a past-tense result (rule 50)")
            if RESULT.search(t) or re.search(r"\d+\s*%", t):
                out.append(f"{b.id}: in-progress project: no results or metrics (rule 50)")
    # overlap notes (A3/C2, B3/Material UI): at most one of each group
    used = {bank_id(bank, ev) for _s, _e, b in bullets for ev in b.evidence_ids}
    for grp in X.get("overlaps", []):
        hit = [g for g in grp if g in used]
        if len(hit) > 1:
            out.append(f"overlapping bullets {', '.join(hit)}: use at most one per resume (master note)")
    # bullets per role (the master's pick ranges win over rules 33–34; rule 32's total range stays)
    total = 0
    for ex in c.experience:
        n = len(ex.bullets)
        total += n
        pick = (roles.get(ex.entry_id) or {}).get("pick")
        if pick and not (pick[0] <= n <= pick[1]):
            out.append(f"{ex.entry_id}: {n} bullets; the master says {pick[0]}–{pick[1]} for this role")
        r = roles.get(ex.entry_id) or {}
        if ex.title and ex.title not in r.get("titles", []) and ex.title != r.get("display_title"):
            out.append(f"{ex.entry_id}: title '{ex.title}' is not one the master records ({'; '.join(r.get('titles', []))}) "
                       "(rules 10, 37)")
        if ex.title and re.search(r"self[- ]employed|\|", ex.title, re.I) and ex.title != r.get("display_title"):
            out.append(f"{ex.entry_id}: never 'Self-Employed' or keyword tags in the title (rule 37)")
    if not (8 <= total <= 11):
        out.append(f"experience: {total} bullets (rule 32: 8–11)")
    # projects (rules 46–48)
    dflt, mx = audience_projects(bank, settings, analysis)
    if len(c.projects) > mx:
        out.append(f"projects: {len(c.projects)} (rules 46–48: {dflt}" + (f", up to {mx} for co-op roles that need it"
                                                                          if mx > dflt else "") + ")")
    # education (rules 58–59): MS first, 0–3 courses, only completed / in-progress (only "Yes" rows are in the bank)
    if c.education and c.education[0].entry_id != next(iter(X.get("education", {})), c.education[0].entry_id):
        out.append("education: the MS degree comes first (rule 58)")
    for ed in c.education:
        if len(ed.course_ids) > 3:
            out.append(f"education: {len(ed.course_ids)} courses (rule 59: 2 by default, at most 3)")
    # skills: tiers (rule 53), excluded, planned, self-reported ≤ 4 (never in bullets); certifications last (rule 29)
    sk = X.get("skills", {})
    self_n, planned_hit = [], []
    approved = {canonical(x) for x in getattr(c.notes, "approved_skills", [])}
    for x in getattr(c.notes, "approved_skills", []):
        if tier_of(bank, x) != "self-reported":
            out.append(f"notes.approved_skills: '{x}' is not a self-reported skill; approval covers only those (rule 53)")
    rows = [r for r in c.skills]
    if not rows or rows[-1].category.casefold() != "certifications":
        out.append("skills: the last row is Certifications (rule 29)")
    certs = {x["id"]: x for x in X.get("certs", [])}
    for row in rows:
        for s in row.items:
            if row.category.casefold() == "certifications":
                continue
            tier = tier_of(bank, s.name)
            also = {canonical(x) for x in sk.get("planned_also", [])}
            if s.name.casefold() in {x.casefold() for x in sk.get("excluded", [])}:
                out.append(f"skills: '{s.name}' is on the master's Excluded list")
            elif tier == "planned" or canonical(s.name) in also:
                planned_hit.append(s.name)
            elif tier == "declined":
                out.append(f"skills: '{s.name}' — you said you haven't used it (evidence_additions.md)")
            elif tier == "self-reported":
                self_n.append(s.name)
                if not mentions(analysis, s.name) and canonical(re.sub(r"\s*\(.*\)$", "", s.name)) not in approved:
                    out.append(f"skills: '{s.name}' is self-reported: only when the JD explicitly asks for it, or "
                               "when Nikhil approves it for this resume (notes.approved_skills) (rule 53)")
            elif tier == "coursework":
                if not mentions(analysis, re.sub(r"\s*\(coursework\)$", "", s.name)):
                    out.append(f"skills: '{s.name}' is coursework-level: only when the JD asks for it (rule 75)")
                if "(coursework)" not in s.name:
                    out.append(f"skills: '{s.name}' is coursework-level: list it as '{s.name} (coursework)' (rule 75)")
            elif tier == "extended" and analysis and not extended_ok(
                    analysis, s.name, bank, " ".join(b.text for _s, _e, b in bullets)):
                out.append(f"skills: '{s.name}' is an extended skill the JD doesn't mention (rule 53; an ML skill "
                           "also when the JD asks for ML and a bullet names it, rule 85)")
            elif tier is None and row.category.casefold() != "concepts":
                out.append(f"skills: '{s.name}' is not in the master's Skills inventory (rule 53)")
    if planned_hit:
        out.append(f"skills: planned (not built) {', '.join(planned_hit)} — never listed (rules 50, 53)")
    if len(self_n) > int(sk.get("self_max", 4)):
        out.append(f"skills: {len(self_n)} self-reported skills ({', '.join(self_n)}); at most {sk.get('self_max', 4)}")
    self_only = {canonical(re.sub(r"\s*\(.*\)$", "", n)) for n, t in sk.get("tier", {}).items()
                 if t in ("self-reported", "coursework", "declined")}
    for sec, eid, b in bullets:
        # an in-progress project's bullets describe its own planned design (rule 50), which may name that
        # project's planned technology (e.g. Redis in VibeSpace); everywhere else a self-reported skill stays out
        own = set()
        p = X.get("projects", {}).get(eid, {})
        if sec == "projects" and p.get("in_progress"):
            own = {canonical(t) for t in (bank.entries[eid].tech if eid in bank.entries else [])}
        evidenced = {canonical(n) for n, tt in sk.get("tier", {}).items() if tt in ("core", "extended")}
        found = set(terms_in(b.text)) | {canonical(re.sub(r"\s*\(.*\)$", "", n)) for n, tt in sk.get("tier", {}).items()
                                         if tt in ("self-reported", "coursework", "declined") and re.search(
                                             rf"(?<![\w.+#]){re.escape(re.sub(r'\s*\(.*\)$', '', n))}(?![\w+#])", b.text)}
        hit = [t for t in found if t in self_only and t not in own and t not in evidenced]
        if hit:
            out.append(f"{b.id}: names self-reported {', '.join(hit)} — never inside a bullet (rule 53)")
    if rows and rows[-1].category.casefold() == "certifications":
        ids = [ev for s in rows[-1].items for ev in s.evidence_ids]
        for cid, cert in certs.items():
            if cert["rule"] == "always" and cid not in ids:
                out.append(f"certifications: always show {cert['name']} (rule 29)")
            if cert["rule"] == "cloud_or_azure" and cid in ids and not (
                    is_cloud(analysis, c.profile) or mentions(analysis, "Azure")):
                out.append(f"certifications: {cert['name']} only for cloud roles or a JD that mentions Azure (rule 29)")
    # rule 82: TDD is a practice of its own; writing tests never implies it
    for _s, _e, b in bullets:
        if TDD.search(b.text) and not any(ev in I and TDD.search(I[ev].text) for ev in b.evidence_ids):
            out.append(f"{b.id}: claims test-driven development, which no cited evidence states; writing tests is not "
                       "TDD (rule 82)")
    for r in c.requirements:
        if TDD.search(r.text) and r.category == "direct" and not any(
                ev in I and TDD.search(I[ev].text) for ev in r.evidence_ids):
            out.append(f"requirements: '{r.text[:70]}' is marked direct from evidence that doesn't state TDD; "
                       "test-writing is not TDD (rule 82): use transferable or missing_evidence")
    out += skill_trace_problems(c, bank)                                       # rule 83
    out += backref_problems(c, bank)                                           # rule 84
    # rule 2: a master note that names this JD's role type is applied ("Use bullet 3 for … MLOps … roles")
    used_ids = {bank_id(bank, ev) for _s, _e, b in bullets for ev in b.evidence_ids}
    on_page = {x.entry_id for x in [*c.experience, *c.projects]}
    for sw in note_swaps(bank, analysis, c.profile):
        if sw["eid"] in on_page and sw["add"] not in used_ids:
            out.append(f"{sw['eid']}: the master's note applies to this JD ({', '.join(sw['kinds'])} role): use "
                       f"{sw['add']} — \"{sw['note'][:120]}\" (rule 2)")
    # rule 67: cut JD-irrelevant skills before a bullet's evaluation / result clause
    spare = irrelevant_skills(c, analysis)
    if spare:
        for _s, _e, b in bullets:
            # the bullet's own source (first cited bullet); later ids may be cited for a single fact (a sector, a
            # platform name), not as the accomplishment being shortened
            ev = next((e for e in b.evidence_ids if I.get(e) and I[e].kind in ("bullet", "snapshot")), None)
            if ev and (lost := dropped_clauses(b.text, I[ev].text)):
                out.append(f"{b.id}: drops the evaluation / result clause of {ev} (\"{lost[0]}\") while Technical "
                           f"Skills still lists JD-irrelevant {', '.join(spare[:4])}: cut those first (rule 67)")
    # rule 31: relocation line
    want = "relocation" if wants_relocation(analysis) else "default"
    if c.contact_location != want:
        out.append(f"contact: location should be the {want} line for this job's location (rule 31)")
    return out


def lint(c, bank: Bank, settings, analysis: dict | None) -> list[dict]:
    X = v4(bank)
    if not X:
        return []
    out = []
    groups = (X.get("roles", {}).get("exp-freelance") or {}).get("groups", {})
    clients: dict[str, list[str]] = {}
    for sec, eid, b in c.all_bullets():
        if eid == "exp-freelance":
            g = next((_group(bank, ev) for ev in b.evidence_ids if _group(bank, ev)), None)
            if g in ("a", "b"):
                clients.setdefault("wholesale distributor (A + B)", []).append(b.id)
            elif g and len(g) == 1:
                clients.setdefault(groups.get(g, g), []).append(b.id)
            sector = groups.get(g or "", "")
            words = [w for w in re.findall(r"[a-z]{5,}", sector.split("—")[-1].casefold())
                     if w not in ("business", "residential", "small", "accessories", "separate", "same")]
            if g and len(g) == 1 and words and not any(w[:6] in b.text.casefold() for w in words) and \
                    not re.search(r"\b(distributor|wholesaler|clinic|retailer|firm|property)\b", b.text, re.I):
                out.append({"where": b.id, "rule": "sector label",
                            "detail": f"name the client's sector ({sector.split('—')[-1].strip()}) — rule 39"})
    for client, ids in clients.items():
        if len(ids) > 2:
            out.append({"where": ", ".join(ids), "rule": "per-client limit",
                        "detail": f"{len(ids)} bullets for one client ({client}); at most 2 by default (rules 38–39)"})
    if analysis:
        from .jd_analysis import set_category_terms
        set_category_terms(settings)                  # rule 77's specific → general map, also in a fresh process
        out += show_dont_list(c, bank, analysis) + area_findings(c, analysis) + ml_skills_listed(c, bank, analysis)
    for ex in [*c.experience, *c.projects]:
        verbs = [b.text.split()[0].lower() for b in ex.bullets if b.text.split()]
        for a, b_ in zip(verbs, verbs[1:]):
            if a == b_:
                out.append({"where": ex.entry_id, "rule": "consecutive verbs",
                            "detail": f"two consecutive bullets start with '{a}' (rule 45)"})
    return out


def show_dont_list(c, bank: Bank, analysis: dict) -> list[dict]:
    """Rule 80: a JD skill that is only in Technical Skills while an unused bank bullet on a role / project already on
    the page demonstrates it."""
    kws = analysis.get("keywords", [])
    imp = [k for k in kws if k.get("section") in ("required", "preferred")] or kws
    shown = {t for _s, _e, b in c.all_bullets() for t in terms_in(b.text)}
    listed = {canonical(re.sub(r"\s*\(.*\)$", "", s.name)) for row in c.skills for s in row.items}
    on_page = {x.entry_id for x in [*c.experience, *c.projects]}
    used = {bank_id(bank, ev) for _s, _e, b in c.all_bullets() for ev in b.evidence_ids}
    terms = jd_terms(analysis)
    out = []
    from .evidence import vocab
    from .jd_analysis import _CATEGORY_TERMS
    v = vocab()
    for k in imp:
        t = k["canonical"]
        if t in shown or t not in listed or shown & {*v.implies.get(t, ()), *_CATEGORY_TERMS.get(t, ())}:
            continue                                  # a specific tool in a bullet shows the general term (rule 77)
        cands = [i.id for i in bank.items.values() if i.kind == "bullet" and i.entry_id in on_page and i.id not in used
                 and t in i.technologies and (getattr(i, "level", None) != "extended" or set(i.technologies) & terms)]
        if cands:
            out.append({"where": "skills", "rule": "show, don't list",
                        "detail": f"{k['term']} is a JD skill shown only in Technical Skills; "
                                  f"{', '.join(cands[:3])} demonstrate{'s' if len(cands) == 1 else ''} it (rule 80)"})
    return out


def area_findings(c, analysis: dict) -> list[dict]:
    """Rule 81: distinct evidence across the areas the JD asks for; not several bullets showing the same one thing."""
    from collections import Counter
    bullets = [b for _s, _e, b in c.all_bullets()]
    out = []
    for a in jd_areas(analysis):
        if not any(AREAS[a].search(b.text) for b in bullets):
            out.append({"where": "resume", "rule": "distinct areas",
                        "detail": f"no bullet shows {a}, which the JD asks for (rule 81)"})
    single = Counter(areas_of(b.text)[0] for b in bullets if len(areas_of(b.text)) == 1)
    for a, n in single.items():
        if n >= 3:
            out.append({"where": "resume", "rule": "distinct areas",
                        "detail": f"{n} bullets show only {a}; swap one for evidence of another area (rule 81)"})
    return out


def ml_skills_listed(c, bank: Bank, analysis: dict) -> list[dict]:
    """Rule 85: when the JD asks for machine learning, an extended ML skill a bullet names belongs in Skills too."""
    if not (ML_JD.search(jd_text(analysis)) or "Machine Learning" in jd_terms(analysis)):
        return []
    text = " ".join(b.text for _s, _e, b in c.all_bullets())
    listed = {canonical(re.sub(r"\s*\(.*\)$", "", x.name)).casefold() for r in c.skills for x in r.items}
    out = []
    for name, tier in v4(bank).get("skills", {}).get("tier", {}).items():
        if tier == "extended" and skill_category(bank, name).casefold() in ("ml", "ai & ml") and named_in(text, name) \
                and canonical(re.sub(r"\s*\(.*\)$", "", name)).casefold() not in listed:
            out.append({"where": "skills", "rule": "ML skills listed",
                        "detail": f"{name} is named in a bullet and the JD asks for ML: list it in Skills (rule 85)"})
    return out
