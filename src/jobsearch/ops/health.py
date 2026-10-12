"""Source health: alert when a source returns 0 jobs, or drops by more than `drop` versus its recent average
(likely a broken endpoint or a changed page), so silent breakage never looks like "no new jobs"."""
from __future__ import annotations

MIN_HISTORY = 2      # successful past runs needed before judging
MIN_AVG_ZERO = 3     # a source that usually lists >= this many and now lists 0
MIN_AVG_DROP = 10    # drop alerts only for sources this big (small boards swing naturally)


def check(outcomes, history, drop: float = 0.70) -> list[dict]:
    """outcomes: this run's SourceOutcomes; history(company, source) -> past job counts (most recent first)."""
    alerts = []
    for o in outcomes:
        if not o.ok or o.unchanged:
            continue
        past = history(o.company, o.source)
        if len(past) < MIN_HISTORY:
            continue
        avg = sum(past) / len(past)
        if o.jobs == 0 and avg >= MIN_AVG_ZERO:
            alerts.append({"company": o.company, "source": o.source, "kind": "zero", "jobs": 0, "avg": round(avg, 1),
                           "message": f"{o.company} ({o.source}) returned 0 jobs; it usually lists ~{avg:.0f}"})
        elif avg >= MIN_AVG_DROP and o.jobs < (1 - drop) * avg:
            alerts.append({"company": o.company, "source": o.source, "kind": "drop", "jobs": o.jobs,
                           "avg": round(avg, 1),
                           "message": f"{o.company} ({o.source}) listed {o.jobs} jobs, down {100 * (1 - o.jobs / avg):.0f}% "
                                      f"from its recent average ~{avg:.0f}"})
    return alerts
