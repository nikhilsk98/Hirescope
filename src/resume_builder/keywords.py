"""Smart keyword matching (rule 61) and category & tool mapping (rule 63) for one job.

For each important JD keyword your confirmed evidence doesn't support:
  - a tool left unticked in tool_checklist.md, or one you said you haven't used → a known gap (never claimed)
  - a specific tool in a family where you have another confirmed tool (JD wants GitHub Actions, you have Jenkins)
    → ask once which tools of that family you actually used (general → specific is never assumed)
  - anything else → ask "Have you used <keyword>? If yes, where?"
  - when a broader true category fits (JD: PostgreSQL, you: MySQL) → suggested wording "relational databases (MySQL)"
Answers: confirmed tool answers are ticked in tool_checklist.md, other answers go to evidence_additions.md; a "no" is
remembered (generated_resumes/_cache/keyword_answers.json) so the same question isn't asked again.

Learn-it list: every prepare records the job's unsupported required / preferred keywords
(generated_resumes/_cache/missing_keywords.json); keywords missing from several jobs are listed by `gaps`, in
generated_resumes/learn_it.md and on the dashboard's Skill Gaps view.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import config as rc
from .evidence import Bank, canonical

MAX_QUESTIONS = 8
TOOLISH = {"cloud_services", "tools", "databases", "web", "skills"}   # HireScope vocabulary categories that name tools


def _cache(name: str) -> Path:
    return rc.resumes_dir() / "_cache" / name


def _load(name: str) -> dict:
    try:
        return json.loads(_cache(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(name: str, data: dict) -> None:
    p = _cache(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def answers() -> dict:
    return _load("keyword_answers.json")


def record_answer(keyword: str, answer: str) -> None:
    """answer: 'no' (you haven't used it) or 'saved' (a confirmed answer was saved)."""
    d = answers()
    d[canonical(keyword).casefold()] = {"keyword": keyword, "answer": answer, "at": date.today().isoformat()}
    _save("keyword_answers.json", d)


def _families(bank: Bank) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name, t in bank.tool_index.items():
        if t.get("family") and not t["family"].casefold().startswith("to confirm"):
            out.setdefault(t["family"], []).append(t["name"])
    return out


def _tool(bank: Bank, term: str, surface: str) -> dict | None:
    for k in (surface.casefold(), term.casefold()):
        if k in bank.tool_index:
            return bank.tool_index[k]
    for t in bank.tool_index.values():
        if canonical(t["name"]) == term:
            return t
    return None


def plan(analysis: dict, bank: Bank, settings) -> dict:
    """{questions: [{kind, keyword, question, options?}], wording: [str], known_gaps: [str], missing: [terms]}."""
    from .jd_analysis import _CATEGORY_TERMS, related_terms
    have = bank.evidenced_terms()
    fam = _families(bank)
    ans = answers()
    out = {"questions": [], "wording": [], "known_gaps": [], "missing": []}
    asked_families = set()
    kws = sorted((k for k in analysis.get("keywords", []) if not k.get("supported") and k["section"] in
                  ("required", "preferred")), key=lambda k: (k["section"] != "required", -float(k.get("weight") or 0)))
    for k in kws:
        term, surface = k["canonical"], k["term"]
        if k.get("category") in ("degree_field", "certifications"):
            continue            # degree / certification requirements: eligibility, not something you "used"
        out["missing"].append(term)
        prev = ans.get(term.casefold())
        tool = _tool(bank, term, surface)
        broader = [c for c, members in _CATEGORY_TERMS.items() if term in members and c != term
                   and any(m in have for m in members)]
        related = related_terms(term, have, settings)
        for c in broader[:1]:
            mine = [m for m in _CATEGORY_TERMS[c] if m in have][:2]
            out["wording"].append(f"{surface}: write \"{c.lower() if c.islower() else c} ({', '.join(mine)})\" — "
                                  f"never claim {surface}")
        if not broader and related:
            out["wording"].append(f"{surface}: related, not the same — you can show {', '.join(related[:2])}; never "
                                  f"claim {surface}")
        if prev and prev.get("answer") == "no":
            out["known_gaps"].append(f"{surface}: you said you haven't used it ({prev['at']})")
            continue
        if tool and not tool.get("ticked"):
            out["known_gaps"].append(f"{surface}: unticked in tool_checklist.md ({tool['family']})")
            continue
        if len(out["questions"]) >= MAX_QUESTIONS:
            continue
        # general → specific (rule 63): a family you have a confirmed tool in — the checklist family, else a
        # category_terms category — is asked about once; tools you left unticked are not offered again
        family = tool["family"] if tool else next((f for f, names in fam.items()
                                                   if any(canonical(n) == term for n in names)), None)
        members = [n for n in fam.get(family, [])] if family else []
        if not (family and any(canonical(n) in have for n in members)):
            cat = next((c for c, ms in _CATEGORY_TERMS.items() if term in ms and any(m in have for m in ms)), None)
            family, members = (cat, list(_CATEGORY_TERMS[cat])) if cat else (None, [])
        unticked = {canonical(t["name"]) for t in bank.tool_index.values() if not t.get("ticked")}
        if family and family not in asked_families and any(canonical(n) in have for n in members):
            asked_families.add(family)
            opts = [n for n in members if canonical(n) not in have and (canonical(n) not in unticked
                                                                        or canonical(n) == term)]
            mine = [n for n in members if canonical(n) in have][:3]
            out["questions"].append({"kind": "tool_family", "keyword": surface, "options": opts,
                                     "question": f"You list {family} ({', '.join(mine)}). The JD asks for {surface}. "
                                                 f"Which of these have you actually used: {', '.join(opts)}? For each: "
                                                 "where (role or project) and level (production / project / "
                                                 "coursework / tutorial)."})
            continue
        if k.get("category", "other") in TOOLISH or k["section"] == "required" or float(k.get("weight") or 0) >= 4:
            out["questions"].append({"kind": "keyword", "keyword": surface,
                                     "question": f"Have you used {surface}? If yes, where (role or project), what did "
                                                 "you do with it, and at what level (production / project / "
                                                 "coursework / tutorial)?"})
    return out


def record_missing(job_pk: int, missing: list[str], weights: dict[str, float] | None = None) -> None:
    d = _load("missing_keywords.json")
    day = date.today().isoformat()
    for t in dict.fromkeys(missing):
        e = d.setdefault(t, {"jobs": {}, "weight": 0})
        e["jobs"][str(job_pk)] = day
        e["weight"] = max(e["weight"], float((weights or {}).get(t, 0)))
    for t, e in d.items():               # a job's earlier entries for keywords it no longer misses
        if t not in missing:
            e["jobs"].pop(str(job_pk), None)
    _save("missing_keywords.json", {t: e for t, e in d.items() if e["jobs"]})


def learn_it(bank: Bank | None = None, min_jobs: int = 2) -> list[dict]:
    """Keywords missing from at least min_jobs prepared jobs and still unsupported by your evidence."""
    have = bank.evidenced_terms() if bank else set()
    ans = answers()
    rows = [{"keyword": t, "jobs": len(e["jobs"]), "last": max(e["jobs"].values()), "weight": e.get("weight", 0),
             "declined": (ans.get(t.casefold()) or {}).get("answer") == "no"}
            for t, e in _load("missing_keywords.json").items() if t not in have and len(e["jobs"]) >= min_jobs]
    return sorted(rows, key=lambda r: (-r["jobs"], -r["weight"], r["keyword"]))


def learn_it_md(rows: list[dict]) -> str:
    L = ["# Learn-it list", "", "JD keywords missing from your confirmed evidence in several jobs you prepared resumes "
         "for (rule 61). Learning one, or confirming you've used it (`add-evidence --keyword … --confirm`), helps the "
         "most jobs.", "", "| Keyword | Jobs | Last seen | Note |", "|---|---|---|---|"]
    L += [f"| {r['keyword']} | {r['jobs']} | {r['last']} | {'you said you haven’t used it' if r['declined'] else ''} |"
          for r in rows] or ["| — | | | |"]
    return "\n".join(L) + "\n"


def write_learn_it(bank: Bank | None = None) -> Path:
    p = rc.resumes_dir() / "learn_it.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(learn_it_md(learn_it(bank)), encoding="utf-8")
    return p
