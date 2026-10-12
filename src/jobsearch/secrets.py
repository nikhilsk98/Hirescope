"""Secrets from `.env` (gitignored) and the environment. Values are never logged or written anywhere.

    IMAP_HOST, IMAP_USER, IMAP_APP_PASSWORD, IMAP_FOLDER      LinkedIn job-alert emails (Phase 5)
    JSEARCH_API_KEY / SERPAPI_API_KEY                         optional aggregator (off by default)
The real environment wins over `.env`.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from . import paths


@lru_cache(maxsize=4)
def _dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def get(name: str, default: str | None = None, path: Path | None = None) -> str | None:
    v = os.environ.get(name)
    if v:
        return v
    return _dotenv(path or paths.ROOT / ".env").get(name) or default


def redact(text: str, *names: str) -> str:
    """Remove any configured secret values from a message before it is logged."""
    for n in names:
        v = get(n)
        if v and len(v) >= 4:
            text = text.replace(v, "***")
    return text
