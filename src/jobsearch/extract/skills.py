"""Synonym-aware skill extraction (config/skills_synonyms.yaml)."""
from __future__ import annotations

import re
from functools import lru_cache

_B = r"(?<![A-Za-z0-9+#])"   # word-ish boundaries that also work for C++, C#, Node.js, CI/CD
_E = r"(?![A-Za-z0-9+#])"


@lru_cache(maxsize=4)
def _compiled(synonyms_key: tuple) -> tuple[re.Pattern | None, dict[str, str], re.Pattern | None, dict[str, str]]:
    """One alternation regex for case-insensitive aliases and one for short case-sensitive ones."""
    ci: dict[str, str] = {}   # lowercased alias -> canonical
    cs: dict[str, str] = {}   # exact alias -> canonical
    for canon, aliases in synonyms_key:
        for a in dict.fromkeys([canon, *aliases]):
            if len(a) <= 2:
                cs.setdefault(a, canon)
            else:
                ci.setdefault(a.lower(), canon)

    def alt(keys):
        return "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True))

    ci_re = re.compile(f"{_B}(?:{alt(ci)}){_E}", re.I) if ci else None
    # Short aliases (Go, C, R, JS, ML) are case-sensitive. At the start of a line/sentence they only
    # count when they read as a list item ("Go, Rust", "Go or C++", a bare "Go" line), so
    # "Go above and beyond." does not count as Go.
    cs_re = re.compile(
        rf"(?<![.!?:]\s)(?<!^){_B}(?:{alt(cs)}){_E}"
        rf"|(?:^|(?<=[.!?:]\s))(?:{alt(cs)})(?=[ \t]*$|[ \t]*[,/;)]|[ \t]+(?:or|and)\b)", re.M) if cs else None
    return ci_re, ci, cs_re, cs


def _key(synonyms: dict[str, list[str]]) -> tuple:
    return tuple((k, tuple(v)) for k, v in synonyms.items())


def extract_skills(text: str, synonyms: dict[str, list[str]]) -> list[str]:
    """Canonical skill names found in text, in synonyms-file order."""
    if not text:
        return []
    ci_re, ci, cs_re, cs = _compiled(_key(synonyms))
    found: set[str] = set()
    if ci_re:
        found.update(ci[m.group(0).lower()] for m in ci_re.finditer(text))
    if cs_re:
        found.update(cs[m.group(0)] for m in cs_re.finditer(text))
    return [c for c in synonyms if c in found]


def canonicalize(name: str, synonyms: dict[str, list[str]]) -> str:
    """Map a free-form skill name (e.g. from the resume Skills list) to its canonical form."""
    n = name.strip()
    for canon, aliases in synonyms.items():
        if n.casefold() in (a.casefold() for a in [canon, *aliases]):
            return canon
    return n
