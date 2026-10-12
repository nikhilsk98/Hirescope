"""Meaning-based pre-filter: rescue jobs the keyword title rules missed (unusual titles like "Member of
Engineering Staff, Product" or "Forward Deployed Engineer") by comparing the title + opening of the JD with a
target-role profile using the same local embeddings as semantic matching. Only jobs hidden *solely* by the
title rule are considered, and only if the title hints at technical work; everything else stays filtered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from ..config import TitleRules, load_title_rules
from .semantic import Embedder

# The target-role profile, contrast profiles and tech-hint words live in config/title_rules.yaml
# (role_profile, contrast_profiles, tech_hint_words). Resume summary lines are added to the profile at runtime.


@lru_cache(maxsize=1)
def _default_rules() -> TitleRules:
    return load_title_rules()


def _rules(rules: TitleRules | None) -> TitleRules:
    return rules or _default_rules()


@lru_cache(maxsize=8)
def _tech_hint(words: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.I) if words else re.compile(r"(?!)")


@dataclass
class Rescue:
    similarity: float
    profile: str


def candidate(job: dict, reasons: list[str], rules: TitleRules | None = None) -> bool:
    """Hidden only because the title didn't match, and the title looks technical (tech_hint_words)."""
    return bool(reasons) and all(r.startswith("Title does not match") for r in reasons) \
        and bool(_tech_hint(tuple(_rules(rules).tech_hint_words)).search(job.get("title") or ""))


def job_text(job: dict, jd: str) -> str:
    reqs = " ".join((job.get("min_reqs") or [])[:3])
    body = reqs or " ".join(ln.strip("- ") for ln in jd.splitlines() if len(ln) > 40)[:400]
    return f"{job.get('title', '')}. {body}"[:600]


def rescue(embedder: Embedder, items: list[tuple[int, str]], threshold: float, margin: float = 0.04,
           extra_profile: list[str] | None = None, rules: TitleRules | None = None) -> dict[int, Rescue]:
    """items: (job pk, text). Rescued when similarity to the profile is >= threshold AND beats every contrast
    profile by >= margin. Returns pk -> Rescue."""
    if not items:
        return {}
    r = _rules(rules)
    profile = [*r.role_profile, *(extra_profile or [])]
    if not profile or not r.contrast_profiles:
        return {}
    p = embedder.embed(profile)
    n = embedder.embed(r.contrast_profiles)
    j = embedder.embed([t for _, t in items])
    pos, neg = j @ p.T, (j @ n.T).max(axis=1)
    best = pos.argmax(axis=1)
    out = {}
    for (pk, _), i, row, ng in zip(items, best, pos, neg):
        s = float(row[i])
        if s >= threshold and s - float(ng) >= margin:
            out[pk] = Rescue(round(s, 3), profile[int(i)])
    return out


def similar_jobs(embedder: Embedder, jobs: list[tuple[int, str, str]], k: int = 5) -> dict[int, list[int]]:
    """jobs: (pk, track, text). Top-k most similar open jobs in the same track (cosine on local embeddings)."""
    out: dict[int, list[int]] = {}
    by_track: dict[str, list[tuple[int, str]]] = {}
    for pk, track, text in jobs:
        by_track.setdefault(track, []).append((pk, text))
    for items in by_track.values():
        if len(items) < 2:
            continue
        v = embedder.embed([t for _, t in items])
        sims = v @ v.T
        np.fill_diagonal(sims, -1)
        top = np.argsort(-sims, axis=1)[:, :k]
        for (pk, _), row, idx in zip(items, sims, top):
            out[pk] = [items[int(i)][0] for i in idx if row[int(i)] > 0.3]
    return out
