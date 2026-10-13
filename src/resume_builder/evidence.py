"""Evidence bank: every fact the resume may use, parsed from inputs/master_resume.md (never modified) and the
confirmed answers in inputs/evidence_additions.md. Each item has an id that tailored bullets must cite.

master_resume.md layout (the same one HireScope's matcher reads):
  ## Experience   ### Role — Organization, Location (Mon YYYY – Mon YYYY)   - bullet ...
  ## Projects     ### Project Name | Tech, Tech                              - bullet ...
  ## Coursework   - Course, Course, ...
  ## Education    ### Degree — Institution, Location (dates)                 - detail ...
  ## Skills       - Category: skill, skill, ...
  ## Summary      - line ... (skill evidence only)

evidence_additions.md (written by the evidence interview, only after you confirm an answer):
  ## <entry id> — YYYY-MM-DD — confirmed
  - Q: question asked
  - A: the fact you confirmed
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
BANK_VERSION = 2


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
    source: str = "master"          # master | additions
    added: str | None = None        # additions: date confirmed


@dataclass
class Bank:
    items: dict[str, Item]
    entries: dict[str, Entry]
    skill_categories: list[tuple[str, list[str]]]   # [(category, [item ids])]
    hashes: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    version: int = BANK_VERSION

    def by_entry(self, entry_id: str) -> list[Item]:
        return [i for i in self.items.values() if i.entry_id == entry_id]

    def skill_names(self) -> dict[str, str]:
        """canonical skill (lowercased) -> skill item id, for every skill in the Skills section."""
        return {i.text.casefold(): i.id for i in self.items.values() if i.section == "skill"}

    def evidenced_terms(self) -> set[str]:
        out: set[str] = set()
        for i in self.items.values():
            out.update(i.technologies)
        return out

    def to_json(self) -> dict:
        return {"version": self.version, "hashes": self.hashes, "warnings": self.warnings,
                "entries": {k: asdict(v) for k, v in self.entries.items()},
                "items": {k: asdict(v) for k, v in self.items.items()},
                "skill_categories": [[c, ids] for c, ids in self.skill_categories]}

    @classmethod
    def from_json(cls, d: dict) -> "Bank":
        return cls(items={k: Item(**v) for k, v in d["items"].items()},
                   entries={k: Entry(**v) for k, v in d["entries"].items()},
                   skill_categories=[(c, ids) for c, ids in d["skill_categories"]],
                   hashes=d["hashes"], warnings=d.get("warnings", []), version=d.get("version", 0))


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


def parse_master(text: str, source_name: str = "master_resume.md") -> tuple[dict[str, Entry], dict[str, Item],
                                                                               list[tuple[str, list[str]]], list[str]]:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    entries: dict[str, Entry] = {}
    items: dict[str, Item] = {}
    cats: list[tuple[str, list[str]]] = []
    warnings: list[str] = []
    errors: list[str] = []
    used: set[str] = set()
    section = None
    entry: Entry | None = None
    counters: dict[str, int] = {}
    SEC = {"experience": "experience", "projects": "project", "project": "project", "coursework": "coursework",
           "education": "education", "skills": "skill", "technical skills": "skill", "summary": "summary"}

    def add_item(sec: str, txt: str, entry_: Entry | None, **kw) -> Item:
        base = f"{entry_.id}-b" if entry_ else f"{sec}-"
        counters[base] = counters.get(base, 0) + 1
        iid = _unique(f"{base}{counters[base]}", used)
        a, o, m = _facts(txt)
        it = Item(iid, sec, txt, entry_.id if entry_ else None, entry_.organization if entry_ else None,
                  entry_.role if entry_ else None, entry_.dates if entry_ else None, terms_in(txt), a, o, m, **kw)
        items[iid] = it
        return it

    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if m := re.match(r"^##\s+(.+)$", line):
            section = SEC.get(m.group(1).strip().lower())
            entry = None
            if section is None:
                warnings.append(f"{source_name}:{n}: section '{m.group(1).strip()}' is not used by the Resume Builder")
            continue
        if not line.strip() or section is None:
            continue
        if m := re.match(r"^###\s+(.+)$", line):
            head = m.group(1).strip()
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
                              loc.strip() if loc else None, dates, start, end)
            elif section == "project":
                name, _, tech = head.partition("|")
                link = None
                if lm := re.search(r"\((https?://[^)\s]+)\)", name):
                    link, name = lm.group(1), name.replace(lm.group(0), "")
                eid = _unique(f"proj-{_slug(name, 4)}", used)
                entry = Entry(eid, "project", head, name.strip(), None, None, None, None, None,
                              [t.strip() for t in tech.split(",") if t.strip()], link)
            else:
                entry = None
                continue
            entries[entry.id] = entry
            terms = terms_in(head) + _degree_terms(entry.role if section == "education" else "")
            items[f"{entry.id}-h"] = Item(f"{entry.id}-h", section, head, entry.id, entry.organization, entry.role,
                                          entry.dates, list(dict.fromkeys(terms)))
            used.add(f"{entry.id}-h")
            continue
        content = re.sub(r"^\s*[-*+]\s+", "", line).strip()
        if section in ("experience", "project", "education"):
            if entry is None:
                errors.append(f"{source_name}:{n}: bullet '{content[:50]}' is not under a '###' heading")
                continue
            add_item(section, content, entry)
        elif section == "coursework":
            for c in [c.strip() for c in re.split(r",|;", content) if c.strip()]:
                iid = _unique(f"course-{_slug(c, 3)}", used)
                items[iid] = Item(iid, "coursework", c, technologies=terms_in(c))
        elif section == "skill":
            cat, _, rest = content.partition(":")
            if not rest:
                errors.append(f"{source_name}:{n}: skills line must be 'Category: a, b, c' (got '{content[:50]}')")
                continue
            ids = []
            for s in [s.strip() for s in rest.split(",") if s.strip()]:
                iid = _unique(f"skill-{_slug(s, 3)}", used)
                terms = terms_in(s) or [s]
                items[iid] = Item(iid, "skill", s, technologies=[canonical(t) for t in terms], category=cat.strip())
                ids.append(iid)
            cats.append((cat.strip(), ids))
        elif section == "summary":
            add_item("summary", content, None)
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
    if errors:
        raise InputError("master resume problems:\n  - " + "\n  - ".join(errors))
    return entries, items, cats, warnings


def parse_additions(text: str, entries: dict[str, Entry], items: dict[str, Item]) -> tuple[list[Item], list[str]]:
    """Confirmed answers only. An answer for an unknown entry is an error (it can't be attributed)."""
    out: list[Item] = []
    warnings: list[str] = []
    head = None
    counters: dict[str, int] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("## "):
            m = ADD_HEAD_RE.match(line)
            head = (m.group(1), m.group(2)) if m else None
            if not m:
                warnings.append(f"evidence_additions.md:{n}: '{line[:60]}' is not '## <entry id> — YYYY-MM-DD — "
                                "confirmed'; its answers are ignored")
            elif m.group(1) not in entries and m.group(1) not in ("skills", "coursework"):
                raise InputError(f"evidence_additions.md:{n}: unknown entry id '{m.group(1)}' (known: "
                                 f"{', '.join(sorted(entries))})")
            continue
        if head and (m := re.match(r"^[-*]\s+A:\s*(.+)$", line)):
            eid, day = head
            e = entries.get(eid)
            counters[eid] = counters.get(eid, 0) + 1
            iid = f"add-{eid}-{counters[eid]}"
            a, o, mt = _facts(m.group(1))
            sec = e.section if e else ("skill" if eid == "skills" else "coursework")
            out.append(Item(iid, sec, m.group(1).strip(), e.id if e else None, e.organization if e else None,
                            e.role if e else None, e.dates if e else None, terms_in(m.group(1)), a, o, mt,
                            source="additions", added=day))
    return out, warnings


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


def build_bank(master: Path | None = None, additions: Path | None = None, template: Path | None = None) -> Bank:
    master = master or rc.MASTER_RESUME
    additions = additions if additions is not None else rc.ADDITIONS
    if not master.exists():
        raise InputError(f"{master} is missing. It is the only source of facts about you.")
    text = master.read_text(encoding="utf-8")
    entries, items, cats, warnings = parse_master(text, master.name)
    hashes = {"master_resume": sha256_file(master)}
    if additions.exists():
        adds, w = parse_additions(additions.read_text(encoding="utf-8"), entries, items)
        warnings += w
        for it in adds:
            items[it.id] = it
        hashes["evidence_additions"] = sha256_file(additions)
    else:
        hashes["evidence_additions"] = "none"
    template = template or rc.TEMPLATE
    if template.exists():
        drift = check_template_drift(template.read_text(encoding="utf-8"), items)
        if drift:
            warnings.append(f"{len(drift)} bullet(s) in resume_template.tex are not in master_resume.md and are "
                            "never used: " + " | ".join(d[:70] for d in drift))
    return Bank(items, entries, cats, hashes, warnings)


def load_bank(cache_dir: Path | None = None, master: Path | None = None, additions: Path | None = None,
              template: Path | None = None) -> Bank:
    """The cached bank, rebuilt when master_resume.md or evidence_additions.md changes (by hash)."""
    master = master or rc.MASTER_RESUME
    additions = additions if additions is not None else rc.ADDITIONS
    cache_dir = cache_dir or rc.resumes_dir() / "_evidence"
    cache = cache_dir / "bank.json"
    want = {"master_resume": sha256_file(master) if master.exists() else None,
            "evidence_additions": sha256_file(additions) if additions.exists() else "none"}
    if cache.exists():
        try:
            d = json.loads(cache.read_text(encoding="utf-8"))
            if d.get("hashes") == want and d.get("version") == BANK_VERSION:
                return Bank.from_json(d)
        except (ValueError, KeyError, TypeError):
            pass
    bank = build_bank(master, additions, template)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(bank.to_json(), indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache)
    return bank


def month_label(ym: str | None) -> str:
    if not ym or ym == "present":
        return "Present"
    y, m = ym.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")
