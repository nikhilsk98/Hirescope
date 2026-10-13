"""Paths and settings for the Resume Builder (config/resume_builder.yaml)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from jobsearch import paths as hs_paths

ROOT = hs_paths.ROOT
INPUTS = hs_paths.RESUME_INPUTS          # inputs/resume/
MASTER_RESUME = INPUTS / "master_resume.md"
TEMPLATE = INPUTS / "resume_template.tex"
RULES = INPUTS / "resume_rules.md"
ADDITIONS = INPUTS / "evidence_additions.md"
CONFIG_FILE = hs_paths.CONFIG / "resume_builder.yaml"
SKILL_DIR = ROOT / ".claude" / "skills" / "resume-builder"


def resumes_dir() -> Path:
    """data.nosync/generated_resumes (gitignored, outside iCloud sync). Read at call time so tests and
    JOBSEARCH_DATA_DIR can redirect it."""
    return hs_paths.DATA / "generated_resumes"


class InputError(Exception):
    """A missing or malformed input the user has to fix (message says exactly what)."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Profile:
    name: str
    signals: list[str]
    lead_with: list[str]


@dataclass
class Settings:
    first_name: str
    last_name: str
    page_count: int
    section_order: dict[str, list[str]]
    summary_enabled: bool
    summary_allow_when_strong_fit: bool
    summary_max_words: int
    limits: dict
    layout: dict
    fonts: dict
    compile: dict
    repair_max_attempts: int
    profiles: dict[str, Profile]
    default_profile: str
    batch_max_jobs: int
    related_groups: dict[str, list[str]] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    path: Path | None = None

    def hash(self) -> str:
        return sha256_text(yaml.safe_dump(self.raw, sort_keys=True))

    def order_for(self, track: str | None) -> list[str]:
        return list(self.section_order.get(track or "full_time") or self.section_order["full_time"])


SECTION_KEYS = {"skills", "education", "experience", "projects"}


def load_settings(path: Path | None = None) -> Settings:
    path = path or CONFIG_FILE
    if not path.exists():
        raise InputError(f"{path} is missing (Resume Builder settings).")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise InputError(f"{path} is not valid YAML: {e}") from e
    try:
        naming = raw.get("naming") or {}
        order = raw["section_order"]
        for track, secs in order.items():
            bad = [s for s in secs if s not in SECTION_KEYS]
            if bad or sorted(secs) != sorted(SECTION_KEYS):
                raise InputError(f"{path}: section_order.{track} must list each of {sorted(SECTION_KEYS)} once "
                                 f"(got {secs})")
        profiles = {k: Profile(k, [str(s).lower() for s in v.get("signals") or []], list(v.get("lead_with") or []))
                    for k, v in (raw.get("profiles") or {}).items()}
        default = raw.get("default_profile") or next(iter(profiles), "fullstack")
        if profiles and default not in profiles:
            raise InputError(f"{path}: default_profile '{default}' is not one of {sorted(profiles)}")
        first, last = str(naming.get("first_name") or "").strip(), str(naming.get("last_name") or "").strip()
        if not (first and last):
            first, last = name_from_master(first, last)
        for label, v in (("first_name", first), ("last_name", last)):
            if not re.fullmatch(r"[A-Za-z][A-Za-z'-]{0,40}", v):
                raise InputError(f"{path}: naming.{label} must be letters only (got '{v}')")
        s = Settings(
            first_name=first, last_name=last,
            page_count=int(raw.get("page_count", 1)),
            section_order={k: list(v) for k, v in order.items()},
            summary_enabled=bool((raw.get("summary") or {}).get("enabled", False)),
            summary_allow_when_strong_fit=bool((raw.get("summary") or {}).get("allow_when_strong_fit", True)),
            summary_max_words=int((raw.get("summary") or {}).get("max_words", 45)),
            limits=raw.get("limits") or {}, layout=raw.get("layout") or {}, fonts=raw.get("fonts") or {},
            compile=raw.get("compile") or {},
            repair_max_attempts=int((raw.get("repair") or {}).get("max_attempts", 3)),
            profiles=profiles, default_profile=default,
            batch_max_jobs=int((raw.get("batch") or {}).get("max_jobs", 15)),
            related_groups={str(k): [str(x) for x in v or []] for k, v in (raw.get("related_groups") or {}).items()},
            raw=raw, path=path)
    except KeyError as e:
        raise InputError(f"{path}: missing setting {e}") from e
    if s.page_count < 1 or s.page_count > 3:
        raise InputError(f"{path}: page_count must be 1-3")
    if s.repair_max_attempts > 3:
        raise InputError(f"{path}: repair.max_attempts is capped at 3 (usage limit)")
    return s


def name_from_master(first: str = "", last: str = "") -> tuple[str, str]:
    """First and last name from the '# First Last' heading of master_resume.md (for the PDF file name)."""
    if MASTER_RESUME.exists():
        for line in MASTER_RESUME.read_text(encoding="utf-8").splitlines():
            if m := re.match(r"^#\s+(\S.*)$", line):
                parts = m.group(1).split()
                if len(parts) >= 2:
                    return first or parts[0], last or parts[-1]
                break
    raise InputError("can't tell your name for the PDF file name: put '# First Last' as the first line of "
                     "inputs/resume/master_resume.md, or set naming.first_name / last_name in config/resume_builder.yaml")


def limit(settings: Settings, name: str, key: str, default: int) -> int:
    v = settings.limits.get(name)
    if isinstance(v, dict):
        return int(v.get(key, default))
    return int(v) if v is not None and key == "max" else default
