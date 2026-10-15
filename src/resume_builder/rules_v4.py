"""resume_rules.md v4.1 checks for a master v4 resume (the bank's `v4` extras). `problems()` are errors (the build
refuses the content, naming the rule); `lint()` findings go to the reviewer.

Precedence (rule 2): the master's Builder notes set style and selection — bullets per role, courses, which bullets
overlap — and override the general rules there; they never relax a truthfulness rule (4–15), which is checked here
whatever a note says.
"""
from __future__ import annotations

import re

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


def mentions(analysis: dict | None, name: str) -> bool:
    """The JD mentions this skill (its canonical term, or the name itself as a word)."""
    if not analysis:
        return False
    base = re.sub(r"\s*\(.*\)$", "", name)
    c = canonical(base)
    if c in jd_terms(analysis):
        return True
    return bool(re.search(rf"(?<![\w.+#]){re.escape(base)}(?![\w+#])", jd_text(analysis), re.I))


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
                if not mentions(analysis, s.name):
                    out.append(f"skills: '{s.name}' is self-reported: only when the JD explicitly asks for it (rule 53)")
            elif tier == "coursework":
                if not mentions(analysis, re.sub(r"\s*\(coursework\)$", "", s.name)):
                    out.append(f"skills: '{s.name}' is coursework-level: only when the JD asks for it (rule 75)")
                if "(coursework)" not in s.name:
                    out.append(f"skills: '{s.name}' is coursework-level: list it as '{s.name} (coursework)' (rule 75)")
            elif tier == "extended" and analysis and not mentions(analysis, s.name):
                out.append(f"skills: '{s.name}' is an extended skill the JD doesn't mention (rule 53)")
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
    for ex in [*c.experience, *c.projects]:
        verbs = [b.text.split()[0].lower() for b in ex.bullets if b.text.split()]
        for a, b_ in zip(verbs, verbs[1:]):
            if a == b_:
                out.append({"where": ex.entry_id, "rule": "consecutive verbs",
                            "detail": f"two consecutive bullets start with '{a}' (rule 45)"})
    return out
