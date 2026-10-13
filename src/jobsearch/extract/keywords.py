"""ATS keyword capture: the JD's exact terms, tagged required / preferred / mentioned, weighted by section and
frequency, compared with the master resume as matched / synonym-matched / missing.

Coverage measures keyword overlap, not the chance of passing an ATS. Missing keywords are gaps only:
nothing here suggests adding a keyword the resume has no evidence for.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from .. import paths

SECTION_WEIGHT = {"required": 3.0, "preferred": 1.5, "mentioned": 1.0}
_B, _E = r"(?<![A-Za-z0-9+#])", r"(?![A-Za-z0-9+#])"


@dataclass(frozen=True, eq=False)
class Vocab:
    canon_of: dict[str, str]           # lowercased alias -> canonical
    aliases: dict[str, tuple[str, ...]]  # canonical -> all surface forms (canonical first)
    category: dict[str, str]
    short_cs: dict[str, str]           # 1-2 char case-sensitive aliases -> canonical
    stop: frozenset[str]
    implies: dict[str, tuple[str, ...]]  # umbrella canonical -> concrete canonicals that satisfy it
    pattern: re.Pattern                # case-insensitive alternation of aliases >= 3 chars
    short_pattern: re.Pattern | None


@lru_cache(maxsize=4)
def load_vocab(synonyms_key: tuple, path: Path = paths.CONFIG / "ats_keywords.yaml") -> Vocab:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged: dict[str, list[str]] = {}
    category: dict[str, str] = {}
    for cat, terms in (raw.get("categories") or {}).items():
        for canon, al in (terms or {}).items():
            merged.setdefault(str(canon), []).extend(str(a) for a in (al or []))
            category.setdefault(str(canon), cat)
    for canon, al in synonyms_key:      # skills_synonyms.yaml wins on canonical names
        key = next((k for k in merged if k.casefold() == canon.casefold()), canon)
        if key != canon:
            merged[canon] = merged.pop(key)
            category[canon] = category.pop(key, "skills")
        merged.setdefault(canon, []).extend(al)
        category.setdefault(canon, "skills")
    canon_of, aliases, short_cs = {}, {}, {}
    for canon, al in merged.items():
        forms = list(dict.fromkeys([canon, *al]))
        aliases[canon] = tuple(forms)
        for f in forms:
            if len(f) <= 2:
                short_cs.setdefault(f, canon)
            else:
                canon_of.setdefault(f.lower(), canon)
    body = "|".join(re.escape(a) for a in sorted(canon_of, key=len, reverse=True))
    sp = None
    if short_cs:
        sbody = "|".join(re.escape(a) for a in sorted(short_cs, key=len, reverse=True))
        sp = re.compile(rf"(?<![.!?:]\s)(?<!^){_B}(?:{sbody}){_E}", re.M)
    implies = {}
    for umbrella, concrete in (raw.get("implies") or {}).items():
        key = canon_of.get(str(umbrella).lower(), str(umbrella))
        implies[key] = tuple(canon_of.get(str(c).lower(), str(c)) for c in concrete or [])
    return Vocab(canon_of, aliases, category, short_cs, frozenset(s.lower() for s in raw.get("stoplist") or []),
                 implies, re.compile(rf"{_B}(?:{body}){_E}", re.I), sp)


def vocab_for(synonyms: dict[str, list[str]]) -> Vocab:
    return load_vocab(tuple((k, tuple(v)) for k, v in synonyms.items()))


def _hits(vocab: Vocab, text: str):
    """(canonical, exact surface, start, end) for every vocabulary mention, longest match first."""
    for m in vocab.pattern.finditer(text):
        canon = vocab.canon_of[m.group(0).lower()]
        end = m.end()
        # keep "Amazon Web Services (AWS)" together when the parenthetical is the same term
        if p := re.match(r"\s*\(([^)]{1,25})\)", text[end:]):
            inner = p.group(1).strip()
            if vocab.canon_of.get(inner.lower()) == canon or vocab.short_cs.get(inner) == canon:
                end += p.end()
        yield canon, text[m.start():end], m.start(), end
    if vocab.short_pattern:
        for m in vocab.short_pattern.finditer(text):
            yield vocab.short_cs[m.group(0)], m.group(0), m.start(), m.end()


_CANDIDATE = re.compile(r"(?<![\w.])([A-Z][a-z]+[A-Z][A-Za-z]+|[A-Z]{2,6}\d*|[A-Za-z]+\.js|[A-Za-z]+(?:\+\+|#))(?![\w])")


@dataclass
class Keyword:
    canonical: str
    term: str                    # the JD's exact wording (most common surface)
    section: str                 # required | preferred | mentioned
    count: int
    weight: float
    category: str
    status: str = "missing"      # matched | synonym | missing
    resume_term: str | None = None
    evidence: str | None = None  # resume line
    evidence_section: str | None = None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("canonical", "term", "section", "count", "weight", "category",
                                               "status", "resume_term", "evidence", "evidence_section")}


@dataclass
class KeywordReport:
    keywords: list[Keyword] = field(default_factory=list)
    coverage: float | None = None
    required_total: int = 0
    required_hit: int = 0

    def as_dict(self) -> dict:
        return {"coverage": self.coverage, "required_total": self.required_total, "required_hit": self.required_hit,
                "keywords": [k.as_dict() for k in self.keywords]}


def extract_keywords(text: str, min_reqs: list[str], pref_reqs: list[str], vocab: Vocab,
                     company: str | None = None) -> list[Keyword]:
    sections = [("required", "\n".join(min_reqs)), ("preferred", "\n".join(pref_reqs)), ("mentioned", text or "")]
    found: dict[str, dict] = {}
    for section, body in sections:
        taken: list[tuple[int, int]] = []
        for canon, surface, a, b in sorted(_hits(vocab, body), key=lambda h: (h[2], -(h[3] - h[2]))):
            if any(s <= a < e for s, e in taken):
                continue
            taken.append((a, b))
            k = found.setdefault(canon, {"section": section, "surfaces": {}, "count": 0})
            k["surfaces"][surface] = k["surfaces"].get(surface, 0) + 1
            k["count"] += 1
        if section != "mentioned":
            # unknown tech-looking terms in requirement lines (CamelCase, ACRONYMS, *.js, C++/C#)
            for m in _CANDIDATE.finditer(body):
                term = m.group(1)
                if term.lower() in vocab.stop or term.lower() in vocab.canon_of or term in vocab.short_cs:
                    continue
                k = found.setdefault(term, {"section": section, "surfaces": {}, "count": 0, "unknown": True})
                k["surfaces"][term] = k["surfaces"].get(term, 0) + 1
                k["count"] += 1
    own = {w for w in re.findall(r"[a-z0-9]+", (company or "").lower()) if len(w) > 2}
    out = []
    for canon, k in found.items():
        if own and set(re.findall(r"[a-z0-9]+", canon.lower())) <= own:
            continue   # the company's own name ("Figma" at Figma) is not a keyword
        term = max(k["surfaces"].items(), key=lambda kv: kv[1])[0]
        # "mentioned" counts include the requirement lines again (they are part of the full text)
        count = k["count"]
        w = SECTION_WEIGHT[k["section"]] * (1 + math.log2(max(1, count)))
        out.append(Keyword(canon, term, k["section"], count, round(min(w, 12.0), 2),
                           "other" if k.get("unknown") else vocab.category.get(canon, "skills")))
    order = {"required": 0, "preferred": 1, "mentioned": 2}
    return sorted(out, key=lambda k: (order[k.section], -k.weight, k.term.lower()))


@dataclass(frozen=True, eq=False)
class ResumeTerms:
    lines: tuple[tuple[str, str], ...]   # (section, line)


def resume_terms(resume) -> ResumeTerms:
    lines = []
    for sec in ("experience", "projects", "coursework", "education", "skills", "summary"):
        for e in resume.sections.get(sec, []):
            for ln in [e.heading, *e.bullets]:
                if ln:
                    lines.append((sec, ln))
    return ResumeTerms(tuple(lines))


def _find_line(rt: ResumeTerms, surface: str) -> tuple[str, str] | None:
    pat = re.compile(rf"{_B}{re.escape(surface)}{_E}", re.I if len(surface) > 2 else 0)
    for sec, ln in rt.lines:
        if pat.search(ln):
            return sec, ln
    return None


def compare(keywords: list[Keyword], rt: ResumeTerms, vocab: Vocab) -> KeywordReport:
    """matched = the JD's exact term (or one of its spellings with the same wording) is in the resume;
    synonym = the resume has the same skill under another name (shows which wording the JD uses)."""
    for k in keywords:
        hit = _find_line(rt, k.term)
        if hit:
            k.status, k.resume_term, (k.evidence_section, k.evidence) = "matched", k.term, hit
            continue
        for alias in vocab.aliases.get(k.canonical, ()):
            if alias.lower() == k.term.lower():
                continue
            if hit := _find_line(rt, alias):
                k.status, k.resume_term, (k.evidence_section, k.evidence) = "synonym", alias, hit
                break
        if k.status == "missing":
            for concrete in vocab.implies.get(k.canonical, ()):
                for alias in vocab.aliases.get(concrete, (concrete,)):
                    if hit := _find_line(rt, alias):
                        k.status, k.resume_term, (k.evidence_section, k.evidence) = "synonym", alias, hit
                        break
                if k.status != "missing":
                    break
    total = sum(k.weight for k in keywords)
    got = sum(k.weight for k in keywords if k.status != "missing")
    req = [k for k in keywords if k.section == "required"]
    return KeywordReport(keywords, round(100 * got / total, 1) if total else None, len(req),
                         sum(1 for k in req if k.status != "missing"))
