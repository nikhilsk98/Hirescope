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

    def section_text(self, section: str) -> str:
        parts = []
        for e in self.sections.get(section, []):
            parts.append(e.heading)
            parts.extend(e.bullets)
        return "\n".join(p for p in parts if p)


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
            f"{path} not found. Copy inputs/master_resume.example.md to {path.name} and replace it with your resume."
        )
    text = path.read_text(encoding="utf-8")
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
