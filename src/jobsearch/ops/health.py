"""Source health: alert when a source returns 0 jobs, or drops by more than `drop` versus its recent average
(likely a broken endpoint or a changed page), so silent breakage never looks like "no new jobs"."""
from __future__ import annotations

from ..config import Health


def check(outcomes, history, cfg: Health | None = None) -> list[dict]:
    """outcomes: this run's SourceOutcomes; history(company, source) -> past job counts (most recent first).
    Thresholds: preferences health (drop, min_history, min_avg_zero, min_avg_drop)."""
    h = cfg or Health()
    drop = h.drop
    alerts = []
    for o in outcomes:
        if not o.ok or o.unchanged:
            continue
        past = history(o.company, o.source)
        if len(past) < h.min_history:
            continue
        avg = sum(past) / len(past)
        if o.jobs == 0 and avg >= h.min_avg_zero:
            alerts.append({"company": o.company, "source": o.source, "kind": "zero", "jobs": 0, "avg": round(avg, 1),
                           "message": f"{o.company} ({o.source}) returned 0 jobs; it usually lists ~{avg:.0f}"})
        elif avg >= h.min_avg_drop and o.jobs < (1 - drop) * avg:
            alerts.append({"company": o.company, "source": o.source, "kind": "drop", "jobs": o.jobs,
                           "avg": round(avg, 1),
                           "message": f"{o.company} ({o.source}) listed {o.jobs} jobs, down {100 * (1 - o.jobs / avg):.0f}% "
                                      f"from its recent average ~{avg:.0f}"})
    return alerts
