"""Typed loading of preferences.yaml and config/*.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from . import paths

RuleMode = Literal["hard", "soft"]


class Salary(BaseModel):
    min: int | None = None
    currency: str = "USD"
    mode: RuleMode = "soft"


class WorkAuth(BaseModel):
    needs_sponsorship: bool | None = None
    us_citizen: bool | None = None
    # co-op/intern posting that states "no sponsorship": flag = keep it with "check CPT/OPT"; filter = hide it
    coop_no_sponsorship: Literal["flag", "filter"] = "flag"


class CompanyTiers(BaseModel):
    tier1: list[str] = []
    tier2: list[str] = []
    tier3: list[str] = []
    excluded: list[str] = []
    check_every_hours: dict[int, float] = {1: 0, 2: 20, 3: 70}   # 0 = every run
    max_pages: dict[int, int] = {1: 100, 2: 25, 3: 10}           # listing pages per company per run
    # companies known only from the SimplifyJobs internship / new-grad lists (curated for early-career hiring) score
    # as this tier instead of 3, so new-grad roles there aren't held back by an "unknown company" tier
    feed_tier: int = 2

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
    yoe: RuleMode = "hard"          # applies to yoe.filter_min_yoe+ ; the stretch band is always soft
    employment_type: RuleMode = "soft"
    posting_age: RuleMode = "soft"
    summer_only: RuleMode = "hard"  # co-op track: summer-only internships (tracks.coop_intern.filter_summer_only)
    grad_window: RuleMode = "hard"  # a stated graduation window that excludes your graduation date
    start_window: RuleMode = "soft" # a stated start date outside the track's window
    stale_posting: RuleMode = "hard"  # posted more than ghost.stale_days ago
    work_authorization: RuleMode = "hard"  # explicit no-sponsorship / citizens-only / clearance (see rules.py)


class Freshness(BaseModel):
    """Additive bonus (capped), so a fresh Weak job can never outrank a clearly stronger one by more than `cap`."""
    boost_24h: float = 8
    boost_72h: float = 4
    decay_days: float = 21          # after 72h the bonus decays linearly to 0 by this age
    cap: float = 10
    coop_multiplier: float = 1.5    # spring co-op hiring is happening now: extra urgency (still capped)


class Boosts(BaseModel):
    new_grad: float = 8             # New Grad / University Grad / Class of 2027 roles (top priority)
    term: float = 4                 # JD states a preferred term (Spring 2027 / Jan 2027; Class of 2027 / 2027 start)
    grad_match: float = 3           # stated graduation window includes yours
    cap: float = 12


class Ghost(BaseModel):
    age_days: int = 90              # posted longer ago than this -> "ghost risk"
    stale_days: int = 365           # posted longer ago than this -> filtered (rules.stale_posting)
    age_penalty: float = 5
    repost_penalty: float = 4       # reopened / reposted
    evergreen_penalty: float = 4    # "talent pool", "evergreen", "future opportunities"


class Prefilter(BaseModel):
    """Meaning-based pre-filter: rescue relevant jobs whose titles the keyword rules miss."""
    enabled: bool = True
    threshold: float = 0.52         # cosine similarity to the target-role profile
    margin: float = 0.05            # ...and at least this much closer than to any contrast profile


class SponsorshipHistory(BaseModel):
    """Optional: past H-1B approvals per company from a manually downloaded USCIS Employer Data Hub CSV.
    Company history only - never job-level eligibility."""
    enabled: bool = False
    file: str = "inputs/h1b/h1b_employer_data.csv"


class Feedback(BaseModel):
    """Learning from your dashboard statuses (shortlist / applied = positive, skip with a reason = negative).
    Bounded and transparent: every adjustment is listed in the dashboard with its evidence."""
    enabled: bool = True
    min_evidence: int = 2           # a company / skill / location / level needs this many labeled jobs
    max_per_feature: float = 3      # points, per feature group (company, skills, domain, location, level)
    max_total: float = 6            # points, per job (all learned adjustments together)
    weight_nudge_max: float = 0.25  # component weights move at most +/-25% of their configured value
    weight_min_jobs: int = 5        # ...and only with this many positive and negative jobs
    positive_weights: dict[str, float] = {"shortlisted": 1.0, "package exported": 1.0, "applied": 1.0,
                                          "interviewing": 1.0, "offer": 1.0, "interested": 0.5}
    # skip reason -> the feature groups it teaches (others teach nothing)
    reason_groups: dict[str, list[str]] = {"company": ["company"], "location": ["location"], "too senior": ["level"],
                                           "too junior": ["level"], "missing skills": ["missing_skills"],
                                           "not interested in role": ["skills", "domain"]}


class Aggregator(BaseModel):
    """Optional paid/free-tier job-search API (JSearch on RapidAPI, or SerpAPI Google Jobs). Off by default."""
    enabled: bool = False
    provider: str = "jsearch"       # jsearch | serpapi (key in .env: JSEARCH_API_KEY / SERPAPI_API_KEY)
    queries: list[str] = []         # your search queries (set in preferences.yaml)
    date_posted: str = "week"       # jsearch: today | 3days | week | month


class Backups(BaseModel):
    dir: str | None = None          # default data.nosync/backups; e.g. "~/Library/Mobile Documents/com~apple~CloudDocs/HireScope"
    keep: int = 14


class Schedule(BaseModel):
    full_times: list[str] = ["08:00", "18:00"]   # launchd full runs (local time)
    quick_hours: float = 2.5                     # quick checks (My Companies + tier 1) between full runs; 0 = off


class ManualChecks(BaseModel):
    remind_days: int = 7      # Manual Checks view + weekly digest: flag a company not checked in this many days


class Notifications(BaseModel):
    enabled: bool = True
    early_bird_hours: int = 48       # "early bird": Strong matches posted within this many hours
    digest_weekday: str = "Sunday"   # weekly digest (data.nosync/digests/) on the first run of this day
    season_remind_days: int = 21     # hiring seasons: remind this many days before a predicted window


class Applications(BaseModel):
    follow_up_days: tuple[int, int] = (7, 10)   # reminder window after applying (or after your last follow-up)
    ghosted_after_days: int = 30                 # suggest "Ghosted" (you confirm; never automatic)
    same_company_window_days: int = 30           # duplicate guard: other applications at the company this recent
    near_duplicate_ratio: int = 85               # duplicate guard: title similarity (0-100) that counts as the same role


class Scoring(BaseModel):
    """Component scores (0-100) and penalties used by match/score.py."""
    soft_rule_penalty: float = 5            # per failed soft rule
    neutral_qualification: float = 50       # qualification when there is no JD text to compare against
    yoe_penalty_per_year: float = 8         # per year above yoe.match_max
    yoe_penalty_cap: float = 30
    citizenship_penalty: float = 30         # only when work_authorization.us_citizen is not set
    clearance_penalty: float = 10           # only when work_authorization.us_citizen is not set
    title_fit: dict[str, float] = {"core": 100, "coop": 100, "related": 85, "semantic": 70}
    level_fit: dict[str, float] = {"intern": 100, "entry": 100, "mid": 100, "senior": 60, "other": 30}
    # <= years_experience / <= match_max / <= stretch_max / above
    yoe_fit: dict[str, float] = {"match": 100, "near": 90, "stretch": 60, "over": 20}
    domain_fit: dict[str, float] = {"match": 100}   # JD / title / company mentions one of your domains
    preferred_skill_weight: float = 0.5     # a preferred-only skill counts this much of a required one
    semantic_scale: list[float] = [0.15, 0.60]   # MiniLM cosine -> 0..100: at or below the first = 0, at the second = 100
    similar_min: float = 0.3                # "More like this": nearest roles at least this similar (cosine)
    grad_fit: dict[str, float] = {"match": 100, "mismatch": 25}
    degree_above_yours: float = 40          # JD asks for a higher degree than your resume shows
    location_fit: dict[str, float] = {"preferred": 100, "remote_us": 90, "other_us": 75, "no_preferences": 100}
    tier_fit: dict[int, float] = {1: 100, 2: 75, 3: 50}


class ApplyToday(BaseModel):
    """The per-track "Apply Today" queue: Strong/Stretch jobs ranked by score + these urgency points."""
    size: int = 5                   # shown per track
    fresh_24h: float = 10
    fresh_72h: float = 5
    closing_soon: float = 6         # deadline within closing_soon_days
    strong: float = 5
    closing_soon_days: int = 7      # also the "Closing soon" badge / view


class Health(BaseModel):
    """Source health alerts (ops/health.py)."""
    drop: float = 0.70              # alert when a source lists this much less than its recent average
    min_history: int = 2            # successful past runs needed before judging
    min_avg_zero: float = 3         # "returned 0" alerts only for sources that usually list at least this many
    min_avg_drop: float = 10        # drop alerts only for sources this big

class Weights(BaseModel):
    qualification: float = 0.65
    preference: float = 0.35
    qualification_parts: dict[str, float] = {"skills": 0.4, "semantic": 0.4, "level": 0.2}
    preference_parts: dict[str, float] = {
        "location": 0.35, "company_tier": 0.35, "employment_type": 0.15, "salary": 0.15, "domain": 0.15,
    }   # freshness is a separate capped bonus (Freshness); domain only counts when it matches


class Thresholds(BaseModel):
    strong: float = 70
    stretch: float = 50


class CoopTrack(BaseModel):
    """Your dates live in preferences.yaml; unset = no start-window rule."""
    enabled: bool = True
    start_window: tuple[str, str] | None = None   # YYYY-MM inclusive
    boost_terms: list[str] = []
    filter_summer_only: bool = True


class FullTimeTrack(BaseModel):
    enabled: bool = True
    start_from: str | None = None                 # YYYY-MM; unset = no start-date rule
    boost_terms: list[str] = []


class Tracks(BaseModel):
    coop_intern: CoopTrack = CoopTrack()
    full_time: FullTimeTrack = FullTimeTrack()


class LevelPrefs(BaseModel):
    include: list[str] = ["intern", "entry", "mid", "unknown"]
    stretch: list[str] = ["senior"]            # kept, label capped at Stretch
    exclude: list[str] = ["staff_plus", "manager"]


class YoePrefs(BaseModel):
    """Unset values come from years_experience (your years +1 / +2 / +3), else 5 / 6 / 7."""
    match_max: float | None = None      # 0..match_max years -> full match
    stretch_max: float | None = None    # (match_max, stretch_max] -> Stretch
    filter_min: float | None = None     # >= filter_min -> filtered (rules.yoe: hard)


class Preferences(BaseModel):
    target_titles: list[str] = []   # added to title_rules.yaml core_titles
    related_titles: list[str] = []  # added to title_rules.yaml related_titles
    excluded_title_words: list[str] = ["staff", "principal", "distinguished", "director", "manager",
                                       "engineering manager", "architect", "head of"]
    levels: LevelPrefs = LevelPrefs()
    yoe: YoePrefs = YoePrefs()
    years_experience: float | None = None    # your own: sets the default YOE window and grades fit inside it
    graduation_date: str | None = None
    tracks: Tracks = Tracks()
    country: str = "US"
    preferred_locations: list[str] = []       # soft boost; any US location / Remote-US still matches
    work_modes: list[str] = ["remote", "hybrid", "onsite"]
    domains: list[str] = []
    salary: Salary = Salary()
    work_authorization: WorkAuth = WorkAuth()
    company_tiers: CompanyTiers = CompanyTiers()
    max_posting_age_days: int | None = 60
    grad_window_slack_months: int = 2          # a stated graduation window still matches this many months either side
    excluded_employment_types: list[str] = ["contract", "part_time", "temporary"]   # rules.employment_type
    rules: Rules = Rules()
    weights: Weights = Weights()
    thresholds: Thresholds = Thresholds()
    freshness: Freshness = Freshness()
    boosts: Boosts = Boosts()
    ghost: Ghost = Ghost()
    prefilter: Prefilter = Prefilter()
    sponsorship_history: SponsorshipHistory = SponsorshipHistory()
    feedback: Feedback = Feedback()
    applications: Applications = Applications()
    aggregator: Aggregator = Aggregator()
    backups: Backups = Backups()
    schedule: Schedule = Schedule()
    notifications: Notifications = Notifications()
    manual_checks: ManualChecks = ManualChecks()
    scoring: Scoring = Scoring()
    apply_today: ApplyToday = ApplyToday()
    health: Health = Health()
    connections_csv: str = "inputs/linkedin/Connections.csv"   # LinkedIn data export (optional): referral finder
    top_n: int = 1000                         # per track
    timezone: str = "America/New_York"
    my_companies_csv: str = "inputs/companies/my_companies.csv"
    # where "Export statuses" files are picked up (newest wins). ~/Downloads is macOS-protected (a scheduled run may
    # be denied it; the Run Log then says so); inputs/statuses/ in the repo always works.
    statuses_import_dir: str | list[str] = ["~/Downloads", "inputs/statuses"]

    @model_validator(mode="after")
    def _yoe_window(self):
        y, base = self.yoe, self.years_experience
        if y.match_max is None:
            y.match_max = base + 1 if base is not None else 5
        if y.stretch_max is None:
            y.stretch_max = max(y.match_max, base + 2 if base is not None else 6)
        if y.filter_min is None:
            y.filter_min = max(y.stretch_max + 1, base + 3 if base is not None else 7)
        return self

    @field_validator("graduation_date", mode="before")
    @classmethod
    def _date_to_str(cls, v):
        # YAML may parse 2027-05 as a string but 2027-05-01 as a date.
        return None if v is None else str(v)


class Company(BaseModel):
    name: str
    source: str                       # adapter: greenhouse | lever | ashby | workday | smartrecruiters | ...
    board: str | None = None          # ATS board token / company identifier / feed URL
    host: str | None = None           # workday: tenant host, e.g. nvidia.wd5.myworkdayjobs.com
    site: str | None = None           # workday: career site, e.g. NVIDIAExternalCareerSite
    tier: int = 3
    careers_url: str | None = None
    verified: str | None = None       # date the endpoint was verified live
    origin: str = "seed"              # seed | my_companies | feed
    notes: str | None = None
    queries: list[str] = []           # aggregator: search queries


class TitleRules(BaseModel):
    abbreviations: dict[str, str] = {}
    core_titles: list[str] = []
    related_titles: list[str] = []
    coop_role_words: list[str] = []
    fuzzy_threshold: int = 90
    employment_type_words: dict[str, list[str]] = {}
    new_grad_patterns: list[str] = []      # regexes: a title that is a New Grad / early-career role
    non_level_phrases: list[str] = []      # phrases with level-like words that are not levels
    role_profile: list[str] = []           # meaning-based pre-filter: what you are looking for
    contrast_profiles: list[str] = []      # ...and technical roles you are not
    tech_hint_words: list[str] = []        # a filtered title needs one of these to be rescued by meaning
    posting_role_words: list[str] = []     # free-text posts (HN): words that make a header segment a role
    search_queries: dict[str, list] = {}   # role keywords searched on query-based sources (workday, ...)


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
