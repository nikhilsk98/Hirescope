"""Dedupe (exact, fuzzy, cross-source), update and closed detection. (DEMO data)"""
from jobsearch.storage import db


def rec(**kw) -> db.JobRecord:
    base = dict(company="DemoCo", title="Software Engineer (DEMO)", normalized_title="software engineer demo",
                job_id="1", source="greenhouse", source_url=None, apply_url="https://example.com/1",
                location="Boston, MA", work_mode="onsite", employment_type=None, seniority="unknown",
                posted_date=None, salary_min=None, salary_max=None, salary_currency=None, jd_path=None,
                jd_hash="h1", min_reqs=[], pref_reqs=[], skills=[], yoe_required=None, yoe_max=None,
                yoe_evidence=None, degree=None, grad_years=[], flags={}, extra={})
    base.update(kw)
    return db.JobRecord(**base)


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def status(conn, pk):
    return conn.execute("SELECT job_status FROM jobs WHERE id=?", (pk,)).fetchone()[0]


def test_migrations_idempotent(conn):
    db.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_exact_dedupe_on_company_and_job_id(conn):
    pk1, s1 = db.upsert_job(conn, rec())
    pk2, s2 = db.upsert_job(conn, rec(company="democo"))  # company match is case-insensitive
    assert (s1, s2) == ("new", "seen") and pk1 == pk2 and count(conn) == 1


def test_same_source_different_ids_are_distinct(conn):
    db.upsert_job(conn, rec(job_id="1"))
    db.upsert_job(conn, rec(job_id="2"))
    assert count(conn) == 2


def test_fuzzy_cross_source_dedupe(conn):
    pk1, _ = db.upsert_job(conn, rec())
    pk2, s = db.upsert_job(conn, rec(source="lever", job_id="abc", title="Software Engineer - DEMO",
                                     normalized_title="software engineer demo", location="Boston, MA"))
    assert pk1 == pk2 and s == "seen" and count(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM job_sources WHERE job_pk=?", (pk1,)).fetchone()[0] == 2


def test_fuzzy_does_not_merge_different_location_or_title(conn):
    db.upsert_job(conn, rec())
    db.upsert_job(conn, rec(source="lever", job_id="x", location="Seattle, WA"))
    db.upsert_job(conn, rec(source="lever", job_id="y", normalized_title="data scientist demo"))
    assert count(conn) == 3


def test_update_detected_by_jd_hash(conn):
    pk, _ = db.upsert_job(conn, rec())
    _, s = db.upsert_job(conn, rec(jd_hash="h2"))
    assert s == "updated" and status(conn, pk) == "updated"
    _, s = db.upsert_job(conn, rec(jd_hash="h2"))
    assert s == "seen"


def test_closed_after_two_consecutive_misses(conn):
    pk, _ = db.upsert_job(conn, rec())
    assert db.mark_missing(conn, "greenhouse", "DemoCo", set()) == 0   # miss 1
    assert status(conn, pk) != "closed"
    assert db.mark_missing(conn, "greenhouse", "DemoCo", set()) == 1   # miss 2
    assert status(conn, pk) == "closed"


def test_seen_again_resets_misses_and_reopen_counts_repost(conn):
    pk, _ = db.upsert_job(conn, rec())
    db.mark_missing(conn, "greenhouse", "DemoCo", set())
    db.upsert_job(conn, rec())                         # back: misses reset
    db.mark_missing(conn, "greenhouse", "DemoCo", set())
    assert status(conn, pk) != "closed"
    db.mark_missing(conn, "greenhouse", "DemoCo", set())
    assert status(conn, pk) == "closed"
    _, s = db.upsert_job(conn, rec())
    assert s == "updated"
    assert conn.execute("SELECT repost_count FROM jobs WHERE id=?", (pk,)).fetchone()[0] == 1


def test_job_listed_by_two_sources_stays_open_while_one_lists_it(conn):
    pk, _ = db.upsert_job(conn, rec())
    db.upsert_job(conn, rec(source="lever", job_id="abc"))
    for _ in range(3):
        db.mark_missing(conn, "greenhouse", "DemoCo", set())
    assert status(conn, pk) != "closed"
    db.mark_missing(conn, "lever", "DemoCo", set())
    db.mark_missing(conn, "lever", "DemoCo", set())
    assert status(conn, pk) == "closed"


def test_json_fields_roundtrip(conn):
    pk, _ = db.upsert_job(conn, rec(skills=["Python"], flags={"sponsorship": {"value": "Not stated", "evidence": None}}))
    row = db.open_jobs(conn)[0]
    assert row["skills"] == ["Python"] and row["flags"]["sponsorship"]["value"] == "Not stated"


def test_reextraction_applies_config_fixes_without_refetch(conn, cfg, tmp_path):
    from jobsearch.adapters.base import RawJob
    from jobsearch.pipeline import ensure_extraction_current, to_record
    raw = RawJob(company="DemoCo", job_id="9", title="Senior Engineer (DEMO)", source="lever", source_url="x",
                 location="Lviv", description="- Python", employment_type="International EOR")
    rec = to_record(raw, cfg, tmp_path)
    rec.track = "coop_intern"               # what the old, buggy parser stored
    pk, _ = db.upsert_job(conn, rec)
    assert ensure_extraction_current(conn, cfg) == 1
    row = db.open_jobs(conn)[0]
    assert row["track"] == "full_time" and row["location_us"] == 0 and row["job_status"] == "new"
    assert ensure_extraction_current(conn, cfg) == 0           # fingerprint unchanged -> nothing to do


def test_merge_canonical_duplicates_keeps_the_job_you_acted_on(conn):
    from jobsearch.adapters.canonical import canonical_key
    u = "https://demo.wd1.myworkdayjobs.com/UR/job/Boston/SWE-Intern_2641701"
    a, _ = db.upsert_job(conn, rec(job_id="a", apply_url=u, company="DemoCo"))
    b, _ = db.upsert_job(conn, rec(job_id="b", apply_url=u.replace("/UR/", "/Search/") + "-1", company="DemoCo Corporation",
                                   normalized_title="other"))
    conn.execute("UPDATE jobs SET user_status='shortlisted', user_status_at='2026-10-12T00:00:00+00:00' WHERE id=?", (b,))
    assert db.merge_canonical_duplicates(conn, canonical_key) == 1
    assert count(conn) == 1 and conn.execute("SELECT id FROM jobs").fetchone()[0] == b
    assert conn.execute("SELECT COUNT(*) FROM job_sources WHERE job_pk=?", (b,)).fetchone()[0] == 2
