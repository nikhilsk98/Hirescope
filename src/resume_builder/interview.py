"""Evidence interview: short questions when a strong JD match lacks detail (scope, scale, outcome) or a requirement
may be covered by experience your inputs don't describe. Only answers you explicitly confirm are saved, dated, to
inputs/evidence_additions.md (gitignored). master_resume.md is never edited.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import config as rc
from .config import InputError
from .evidence import Bank

MAX_QUESTIONS = 5
HEADER = ("# Evidence additions\n\n"
          "Confirmed details added through the Resume Builder's evidence interview. Only sections marked\n"
          "`confirmed` are used. Edit or delete freely; the evidence bank is rebuilt when this file changes.\n")


def questions(analysis: dict, content, bank: Bank) -> list[dict]:
    """Up to 5 questions, most valuable first: missing-evidence requirements, then strong-match bullets without any
    stated scale or outcome."""
    out = []
    for r in content.requirements if content else []:
        if r.category == "missing_evidence":
            out.append({"id": f"q{len(out) + 1}", "entry_id": None, "kind": "missing_evidence",
                        "question": f"The JD asks: “{r.text[:160]}”. Have you done this? If yes: in which role or "
                                    "project, what exactly did you do, and what was the result?",
                        "why": r.note[:160]})
    jd_terms = {k["canonical"] for k in analysis.get("keywords", []) if k.get("supported")}
    for r in analysis.get("requirements", []):
        jd_terms.update(r.get("terms", []))
        jd_terms.update(r.get("related_terms", []))     # transferable evidence: scope makes it convincing
    scored = []
    for _sec, eid, b in (content.all_bullets() if content else []):
        items = [bank.items[i] for i in b.evidence_ids if i in bank.items]
        if not items or any(i.metrics for i in items):
            continue
        hits = len(jd_terms & {t for i in items for t in i.technologies})
        if hits:
            scored.append((hits, eid, items[0]))
    for hits, eid, it in sorted(scored, key=lambda x: -x[0]):
        out.append({"id": f"q{len(out) + 1}", "entry_id": eid, "kind": "scope",
                    "question": f"“{it.text[:140]}” — what was the scale (users, requests, services, data size, team) "
                                "and the measurable result? Only numbers you can back up in an interview.",
                    "why": f"matches {hits} JD keyword(s) but states no scope or outcome"})
    return out[:MAX_QUESTIONS]


def _clean(s: str, n: int) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s.lstrip("#").strip()[:n]


def save_answer(entry_id: str, question: str, answer: str, confirmed: bool, bank: Bank,
                path: Path | None = None, day: str | None = None) -> Path:
    """Append one confirmed answer. Refuses without explicit confirmation, for unknown entries, or empty answers."""
    if not confirmed:
        raise InputError("not saved: an answer is only added after you explicitly confirm it (--confirm)")
    if entry_id not in bank.entries and entry_id not in ("skills", "coursework"):
        raise InputError(f"unknown entry '{entry_id}' (known: {', '.join(sorted(bank.entries))})")
    a = _clean(answer, 500)
    if len(a) < 5:
        raise InputError("the answer is empty")
    path = path or rc.ADDITIONS
    day = day or date.today().isoformat()
    text = path.read_text(encoding="utf-8") if path.exists() else HEADER
    block = f"\n## {entry_id} — {day} — confirmed\n- Q: {_clean(question, 300)}\n- A: {a}\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
    tmp.replace(path)
    return path
