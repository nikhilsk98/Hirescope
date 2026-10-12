"""Meaning-based pre-filter: rescue jobs the keyword title rules missed (unusual titles like "Member of
Engineering Staff, Product" or "Forward Deployed Engineer") by comparing the title + opening of the JD with a
target-role profile using the same local embeddings as semantic matching. Only jobs hidden *solely* by the
title rule are considered, and only if the title hints at technical work; everything else stays filtered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .semantic import Embedder

# The target-role profile: what both tracks are looking for. Resume summary lines are added at runtime.
PROFILE = [
    "Software engineer building backend services, APIs and distributed systems",
    "Full-stack web developer building React and Node.js applications",
    "Frontend engineer building web user interfaces in JavaScript and TypeScript",
    "Software engineering intern or co-op student writing production code",
    "Cloud and platform engineer building infrastructure, CI/CD and developer tooling",
    "Software developer writing, testing and deploying application code",
    "New graduate software engineer joining an engineering team",
]
# Contrast profiles: technical roles that are NOT this search. A rescue must be closer to PROFILE than to these.
NOT_PROFILE = [
    "Electrical or hardware engineer designing circuits, boards and power electronics",
    "Mechanical, manufacturing, process or industrial engineer working on physical products",
    "Digital IC, ASIC, FPGA or chip design and verification engineer",
    "Civil, structural, project or construction engineer",
    "Sales engineer, solutions consultant or customer-facing technical support",
    "Lab technician, test technician or field service technician",
    "Research scientist publishing papers in a research lab",
]
# A rescue needs a software-ish word in the title; generic "Engineering Intern" / "Systems Engineer" / "Design
# Engineer" titles are too often mechanical, electrical or process roles.
_TECH_HINT = re.compile(r"\b(software|develop(er|ers|ment engineer\w*)|programm\w*|cloud|devops|devsecops|platform|"
                        r"full[- ]?stack|backend|back[- ]end|frontend|front[- ]end|web|data engineer\w*|mts|technical staff|"
                        r"technology|application(s)?|java|python|\.?net|salesforce|sde|swe|sre|site reliability|"
                        r"infrastructure|automation|integration|ios|android|mobile|coding|computer science|it)\b", re.I)


@dataclass
class Rescue:
    similarity: float
    profile: str


def candidate(job: dict, reasons: list[str]) -> bool:
    """Hidden only because the title didn't match, and the title looks technical."""
    return bool(reasons) and all(r.startswith("Title does not match") for r in reasons) \
        and bool(_TECH_HINT.search(job.get("title") or ""))


def job_text(job: dict, jd: str) -> str:
    reqs = " ".join((job.get("min_reqs") or [])[:3])
    body = reqs or " ".join(ln.strip("- ") for ln in jd.splitlines() if len(ln) > 40)[:400]
    return f"{job.get('title', '')}. {body}"[:600]


def rescue(embedder: Embedder, items: list[tuple[int, str]], threshold: float, margin: float = 0.04,
           extra_profile: list[str] | None = None) -> dict[int, Rescue]:
    """items: (job pk, text). Rescued when similarity to the profile is >= threshold AND beats every contrast
    profile by >= margin. Returns pk -> Rescue."""
    if not items:
        return {}
    profile = [*PROFILE, *(extra_profile or [])]
    p = embedder.embed(profile)
    n = embedder.embed(NOT_PROFILE)
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
