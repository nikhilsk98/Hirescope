"""Text HireScope writes that an employer may see — resumes, job packages meant to be shared (and any cover letter or
application answer written from them): Accenture's client is never named there (resume rule 39, v4.8); it is "a
banking client". Internal notes (master_resume.md, build reports, the dashboard's own views) may keep the name."""
from __future__ import annotations

import re
import unicodedata

NEVER_NAMED = ("Macquarie",)
_NAMED = re.compile(r"\b(?:the\s+)?Macquarie(?:\s+(?:Bank|Group|Group\s+Limited|Bank\s+Limited))?(?:,\s*Australia)?('s|’s)?",
                    re.I)


def never_named(text: str) -> list[str]:
    """Names from NEVER_NAMED in `text` (any case; also split at a line end or by a hyphen)."""
    t = unicodedata.normalize("NFKC", text or "").casefold()
    squashed = re.sub(r"[\s\-‐‑­]+", "", t)
    return [n for n in NEVER_NAMED if n.casefold() in t or n.casefold() in squashed]


def scrub(text: str | None) -> str | None:
    """`text` with the client's name replaced by the neutral wording ("Macquarie Bank's" → "a banking client's")."""
    if not text:
        return text
    return _NAMED.sub(lambda m: "a banking client" + ("'s" if m.group(1) else ""), text)
