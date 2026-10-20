"""Stale-posting rule (2026-10-20): a job posted 365+ days ago that the employer's job system still lists is shown with
an "Old posting" badge and a ghost-risk penalty; one no source can confirm stays hidden. All data is DEMO."""
from datetime import datetime, timedelta, timezone

from jobsearch.adapters.base import RawJob
from jobsearch.match.rules import apply_rules
from jobsearch.match.score import ghost_signals
from jobsearch.output.dashboard import _badges
from jobsearch.pipeline import listed_open, to_record
from jobsearch.storage import db

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def _job(cfg, **kw):
    rec = to_record(RawJob(company="DemoCo", job_id="1", title="Software Engineer I", source="greenhouse", source_url="",
                           apply_url=None, location="Boston, MA", description="Python services.",
                           posted_date=NOW - timedelta(days=500)), cfg, None)
    return {**rec.__dict__, "job_status": "seen", "first_seen": "2026-10-01T00:00:00+00:00", **kw}


def test_old_posting_still_listed_is_shown_with_badge_and_penalty(cfg):
    j = _job(cfg, listed_open=True)
    r = apply_rules(j, cfg.prefs, cfg.title_rules, NOW)
    assert not any("stale" in x for x in r.reasons) and r.signals["old_posting"] == 500
    ghosts = ghost_signals(j, cfg.prefs, NOW)
    assert ghosts[0][0].startswith("Old posting") and ghosts[0][1] == cfg.prefs.ghost.stale_penalty
    assert "old" in _badges({**j, "signals": {"old_posting": 500}}, "2026-10-20", "2026-10-20", "America/New_York", set())


def test_old_posting_no_source_confirms_stays_hidden(cfg):
    r = apply_rules(_job(cfg, listed_open=False), cfg.prefs, cfg.title_rules, NOW)
    assert any("can't confirm" in x for x in r.reasons) and "old_posting" not in r.signals


def test_listed_open_counts_official_sources_seen_recently(conn, cfg):
    def add(source, last_seen):
        rec = to_record(RawJob(company="DemoCo", job_id=f"{source}-1", title=f"Engineer {source}", source=source,
                               source_url="", apply_url=None, description="x"), cfg, None)
        pk, _ = db.upsert_job(conn, rec, now=last_seen)
        return pk
    gh = add("greenhouse", "2026-10-19T00:00:00+00:00")
    old = add("lever", "2026-09-01T00:00:00+00:00")
    feed = add("simplify_newgrad", "2026-10-19T00:00:00+00:00")
    assert listed_open(conn, NOW) == {gh} and old not in listed_open(conn, NOW) and feed not in listed_open(conn, NOW)
