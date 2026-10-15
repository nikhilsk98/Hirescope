"""Evidence interview: short questions when a strong JD match lacks detail (scope, scale, outcome) or a requirement
may be covered by experience your inputs don't describe. Only answers you explicitly confirm are saved, dated, to
inputs/resume/evidence_additions.md (gitignored), one block per answer:

    ## YYYY-MM-DD — <entry id>
    - Question: …
    - Answer: …
    - Status: confirmed
    - Role/project: <the role or project>
    - Level: production | project | coursework | tutorial        (only when it is about a tool)

A confirmed answer about a tool is written to inputs/resume/tool_checklist.md instead (ticked, with where and level),
never duplicated here. master_resume.md is never edited.
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
          "Confirmed details added through the Resume Builder's evidence interview (rule 61). Only blocks with\n"
          "`Status: confirmed` are used. Tool answers go to tool_checklist.md instead. Edit or delete freely; the\n"
          "evidence bank is rebuilt when this file changes.\n\n"
          "Format, one block per answer:\n\n"
          "    ## YYYY-MM-DD — <entry id, e.g. exp-accenture, or skills>\n"
          "    - Keyword: …          (the JD keyword asked about; \"Answer: no\" records that you haven't used it)\n"
          "    - Question: …\n"
          "    - Answer: …\n"
          "    - Status: confirmed\n"
          "    - Role/project: …\n"
          "    - Level: production | project | coursework | tutorial   (tools only)\n")


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


def is_tool(keyword: str, bank: Bank) -> bool:
    """A tool / platform / database (goes to tool_checklist.md), not an experience fact."""
    from .evidence import canonical, vocab
    c = canonical(keyword)
    if keyword.strip().casefold() in bank.tool_index or any(canonical(t["name"]) == c for t in bank.tool_index.values()):
        return True
    return vocab().category.get(c) in ("tools", "cloud_services", "databases")


def save_answer(entry_id: str, question: str, answer: str, confirmed: bool, bank: Bank,
                path: Path | None = None, day: str | None = None, keyword: str | None = None,
                level: str | None = None, tool_path: Path | None = None, where: str | None = None) -> Path:
    """Save one confirmed answer. A tool answer (keyword is a tool) ticks it in tool_checklist.md with where and
    level; anything else is appended to evidence_additions.md. Refuses without explicit confirmation, for unknown
    entries, or empty answers."""
    from . import keywords, tools
    if not confirmed:
        raise InputError("not saved: an answer is only added after you explicitly confirm it (--confirm)")
    if entry_id not in bank.entries and entry_id not in ("skills", "coursework"):
        raise InputError(f"unknown entry '{entry_id}' (known: {', '.join(sorted(bank.entries))}, skills, coursework)")
    a = _clean(answer, 500)
    if len(a) < 5 and not (keyword and a.casefold() in ("no", "none")):
        raise InputError("the answer is empty")
    lv = (level or "").strip().casefold() or None
    if lv and lv not in tools.LEVELS:
        raise InputError(f"level must be one of {', '.join(tools.LEVELS)}")
    e = bank.entries.get(entry_id)
    place = where or ((e.organization or e.role) if e else "Multiple roles")
    if keyword and is_tool(keyword, bank) and entry_id != "skills":
        if not lv:
            raise InputError(f"{keyword} is a tool: say the level (--level production|project|coursework|tutorial)")
        tp = tool_path or rc.TOOL_CHECKLIST
        tools.confirm(tp, _clean(keyword, 60), place, lv)
        keywords.record_answer(keyword, "saved")
        return tp
    path = path or rc.ADDITIONS
    day = day or date.today().isoformat()
    text = path.read_text(encoding="utf-8") if path.exists() else HEADER
    block = (f"\n## {day} — {entry_id}\n" + (f"- Keyword: {_clean(keyword, 60)}\n" if keyword else "")
             + f"- Question: {_clean(question, 300)}\n- Answer: {a}\n- Status: confirmed\n"
             f"- Role/project: {_clean(where or (e.heading if e else entry_id), 200)}\n" + (f"- Level: {lv}\n" if lv else ""))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
    tmp.replace(path)
    if keyword:
        keywords.record_answer(keyword, "saved")
    return path
