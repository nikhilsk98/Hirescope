"""Deterministic baseline: selection and ranking of your own bullets (verbatim) for a job. The drafter (LLM) starts
from this, rewrites for the JD, and keeps every evidence id; locked bullets and preserved manual edits are already in
place and must stay unchanged.

Relevance of an evidence item = Σ JD keyword weights it covers (required ×1, preferred ×1, mentioned ×1, weights
from the HireScope keyword engine already reflect section and frequency) + 0.5 per profile `lead_with` term + 0.3
when it states a metric. Recency breaks ties.
"""
from __future__ import annotations

from .config import Settings, limit
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


def baseline(analysis: dict, bank: Bank, settings: Settings) -> Content:
    track = analysis["track"] if analysis["track"] in ("coop_intern", "full_time", "unclear") else "full_time"
    rel = {i.id: relevance(i, analysis, settings) for i in bank.items.values()}
    notes = Notes(stage="baseline")

    # experience: reverse chronological; bullet budget by relevance rank (most recent role always kept)
    exps = sorted((e for e in bank.entries.values() if e.section == "experience"),
                  key=lambda e: (e.end or "9999") if e.end != "present" else "9999", reverse=True)
    exp_rel = {e.id: sum(sorted((rel[i.id] for i in _bullet_items(bank, e.id)), reverse=True)[:3]) for e in exps}
    ranked = sorted(exps, key=lambda e: -exp_rel[e.id])
    max_b = limit(settings, "bullets_per_role", "max", 4)
    budget = {}
    for rank, e in enumerate(ranked):
        budget[e.id] = max_b if rank == 0 else max_b - 1 if rank == 1 else max(2, max_b - 2) if rank == 2 else 2
    budget[exps[-1].id] = min(budget[exps[-1].id], 2) if len(exps) > 2 else budget[exps[-1].id]
    experience = []
    for e in exps:
        its = sorted(_bullet_items(bank, e.id), key=lambda i: -rel[i.id])[:budget[e.id]]
        order = {i.id: n for n, i in enumerate(_bullet_items(bank, e.id))}
        its.sort(key=lambda i: (-rel[i.id], order[i.id]))
        bullets = [Bullet(text=i.text, evidence_ids=[i.id], origin="verbatim",
                          priority=1 if n == 0 else 2 if n == 1 else 3 if rel[i.id] > 0 else 4)
                   for n, i in enumerate(its)]
        if bullets:
            pr = 1 + min(3, ranked.index(e))
            experience.append(ExperienceEntry(entry_id=e.id, priority=pr, bullets=bullets))
    notes.selection.append("Experience in reverse chronological order; bullets ranked by JD keyword coverage "
                           f"({analysis['profile']['picked']} profile).")

    # projects: by relevance, within limits
    projs = sorted((e for e in bank.entries.values() if e.section == "project"),
                   key=lambda e: -sum(rel[i.id] for i in bank.by_entry(e.id)))
    pmax, bmax = limit(settings, "projects", "max", 3), limit(settings, "bullets_per_project", "max", 3)
    projects = []
    for n, e in enumerate(projs[:pmax]):
        its = sorted(_bullet_items(bank, e.id), key=lambda i: -rel[i.id])[:bmax]
        projects.append(ProjectEntry(entry_id=e.id, priority=2 + min(n, 2), bullets=[
            Bullet(text=i.text, evidence_ids=[i.id], origin="verbatim", priority=2 if k == 0 else 3)
            for k, i in enumerate(its)]))

    # skills: categories as in the master resume, JD-relevant skills first
    jd_terms = {k["canonical"] for k in analysis.get("keywords", [])} | {
        t for r in analysis.get("requirements", []) for t in r.get("terms", [])}
    cap = int(settings.limits.get("skills_per_category", 8))
    skills = []
    for cat, ids in bank.skill_categories:
        its = [bank.items[i] for i in ids]
        its.sort(key=lambda i: (not (set(i.technologies) & jd_terms), ids.index(i.id)))
        skills.append(SkillCategory(category=cat, items=[SkillItem(name=i.text, evidence_ids=[i.id]) for i in its[:cap]]))

    # education: every entry (master order); courses ranked by relevance
    cmax = limit(settings, "courses", "max", 5)
    courses = sorted((i for i in bank.items.values() if i.section == "coursework"), key=lambda i: -rel[i.id])[:cmax]
    education = []
    for n, e in enumerate(e for e in bank.entries.values() if e.section == "education"):
        details = [i.id for i in _bullet_items(bank, e.id)]
        education.append(EducationEntry(entry_id=e.id, course_ids=[c.id for c in courses] if n == 0 else [],
                                        detail_ids=details))

    reqs = [RequirementMap(text=r["text"][:600], kind=r["kind"], category=r["category"],
                           evidence_ids=r["evidence_ids"][:12], note=r.get("note", "")[:400])
            for r in analysis.get("requirements", [])[:60]]
    return Content(job_pk=analysis["job"]["pk"], profile=analysis["profile"]["picked"], track=track,
                   section_order=settings.order_for(track), skills=skills, education=education,
                   experience=experience, projects=projects, requirements=reqs, notes=notes)
