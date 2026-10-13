"""Internal reports saved with each version (never part of the employer-facing PDF)."""
from __future__ import annotations

from .validate import Report


def validation_md(rep: Report, attempts: list[dict], repairs: list[str], font_used: str | None,
                  preferred_font: str | None) -> str:
    L = ["# Validation report", "", f"Result: **{'passed' if rep.ok else 'FAILED'}** · pages: {rep.pages} · "
         f"main font: {font_used or rep.font_main or '?'}"
         + (f" (Calibri is not installed; Carlito is metric-compatible, so line breaks match)"
            if font_used and preferred_font and font_used != preferred_font else ""), ""]
    if rep.bottom_whitespace is not None:
        L.append(f"Empty space at the bottom of page 1: {rep.bottom_whitespace:.0%}")
    L += ["", "| Check | Result | Detail |", "|---|---|---|"]
    for c in rep.checks:
        res = "✓" if c.ok else ("✗ error" if c.severity == "error" else "! warning")
        L.append(f"| {c.name} | {res} | {c.detail.replace('|', '/')[:220] if (not c.ok or c.detail) else ''} |")
    if attempts:
        L += ["", "## Attempts (repair loop, max 3)"]
        for a in attempts:
            L.append(f"- attempt {a['attempt']}: {'ok' if a['ok'] else 'failed'} ({a['pages']} page(s))"
                     + (f" — {'; '.join(a['errors'])[:300]}" if a["errors"] else ""))
    if repairs:
        L += ["", "## Automatic repairs", *[f"- {r}" for r in repairs]]
    return "\n".join(L) + "\n"
