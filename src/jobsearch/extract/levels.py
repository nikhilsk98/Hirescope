"""Company-specific and generic title level mapping (config/levels.yaml) onto one scale."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .. import paths

LEVELS = ("intern", "entry", "mid", "senior", "staff_plus", "manager", "unknown")


@dataclass(frozen=True)
class LevelRules:
    mid_max_yoe: float
    entry_max_yoe: float
    companies: dict[str, tuple[tuple[re.Pattern, str], ...]]   # casefolded company -> rules
    generic: tuple[tuple[re.Pattern, str], ...]


@lru_cache(maxsize=4)
def load_level_rules(path: Path = paths.CONFIG / "levels.yaml") -> LevelRules:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def comp(rules):
        return tuple((re.compile(r["pattern"]), r["level"]) for r in rules or [])
    return LevelRules(
        mid_max_yoe=float(raw.get("mid_max_yoe", 5)),
        entry_max_yoe=float(raw.get("entry_max_yoe", 1)),
        companies={k.casefold(): comp(v) for k, v in (raw.get("companies") or {}).items()},
        generic=comp(raw.get("generic")),
    )


@dataclass
class Level:
    level: str          # one of LEVELS
    evidence: str       # what decided it


def map_level(norm_title: str, company: str, yoe_required: float | None = None,
              rules: LevelRules | None = None) -> Level:
    rules = rules or load_level_rules()
    company_rules = rules.companies.get(company.casefold(), ())
    for source, rs in ((company, company_rules), ("generic", rules.generic)):
        for pat, lvl in rs:
            if m := pat.search(norm_title):
                where = f"{source} rule “{m.group(0)}”"
                if lvl == "mid_or_senior":
                    if yoe_required is None:
                        return Level("mid", f"{where}; no YOE stated, assumed mid")
                    lvl = "mid" if yoe_required <= rules.mid_max_yoe else "senior"
                    return Level(lvl, f"{where} with {yoe_required:g}+ YOE")
                return Level(lvl, where)
    if yoe_required is not None:  # no level in the title: fall back to stated years
        if yoe_required <= rules.entry_max_yoe:
            return Level("entry", f"no level in title; {yoe_required:g}+ YOE")
        if yoe_required <= rules.mid_max_yoe:
            return Level("mid", f"no level in title; {yoe_required:g}+ YOE")
        return Level("senior", f"no level in title; {yoe_required:g}+ YOE")
    return Level("unknown", "no level in title or YOE")
