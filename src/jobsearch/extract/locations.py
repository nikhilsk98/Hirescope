"""Normalize location strings and decide whether a posting is in the US (config/locations.yaml)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from .. import paths


@dataclass(frozen=True, eq=False)   # hashed by identity (the loaded config is cached)
class LocationConfig:
    aliases: dict[str, str]
    remote_us: tuple[str, ...]
    us_names: tuple[str, ...]
    states: dict[str, str]          # abbr -> name
    us_cities: tuple[str, ...]
    non_us: tuple[str, ...]
    metros: dict[str, tuple[str, ...]]


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
        metros={k.lower(): tuple(c.lower() for c in v or []) for k, v in (raw.get("metros") or {}).items()},
    )


def _alt(phrases) -> re.Pattern:
    """One compiled alternation with letter boundaries (longest phrases first)."""
    body = "|".join(re.escape(p) for p in sorted(set(phrases), key=len, reverse=True)) or "(?!)"
    return re.compile(rf"(?<![a-z])(?:{body})(?![a-z])")


@lru_cache(maxsize=4)
def _patterns(cfg: "LocationConfig") -> dict[str, re.Pattern]:
    return {"remote_us": _alt(cfg.remote_us), "non_us": _alt(cfg.non_us),
            "states": _alt(n.lower() for n in cfg.states.values()), "us_names": _alt(cfg.us_names),
            "us_cities": _alt(cfg.us_cities)}


@dataclass(frozen=True)
class Place:
    text: str
    country: str | None     # "US" | "non-US" | None (unknown)
    remote: bool


@dataclass
class LocationInfo:
    places: list[Place] = field(default_factory=list)

    @property
    def us(self) -> bool | None:
        """True if any place is in the US; False if none is and at least one is known non-US
        ("Warsaw - Poland · Digital"); None if nothing is known."""
        if any(p.country == "US" for p in self.places):
            return True
        if any(p.country == "non-US" for p in self.places):
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


def _classify(part: str, cfg: LocationConfig) -> Place:
    raw = part.strip(" -–,()")
    low = re.sub(r"\s+", " ", raw.lower())
    if low in cfg.aliases:
        raw, low = cfg.aliases[low], cfg.aliases[low].lower()
    pat = _patterns(cfg)
    remote = "remote" in low or "telecommute" in low or "work from home" in low or "anywhere" in low
    if pat["remote_us"].search(low):
        return Place("Remote (US)", "US", True)

    # "CA-QC-Longueuil" / "CAN-ON-Toronto": Canada (country code + province), not California; "IN-KA-Bengaluru":
    # India + state code, not Indiana
    if re.match(r"^(CA|CAN|Canada)\s*[-–,]\s*(QC|ON|BC|AB|MB|NS|NB|NL|PE|SK|YT|NT|NU)\b", raw, re.I) or \
            re.match(r"^(IN|IND|India)\s*[-–,]\s*(KA|TS|TG|MH|TN|DL|HR|UP|GJ|AP|WB|KL|RJ|MP|PB|OR|OD|CH)\b", raw, re.I):
        return Place(raw, "non-US", remote)
    # a full state name after a comma: "Vancouver, Washington", "Oxford, Ohio", "Athens, Georgia" (not the
    # country's cities: "Tbilisi, Georgia")
    if m := re.search(r",\s*(" + "|".join(sorted((re.escape(n) for n in cfg.states.values()), key=len, reverse=True))
                      + r")\b(?:\s+\d{5})?\s*$", raw, re.I):
        if m.group(1).lower() == "georgia" and re.search(r"tbilisi|batumi|kutaisi|rustavi", low):
            return Place(raw, "non-US", remote)
        return Place(raw, "US", remote)
    # "US, CA, Santa Clara" / "USA - CA - Santa Clara" / "US-WA-Seattle" (Amazon, Workday)
    toks = [t.strip() for t in re.split(r"\s*[,\-–]\s*", raw) if t.strip()]
    if toks and toks[0].lower() in cfg.us_names and len(toks) >= 2:
        if len(toks) >= 3 and toks[1].upper() in cfg.states:
            return Place(f"{toks[2]}, {toks[1].upper()}", "US", remote)
        if toks[1].lower() == "remote":
            return Place("Remote (US)", "US", True)
        return Place(raw, "US", remote)
    # an explicit US country at the end wins over town names shared with other countries ("North Reading,
    # Massachusetts, USA", "New Berlin, Wisconsin, United States of America", "Ontario, CA, US")
    if re.search(r"(?:,|\s[-–])\s*(?:US|USA|U\.S\.A?\.?|United States(?: of America)?)\s*$", raw, re.I) or \
            re.match(r"^(?:United States|USA)\b", raw, re.I):
        return Place("Remote (US)" if remote else raw, "US", remote)

    # ", CA" / ", NY 10001" / "...Meade MD" state abbreviation at the end (case-sensitive: "on"/"in" never count)
    if m := re.search(r"(?:,|\s)\s*([A-Z]{2})(?:\s+\d{5})?\s*(?:,\s*(?:US|USA|United States)\s*)?$", raw):
        # "Toronto, CA" is Canada's country code after a known non-US city, not California
        if m.group(1) == "CA" and pat["non_us"].search(low[:m.start()]):
            return Place(raw, "non-US", remote)
        if m.group(1) in cfg.states:
            return Place(raw, "US", remote)
    # "CT - Hartford" / "GA - Alpharetta" (state code first; "DE-Berlin" is Germany)
    if (m := re.match(r"^([A-Z]{2})\s*[-–]\s*[A-Za-z]", raw)) and m.group(1) in cfg.states \
            and not pat["non_us"].search(low[m.end() - 1:]):
        return Place(raw, "US", remote)
    if pat["non_us"].search(low.replace("new mexico", "")):  # before US cities: "London" is London, UK unless a state was given
        return Place(raw, "non-US", remote)
    if pat["states"].search(low):
        return Place(raw, "US", remote)
    if pat["us_names"].search(low):
        return Place("Remote (US)" if remote else raw, "US", remote)
    if pat["us_cities"].search(low):
        return Place(raw, "US", remote)
    return Place(raw if raw else "Unknown", None, remote)


@lru_cache(maxsize=20000)
def _normalize_cached(text: str, cfg: LocationConfig) -> tuple[Place, ...]:
    parts = [p for p in re.split(r"\s*[·|;•\n]\s*|\s+/\s+", text) if p.strip(" -–,()")]
    return tuple(_classify(p, cfg) for p in parts)


def normalize_location(text: str | None, cfg: LocationConfig | None = None) -> LocationInfo:
    """Split on · | ; • and newlines, classify each part. Duplicate parts are merged."""
    cfg = cfg or load_location_config()
    if not text or not text.strip():
        return LocationInfo()
    return LocationInfo(list(_normalize_cached(text, cfg)))


@lru_cache(maxsize=64)
def _metro_re(names: tuple[str, ...]) -> re.Pattern:
    body = "|".join(re.escape(n) for n in sorted(set(names), key=len, reverse=True)) or "(?!)"
    return re.compile(rf"(?<![a-z])(?:{body})(?![a-z])", re.I)


def preferred_match(location: str | None, preferred: list[str], cfg: LocationConfig | None = None) -> str | None:
    """The preferred location (metro name or plain place) a job's US location falls in, or None.
    Metro names expand to their cities (config/locations.yaml -> metros); other entries match as place names."""
    if not location or not preferred:
        return None
    cfg = cfg or load_location_config()
    us_parts = " · ".join(p.text for p in normalize_location(location, cfg).places if p.country == "US") or location
    for pref in preferred:
        names = cfg.metros.get(pref.lower(), (pref.lower(),))
        if _metro_re(tuple(names)).search(us_parts):
            return pref
    return None
