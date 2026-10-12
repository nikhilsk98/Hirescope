"""Normalize location strings and decide whether a posting is in the US (config/locations.yaml)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from .. import paths


@dataclass(frozen=True)
class LocationConfig:
    aliases: dict[str, str]
    remote_us: tuple[str, ...]
    us_names: tuple[str, ...]
    states: dict[str, str]          # abbr -> name
    us_cities: tuple[str, ...]
    non_us: tuple[str, ...]


@lru_cache(maxsize=4)
def load_location_config(path: Path = paths.CONFIG / "locations.yaml") -> LocationConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return LocationConfig(
        aliases={k.lower(): v for k, v in (raw.get("aliases") or {}).items()},
        remote_us=tuple(s.lower() for s in raw.get("remote_us") or []),
        us_names=tuple(s.lower() for s in raw.get("us_names") or []),
        states={k.upper(): v for k, v in (raw.get("us_states") or {}).items()},
        us_cities=tuple(s.lower() for s in raw.get("us_cities") or []),
        non_us=tuple(s.lower() for s in raw.get("non_us") or []),
    )


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", text) is not None


@dataclass
class Place:
    text: str
    country: str | None     # "US" | "non-US" | None (unknown)
    remote: bool


@dataclass
class LocationInfo:
    places: list[Place] = field(default_factory=list)

    @property
    def us(self) -> bool | None:
        """True if any place is in the US, False if every place is known non-US, None if unknown."""
        if any(p.country == "US" for p in self.places):
            return True
        if self.places and all(p.country == "non-US" for p in self.places):
            return False
        return None

    @property
    def remote(self) -> bool:
        return any(p.remote for p in self.places)

    @property
    def remote_us(self) -> bool:
        return any(p.remote and p.country == "US" for p in self.places)

    @property
    def display(self) -> str:
        return " · ".join(dict.fromkeys(p.text for p in self.places)) or "Unknown"

    def us_places(self) -> list[str]:
        return [p.text for p in self.places if p.country == "US"]


def _classify(part: str, cfg: LocationConfig) -> Place:
    raw = part.strip(" -–,()")
    low = re.sub(r"\s+", " ", raw.lower())
    if low in cfg.aliases:
        raw, low = cfg.aliases[low], cfg.aliases[low].lower()
    remote = "remote" in low or "telecommute" in low or "work from home" in low or "anywhere" in low
    if any(_has(low, p) for p in cfg.remote_us):
        return Place("Remote (US)", "US", True)

    # "US, CA, Santa Clara" / "USA - CA - Santa Clara" / "US-WA-Seattle" (Amazon, Workday)
    toks = [t.strip() for t in re.split(r"\s*[,\-–]\s*", raw) if t.strip()]
    if toks and toks[0].lower() in cfg.us_names and len(toks) >= 2:
        if len(toks) >= 3 and toks[1].upper() in cfg.states:
            return Place(f"{toks[2]}, {toks[1].upper()}", "US", remote)
        if toks[1].lower() == "remote":
            return Place("Remote (US)", "US", True)
        return Place(raw, "US", remote)

    # ", CA" / ", NY 10001" state abbreviation (case-sensitive so "on" or "in" never count)
    if m := re.search(r",\s*([A-Z]{2})(?:\s+\d{5})?\s*(?:,\s*(?:US|USA|United States)\s*)?$", raw):
        if m.group(1) in cfg.states:
            return Place(raw, "US", remote)
    for name in cfg.non_us:  # before US cities: "London" is London, UK unless a state was given
        if _has(low, name):
            return Place(raw, "non-US", remote)
    for name in cfg.states.values():
        if _has(low, name.lower()):
            return Place(raw, "US", remote)
    if any(_has(low, n) for n in cfg.us_names):
        return Place("Remote (US)" if remote else raw, "US", remote)
    if any(_has(low, c) for c in cfg.us_cities):
        return Place(raw, "US", remote)
    return Place(raw if raw else "Unknown", None, remote)


def normalize_location(text: str | None, cfg: LocationConfig | None = None) -> LocationInfo:
    """Split on · | ; • and newlines, classify each part. Duplicate parts are merged."""
    cfg = cfg or load_location_config()
    if not text or not text.strip():
        return LocationInfo()
    parts = [p for p in re.split(r"\s*[·|;•\n]\s*|\s+/\s+", text) if p.strip(" -–,()")]
    return LocationInfo([_classify(p, cfg) for p in parts])
