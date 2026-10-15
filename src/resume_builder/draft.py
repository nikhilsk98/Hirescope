"""Deterministic baseline: selection and ranking of your own bullets (verbatim) for a job. The drafter (LLM) starts
from this, rewrites for the JD, and keeps every evidence id; locked bullets and preserved manual edits are already in
place and must stay unchanged.

Relevance of an evidence item = Σ JD keyword weights it covers (required ×1, preferred ×1, mentioned ×1, weights
from the HireScope keyword engine already reflect section and frequency) + 0.5 per profile `lead_with` term + 0.3
when it states a metric. Recency breaks ties.
"""
from __future__ import annotations

import re

from .config import Settings, limit, rules_budget
from .content_schema import (Bullet, Content, EducationEntry, ExperienceEntry, Notes, ProjectEntry, RequirementMap,
                             SkillCategory, SkillItem)
from .evidence import Bank, Item, canonical


def relevance(item: Item, analysis: dict, settings: Settings) -> float:
    weights: dict[str, float] = {}
    for k in analysis.get("keywords", []):
        weights[k["canonical"]] = max(weights.get(k["canonical"], 0), float(k.get("weight") or 1))
    for r in analysis.get("requirements", []):
        for t in r.get("terms", []):
            weights.setdefault(t, 1.0 if r["kind"] != "mentioned" else 0.5)
    prof = settings.profiles.get(analysis["profile"]["picked"])
    lead = {canonical(t) for t in (prof.lead_with if prof else [])}
    s = sum(weights.get(t, 0) for t in set(item.technologies))
    s += 0.5 * len(lead & set(item.technologies))
    if item.metrics:
        s += 0.3
    return round(s, 3)


def _bullet_items(bank: Bank, entry_id: str) -> list[Item]:
    return [i for i in bank.by_entry(entry_id) if not i.id.endswith("-h")]


def _candidates(bank: Bank, exp_id: str) -> list[Item]:
    """A role's own bullets plus the bullets of projects tagged as client / employer work for it (rule 62)."""
    out = _bullet_items(bank, exp_id)
    for e in bank.entries.values():
        if e.section == "project" and e.client_of == exp_id:
            out += _bullet_items(bank, e.id)
    return out


def experience_budget(exps: list, ranked: list, bank: Bank, analysis: dict, budget: dict,
                      rel: dict[str, float]) -> tuple[dict[str, int], list[str]]:
    """Bullets per role (rules 22–24): rule 24's starting allocation for the profile when your rules name the role,
    else by relevance rank (primary / second / internship / older); then the total is kept within rule 22's range."""
    from .evidence import where_entries
    roles, notes = budget["roles"], []
    alloc = budget["allocations"].get(analysis["profile"]["picked"], {})
    by_name = {}
    for name, n in alloc.items():
        hit = where_entries(name, {e.id: e for e in exps})
        if hit:
            by_name[hit[0]] = (name, n)
    cls, out, caps = {}, {}, {}
    interns_seen = 0
    for rank, e in enumerate(ranked):
        intern = "intern" in (e.role or "").casefold()
        if rank == 0:
            c = "primary"
        elif rank == 1 and not intern:
            c = "second"
        elif intern and interns_seen == 0 and rank <= 2:
            c = "internship"
        elif rank == 1:
            c = "second"
        else:
            c = "other"
        interns_seen += intern and c == "internship"
        cls[e.id] = c
        caps[e.id] = int(roles[c]["max"])
        start = int(roles[c]["start"])
        if e.id in by_name:
            start = by_name[e.id][1]
        if c == "other" and not any(rel[i.id] > 0 for i in _candidates(bank, e.id)):
            start = int(roles["other"].get("min", 0))
        out[e.id] = min(start, len(_candidates(bank, e.id)))
    if by_name:
        notes.append("Starting bullet allocation from resume_rules.md rule 24 (" + analysis["profile"]["picked"] + "): "
                     + ", ".join(f"{name} {n}" for name, n in by_name.values()) + ".")
    recent = exps[0].id if exps else None
    if recent and out.get(recent, 0) == 0:
        out[recent] = 1                   # the most recent role always stays
    lo, hi = int(budget["total"]["min"]), int(budget["total"]["max"])
    order = [e.id for e in ranked]
    while sum(out.values()) > hi:
        k = next((x for x in reversed(order) if out[x] > (1 if x == recent else 0)), None)
        if k is None:
            break
        out[k] -= 1
    while sum(out.values()) < lo:
        k = next((x for x in order if out[x] < min(caps[x], len(_candidates(bank, x)))), None)
        if k is None:
            break
        out[k] += 1
    notes.append(f"Experience bullets: {sum(out.values())} (rule 22: {lo}–{hi}); "
                 + ", ".join(f"{x} {out[x]} ({cls[x]}, max {caps[x]})" for x in order) + ".")
    return out, notes


def tool_category(family: str, cats: list[str]) -> str:
    f = family.casefold()
    want = (r"datab" if "database" in f else
            r"framework|web" if re.search(r"frontend|backend|api", f) else
            r"cloud|devops" if re.search(r"ci/cd|container|infrastructure|cloud|operating|version|messaging|monitor", f)
            else r"test|librar|tool")
    return next((c for c in cats if re.search(want, c, re.I)), cats[-1] if cats else "Tools")


def baseline(analysis: dict, bank: Bank, settings: Settings) -> Content:
    if bank.v4:                                   # master v4: start from its Default one-page snapshot (rule 3)
        from . import v4build
        return v4build.baseline(analysis, bank, settings)
    track = analysis["track"] if analysis["track"] in ("coop_intern", "full_time", "unclear") else "full_time"
    rel = {i.id: relevance(i, analysis, settings) for i in bank.items.values()}
    notes = Notes(stage="baseline")
    yoe = (analysis.get("eligibility") or {}).get("yoe_required")
    aud = settings.audience(track, analysis.get("seniority"), yoe)
    budget = rules_budget(settings)

    # experience: reverse chronological; bullet budget per role (rules 22–24)
    exps = sorted((e for e in bank.entries.values() if e.section == "experience"),
                  key=lambda e: (e.end or "9999") if e.end != "present" else "9999", reverse=True)
    exp_rel = {e.id: sum(sorted((rel[i.id] for i in _candidates(bank, e.id)), reverse=True)[:3]) for e in exps}
    ranked = sorted(exps, key=lambda e: -exp_rel[e.id])
    per_role, bnotes = experience_budget(exps, ranked, bank, analysis, budget, rel)
    experience = []
    for e in exps:
        cands = _candidates(bank, e.id)
        its = sorted(cands, key=lambda i: -rel[i.id])[:per_role[e.id]]
        order = {i.id: n for n, i in enumerate(cands)}
        its.sort(key=lambda i: (-rel[i.id], order[i.id]))
        bullets = [Bullet(text=i.text, evidence_ids=[i.id], origin="verbatim",
                          priority=1 if n == 0 else 2 if n == 1 else 3 if rel[i.id] > 0 else 4)
                   for n, i in enumerate(its)]
        if bullets:
            pr = 1 + min(3, ranked.index(e))
            experience.append(ExperienceEntry(entry_id=e.id, priority=pr, bullets=bullets))
    notes.selection.append(f"Audience: {aud} (track {track}, level {analysis.get('seniority')}) → section order "
                           f"{settings.order_for(track, analysis.get('seniority'), yoe)} (rules 15–16).")
    notes.selection.append("Experience in reverse chronological order; bullets ranked by JD keyword coverage "
                           f"({analysis['profile']['picked']} profile).")
    notes.selection += bnotes

    # projects (rules 36–38, 62): 2 for students (a 3rd only for co-op roles when it adds a JD term nothing else
    # covers), 1 for mid-level; client / employer work is under its role, not here
    projs = sorted((e for e in bank.entries.values() if e.section == "project" and not e.client_of),
                   key=lambda e: -sum(rel[i.id] for i in bank.by_entry(e.id)))
    pcfg = budget["projects"][aud]
    bmax = limit(settings, "bullets_per_project", "max", 2)
    jd_terms = {k["canonical"] for k in analysis.get("keywords", [])} | {
        t for r in analysis.get("requirements", []) for t in r.get("terms", [])}
    covered = {t for ex in experience for b in ex.bullets for ev in b.evidence_ids for t in bank.items[ev].technologies}
    projects = []
    for n, e in enumerate(projs):
        if n >= int(pcfg["count"]):
            if not (track == "coop_intern" and n < int(pcfg["max"])):
                break
            adds = (jd_terms & {t for i in bank.by_entry(e.id) for t in i.technologies}) - covered
            if not adds:
                break
            notes.selection.append(f"3rd project {e.id}: fills JD gap(s) {', '.join(sorted(adds))} (rule 37).")
        its = sorted(_bullet_items(bank, e.id), key=lambda i: -rel[i.id])[:bmax]
        covered |= {t for i in its for t in i.technologies}
        projects.append(ProjectEntry(entry_id=e.id, priority=2 + min(n, 2), bullets=[
            Bullet(text=i.text, evidence_ids=[i.id], origin="verbatim", priority=2 if k == 0 else 3)
            for k, i in enumerate(its)]))
    notes.selection.append(f"Projects: {len(projects)} ({aud}: {pcfg['count']}" +
                           (f", up to {pcfg['max']} for co-op" if aud == "student" else "") + "; rules 36–38).")

    # skills: categories as in the master resume, JD-relevant skills first; JD-relevant ticked tools from
    # tool_checklist.md are added (rule 63; never an unticked one)
    cap = int(settings.limits.get("skills_per_category", 8))
    names = [c for c, _ids in bank.skill_categories]
    have = {t for c, ids in bank.skill_categories for i in ids for t in bank.items[i].technologies}
    extra: dict[str, list[Item]] = {}
    for it in bank.items.values():
        if it.section == "tool" and set(it.technologies) & jd_terms and not set(it.technologies) & have:
            extra.setdefault(tool_category(it.category or "", names), []).append(it)
            have |= set(it.technologies)
    skills = []
    held = {canonical(n) for n in bank.conflicts}           # named in the master, unticked in the checklist
    if held:
        notes.selection.append("Skills: left out until you resolve the checklist conflict: "
                               + ", ".join(bank.conflicts) + ".")
    for cat, ids in bank.skill_categories:
        its = [bank.items[i] for i in ids if not (set(bank.items[i].technologies) & held
                                                   or bank.items[i].text in bank.conflicts)] + extra.pop(cat, [])
        if not its:
            continue
        base_ids = list(ids)
        its.sort(key=lambda i: (not (set(i.technologies) & jd_terms), base_ids.index(i.id) if i.id in base_ids else -1))
        skills.append(SkillCategory(category=cat, items=[SkillItem(name=_skill_name(i), evidence_ids=[i.id])
                                                         for i in its[:cap]]))
    added = sorted(i.text for c in skills for x in c.items for i in [bank.items[x.evidence_ids[0]]] if i.section == "tool")
    if added:
        notes.selection.append(f"Skills: added from tool_checklist.md (ticked, JD-relevant): {', '.join(added)}.")

    # education: every entry (master order); 0–3 relevant courses (rule 46)
    cmax = limit(settings, "courses", "max", 3)
    courses = [i for i in sorted((i for i in bank.items.values() if i.section == "coursework"), key=lambda i: -rel[i.id])
               if rel[i.id] > 0][:cmax]
    education = []
    for n, e in enumerate(e for e in bank.entries.values() if e.section == "education"):
        details = [i.id for i in _bullet_items(bank, e.id)]
        education.append(EducationEntry(entry_id=e.id, course_ids=[c.id for c in courses] if n == 0 else [],
                                        detail_ids=details))

    reqs = [RequirementMap(text=r["text"][:600], kind=r["kind"], category=r["category"],
                           evidence_ids=r["evidence_ids"][:12], note=r.get("note", "")[:400])
            for r in analysis.get("requirements", [])[:60]]
    return Content(job_pk=analysis["job"]["pk"], profile=analysis["profile"]["picked"], track=track,
                   section_order=settings.order_for(track, analysis.get("seniority"), yoe), skills=skills,
                   education=education, experience=experience, projects=projects, requirements=reqs, notes=notes)


def _skill_name(it: Item) -> str:
    """Honest exposure (rule 61): a coursework / tutorial-level tool says so."""
    if it.section == "tool" and it.level in ("coursework", "tutorial"):
        return f"{it.text} ({it.level})"[:40]
    return it.text
