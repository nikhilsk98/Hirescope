"""Job package: the stable contract consumed by the future resume builder (documented in README).

Format v1 — Markdown with YAML front matter:
  front matter keys (in order): package_format, company, title, job_id, apply_url, source,
  posted_date, fetched_at, resume_version_hash, score, label
  body sections (in order, each '## <name>'): Full JD, Minimum requirements, Preferred requirements,
  Skills required, ATS keywords, My matching evidence, Gaps, Flags
  ATS keywords lines: "- <JD exact term> | <required|preferred|mentioned> | <matched|synonym|missing> |
  weight <w>[ | resume says <term>][ | evidence (<section>): <resume line>]" after a "Coverage: N%" line.
"""
from __future__ import annotations

import re

import yaml

from .employer_facing import scrub

PACKAGE_FORMAT = 2   # 2: adds the "ATS keywords" section
FRONT_MATTER_KEYS = ["package_format", "company", "title", "job_id", "apply_url", "source", "posted_date",
                     "fetched_at", "resume_version_hash", "score", "label"]
SECTIONS = ["Full JD", "Minimum requirements", "Preferred requirements", "Skills required", "ATS keywords",
            "My matching evidence", "Gaps", "Flags"]


def package_filename(company: str, title: str, job_id: str) -> str:
    def clean(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:60] or "x"
    return f"{clean(company)}_{clean(title)}_{clean(job_id)}.md"


def _bullets(items: list[str], empty: str = "_None stated._") -> str:
    return "\n".join(f"- {i}" for i in items) if items else empty


def render_package(job: dict, jd_text: str, resume_hash: str) -> str:
    fm = {
        "package_format": PACKAGE_FORMAT,
        "company": job["company"],
        "title": job["title"],
        "job_id": str(job["job_id"]),
        "apply_url": job.get("apply_url"),
        "source": job["source"],
        "posted_date": job.get("posted_date") or "Unknown",
        "fetched_at": job["last_checked"],
        "resume_version_hash": resume_hash,
        "score": job.get("score"),
        "label": job.get("label"),
    }
    bd = job.get("breakdown") or {}
    skills = job.get("skills") or []
    matched = set(bd.get("matched_skills") or [])
    skill_lines = [f"{s} — {'in resume' if s in matched else 'not in resume'}" for s in skills]

    evidence = []
    for e in bd.get("evidence") or []:
        evidence.append(f"JD: {e['jd']}\n  Resume ({e['section']}): {scrub(e['resume'])}")
    for sk, line in (bd.get("skill_evidence") or {}).items():
        evidence.append(f"{sk}: {scrub(line)}")    # resume lines: a package may be shared (resume rule 39)

    kw = job.get("keywords") or {}
    kw_lines = []
    if kw.get("keywords"):
        kw_lines.append(f"Coverage: {kw.get('coverage')}% (required {kw.get('required_hit')}/{kw.get('required_total')}) — "
                        "keyword overlap, not your chance of passing an ATS")
        for k in kw["keywords"]:
            line = f"{k['term']} | {k['section']} | {k['status']} | weight {k['weight']}"
            if k["status"] == "synonym":
                line += f" | resume says {k['resume_term']}"
            if k.get("evidence"):
                line += f" | evidence ({k['evidence_section']}): {scrub(k['evidence'])}"
            kw_lines.append(line)

    flags = job.get("flags") or {}
    flag_lines = []
    for k in ("sponsorship", "citizenship", "clearance"):
        f = flags.get(k) or {"value": "Not stated", "evidence": None}
        flag_lines.append(f"{k.capitalize()}: {f['value']}" + (f" — “{f['evidence']}”" if f.get("evidence") else ""))
    if job.get("yoe_required") is not None:
        flag_lines.append(f"Years of experience: {job['yoe_required']:g}+ — “{job.get('yoe_evidence') or ''}”")
    if job.get("grad_years"):
        flag_lines.append(f"Graduation years mentioned: {', '.join(map(str, job['grad_years']))}")
    if job.get("degree"):
        flag_lines.append(f"Degree mentioned: {job['degree']}")
    if job.get("eligibility_note"):
        flag_lines.append(f"Check eligibility: “{job['eligibility_note']}”")
    if job.get("deadline"):
        flag_lines.append(f"Apply by: {job['deadline']}" + (f" — “{job['deadline_evidence']}”" if job.get('deadline_evidence') else ""))

    body = {
        # escape heading-like JD lines so they can't be mistaken for package sections
        "Full JD": re.sub(r"(?m)^(#+ )", r"\\\1", jd_text.strip()) or "_JD text unavailable._",
        "Minimum requirements": _bullets(job.get("min_reqs") or []),
        "Preferred requirements": _bullets(job.get("pref_reqs") or []),
        "Skills required": _bullets(skill_lines),
        "ATS keywords": _bullets(kw_lines, "_No keywords extracted._"),
        "My matching evidence": _bullets(evidence, "_No direct evidence found._"),
        "Gaps": _bullets(job.get("gaps") or [], "_None identified._"),
        "Flags": _bullets(flag_lines),
    }
    out = ["---", yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip(), "---", ""]
    for name in SECTIONS:
        out += [f"## {name}", "", body[name], ""]
    return "\n".join(out)


def parse_package(text: str) -> tuple[dict, dict[str, str]]:
    """Inverse of render_package (used by tests and, later, the resume builder)."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise ValueError("missing front matter")
    fm = yaml.safe_load(m.group(1))
    sections: dict[str, str] = {}
    current = None
    for line in m.group(2).splitlines():
        if line.startswith("## ") and line[3:].strip() in SECTIONS:
            current = line[3:].strip()
            sections[current] = ""
        elif current:
            sections[current] += line + "\n"
    return fm, {k: v.strip() for k, v in sections.items()}
