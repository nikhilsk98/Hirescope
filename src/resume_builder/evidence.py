"""Evidence bank: every fact the resume may use, parsed from inputs/resume/master_resume.md (never modified) and the
confirmed answers in inputs/resume/evidence_additions.md. Each item has an id that tailored bullets must cite.

master_resume.md layout (the same one HireScope's matcher reads):
  ## Experience   ### Role — Organization, Location (Mon YYYY – Mon YYYY)   - bullet ...
  ## Projects     ### Project Name | Tech, Tech                              - bullet ...
  ## Coursework   - Course, Course, ...
  ## Education    ### Degree — Institution, Location (dates)                 - detail ...
  ## Skills       - Category: skill, skill, ...
  ## Summary      - line ... (skill evidence only)

Status tags (rule 1): only items marked confirmed are used. A tag goes at the end of a line —
  [confirmed]   [needs confirmation]   [demo]
— on a '## Section' or '### Entry' heading it applies to everything below it unless a line has its own tag; inside a
skills / coursework list it may follow one item ("Kotlin [needs confirmation]"). Untagged items are not confirmed.
A project done for a client or employer is tagged on its heading, e.g. `[client work: Freelance]` (rule 62): only then
may its bullets appear under that role.

evidence_additions.md (written by the evidence interview, only after you confirm an answer):
  ## YYYY-MM-DD — <entry id>
  - Question: question asked
  - Answer: the fact you confirmed
  - Status: confirmed
  - Role/project: the role or project it belongs to
  - Level: production | project | coursework | tutorial      (tools only; tools usually go to tool_checklist.md)
The older form "## <entry id> — YYYY-MM-DD — confirmed" with "- Q:" / "- A:" lines is read too.

inputs/resume/tool_checklist.md (rule 63): ticked tools are confirmed evidence (see tools.py).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from jobsearch.config import load_synonyms
from jobsearch.extract.keywords import _hits, vocab_for

from . import config as rc
from .config import InputError, sha256_file

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
DATES_RE = re.compile(rf"\(((?:Expected\s+)?{_MON}\s+\d{{4}}(?:\s*[–-]\s*(?:{_MON}\s+\d{{4}}|Present))?)\)\s*$")
METRIC_RE = re.compile(r"(?<![\w.\-/])(\d+(?:[.,]\d+)?)(\s?%|\+|x\b|k\b|K\b|-member|-person)?")
ADD_HEAD_RE = re.compile(r"^##\s+([a-z0-9-]+)\s+[—-]\s+(\d{4}-\d{2}-\d{2})\s+[—-]\s+(confirmed)\s*$", re.I)
ADD_HEAD2_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s+[—-]\s+([a-z0-9-]+)\s*$", re.I)
STATUSES = ("confirmed", "needs confirmation", "demo")
TAG_RE = re.compile(r"\s*\[(confirmed|needs[ -]confirmation|demo|(?:client|employer) work:\s*[^\]]+)\]\s*$", re.I)
BANK_VERSION = 3


def split_tags(line: str) -> tuple[str, str | None, str | None]:
    """'text [confirmed] [client work: Freelance]' -> ('text', 'confirmed', 'Freelance'). Tags only at the end."""
    status = client = None
    while m := TAG_RE.search(line):
        tag = m.group(1).strip()
        low = tag.casefold().replace("-", " ")
        if low in STATUSES:
            status = status or low
        else:
            client = client or tag.split(":", 1)[1].strip()
        line = line[:m.start()]
    return line.rstrip(), status, client


@dataclass
class Entry:
    id: str
    section: str                    # experience | project | education
    heading: str                    # heading text without the dates, exactly as in the master resume
    role: str | None = None         # experience: title; education: degree; project: name
    organization: str | None = None
    location: str | None = None
    dates: str | None = None        # exactly as written, e.g. "Feb 2021 – Dec 2021", "Expected Aug 2027"
    start: str | None = None        # YYYY-MM
    end: str | None = None          # YYYY-MM | "present"
    tech: list[str] = field(default_factory=list)   # project title-line tech
    link: str | None = None
    status: str | None = None       # confirmed | needs confirmation | demo | None (untagged)
    client_of: str | None = None    # projects tagged [client work: X]: the experience entry id X matched


@dataclass
class Item:
    id: str
    section: str                    # experience | project | coursework | education | skill | summary
    text: str
    entry_id: str | None = None
    organization: str | None = None
    role: str | None = None
    dates: str | None = None
    technologies: list[str] = field(default_factory=list)   # canonical terms (HireScope keyword vocabulary)
    actions: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)        # only numbers actually written in the source
    category: str | None = None     # skills: Languages, Web/Frameworks, ...
    source: str = "master"          # master | additions | tools
    added: str | None = None        # additions: date confirmed
    status: str | None = "confirmed"
    where: str | None = None        # tools: where used (tool_checklist.md "Where")
    where_entries: list[str] = field(default_factory=list)   # tools: entry ids that "Where" names
    level: str | None = None        # tools / additions: production | project | coursework | tutorial
    line: int | None = None         # line in its source file
    kind: str = "bullet"            # v4: bullet | snapshot | own_words | context | metric | course | skill | cert
    variant: bool = False           # v4: a compact / combined variant of another bullet


@dataclass
class Bank:
    items: dict[str, Item]
    entries: dict[str, Entry]
    skill_categories: list[tuple[str, list[str]]]   # [(category, [item ids])]
    hashes: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    version: int = BANK_VERSION
    excluded: list[dict] = field(default_factory=list)       # not confirmed: {id, line, text, status, source}
    metric_flags: list[dict] = field(default_factory=list)   # rule 3: {metric, items: [ids], detail}
    tool_index: dict[str, dict] = field(default_factory=dict)  # every checklist tool: name -> {ticked, family}
    conflicts: list[str] = field(default_factory=list)       # tools the master names but the checklist leaves unticked
    v4: dict = field(default_factory=dict)                   # master v4 extras (master_v4.V4), {} for the older format

    def by_entry(self, entry_id: str) -> list[Item]:
        return [i for i in self.items.values() if i.entry_id == entry_id]

    def skill_names(self) -> dict[str, str]:
        """canonical skill (lowercased) -> skill item id, for every skill in the Skills section."""
        return {i.text.casefold(): i.id for i in self.items.values() if i.section == "skill"}

    def evidenced_terms(self) -> set[str]:
        out: set[str] = set()
        for i in self.items.values():
            if i.kind != "declined":
                out.update(i.technologies)
        return out

    def declined(self) -> set[str]:
        """Canonical terms you confirmed you haven't used (evidence_additions.md "Answer: no")."""
        return {t for i in self.items.values() if i.kind == "declined" for t in i.technologies}

    def to_json(self) -> dict:
        return {"version": self.version, "hashes": self.hashes, "warnings": self.warnings, "excluded": self.excluded,
                "metric_flags": self.metric_flags, "tool_index": self.tool_index, "conflicts": self.conflicts, "v4": self.v4,
                "entries": {k: asdict(v) for k, v in self.entries.items()},
                "items": {k: asdict(v) for k, v in self.items.items()},
                "skill_categories": [[c, ids] for c, ids in self.skill_categories]}

    @classmethod
    def from_json(cls, d: dict) -> "Bank":
        return cls(items={k: Item(**v) for k, v in d["items"].items()},
                   entries={k: Entry(**v) for k, v in d["entries"].items()},
                   skill_categories=[(c, ids) for c, ids in d["skill_categories"]],
                   hashes=d["hashes"], warnings=d.get("warnings", []), version=d.get("version", 0),
                   excluded=d.get("excluded", []), metric_flags=d.get("metric_flags", []),
                   tool_index=d.get("tool_index", {}), conflicts=d.get("conflicts", []), v4=d.get("v4", {}))


# --- term helpers (shared with jd_analysis / verify) ------------------------------------------------------------
_VOCAB = None


def vocab():
    global _VOCAB
    if _VOCAB is None:
        _VOCAB = vocab_for(load_synonyms())
    return _VOCAB


def terms_in(text: str) -> list[str]:
    """Canonical technology / skill terms mentioned in text (longest match wins), in order of appearance."""
    v = vocab()
    seen, taken = [], []
    for canon, _surface, a, b in sorted(_hits(v, text or ""), key=lambda h: (h[2], -(h[3] - h[2]))):
        if any(s <= a < e for s, e in taken):
            continue
        taken.append((a, b))
        if canon not in seen:
            seen.append(canon)
    return seen


def canonical(term: str) -> str:
    v = vocab()
    t = term.strip()
    return v.canon_of.get(t.lower()) or v.short_cs.get(t) or t


def metrics_in(text: str) -> list[str]:
    """Numbers as written ("15+", "40%", "5-member"), normalized: no space before %."""
    out = []
    for m in METRIC_RE.finditer(text or ""):
        num, suf = m.group(1), (m.group(2) or "").replace(" ", "")
        if re.fullmatch(r"(19|20)\d\d", num) and not suf:
            continue   # a year, not a metric
        out.append(num + suf)
    return out


def _slug(s: str, words: int = 1) -> str:
    parts = [p for p in re.split(r"[^a-z0-9]+", s.lower()) if p]
    picked = parts[:words]
    if words == 1 and picked and len(picked[0]) < 4 and len(parts) > 1:
        picked = parts[:2]
    return "-".join(picked) or "x"


def _month(s: str) -> str | None:
    m = re.match(rf"({_MON})\s+(\d{{4}})", s.strip())
    return f"{m.group(2)}-{MONTHS.index(m.group(1)) + 1:02d}" if m else None


def _parse_dates(text: str) -> tuple[str, str | None, str | None]:
    t = text.strip()
    if t.lower().startswith("expected"):
        return t, None, _month(t[len("expected"):])
    parts = re.split(r"\s*[–-]\s*", t)
    start = _month(parts[0])
    end = None if len(parts) < 2 else ("present" if parts[1].lower() == "present" else _month(parts[1]))
    return t, start, end


_VERB_RE = re.compile(r"^([A-Z][a-z]+(?:ed|t|lt|wn|de|ng)?)\b")
_OUTCOME_RE = re.compile(r"\b(reduc|improv|increas|accelerat|enabl|rais|enhanc|identif|ensur|deliver|cut|sav)\w*[^,.;]*",
                         re.I)


def _facts(text: str) -> tuple[list[str], list[str], list[str]]:
    actions = [m.group(1) for m in [_VERB_RE.match(text)] if m]
    outcomes = [m.group(0).strip() for m in _OUTCOME_RE.finditer(text)]
    return actions, outcomes, metrics_in(text)


def _degree_terms(degree: str) -> list[str]:
    d = degree or ""
    out = []
    if re.search(r"\bbachelor|\bB\.?(?:S|E|Tech|A)\b", d, re.I):
        out.append(canonical("Bachelor's Degree"))
    if re.search(r"\bmaster|\bM\.?S\.?\b|\bM\.?Eng\b", d, re.I):
        out.append(canonical("Master's Degree"))
    return out


def _unique(base: str, used: set[str]) -> str:
    out, n = base, 2
    while out in used:
        out, n = f"{base}-{n}", n + 1
    used.add(out)
    return out


def parse_master(text: str, source_name: str = "master_resume.md", strict: bool = True) -> tuple[
        dict[str, Entry], dict[str, Item], list[tuple[str, list[str]]], list[str]]:
    """Every entry and item with its status (rule 1); ids are assigned to all of them, so excluding an unconfirmed
    bullet never renumbers the others. Callers keep only confirmed ones (`confirmed_only`). strict=False skips the
    structural errors (for the status report)."""
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    entries: dict[str, Entry] = {}
    items: dict[str, Item] = {}
    cats: list[tuple[str, list[str]]] = []
    warnings: list[str] = []
    errors: list[str] = []
    used: set[str] = set()
    section = None
    sec_status: str | None = None
    entry: Entry | None = None
    counters: dict[str, int] = {}
    SEC = {"experience": "experience", "projects": "project", "project": "project", "coursework": "coursework",
           "education": "education", "skills": "skill", "technical skills": "skill", "summary": "summary"}

    def add_item(sec: str, txt: str, entry_: Entry | None, status: str | None, line: int, **kw) -> Item:
        base = f"{entry_.id}-b" if entry_ else f"{sec}-"
        counters[base] = counters.get(base, 0) + 1
        iid = _unique(f"{base}{counters[base]}", used)
        a, o, m = _facts(txt)
        it = Item(iid, sec, txt, entry_.id if entry_ else None, entry_.organization if entry_ else None,
                  entry_.role if entry_ else None, entry_.dates if entry_ else None, terms_in(txt), a, o, m,
                  status=status, line=line, **kw)
        items[iid] = it
        return it

    for n, raw in enumerate(text.splitlines(), 1):
        line, tag, client = split_tags(raw.rstrip())
        if section in ("skill", "coursework") and not raw.lstrip().startswith("#"):
            line, tag, client = raw.rstrip(), None, None      # list lines: a trailing tag belongs to its last item
        if m := re.match(r"^##\s+(.+)$", line):
            section = SEC.get(m.group(1).strip().lower())
            sec_status, entry = tag, None
            if section is None:
                warnings.append(f"{source_name}:{n}: section '{m.group(1).strip()}' is not used by the Resume Builder")
            continue
        if not line.strip() or section is None:
            continue
        if m := re.match(r"^###\s+(.+)$", line):
            head = m.group(1).strip()
            status = tag or sec_status
            if section == "experience" or section == "education":
                dm = DATES_RE.search(head)
                if not dm:
                    errors.append(f"{source_name}:{n}: '{head}' has no dates in '(Mon YYYY – Mon YYYY)' form")
                    entry = None
                    continue
                body = head[:dm.start()].strip()
                if " — " not in body:
                    errors.append(f"{source_name}:{n}: '{head}' must be 'Title — Organization, Location (dates)'")
                    entry = None
                    continue
                role, rest = body.split(" — ", 1)
                org, loc = (rest.rsplit(", ", 1) + [None])[:2] if ", " in rest else (None, rest)
                if section == "education":   # "Degree — Institution, Location"
                    org, loc = (rest.split(", ", 1) + [None])[:2] if ", " in rest else (rest, None)
                dates, start, end = _parse_dates(dm.group(1))
                if start and end and end != "present" and end < start:
                    errors.append(f"{source_name}:{n}: '{head}' ends before it starts")
                pre = "exp" if section == "experience" else "edu"
                eid = _unique(f"{pre}-{_slug(org or role) if section == 'experience' else _slug(org or role)}", used)
                entry = Entry(eid, section, body, role.strip(), org.strip() if org else None,
                              loc.strip() if loc else None, dates, start, end, status=status)
            elif section == "project":
                name, _, tech = head.partition("|")
                link = None
                if lm := re.search(r"\((https?://[^)\s]+)\)", name):
                    link, name = lm.group(1), name.replace(lm.group(0), "")
                eid = _unique(f"proj-{_slug(name, 4)}", used)
                entry = Entry(eid, "project", head, name.strip(), None, None, None, None, None,
                              [t.strip() for t in tech.split(",") if t.strip()], link, status=status,
                              client_of=client)
            else:
                entry = None
                continue
            entries[entry.id] = entry
            terms = terms_in(head) + _degree_terms(entry.role if section == "education" else "")
            items[f"{entry.id}-h"] = Item(f"{entry.id}-h", section, head, entry.id, entry.organization, entry.role,
                                          entry.dates, list(dict.fromkeys(terms)), status=status, line=n)
            used.add(f"{entry.id}-h")
            continue
        content = re.sub(r"^\s*[-*+]\s+", "", line).strip()
        if section in ("experience", "project", "education"):
            if entry is None:
                errors.append(f"{source_name}:{n}: bullet '{content[:50]}' is not under a '###' heading")
                continue
            # an entry that isn't confirmed keeps all its lines out, whatever their own tags
            st = (tag or entry.status) if entry.status == "confirmed" else (entry.status if not tag or tag == "confirmed"
                                                                           else tag)
            add_item(section, content, entry, st, n)
        elif section == "coursework":
            if m := re.match(r"^\[([^\]]+)\]\s*:?\s*(.*)$", content):      # "- [confirmed] DBMS, Algorithms"
                tag, content = split_tags(f"x [{m.group(1)}]")[1], m.group(2)
            for c in [c.strip() for c in re.split(r",|;", content) if c.strip()]:
                c, ctag, _ = split_tags(c)
                iid = _unique(f"course-{_slug(c, 3)}", used)
                items[iid] = Item(iid, "coursework", c, technologies=terms_in(c), status=ctag or tag or sec_status,
                                  line=n)
        elif section == "skill":
            cat, _, rest = content.partition(":")
            cat, ctag, _ = split_tags(cat)                     # "- Languages [confirmed]: Java, Python"
            tag = ctag or tag
            if not rest:
                errors.append(f"{source_name}:{n}: skills line must be 'Category: a, b, c' (got '{content[:50]}')")
                continue
            ids = []
            for sk in [x.strip() for x in rest.split(",") if x.strip()]:
                sk, stag, _ = split_tags(sk)
                iid = _unique(f"skill-{_slug(sk, 3)}", used)
                terms = terms_in(sk) or [sk]
                items[iid] = Item(iid, "skill", sk, technologies=[canonical(t) for t in terms], category=cat.strip(),
                                  status=stag or tag or sec_status, line=n)
                ids.append(iid)
            cats.append((cat.strip(), ids))
        elif section == "summary":
            add_item("summary", content, None, tag or sec_status, n)
    # duplicates with different dates are contradictory
    seen: dict[tuple, Entry] = {}
    for e in entries.values():
        k = (e.section, (e.role or "").casefold(), (e.organization or e.location or "").casefold())
        if k in seen and seen[k].dates != e.dates:
            errors.append(f"{source_name}: '{e.heading}' appears twice with different dates "
                          f"({seen[k].dates} vs {e.dates}); keep one")
        seen[k] = e
    if not any(e.section == "experience" for e in entries.values()):
        errors.append(f"{source_name}: no '## Experience' entries found")
    if not cats:
        errors.append(f"{source_name}: no '## Skills' lines found")
    if errors and strict:
        raise InputError("master resume problems:\n  - " + "\n  - ".join(errors))
    return entries, items, cats, warnings


def confirmed_only(entries: dict[str, Entry], items: dict[str, Item], cats: list[tuple[str, list[str]]],
                   source_name: str = "master_resume.md") -> tuple[dict, dict, list, list[dict]]:
    """Keep confirmed entries and items (rule 1); the rest is listed in `excluded`."""
    ok = lambda st: st in (None, "confirmed")       # noqa: E731 — untagged is usable (older format)
    excluded = [{"id": i.id, "line": i.line, "text": i.text, "status": i.status, "source": source_name,
                 "section": i.section} for i in items.values() if not ok(i.status)]
    keep_e = {k: e for k, e in entries.items() if ok(e.status)}
    keep_i = {k: i for k, i in items.items() if ok(i.status) and (i.entry_id is None or i.entry_id in keep_e)}
    excluded += [{"id": i.id, "line": i.line, "text": i.text, "status": "entry not usable", "source": source_name,
                  "section": i.section} for i in items.values()
                 if ok(i.status) and i.entry_id and i.entry_id not in keep_e]
    keep_c = [(c, [i for i in ids if i in keep_i]) for c, ids in cats]
    return keep_e, keep_i, [(c, ids) for c, ids in keep_c if ids], excluded


# test / DEMO markers (rule 1: never use demo content): the uppercase DEMO label this project's test data carries, and
# obvious placeholders; an ordinary "built a demo for the client" is not one
DEMO_RE = re.compile(r"(?<![A-Za-z])DEMO(?![A-Za-z])|\b(?:lorem ipsum|test entry|placeholder|dummy data)\b")


def parse_additions(text: str, entries: dict[str, Entry], items: dict[str, Item]) -> tuple[list[Item], list[str]]:
    """Confirmed answers only (both formats, see the module doc). An answer for an unknown entry is an error (it can't
    be attributed); answers whose status isn't confirmed, or that look like test / DEMO text, are left out."""
    out: list[Item] = []
    warnings: list[str] = []
    counters: dict[str, int] = {}
    blocks: list[dict] = []
    cur: dict | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if raw.startswith("    "):
            continue                          # indented: the format example in the file's header
        if line.startswith("## "):
            m, m2 = ADD_HEAD_RE.match(line), ADD_HEAD2_RE.match(line)
            if m:
                cur = {"entry": m.group(1), "day": m.group(2), "status": "confirmed", "line": n, "old": True}
            elif m2:
                cur = {"entry": m2.group(2), "day": m2.group(1), "status": None, "line": n}
            else:
                cur = None
                warnings.append(f"evidence_additions.md:{n}: '{line[:60]}' is not '## YYYY-MM-DD — <entry id>'; "
                                "its answers are ignored")
                continue
            if cur["entry"] not in entries and cur["entry"] not in ("skills", "coursework"):
                raise InputError(f"evidence_additions.md:{n}: unknown entry id '{cur['entry']}' (known: "
                                 f"{', '.join(sorted(entries))})")
            blocks.append(cur)
            continue
        if not cur or not (m := re.match(r"^[-*]\s+([A-Za-z/ ]+?):\s*(.*)$", line)):
            continue
        k, v = m.group(1).strip().casefold(), m.group(2).strip()
        key = {"q": "question", "question": "question", "a": "answer", "answer": "answer", "status": "status",
               "role/project": "where", "role": "where", "project": "where", "level": "level",
               "keyword": "keyword"}.get(k)
        if key == "answer" and cur.get("old"):
            blocks.append(dict(cur, answer=v, line=n))   # old form: several A: lines per block
            cur["consumed"] = True
        elif key:
            cur[key] = v.casefold() if key in ("status", "level") else v
    for blk in blocks:
        if blk.get("old") and "answer" not in blk:
            continue                      # an old-format heading; its answers are separate blocks above
        ans = (blk.get("answer") or "").strip()
        if not ans:
            continue
        if blk.get("status") != "confirmed":
            warnings.append(f"evidence_additions.md:{blk['line']}: status '{blk.get('status') or 'missing'}', not "
                            "confirmed: not used")
            continue
        if DEMO_RE.search(ans) or DEMO_RE.search(blk.get("question") or ""):
            warnings.append(f"evidence_additions.md:{blk['line']}: looks like a test / DEMO entry: not used")
            continue
        eid, day = blk["entry"], blk["day"]
        e = entries.get(eid)
        counters[eid] = counters.get(eid, 0) + 1
        iid = f"add-{eid}-{counters[eid]}"
        kw = (blk.get("keyword") or "").strip()
        if kw and re.fullmatch(r"(?i)no|none|not used|never(?: used)?|i haven'?t(?: used it)?", ans.strip(" .")):
            # a confirmed "no": never claimed, never asked again (kept here so it survives cache resets)
            it = Item(iid, "declined", kw, technologies=[canonical(kw)], source="additions", added=day,
                      line=blk["line"])
            it.kind = "declined"
            out.append(it)
            continue
        a, o, mt = _facts(ans)
        sec = e.section if e else ("skill" if eid == "skills" else "coursework")
        it = Item(iid, sec, ans, e.id if e else None, e.organization if e else None,
                  e.role if e else None, e.dates if e else None, terms_in(ans), a, o, mt,
                  source="additions", added=day, level=blk.get("level"), where=blk.get("where"), line=blk["line"])
        if kw and sec == "skill":
            # a confirmed skill answer ("Go — coursework"): the skill itself, at its level
            it.text, it.technologies, it.kind = kw, [canonical(kw)], "skill"
            it.category = blk.get("level") or "self-reported"
        out.append(it)
    return out, warnings


def where_entries(where: str | None, entries: dict[str, Entry]) -> list[str]:
    """Entry ids named by a "Where" / "client work" value ("Accenture, CS6650", "Freelance")."""
    from .tools import GENERIC_WHERE
    if not where or where.strip().casefold() in GENERIC_WHERE:
        return []
    out = []
    for tok in [t.strip().casefold() for t in re.split(r"[,;/]| and ", where) if t.strip()]:
        for e in entries.values():
            hay = " ".join(x for x in (e.heading, e.role, e.organization) if x).casefold()
            if len(tok) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", hay) and e.id not in out:
                out.append(e.id)
    return out


def metric_flags(items: dict[str, Item]) -> list[dict]:
    """Rule 3: the same percentage / multiplier claimed by two different bullets looks inconsistent. Flagged for you
    to check, never changed."""
    by: dict[str, list[Item]] = {}
    for i in items.values():
        if i.section not in ("experience", "project", "summary"):
            continue
        for m in set(i.metrics):
            if m.endswith(("%", "x")):
                by.setdefault(m, []).append(i)
    out = []
    for m, its in sorted(by.items()):
        if len(its) > 1:
            out.append({"metric": m, "items": [i.id for i in its],
                        "detail": f"{m} is claimed by {len(its)} bullets: " + " | ".join(
                            f"{i.id}: {i.text[:70]}" for i in its) + " — check it isn't the same result counted twice "
                        "or two different measures (rule 3); it is never changed automatically"})
    return out


def tool_items(path: Path, entries: dict[str, Entry]) -> tuple[list[Item], dict[str, dict], list[str]]:
    from . import tools as T
    if not path.exists():
        return [], {}, []
    tl, warnings = T.parse(path.read_text(encoding="utf-8"))
    index = {t.name.casefold(): {"name": t.name, "ticked": t.ticked or index_ticked(tl, t), "family": t.family}
             for t in tl}
    out, seen = [], set()
    for t in T.ticked(tl):
        terms = list(dict.fromkeys([*terms_in(t.name), *[canonical(x) for x in t.names]]))
        key = tuple(sorted(terms)) or (t.name.casefold(),)
        if key in seen:
            continue
        seen.add(key)
        out.append(Item(_unique(f"tool-{_slug(t.name, 3)}", {i.id for i in out}), "tool", t.name,
                        technologies=terms, category=t.family, source="tools", status="confirmed",
                        where=t.where or None, where_entries=where_entries(t.where, entries),
                        level=t.level or None, line=t.line))
    return out, index, warnings


def index_ticked(tl, t) -> bool:
    return any(x.ticked and x.name.casefold() == t.name.casefold() for x in tl)


def check_template_drift(template_text: str, items: dict[str, Item]) -> list[str]:
    """Bullets in the template body that are not in the master resume: never used, reported so you can add them
    to the master resume (or to evidence_additions.md through the interview) if they are true."""
    from rapidfuzz import fuzz
    body = template_text.split("\\begin{document}", 1)[-1]
    m = re.search(r"\\section\{(Experience|Projects)", body)
    body = body[m.start():] if m else ""
    known = [i.text for i in items.values() if i.section in ("experience", "project")]
    out = []
    for m in re.finditer(r"\\item\s+(.+)", body):
        t = re.sub(r"\\%", "%", m.group(1)).strip()
        t = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", t)
        if not any(fuzz.ratio(t, k) >= 90 for k in known):
            out.append(t)
    return out


def build_bank(master: Path | None = None, additions: Path | None = None, template: Path | None = None,
               checklist: Path | None = None) -> Bank:
    master = master or rc.MASTER_RESUME
    additions = additions if additions is not None else rc.ADDITIONS
    checklist = checklist if checklist is not None else rc.TOOL_CHECKLIST
    if not master.exists():
        raise InputError(f"{master} is missing. It is the only source of facts about you.")
    text = master.read_text(encoding="utf-8")
    from . import master_v4
    if master_v4.is_v4(text):
        return _build_v4(text, master, additions, template, checklist)
    all_entries, all_items, all_cats, warnings = parse_master(text, master.name)
    entries, items, cats, excluded = confirmed_only(all_entries, all_items, all_cats, master.name)
    problems = []
    if not any(e.section == "experience" for e in entries.values()):
        problems.append("no confirmed Experience entries")
    if not cats:
        problems.append("no confirmed Skills")
    if problems:
        raise InputError(f"{master.name}: {' and '.join(problems)} (items marked [needs confirmation] or [demo] are "
                         "never used); `python -m resume_builder tags` lists every item's status.")
    for e in entries.values():
        if e.client_of:
            hit = where_entries(e.client_of, {k: x for k, x in entries.items() if x.section == "experience"})
            if hit:
                e.client_of = hit[0]
            else:
                warnings.append(f"{master.name}: project '{e.role}' is tagged client work for '{e.client_of}', which "
                                "matches no confirmed role; it stays under Projects")
                e.client_of = None
    hashes = {"master_resume": sha256_file(master)}
    if additions.exists():
        adds, w = parse_additions(additions.read_text(encoding="utf-8"), entries, items)
        warnings += w
        for it in adds:
            items[it.id] = it
        hashes["evidence_additions"] = sha256_file(additions)
    else:
        hashes["evidence_additions"] = "none"
    tools_, index, w = tool_items(checklist, entries)
    warnings += w
    master_terms = {t for i in items.values() if i.source == "master" for t in i.technologies}
    master_text = " ".join(i.text for i in items.values() if i.source == "master")

    def named(tool_name: str) -> bool:
        return canonical(tool_name) in master_terms or bool(re.search(
            rf"(?<![\w.]){re.escape(tool_name)}(?![\w])", master_text, re.I))
    conflicts = []
    for name, t in index.items():
        if not t["ticked"] and named(t["name"]) and not any(
                x["ticked"] and canonical(x["name"]) == canonical(t["name"]) for x in index.values()):
            if t["name"] in conflicts:
                continue
            conflicts.append(t["name"])
            warnings.append(f"conflict: {master.name} names {t['name']}, but tool_checklist.md leaves it unticked — "
                            "tick it (with where / level) or remove it from the master resume; the builder won't "
                            "list it in Technical Skills meanwhile, and flags bullets that name it")
    for it in tools_:
        items[it.id] = it
    hashes["tool_checklist"] = sha256_file(checklist) if checklist.exists() else "none"
    hashes["code"] = code_hash()
    template = template or rc.TEMPLATE
    if template.exists():
        drift = check_template_drift(template.read_text(encoding="utf-8"), items)
        if drift:
            warnings.append(f"{len(drift)} bullet(s) in resume_template.tex are not in master_resume.md and are "
                            "never used: " + " | ".join(d[:70] for d in drift))
    return Bank(items, entries, cats, hashes, warnings, excluded=excluded, metric_flags=metric_flags(items),
                tool_index=index, conflicts=conflicts)


def code_hash() -> str:
    """The parsing code itself: a builder update rebuilds the cached bank even when your inputs didn't change."""
    here = Path(__file__).parent
    return "".join(sha256_file(here / f)[:12] for f in ("evidence.py", "tools.py", "master_v4.py"))


def _build_v4(text: str, master: Path, additions: Path, template: Path | None, checklist: Path) -> Bank:
    """Master v4: statuses come from the master's own labels (verified / estimated usable; unverified, pending,
    planned, Legacy and Excluded never). Tool checklist ticks: a tool the master has evidence for keeps the master's
    tier; a ticked tool with no evidence in the master is self-reported; the master's Excluded list always wins."""
    from . import master_v4 as M
    from . import tools as T
    entries, items, cats, warnings, excluded, v4 = M.parse(text, master.name)
    hashes = {"master_resume": sha256_file(master)}
    if additions.exists():
        adds, w = parse_additions(additions.read_text(encoding="utf-8"), entries, items)
        warnings += w
        for it in adds:
            items[it.id] = it
        hashes["evidence_additions"] = sha256_file(additions)
    else:
        hashes["evidence_additions"] = "none"
    sk = v4.skills
    banned = {canonical(x).casefold() for x in sk["excluded"]} | {x.casefold() for x in sk["excluded"]}
    index = {}
    if checklist.exists():
        tl, w = T.parse(checklist.read_text(encoding="utf-8"))
        warnings += w
        index = {t.name.casefold(): {"name": t.name, "ticked": index_ticked(tl, t), "family": t.family} for t in tl}
        def keys(name: str) -> set[str]:
            out = set()
            for x in [name, *re.findall(r"\(([^)]*)\)", name)[0].split(",")] if "(" in name else [name]:
                x = re.sub(r"\s*\(.*\)$", "", x).strip()
                for y in (x, canonical(x)):
                    y = y.casefold()
                    out |= {y, re.sub(r"^(?:aws|amazon|gcp|google cloud)\s+", "", y)}
            return out
        known = set().union(*[keys(n) for n, tier in sk["tier"].items() if tier != "planned"])
        planned = set().union(*[keys(n) for n in sk["planned"]]) | {"gcp", "google cloud"}
        for t in T.ticked(tl):
            names = set().union(*[keys(x) for x in [t.name, *t.names]])
            if names & banned:
                warnings.append(f"tool_checklist.md: {t.name} is ticked but on the master's Excluded list: never used")
                continue
            if names & known:
                continue                                # the master's tier wins
            if names & planned or re.match(r"^(?:gcp|google cloud)\b", t.name, re.I):
                continue                                # planned in the master: never listed until built
            iid = f"skill-{_slug(t.name, 4)}"
            if iid in items:
                continue
            items[iid] = Item(iid, "skill", t.name, technologies=list(dict.fromkeys([*terms_in(t.name),
                              *[canonical(x) for x in t.names]])), category="self-reported", source="tools",
                              status="confirmed", where=t.where or None, level=t.level or None, line=t.line)
            items[iid].kind = "skill"
            sk["tier"].setdefault(t.name, "self-reported")
    hashes["tool_checklist"] = sha256_file(checklist) if checklist.exists() else "none"
    hashes["code"] = code_hash()
    no = {i.technologies[0] for i in items.values() if i.kind == "declined"}
    for name, tier in list(sk["tier"].items()):
        if canonical(re.sub(r"\s*\(.*\)$", "", name)) in no and tier != "planned":
            sk["tier"][name] = "declined"            # you said no: overrides a self-reported entry in the master
            for k in [k for k, i in items.items() if i.kind == "skill" and i.text == name]:
                del items[k]
    for i in items.values():
        if i.source == "additions" and i.kind == "skill":
            sk["tier"][i.text] = "coursework" if i.category in ("coursework", "tutorial") else "self-reported"
    for x in v4.excluded_roles + v4.excluded_projects:
        excluded.append({"id": f"excluded-{_slug(x, 4)}", "line": None, "text": x, "status": "excluded",
                         "source": master.name, "section": "experience"})
    # rule 6: compare distinct bank bullets (a snapshot line and its variants restate the same result)
    bank = Bank(items, entries, cats, hashes, warnings, excluded=excluded, metric_flags=metric_flags(
        {k: i for k, i in items.items() if i.kind == "bullet" and not i.variant}), tool_index=index, v4=v4.to_json())
    return bank


def input_hashes(master: Path, additions: Path, checklist: Path) -> dict:
    return {"master_resume": sha256_file(master) if master.exists() else None, "code": code_hash(),
            "evidence_additions": sha256_file(additions) if additions.exists() else "none",
            "tool_checklist": sha256_file(checklist) if checklist.exists() else "none"}


def load_bank(cache_dir: Path | None = None, master: Path | None = None, additions: Path | None = None,
              template: Path | None = None, checklist: Path | None = None) -> Bank:
    """The cached bank, rebuilt when master_resume.md, evidence_additions.md or tool_checklist.md changes (by hash)."""
    master = master or rc.MASTER_RESUME
    additions = additions if additions is not None else rc.ADDITIONS
    checklist = checklist if checklist is not None else rc.TOOL_CHECKLIST
    cache_dir = cache_dir or rc.resumes_dir() / "_cache"
    cache = cache_dir / "evidence_bank.json"
    want = input_hashes(master, additions, checklist)
    if cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            if d.get("hashes") == want and d.get("version") == BANK_VERSION:
                return Bank.from_json(d)
        except (ValueError, KeyError, TypeError):
            pass
    bank = build_bank(master, additions, template, checklist)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(bank.to_json(), indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache)
    return bank


def status_report(master: Path | None = None, additions: Path | None = None) -> dict:
    """Rule 1 status of every item: what is confirmed, what needs a tag (untagged), needs confirmation, or demo."""
    master = master or rc.MASTER_RESUME
    additions = additions if additions is not None else rc.ADDITIONS
    entries, items, _cats, _w = parse_master(master.read_text(encoding="utf-8"), master.name, strict=False)
    rows = []
    for i in items.values():
        e = entries.get(i.entry_id) if i.entry_id else None
        st = i.status or "untagged"
        if e and e.status != "confirmed" and i.id.endswith("-h") is False and st == "confirmed":
            st = f"confirmed (but its entry is {e.status or 'untagged'})"
        rows.append({"id": i.id, "line": i.line, "section": i.section, "status": st,
                     "text": i.text if not i.id.endswith("-h") else f"### {i.text}"})
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"].split(" (")[0]] = counts.get(r["status"].split(" (")[0], 0) + 1
    add_warn = []
    if additions.exists():
        try:
            _a, add_warn = parse_additions(additions.read_text(encoding="utf-8"), entries, items)
        except InputError as ex:
            add_warn = [str(ex)]
    return {"rows": sorted(rows, key=lambda r: r["line"] or 0), "counts": counts, "additions_warnings": add_warn}


def month_label(ym: str | None) -> str:
    if not ym or ym == "present":
        return "Present"
    y, m = ym.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")
