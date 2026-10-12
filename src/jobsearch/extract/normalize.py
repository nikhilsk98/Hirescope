"""Normalize titles, locations, work mode, employment type and salary."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ..config import TitleRules

def _words(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9+#./\- ]", " ", text)
    text = re.sub(r"[/\-,]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=8)
def _abbr_pattern(items: tuple[tuple[str, str], ...]) -> tuple[re.Pattern, dict[str, str]] | None:
    if not items:
        return None
    mapping = {a.lower(): f.lower() for a, f in items}
    body = "|".join(re.escape(a) for a in sorted(mapping, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])"), mapping


def normalize_title(title: str, rules: TitleRules) -> str:
    """Lowercase, strip punctuation, expand abbreviations ("Sr. SWE" -> "senior software engineer")."""
    return _normalize_title(title, tuple(rules.abbreviations.items()))


@lru_cache(maxsize=50000)
def _normalize_title(title: str, abbreviations: tuple[tuple[str, str], ...]) -> str:
    t = _words(title.replace(".", " "))
    if pm := _abbr_pattern(abbreviations):
        pat, mapping = pm
        t = pat.sub(lambda m: mapping[m.group(0)], t)
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=4096)
def _word_re(word: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(word.lower())}(?![a-z0-9])")


def has_word(text: str, word: str) -> bool:
    return _word_re(word).search(text) is not None


def employment_type_from_title(norm_title: str, rules: TitleRules) -> str | None:
    for etype, words in rules.employment_type_words.items():
        if any(has_word(norm_title, w) for w in words):
            return etype
    return None


def clean_location(location: str | None) -> str | None:
    """'SF, CA | NYC; SF, CA | Seattle' -> 'SF, CA · NYC · Seattle' (split on | ; •, dedupe, keep order)."""
    if not location:
        return None
    parts = [p.strip() for p in re.split(r"[|;•]", location)]
    out = list(dict.fromkeys(p for p in parts if p))
    return " · ".join(out) or None


def work_mode(location: str | None, description: str = "") -> str:
    """remote / hybrid / onsite / unknown. Location text wins; the JD is only checked for 'hybrid'/'remote' phrases."""
    loc = (location or "").lower()
    if "hybrid" in loc:
        return "hybrid"
    if "remote" in loc:
        return "remote"
    d = description.lower()
    if re.search(r"\bhybrid\b(?! cloud| search| model| approach| architecture)", d):
        return "hybrid"
    if re.search(r"\b(fully remote|remote[- ]first|this (role|position) is remote|remote within)\b", d):
        return "remote"
    if loc:
        return "onsite"
    return "unknown"


@dataclass
class SalaryRange:
    min: int | None
    max: int | None
    currency: str | None


_CURRENCY = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
             "cad": "CAD", "c$": "CAD"}
_AMOUNT = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kK])?"
_SALARY_RE = re.compile(
    rf"(?P<cur>c\$|\$|€|£)\s*{_AMOUNT}\s*(?:-|–|—|to)\s*(?:c\$|\$|€|£)?\s*{_AMOUNT}\s*(?P<code>USD|CAD|EUR|GBP)?",
    re.IGNORECASE,
)


def _amount(num: str, k: str | None) -> float:
    v = float(num.replace(",", ""))
    return v * 1000 if k else v


def parse_salary(text: str) -> SalaryRange:
    """Find an annual salary range like '$120,000 - $150,000 USD' or '$120K–$150K'.

    Ranges below 1,000 are treated as hourly and annualized at 2,080 h. Returns Nones if absent.
    """
    for m in _SALARY_RE.finditer(text or ""):
        lo = _amount(m.group(2), m.group(3))
        hi = _amount(m.group(4), m.group(5))
        if hi < lo:
            continue
        if hi < 1000:  # hourly
            lo, hi = lo * 2080, hi * 2080
        if lo < 15000:  # not a salary (e.g. "$5 - $10 million" funding text without k)
            continue
        code = (m.group("code") or "").lower()
        cur = _CURRENCY.get(code) or _CURRENCY.get(m.group("cur").lower(), None)
        return SalaryRange(int(lo), int(hi), cur)
    return SalaryRange(None, None, None)
