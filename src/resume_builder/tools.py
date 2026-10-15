"""inputs/resume/tool_checklist.md (rule 63): a tick-box list of tool families. Ticked tools are confirmed evidence,
with where each was used and at what level; unticked tools are never claimed.

Line format (anything after the tool name is optional):
  - [x] <Tool> | Proficiency: <level> | Where: <roles or projects> | Level: production|project|coursework|tutorial
Families are the '## ' headings. "Where: Multiple roles" (or empty) means the tool may appear in Technical Skills only;
naming roles or projects (e.g. "Accenture, CS6650") lets it appear in those entries' bullets too.

Confirmed tool answers from the evidence interview are written here (ticking an existing line or adding one under
"## Other"), never duplicated in evidence_additions.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

LEVELS = ("production", "project", "coursework", "tutorial")
LINE_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s*(.*)$")
GENERIC_WHERE = {"", "multiple roles", "multiple", "various", "all roles", "everywhere"}
SKIP_FAMILIES = {"to confirm"}           # hints, not tools: a tick there still counts (name before " (")


@dataclass
class Tool:
    name: str
    ticked: bool
    family: str
    proficiency: str = ""
    where: str = ""
    level: str = ""
    line: int = 0
    names: list[str] = field(default_factory=list)   # "AWS CodePipeline / CodeBuild" -> both parts

    @property
    def skills_only(self) -> bool:
        return self.where.strip().casefold() in GENERIC_WHERE


def _skip(family: str) -> bool:
    return any(family.casefold().startswith(f) for f in SKIP_FAMILIES)


def _fields(rest: str) -> tuple[str, dict]:
    parts = [p.strip() for p in rest.split("|")]
    name = parts[0]
    kv = {}
    for p in parts[1:]:
        k, _, v = p.partition(":")
        kv[k.strip().casefold()] = v.strip()
    return name, kv


def parse(text: str) -> tuple[list[Tool], list[str]]:
    """(tools, warnings). Duplicate ticked tools (the same tool under two families) are kept once."""
    tools: list[Tool] = []
    warnings: list[str] = []
    family = ""
    for n, raw in enumerate(text.splitlines(), 1):
        if m := re.match(r"^##\s+(.+?)\s*$", raw):
            family = m.group(1)
            continue
        m = LINE_RE.match(raw)
        if not m:
            continue
        name, kv = _fields(m.group(2))
        name = re.sub(r"\s*\(.*$", "", name).strip() if _skip(family) else name.strip()
        if not name:
            continue
        ticked = m.group(1).lower() == "x"
        level = kv.get("level", "").casefold()
        if ticked and level and level not in LEVELS:
            warnings.append(f"tool_checklist.md:{n}: {name}: Level '{level}' is not one of {', '.join(LEVELS)}")
        tools.append(Tool(name, ticked, family, kv.get("proficiency", ""), kv.get("where", ""), level, n,
                          [x.strip() for x in re.split(r"\s+/\s+", name) if x.strip()]))
    return tools, warnings


def ticked(tools: list[Tool]) -> list[Tool]:
    seen, out = set(), []
    for t in tools:
        if t.ticked and t.name.casefold() not in seen:
            seen.add(t.name.casefold())
            out.append(t)
    return out


def families(tools: list[Tool]) -> dict[str, list[Tool]]:
    out: dict[str, list[Tool]] = {}
    for t in tools:
        if not _skip(t.family):
            out.setdefault(t.family, []).append(t)
    return out


def find(tools: list[Tool], name: str) -> Tool | None:
    k = name.strip().casefold()
    return next((t for t in tools if t.name.casefold() == k or k in (x.casefold() for x in t.names)), None)


def confirm(path: Path, name: str, where: str, level: str, proficiency: str = "") -> str:
    """Tick `name` (or add it under '## Other') with where / level. Returns 'ticked', 'added' or 'updated'."""
    level = level.strip().casefold()
    if level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)} (got '{level}')")
    if not where.strip():
        raise ValueError("say where you used it (a role or project)")
    clean = lambda s: re.sub(r"[|\r\n]+", " ", s).strip()  # noqa: E731
    line = (f"- [x] {clean(name)} | Proficiency: {clean(proficiency) or 'intermediate'} | Where: {clean(where)} | "
            f"Level: {level}")
    text = path.read_text(encoding="utf-8") if path.exists() else "# Tool Checklist\n\n## Other\n"
    lines = text.splitlines()
    tools, _ = parse(text)
    hit = next((t for t in tools if not _skip(t.family) and
                (t.name.casefold() == name.strip().casefold() or name.strip().casefold() in (x.casefold() for x in t.names))),
               None)
    if hit:
        state = "updated" if hit.ticked else "ticked"
        lines[hit.line - 1] = line.replace(clean(name), hit.name, 1)
    else:
        state = "added"
        i = next((k for k, ln in enumerate(lines) if re.match(r"^##\s+Other\s*$", ln)), None)
        if i is None:
            lines += ["", "## Other", line]
        else:
            j = i + 1           # after the heading; replace an empty template line if there is one
            while j < len(lines) and lines[j].strip() and not lines[j].startswith("## "):
                if re.match(r"^\s*[-*]\s+\[ \]\s*\|", lines[j]):
                    break
                j += 1
            if j < len(lines) and re.match(r"^\s*[-*]\s+\[ \]\s*\|", lines[j]):
                lines[j] = line
            else:
                lines.insert(j, line)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    tmp.replace(path)
    return state
