"""Internal reports saved with each version (never part of the employer-facing PDF)."""
from __future__ import annotations

from .validate import Report


def validation_md(rep: Report, attempts: list[dict], repairs: list[str], font_used: str | None,
                  preferred_font: str | None) -> str:
    L = ["# Validation report", "", f"Result: **{'passed' if rep.ok else 'FAILED'}** · pages: {rep.pages} · "
         f"main font: {font_used or rep.font_main or '?'}"
         + (f" (Calibri is not installed; Carlito is metric-compatible, so line breaks match)"
            if font_used and preferred_font and font_used != preferred_font else ""), ""]
    if rep.bottom_whitespace is not None:
        L.append(f"Empty space at the bottom of page 1: {rep.bottom_whitespace:.0%}")
    L += ["", "| Check | Result | Detail |", "|---|---|---|"]
    for c in rep.checks:
        res = "✓" if c.ok else ("✗ error" if c.severity == "error" else "! warning")
        L.append(f"| {c.name} | {res} | {c.detail.replace('|', '/')[:220] if (not c.ok or c.detail) else ''} |")
    if attempts:
        L += ["", "## Attempts (repair loop, max 3)"]
        for a in attempts:
            L.append(f"- attempt {a['attempt']}: {'ok' if a['ok'] else 'failed'} ({a['pages']} page(s))"
                     + (f" — {'; '.join(a['errors'])[:300]}" if a["errors"] else ""))
    if repairs:
        L += ["", "## Automatic repairs", *[f"- {r}" for r in repairs]]
    return "\n".join(L) + "\n"


# --- coverage ------------------------------------------------------------------------------------------------
COVERAGE_DEFINITION = ("Internal coverage measure, not an ATS score: the share of the JD's required and preferred "
                       "keyword weight (HireScope keyword engine) that appears in the final resume text. It is not "
                       "a prediction of passing any applicant tracking system.")


def resume_text(c, bank) -> str:
    from .render import plain_lines
    return "\n".join(t for _k, t in plain_lines(c, bank))


def used_evidence(c) -> set[str]:
    """Every evidence id the resume shows: bullets, skills, education entries (heading), courses and details."""
    ids = {e for _s, _e, b in c.all_bullets() for e in b.evidence_ids}
    ids |= {e for cat in c.skills for i in cat.items for e in i.evidence_ids}
    for ed in c.education:
        ids |= {ed.entry_id + "-h", *ed.course_ids, *ed.detail_ids}
    for x in [*c.experience, *c.projects]:
        ids.add(x.entry_id + "-h")
    if c.summary:
        ids |= set(c.summary.evidence_ids)
    return ids


def coverage(c, bank, analysis: dict) -> dict:
    import re as _re
    from .evidence import terms_in, vocab
    v = vocab()
    text = resume_text(c, bank)
    low = text.lower()
    on_resume = set(terms_in(text))
    rows = []
    for k in analysis.get("keywords", []):
        forms = v.aliases.get(k["canonical"], (k["canonical"],)) + (k["term"],)
        present = k["canonical"] in on_resume or any(
            _re.search(rf"(?<![\w]){_re.escape(f.lower())}(?![\w])", low) for f in forms)
        concrete = [x for x in v.implies.get(k["canonical"], ()) if x in on_resume]
        rows.append({"term": k["term"], "canonical": k["canonical"], "section": k["section"],
                     "weight": k["weight"], "supported": k["supported"], "represented": present or bool(concrete),
                     "via": concrete[:3]})
    imp = [r for r in rows if r["section"] in ("required", "preferred")] or rows
    total = sum(r["weight"] for r in imp)
    got = sum(r["weight"] for r in imp if r["represented"])
    shown = used_evidence(c)
    reqs = [{"text": r.text, "kind": r.kind, "category": r.category, "evidence_ids": r.evidence_ids, "note": r.note,
             "represented": any(i in shown for i in r.evidence_ids)} for r in c.requirements]
    return {
        "definition": COVERAGE_DEFINITION,
        "coverage_pct": round(100 * got / total, 1) if total else None,
        "basis": "required + preferred keywords" if imp is not rows else "all JD keywords (no required/preferred)",
        "keywords": rows,
        "missing_resume_keywords": [r["term"] for r in rows if r["supported"] and not r["represented"]],
        "missing_qualifications": [r["term"] for r in rows if not r["supported"] and not r["represented"]],
        "requirements": reqs,
    }


def coverage_md(cov: dict) -> str:
    L = ["# Coverage report", "", f"**{cov['coverage_pct'] if cov['coverage_pct'] is not None else '—'}%** "
         f"({cov['basis']})", "", f"_{cov['definition']}_", "",
         "## Missing resume keywords (you have the evidence; not on this resume)",
         *([f"- {t}" for t in cov["missing_resume_keywords"]] or ["- none"]), "",
         "## Missing qualifications (no evidence in your inputs: never added)",
         *([f"- {t}" for t in cov["missing_qualifications"]] or ["- none"]), "",
         "## JD keywords", "| Term | Section | Weight | Supported by evidence | On the resume |", "|---|---|---|---|---|"]
    for r in cov["keywords"]:
        L.append(f"| {r['term']} | {r['section']} | {r['weight']} | {'yes' if r['supported'] else 'no'} | "
                 f"{'yes' + (' (via ' + ', '.join(r['via']) + ')' if r['via'] else '') if r['represented'] else 'no'} |")
    if cov["requirements"]:
        L += ["", "## Requirements", "| Requirement | Kind | Category | On the resume | Note |", "|---|---|---|---|---|"]
        for r in cov["requirements"]:
            L.append(f"| {r['text'][:120].replace('|', '/')} | {r['kind']} | {r['category']} | "
                     f"{'yes' if r['represented'] else 'no'} | {r['note'][:120].replace('|', '/')} |")
    return "\n".join(L) + "\n"


# --- change report ------------------------------------------------------------------------------------------
def changes_md(c, bank, analysis: dict, repairs: list[str], cov: dict) -> str:
    used = {e for _s, _e, b in c.all_bullets() for e in b.evidence_ids}
    L = ["# Change report", "", f"Job: {analysis['job']['company']} — {analysis['job']['title']} · track "
         f"{c.track} · profile {c.profile} ({analysis['profile']['reason']})", "", "## Selection"]
    L += [f"- {s}" for s in c.notes.selection] or ["- (no notes)"]
    L.append(f"- Section order: {', '.join(c.section_order)}")
    L += ["- Projects: " + (", ".join(bank.entries[p.entry_id].role or p.entry_id for p in c.projects) or "none")]
    L += ["", "## Bullets (before → after)"]
    for _sec, eid, b in c.all_bullets():
        before = " / ".join(bank.items[i].text for i in b.evidence_ids if i in bank.items)
        if b.origin == "verbatim" or before.strip() == b.text.strip():
            L.append(f"- **{eid}** kept as written: {b.text}")
        else:
            L.append(f"- **{eid}** ({b.origin})\n  - before: {before}\n  - after: {b.text}")
    removed = [i for i in bank.items.values() if i.section in ("experience", "project") and not i.id.endswith("-h")
               and i.id not in used]
    L += ["", "## Not used on this resume"]
    L += [f"- {i.id}: {i.text[:140]}" for i in removed] or ["- nothing"]
    dropped_entries = [e for e in bank.entries.values() if e.section in ("experience", "project")
                       and e.id not in {x.entry_id for x in [*c.experience, *c.projects]}]
    L += [f"- whole entry left out: {e.heading}" for e in dropped_entries]
    L += [f"- {r}" for r in [*c.notes.removed, *repairs] if r]
    L += ["", "## Reviewer changes", *([f"- {r}" for r in c.notes.reviewer] or ["- none recorded"])]
    gaps = [r for r in c.requirements if r.category in ("gap", "missing_evidence")]
    L += ["", "## Remaining gaps"]
    L += [f"- [{r.category}] {r.text[:160]}" + (f" — {r.note[:120]}" if r.note else "") for r in gaps]
    L += [f"- missing qualification (keyword): {t}" for t in cov["missing_qualifications"]]
    if not gaps and not cov["missing_qualifications"]:
        L.append("- none identified")
    return "\n".join(L) + "\n"


# --- interview defense ----------------------------------------------------------------------------------------
def defense_md(c, bank) -> str:
    L = ["# Interview defense notes", "", "2–3 talking points per bullet, from your own evidence. Internal only.", ""]
    for _sec, eid, b in c.all_bullets():
        items = [bank.items[i] for i in b.evidence_ids if i in bank.items]
        e = bank.entries.get(eid)
        L.append(f"### {b.text}")
        pts = list(b.talking_points)
        src = " ".join(i.text for i in items)
        if e:
            pts.append(f"Context: {e.heading} ({e.dates or ''}); source: “{src[:220]}”")
        tech = list(dict.fromkeys(t for i in items for t in i.technologies))
        if tech and len(pts) < 3:
            pts.append(f"Be ready to go deep on {', '.join(tech[:5])}: why chosen, what you configured or wrote, "
                       "what broke and how you fixed it.")
        mets = list(dict.fromkeys(m for i in items for m in i.metrics))
        if mets and len(pts) < 3:
            pts.append(f"Metric{'s' if len(mets) > 1 else ''} {', '.join(mets)}: how it was measured, the baseline, "
                       "and your part in it.")
        elif len(pts) < 3:
            pts.append("No metric stated: describe the concrete scope (who used it, how often, what it replaced).")
        L += [f"- {p}" for p in pts[:3]] + [""]
    return "\n".join(L)
