"""Typed loading of preferences.yaml and config/*.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from . import paths

RuleMode = Literal["hard", "soft"]


class Salary(BaseModel):
    min: int | None = None
    currency: str = "USD"
    mode: RuleMode = "soft"


class WorkAuth(BaseModel):
    needs_sponsorship: bool | None = None
    us_citizen: bool | None = None


class CompanyTiers(BaseModel):
    tier1: list[str] = []
    tier2: list[str] = []
    tier3: list[str] = []
    excluded: list[str] = []

    def tier_of(self, company: str) -> int | None:
        key = company.casefold()
        for n, names in ((1, self.tier1), (2, self.tier2), (3, self.tier3)):
            if key in (x.casefold() for x in names):
                return n
        return None

    def is_excluded(self, company: str) -> bool:
        return company.casefold() in (x.casefold() for x in self.excluded)


class Rules(BaseModel):
    title: RuleMode = "hard"
    seniority: RuleMode = "hard"
    location: RuleMode = "hard"
    yoe: RuleMode = "soft"
    employment_type: RuleMode = "soft"
    posting_age: RuleMode = "soft"


class Weights(BaseModel):
    qualification: float = 0.65
    preference: float = 0.35
    qualification_parts: dict[str, float] = {"skills": 0.4, "semantic": 0.4, "level": 0.2}
    preference_parts: dict[str, float] = {
        "location": 0.35, "company_tier": 0.25, "freshness": 0.2,
        "employment_type": 0.1, "salary": 0.1,
    }


class Thresholds(BaseModel):
    strong: float = 70
    stretch: float = 50


class Preferences(BaseModel):
    target_titles: list[str]
    related_titles: list[str] = []
    excluded_title_words: list[str] = []
    experience_level: str = "entry"
    max_yoe: float | None = None
    employment_types: list[str] = []
    graduation_date: str | None = None
    start_date: str | None = None
    locations: list[str] = []
    work_modes: list[str] = ["remote", "hybrid", "onsite"]
    remote_countries: list[str] = ["United States", "US", "USA"]
    domains: list[str] = []
    salary: Salary = Salary()
    work_authorization: WorkAuth = WorkAuth()
    company_tiers: CompanyTiers = CompanyTiers()
    max_posting_age_days: int | None = None
    rules: Rules = Rules()
    weights: Weights = Weights()
    thresholds: Thresholds = Thresholds()
    top_n: int = 200
    timezone: str = "America/New_York"

    @field_validator("graduation_date", "start_date", mode="before")
    @classmethod
    def _date_to_str(cls, v):
        # YAML may parse 2027-05 as a string but 2027-05-01 as a date.
        return None if v is None else str(v)


class Company(BaseModel):
    name: str
    source: str
    board: str | None = None
    tier: int = 3
    careers_url: str | None = None


class TitleRules(BaseModel):
    abbreviations: dict[str, str] = {}
    excluded_level_numerals: list[str] = []
    fuzzy_threshold: int = 85
    employment_type_words: dict[str, list[str]] = {}


def _yaml(path: Path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_preferences(path: Path = paths.PREFERENCES) -> Preferences:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy inputs/preferences.example.yaml to {path.name} and fill it in."
        )
    return Preferences.model_validate(_yaml(path))


def load_companies(path: Path = paths.COMPANIES) -> list[Company]:
    return [Company.model_validate(c) for c in _yaml(path).get("companies", [])]


def load_title_rules(path: Path = paths.TITLE_RULES) -> TitleRules:
    return TitleRules.model_validate(_yaml(path))


def load_synonyms(path: Path = paths.SYNONYMS) -> dict[str, list[str]]:
    raw = _yaml(path)
    return {str(k): [str(a) for a in (v or [])] for k, v in raw.items()}


class Config(BaseModel):
    """Everything the pipeline needs, loaded once per run."""
    prefs: Preferences
    companies: list[Company] = Field(default_factory=list)
    title_rules: TitleRules = TitleRules()
    synonyms: dict[str, list[str]] = {}


def load_config(prefs_path: Path = paths.PREFERENCES) -> Config:
    return Config(
        prefs=load_preferences(prefs_path),
        companies=load_companies(),
        title_rules=load_title_rules(),
        synonyms=load_synonyms(),
    )
