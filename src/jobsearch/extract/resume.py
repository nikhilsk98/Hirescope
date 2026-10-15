"""Parse master_resume.md into structured sections. The resume is the only source of facts about the user."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .skills import canonicalize, extract_skills

SECTIONS = ("experience", "projects", "coursework", "education", "skills", "summary")


@dataclass
class Entry:
    heading: str                     # "Software Engineering Intern — Example Corp (Jun 2025 – Aug 2025)"
    bullets: list[str] = field(default_factory=list)


@dataclass
class Resume:
    path: str
    version_hash: str
    sections: dict[str, list[Entry]]
    skills: list[str]                # canonical names: Skills section + skills evidenced in bullets
    listed_skills: list[str]         # only what the Skills section lists
    skill_evidence: dict[str, str]   # canonical skill -> first resume line that mentions it

    def bullets(self, section: str) -> list[str]:
        """Every line in a section usable as evidence (entry headings excluded)."""
        out = []
        for e in self.sections.get(section, []):
            out.extend(e.bullets)
        return out


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _section_key(heading: str) -> str | None:
    h = heading.strip().lower()
    for s in SECTIONS:
        if h.startswith(s) or (s == "skills" and "skill" in h) or (s == "projects" and "project" in h):
            return s
    return None


def parse_resume(path: Path, synonyms: dict[str, list[str]]) -> Resume:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy inputs/resume/master_resume.example.md to {path.name} and replace it with your resume."
        )
    text = path.read_text(encoding="utf-8")
    v4 = _parse_v4(path, text, synonyms)
    if v4 is not None:
        return v4
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    sections: dict[str, list[Entry]] = {s: [] for s in SECTIONS}
    current: str | None = None
    entry: Entry | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if m := re.match(r"^##\s+(.+)$", line):
            current = _section_key(m.group(1))
            entry = None
            continue
        if current is None or not line.strip():
            continue
        if m := re.match(r"^###\s+(.+)$", line):
            entry = Entry(m.group(1).strip())
            sections[current].append(entry)
            continue
        content = re.sub(r"^\s*[-*+]\s+", "", line).strip()
        if entry is None:
            entry = Entry("")
            sections[current].append(entry)
        entry.bullets.append(content)

    listed: list[str] = []
    for line in (b for e in sections["skills"] for b in e.bullets):
        items = line.split(":", 1)[1] if ":" in line else line
        for item in re.split(r",|;|\|", items):
            if item.strip():
                listed.append(canonicalize(item, synonyms))
    listed = list(dict.fromkeys(listed))

    evidence: dict[str, str] = {}
    for sec in ("experience", "projects", "coursework", "education", "summary"):
        for e in sections[sec]:
            for line in [e.heading, *e.bullets]:
                for sk in extract_skills(line, synonyms):
                    evidence.setdefault(sk, line)
    skills = list(dict.fromkeys([*listed, *evidence]))
    return Resume(str(path), file_hash(path), sections, skills, listed, evidence)


def _parse_v4(path: Path, text: str, synonyms: dict[str, list[str]]) -> Resume | None:
    """A master_resume.md in the Resume Builder's v4 format (Default snapshot, bullet banks, Skills inventory):
    matching uses only what a resume may use — usable bullets and facts per role / project, "Yes" courses, and the
    core / extended / self-reported skills — never Builder notes, Legacy bullets, planned or Excluded items."""
    try:
        from resume_builder import master_v4
    except ImportError:                    # the Resume Builder isn't installed: read it the plain way
        return None
    if not master_v4.is_v4(text):
        return None
    entries, items, _cats, _w, _ex, v4 = master_v4.parse(text, path.name)
    sections: dict[str, list[Entry]] = {s: [] for s in SECTIONS}
    for eid, e in entries.items():
        sec = {"experience": "experience", "project": "projects", "education": "education"}.get(e.section)
        if not sec:
            continue
        lines = [i.text for i in items.values() if i.entry_id == eid and i.kind in ("bullet", "snapshot", "own_words")]
        if e.section == "education":
            lines = [i.text for i in items.values() if i.entry_id == eid]
        sections[sec].append(Entry(e.heading, lines))
    sections["coursework"] = [Entry("", [i.text for i in items.values() if i.kind == "course"])]
    tiers = dict(v4.skills.get("tier", {}))
    declined: set[str] = set()
    adds = path.parent / "evidence_additions.md"
    if adds.exists():                      # confirmed answers: coursework-level skills, and "no" (never counted)
        from resume_builder.evidence import parse_additions
        try:
            for it in parse_additions(adds.read_text(encoding="utf-8"), entries, items)[0]:
                if it.kind == "declined":
                    declined.add(canonicalize(it.text, synonyms))
                elif it.kind == "skill":
                    tiers[it.text] = "coursework" if it.category in ("coursework", "tutorial") else "self-reported"
        except Exception:  # noqa: BLE001 — a malformed additions file is reported by the Resume Builder, not here
            pass
    listed = list(dict.fromkeys(canonicalize(n, synonyms) for n, t in tiers.items()
                                if t in ("core", "extended", "self-reported", "coursework")
                                and canonicalize(n, synonyms) not in declined
                                and n not in v4.skills.get("planned_also", [])))
    sections["skills"] = [Entry("", [n for n, t in tiers.items() if t in ("core", "extended", "self-reported")])]
    evidence: dict[str, str] = {}
    for sec in ("experience", "projects", "coursework", "education"):
        for e in sections[sec]:
            for line in e.bullets:
                for sk in extract_skills(line, synonyms):
                    evidence.setdefault(sk, line)
    never = {canonicalize(n, synonyms) for n, t in tiers.items() if t == "planned"} | {
        canonicalize(n, synonyms) for n in [*v4.skills.get("excluded", []), *v4.skills.get("planned_also", [])]} | declined
    evidence = {k: v for k, v in evidence.items() if k not in never}   # a design bullet naming planned tech
    skills = [x for x in dict.fromkeys([*listed, *evidence]) if x not in never]
    return Resume(str(path), file_hash(path), sections, skills, listed, evidence)
