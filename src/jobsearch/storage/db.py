"""SQLite storage with versioned migrations, dedupe, update and closed-job detection.

Presence per source lives in `job_sources`; a `jobs` row is one real-world posting that may be
listed by several sources. A job is closed when every source listing it has missed it on
CLOSE_AFTER_MISSES consecutive *successful* fetches (a failed fetch never closes anything).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rapidfuzz import fuzz

CLOSE_AFTER_MISSES = 2
FUZZY_TITLE_THRESHOLD = 92

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        company TEXT NOT NULL,
        company_key TEXT NOT NULL,
        title TEXT NOT NULL,
        normalized_title TEXT NOT NULL,
        job_id TEXT NOT NULL,
        location TEXT,
        work_mode TEXT,
        employment_type TEXT,
        seniority TEXT,
        posted_date TEXT,
        first_seen TEXT NOT NULL,
        last_checked TEXT NOT NULL,
        source TEXT NOT NULL,
        source_url TEXT,
        apply_url TEXT,
        salary_min INTEGER,
        salary_max INTEGER,
        salary_currency TEXT,
        jd_path TEXT,
        jd_hash TEXT,
        min_reqs TEXT,
        pref_reqs TEXT,
        skills TEXT,
        yoe_required REAL,
        yoe_max REAL,
        yoe_evidence TEXT,
        degree TEXT,
        grad_years TEXT,
        flags TEXT,
        repost_count INTEGER NOT NULL DEFAULT 0,
        score REAL,
        qual_score REAL,
        pref_score REAL,
        label TEXT,
        breakdown TEXT,
        explanation TEXT,
        gaps TEXT,
        filtered INTEGER NOT NULL DEFAULT 0,
        filter_reasons TEXT,
        job_status TEXT NOT NULL DEFAULT 'new',
        status_changed_at TEXT,
        user_status TEXT,
        skip_reason TEXT,
        user_status_at TEXT,
        extra TEXT
    );
    CREATE INDEX jobs_company ON jobs(company_key);
    CREATE INDEX jobs_status ON jobs(job_status);
    CREATE TABLE job_sources (
        job_pk INTEGER NOT NULL REFERENCES jobs(id),
        source TEXT NOT NULL,
        company_key TEXT NOT NULL,
        source_job_id TEXT NOT NULL,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        miss_count INTEGER NOT NULL DEFAULT 0,
        UNIQUE(source, company_key, source_job_id)
    );
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        dry_run INTEGER NOT NULL DEFAULT 0,
        summary TEXT
    );
    CREATE TABLE source_status (
        company_key TEXT NOT NULL,
        company TEXT NOT NULL,
        source TEXT NOT NULL,
        last_checked TEXT NOT NULL,
        status TEXT NOT NULL,
        error TEXT,
        job_count INTEGER,
        PRIMARY KEY (company_key, source)
    );
    CREATE TABLE embeddings (
        text_hash TEXT NOT NULL,
        model TEXT NOT NULL,
        vector BLOB NOT NULL,
        PRIMARY KEY (text_hash, model)
    );
    CREATE TABLE resume_versions (
        hash TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        first_parsed TEXT NOT NULL
    );
    """,
    # 2: tracks, levels, cross-source canonical keys, caches, coverage
    """
    ALTER TABLE jobs ADD COLUMN track TEXT;
    ALTER TABLE jobs ADD COLUMN track_evidence TEXT;
    ALTER TABLE jobs ADD COLUMN track_confidence TEXT;
    ALTER TABLE jobs ADD COLUMN track_override TEXT;
    ALTER TABLE jobs ADD COLUMN level TEXT;
    ALTER TABLE jobs ADD COLUMN level_evidence TEXT;
    ALTER TABLE jobs ADD COLUMN terms TEXT;
    ALTER TABLE jobs ADD COLUMN canonical_key TEXT;
    ALTER TABLE jobs ADD COLUMN location_us INTEGER;
    ALTER TABLE jobs ADD COLUMN remote_us INTEGER;
    ALTER TABLE jobs ADD COLUMN source_official INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE jobs ADD COLUMN link_status TEXT;
    ALTER TABLE jobs ADD COLUMN link_checked_at TEXT;
    CREATE INDEX jobs_canonical ON jobs(canonical_key);
    ALTER TABLE job_sources ADD COLUMN canonical_key TEXT;
    ALTER TABLE job_sources ADD COLUMN detail_fetched_at TEXT;
    CREATE INDEX job_sources_canonical ON job_sources(canonical_key);
    ALTER TABLE source_status ADD COLUMN last_ok TEXT;
    ALTER TABLE source_status ADD COLUMN origin TEXT;
    ALTER TABLE source_status ADD COLUMN tier INTEGER;
    ALTER TABLE source_status ADD COLUMN url TEXT;
    ALTER TABLE source_status ADD COLUMN partial TEXT;
    ALTER TABLE source_status ADD COLUMN detected_via TEXT;
    CREATE TABLE http_cache (
        url TEXT PRIMARY KEY,
        etag TEXT,
        last_modified TEXT,
        fetched_at TEXT NOT NULL
    );
    CREATE TABLE company_sources (
        company_key TEXT PRIMARY KEY,
        company TEXT NOT NULL,
        source TEXT NOT NULL,
        board TEXT,
        host TEXT,
        site TEXT,
        careers_url TEXT,
        detected_via TEXT NOT NULL,
        detected_at TEXT NOT NULL,
        fail_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE link_checks (
        url TEXT PRIMARY KEY,
        checked_at TEXT NOT NULL,
        http_status INTEGER,
        ok INTEGER,
        final_url TEXT,
        note TEXT
    );
    CREATE TABLE page_cache (
        url TEXT PRIMARY KEY,
        lastmod TEXT,
        fetched_at TEXT NOT NULL,
        payload TEXT
    );
    """,
    # 3: job_sources lookups by job (fuzzy-dedupe NOT EXISTS, closed detection) were full scans
    """
    CREATE INDEX job_sources_job ON job_sources(job_pk, source);
    """,
    # 4: key/value metadata (extraction version + config fingerprint)
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """,
    # 5: Phase 3 - two-track dates, eligibility, deadlines, ATS keywords, similar jobs
    """
    ALTER TABLE jobs ADD COLUMN grad_windows TEXT;
    ALTER TABLE jobs ADD COLUMN start_windows TEXT;
    ALTER TABLE jobs ADD COLUMN summer_only INTEGER;
    ALTER TABLE jobs ADD COLUMN eligibility_note TEXT;
    ALTER TABLE jobs ADD COLUMN deadline TEXT;
    ALTER TABLE jobs ADD COLUMN deadline_evidence TEXT;
    ALTER TABLE jobs ADD COLUMN keywords TEXT;
    ALTER TABLE jobs ADD COLUMN keywords_key TEXT;
    ALTER TABLE jobs ADD COLUMN ats_coverage REAL;
    ALTER TABLE jobs ADD COLUMN signals TEXT;
    ALTER TABLE jobs ADD COLUMN similar TEXT;
    """,
    # 6: Phase 4 - My Applications (merged from dashboard exports), track-override timestamps
    """
    ALTER TABLE jobs ADD COLUMN track_override_at TEXT;
    ALTER TABLE jobs ADD COLUMN user_notes TEXT;
    CREATE TABLE applications (
        id TEXT PRIMARY KEY,
        job_key TEXT,
        job_pk INTEGER,
        company TEXT NOT NULL,
        company_key TEXT NOT NULL,
        title TEXT NOT NULL,
        track TEXT,
        link TEXT,
        source TEXT,
        applied_date TEXT,
        stage TEXT NOT NULL DEFAULT 'applied',
        next_step TEXT,
        next_step_date TEXT,
        contact TEXT,
        referral TEXT,
        notes TEXT,
        package TEXT,
        jd_path TEXT,
        followed_up TEXT,
        deleted INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        extra TEXT
    );
    CREATE INDEX applications_company ON applications(company_key);
    CREATE TABLE application_events (
        id INTEGER PRIMARY KEY,
        app_id TEXT NOT NULL REFERENCES applications(id),
        at TEXT NOT NULL,
        kind TEXT NOT NULL,
        from_stage TEXT NOT NULL DEFAULT '',
        to_stage TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        UNIQUE(app_id, at, kind, to_stage, note)
    );
    """,
    # 7: Manual Checks (companies checked by hand; merged from dashboard exports)
    """
    CREATE TABLE manual_checks (
        company_key TEXT PRIMARY KEY,
        company TEXT NOT NULL,
        checked_at TEXT,
        history TEXT,
        updated_at TEXT NOT NULL
    );
    """,
    # 8: Resume Builder (per-job resume status + every built version; the resume version used for an application)
    """
    CREATE TABLE resume_status (
        job_pk INTEGER PRIMARY KEY,
        status TEXT NOT NULL,
        current_version INTEGER,
        latest_version INTEGER,
        message TEXT,
        job_dir TEXT,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE resume_builds (
        job_pk INTEGER NOT NULL,
        version INTEGER NOT NULL,
        status TEXT NOT NULL,
        pdf_path TEXT,
        tex_path TEXT,
        manifest_path TEXT,
        manifest_sha TEXT,
        created_at TEXT NOT NULL,
        reviewed_at TEXT,
        summary TEXT,
        PRIMARY KEY (job_pk, version)
    );
    ALTER TABLE applications ADD COLUMN resume TEXT;
    """,
    # 9: zero-CLI resumes — the background resume queue, the Bucket (apply-list), keyword questions from --queue builds
    """
    CREATE TABLE resume_queue (
        id INTEGER PRIMARY KEY,
        job_pk INTEGER NOT NULL,
        regenerate INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0,
        batch TEXT,
        origin TEXT,
        added_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        pid INTEGER,
        message TEXT,
        result TEXT
    );
    CREATE INDEX resume_queue_status ON resume_queue(status, id);
    CREATE TABLE bucket (
        job_pk INTEGER PRIMARY KEY,
        job_key TEXT NOT NULL,
        in_bucket INTEGER NOT NULL DEFAULT 1,
        added_at TEXT,
        updated_at TEXT NOT NULL,
        snapshot TEXT
    );
    CREATE TABLE resume_questions (
        id INTEGER PRIMARY KEY,
        job_pk INTEGER NOT NULL,
        kind TEXT NOT NULL,
        keyword TEXT NOT NULL,
        question TEXT NOT NULL,
        options TEXT,
        asked_at TEXT NOT NULL,
        answered_at TEXT,
        answer TEXT,
        UNIQUE(job_pk, keyword)
    );
    """,
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def company_key(name: str) -> str:
    return " ".join(name.casefold().split())


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # wait up to 30 s for another writer (a scheduled run, a Resume Builder command) instead of failing at once
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        with conn:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {i}")


@dataclass
class JobRecord:
    """Extracted fields written on ingest (scores are written separately)."""
    company: str
    title: str
    normalized_title: str
    job_id: str
    source: str
    source_url: str | None
    apply_url: str | None
    location: str | None
    work_mode: str | None
    employment_type: str | None
    seniority: str | None
    posted_date: str | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    jd_path: str | None
    jd_hash: str
    min_reqs: list[str]
    pref_reqs: list[str]
    skills: list[str]
    yoe_required: float | None
    yoe_max: float | None
    yoe_evidence: str | None
    degree: str | None
    grad_years: list[int]
    flags: dict
    extra: dict
    track: str | None = None
    track_evidence: str | None = None
    track_confidence: str | None = None
    level: str | None = None
    level_evidence: str | None = None
    terms: list[str] = field(default_factory=list)
    canonical_key: str | None = None
    location_us: bool | None = None
    remote_us: bool | None = None
    source_official: bool = True
    grad_windows: list[dict] = field(default_factory=list)
    start_windows: list[dict] = field(default_factory=list)
    summer_only: bool = False
    eligibility_note: str | None = None
    deadline: str | None = None
    deadline_evidence: str | None = None


_JSON_FIELDS = {"min_reqs", "pref_reqs", "skills", "grad_years", "flags", "extra",
                "breakdown", "gaps", "filter_reasons", "terms", "grad_windows", "start_windows", "keywords",
                "signals", "similar"}
_EXTRACTED = [f for f in JobRecord.__dataclass_fields__ if f not in ("company", "job_id", "source")]
# when an official source takes over from an aggregator, these identify the posting too
_IDENTITY = ["job_id", "source"]


def _enc(field: str, value):
    if field in _JSON_FIELDS and value is not None:
        return json.dumps(value)
    if isinstance(value, bool):
        return int(value)
    return value


def decode_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in _JSON_FIELDS & d.keys():
        if d[f] is not None:
            d[f] = json.loads(d[f])
    return d


def _location_key(loc: str | None) -> str:
    return " ".join((loc or "").casefold().replace(",", " ").replace("·", " ").split())


def find_fuzzy(conn, rec: JobRecord) -> int | None:
    """Cross-source match: same company, near-identical normalized title, same location, from another source."""
    ck = company_key(rec.company)
    rows = conn.execute(
        "SELECT j.id, j.normalized_title, j.location FROM jobs j WHERE j.company_key = ? AND j.job_status != 'closed' "
        "AND NOT EXISTS (SELECT 1 FROM job_sources s WHERE s.job_pk = j.id AND s.source = ?)",
        (ck, rec.source),
    ).fetchall()
    loc = _location_key(rec.location)
    for r in rows:
        exact = r["normalized_title"] == rec.normalized_title
        # an unknown location only matches the same title exactly (different requisitions share roles)
        same_loc = (_location_key(r["location"]) == loc or fuzz.token_set_ratio(_location_key(r["location"]), loc) >= 90) \
            if loc and r["location"] else exact
        if same_loc and (exact or fuzz.token_sort_ratio(r["normalized_title"], rec.normalized_title) >= FUZZY_TITLE_THRESHOLD) \
                and not _other_posting(conn, r["id"], rec.canonical_key):
            return r["id"]
    return None


def _other_posting(conn, pk: int, key: str | None) -> bool:
    """The job is a different posting than `key`: it (or one of its listings) carries another ATS posting key."""
    if not key:
        return False
    keys = {k for (k,) in conn.execute("SELECT canonical_key FROM job_sources WHERE job_pk=? AND canonical_key IS NOT "
                                       "NULL UNION SELECT canonical_key FROM jobs WHERE id=? AND canonical_key IS NOT "
                                       "NULL", (pk, pk))}
    from ..adapters.canonical import normalized
    return bool(keys) and normalized(key) not in {normalized(k) for k in keys}


def split_wrong_merges(conn) -> int:
    """Listings joined to a job whose posting key they contradict (an earlier fuzzy match of a different requisition:
    P&G's internship under an Analytics Manager role) are detached; the next fetch stores them as their own job.
    Returns listings detached."""
    rows = conn.execute("SELECT s.rowid, s.canonical_key AS sk, j.canonical_key AS jk FROM job_sources s JOIN jobs j "
                        "ON j.id = s.job_pk WHERE j.job_status != 'closed' AND s.canonical_key IS NOT NULL AND "
                        "j.canonical_key IS NOT NULL AND s.canonical_key != j.canonical_key").fetchall()
    from ..adapters.canonical import normalized
    bad = [r["rowid"] for r in rows if r["sk"].split(":")[0] == r["jk"].split(":")[0]
           and normalized(r["sk"]) != normalized(r["jk"])]
    for rowid in bad:
        conn.execute("DELETE FROM job_sources WHERE rowid=?", (rowid,))
    conn.commit()
    return len(bad)


def find_by_canonical(conn, rec: JobRecord) -> int | None:
    """Same posting from another source, identified by the employer ATS + job id parsed from its URL."""
    if not rec.canonical_key:
        return None
    row = conn.execute(
        "SELECT job_pk FROM job_sources WHERE canonical_key=? ORDER BY rowid LIMIT 1", (rec.canonical_key,)
    ).fetchone() or conn.execute("SELECT id AS job_pk FROM jobs WHERE canonical_key=? LIMIT 1",
                                 (rec.canonical_key,)).fetchone()
    return row["job_pk"] if row else None


def _add_source(conn, pk: int, rec: JobRecord, ck: str, now: str) -> None:
    conn.execute(
        "INSERT INTO job_sources (job_pk, source, company_key, source_job_id, first_seen, last_seen, canonical_key, "
        "detail_fetched_at) VALUES (?,?,?,?,?,?,?,?)",
        (pk, rec.source, ck, rec.job_id, now, now, rec.canonical_key, now if rec.jd_path else None),
    )


def upsert_job(conn: sqlite3.Connection, rec: JobRecord, now: str | None = None, fuzzy: bool = True) -> tuple[int, str]:
    """Insert or update one job. Returns (job pk, 'new' | 'updated' | 'seen').

    Match order: same source + company + job id -> same canonical ATS key from any source -> fuzzy
    (company + normalized title + location). An official ATS source replaces an aggregator's data.
    fuzzy=False skips the fuzzy step (external jobs: their own duplicate check already decided).
    """
    now = now or utcnow()
    ck = company_key(rec.company)
    row = conn.execute(
        "SELECT job_pk FROM job_sources WHERE source=? AND company_key=? AND source_job_id=?",
        (rec.source, ck, rec.job_id),
    ).fetchone()
    pk = row["job_pk"] if row else (find_by_canonical(conn, rec) or (find_fuzzy(conn, rec) if fuzzy else None))

    if pk is None:
        cols = ["company", "company_key", "job_id", "source", "first_seen", "last_checked",
                "job_status", "status_changed_at", *_EXTRACTED]
        vals = [rec.company, ck, rec.job_id, rec.source, now, now, "new", now,
                *(_enc(f, getattr(rec, f)) for f in _EXTRACTED)]
        cur = conn.execute(f"INSERT INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        pk = cur.lastrowid
        _add_source(conn, pk, rec, ck, now)
        return pk, "new"

    existing = conn.execute("SELECT * FROM jobs WHERE id=?", (pk,)).fetchone()
    if row is None:
        _add_source(conn, pk, rec, ck, now)
    else:
        conn.execute(
            "UPDATE job_sources SET last_seen=?, miss_count=0, canonical_key=COALESCE(?, canonical_key)"
            + (", detail_fetched_at=?" if rec.jd_path else "")
            + " WHERE source=? AND company_key=? AND source_job_id=?",
            (now, rec.canonical_key, *((now,) if rec.jd_path else ()), rec.source, ck, rec.job_id),
        )

    takeover = rec.source_official and not existing["source_official"] and existing["source"] != rec.source
    primary = existing["source"] == rec.source and existing["job_id"] == rec.job_id
    sets: dict = {"last_checked": now}
    if primary or takeover:
        sets.update({f: _enc(f, getattr(rec, f)) for f in _EXTRACTED})
        if takeover:
            sets.update(job_id=rec.job_id, source=rec.source, company=rec.company)
    reopened = existing["job_status"] == "closed"
    changed = (primary or takeover) and existing["jd_hash"] != rec.jd_hash and not takeover
    if changed or reopened:
        status = "updated"
        sets.update(job_status=status, status_changed_at=now)
        if reopened:
            sets["repost_count"] = existing["repost_count"] + 1
    else:
        status = "seen"
        if existing["job_status"] != status:
            sets.update(job_status=status, status_changed_at=now)
    conn.execute(f"UPDATE jobs SET {','.join(f'{k}=?' for k in sets)} WHERE id=?", [*sets.values(), pk])
    return pk, status


def touch_job(conn, source: str, company: str, job_id: str, now: str | None = None) -> tuple[int, str] | None:
    """Mark an already-stored listing as seen without re-extracting it (detail not re-fetched).

    Returns (pk, status) or None if the listing is unknown.
    """
    now = now or utcnow()
    ck = company_key(company)
    row = conn.execute("SELECT job_pk FROM job_sources WHERE source=? AND company_key=? AND source_job_id=?",
                       (source, ck, job_id)).fetchone()
    if not row:
        return None
    pk = row["job_pk"]
    conn.execute("UPDATE job_sources SET last_seen=?, miss_count=0 WHERE source=? AND company_key=? AND source_job_id=?",
                 (now, source, ck, job_id))
    st = conn.execute("SELECT job_status, repost_count FROM jobs WHERE id=?", (pk,)).fetchone()
    if st["job_status"] == "closed":
        conn.execute("UPDATE jobs SET job_status='updated', status_changed_at=?, repost_count=?, last_checked=? "
                     "WHERE id=?", (now, st["repost_count"] + 1, now, pk))
        return pk, "updated"
    conn.execute("UPDATE jobs SET last_checked=?, job_status=CASE WHEN job_status='closed' THEN job_status "
                 "ELSE 'seen' END WHERE id=?", (now, pk))
    return pk, "seen"


def touch_all(conn, source: str, company: str | None, now: str | None = None) -> set[str]:
    """A conditional GET returned 304: the listing is exactly what the last successful fetch saw. Listings present
    then (miss_count 0, job not closed) are marked seen; listings already missing then are left alone (the caller
    counts another miss for full listings) and a closed job is never reopened. company=None covers a whole
    multi-company feed. Returns the source job ids that are present."""
    now = now or utcnow()
    sql = ("SELECT s.company_key, s.source_job_id FROM job_sources s JOIN jobs j ON j.id = s.job_pk "
           "WHERE s.source=? AND s.miss_count = 0 AND j.job_status != 'closed'")
    rows = (conn.execute(sql, (source,)) if company is None else
            conn.execute(sql + " AND s.company_key=?", (source, company_key(company)))).fetchall()
    for ck, jid in rows:
        touch_job(conn, source, ck, jid, now)
    return {jid for _, jid in rows}


def known_listings(conn, source: str, company: str | None = None) -> dict[str, str | None]:
    """source job id -> detail_fetched_at, for adapters that can skip re-fetching details."""
    if company is None:
        q, args = "SELECT source_job_id, detail_fetched_at FROM job_sources WHERE source=?", (source,)
    else:
        q = "SELECT source_job_id, detail_fetched_at FROM job_sources WHERE source=? AND company_key=?"
        args = (source, company_key(company))
    return {r[0]: r[1] for r in conn.execute(q, args)}


def mark_missing(conn: sqlite3.Connection, source: str, company: str | None, seen_ids: set[str],
                 now: str | None = None) -> int:
    """After a *successful* fetch: bump misses for unseen listings and close jobs missing everywhere.

    company=None covers every company for a multi-company feed (e.g. SimplifyJobs).
    Returns the number of jobs newly closed.
    """
    now = now or utcnow()
    if company is None:
        rows = conn.execute("SELECT job_pk, company_key, source_job_id FROM job_sources WHERE source=?",
                            (source,)).fetchall()
    else:
        rows = conn.execute("SELECT job_pk, company_key, source_job_id FROM job_sources WHERE source=? AND "
                            "company_key=?", (source, company_key(company))).fetchall()
    missing = [r for r in rows if r["source_job_id"] not in seen_ids]
    for r in missing:
        conn.execute(
            "UPDATE job_sources SET miss_count = miss_count + 1 WHERE source=? AND company_key=? AND source_job_id=?",
            (source, r["company_key"], r["source_job_id"]),
        )
    closed = 0
    for pk in {r["job_pk"] for r in missing}:
        still_listed = conn.execute(
            "SELECT 1 FROM job_sources WHERE job_pk=? AND miss_count < ?", (pk, CLOSE_AFTER_MISSES)
        ).fetchone()
        if not still_listed:
            cur = conn.execute(
                "UPDATE jobs SET job_status='closed', status_changed_at=? WHERE id=? AND job_status!='closed'", (now, pk)
            )
            closed += cur.rowcount
    return closed


def mark_missing_ids(conn, source: str, company: str | None, ids: set[str], now: str | None = None) -> int:
    """Bump misses only for these listings (verified gone), closing jobs missing everywhere. Returns closed."""
    now = now or utcnow()
    closed = 0
    for jid in ids:
        q = "SELECT job_pk, company_key FROM job_sources WHERE source=? AND source_job_id=?"
        args: tuple = (source, jid)
        if company is not None:
            q += " AND company_key=?"
            args += (company_key(company),)
        for r in conn.execute(q, args).fetchall():
            conn.execute("UPDATE job_sources SET miss_count = miss_count + 1 WHERE source=? AND company_key=? "
                         "AND source_job_id=?", (source, r["company_key"], jid))
            if not conn.execute("SELECT 1 FROM job_sources WHERE job_pk=? AND miss_count < ?",
                                (r["job_pk"], CLOSE_AFTER_MISSES)).fetchone():
                closed += conn.execute("UPDATE jobs SET job_status='closed', status_changed_at=? WHERE id=? AND "
                                       "job_status!='closed'", (now, r["job_pk"])).rowcount
    return closed


def close_unseen(conn, source: str, company: str | None, present: set[str], days: int, now: str | None = None) -> int:
    """For searches that stopped early: listings not returned now that no search has returned for `days` and that
    aren't shown (filtered) are treated as gone; shown ones are left to verification. Returns jobs closed."""
    now = now or utcnow()
    cutoff = (datetime.fromisoformat(now) - timedelta(days=days)).isoformat(timespec="seconds")
    q = ("SELECT s.source_job_id FROM job_sources s JOIN jobs j ON j.id = s.job_pk WHERE s.source=? AND "
         "s.last_seen < ? AND j.filtered = 1 AND j.job_status != 'closed'")
    args: tuple = (source, cutoff)
    if company is not None:
        q += " AND s.company_key=?"
        args += (company_key(company),)
    ids = {r[0] for r in conn.execute(q, args)} - set(present)
    return mark_missing_ids(conn, source, company, ids, now) if ids else 0


def relevant_listings(conn, source: str, company: str | None) -> dict[str, str | None]:
    """Visible (not filtered) open listings for a source/company: job id -> apply url."""
    q = ("SELECT s.source_job_id, j.apply_url FROM job_sources s JOIN jobs j ON j.id = s.job_pk WHERE s.source=? "
         "AND j.filtered = 0 AND j.job_status != 'closed'")
    args: tuple = (source,)
    if company is not None:
        q += " AND s.company_key=?"
        args += (company_key(company),)
    return {r[0]: r[1] for r in conn.execute(q, args)}


def save_scores(conn: sqlite3.Connection, pk: int, **fields) -> None:
    conn.execute(
        f"UPDATE jobs SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
        [*(_enc(k, v) for k, v in fields.items()), pk],
    )


def set_source_status(conn, company: str, source: str, status: str, error: str | None, job_count: int | None,
                      now: str | None = None, **extra) -> None:
    """status: ok | failed | manual | skipped | unavailable. extra: origin, tier, url, partial, detected_via."""
    now = now or utcnow()
    cols = {"company_key": company_key(company), "company": company, "source": source, "last_checked": now,
            "status": status, "error": error, "job_count": job_count, **extra}
    if status == "ok":
        cols["last_ok"] = now
    names = list(cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in names if c not in ("company_key", "source"))
    conn.execute(f"INSERT INTO source_status ({','.join(names)}) VALUES ({','.join('?' * len(names))}) "
                 f"ON CONFLICT(company_key, source) DO UPDATE SET {updates}", list(cols.values()))


def prune_source_status(conn, before: str) -> int:
    """Drop source rows a complete run didn't touch (a company now read through another job system, or no longer
    listed): they would show old failures ("Microsoft — jsonld — failed") on the coverage view."""
    n = conn.execute("DELETE FROM source_status WHERE last_checked < ?", (before,)).rowcount
    conn.commit()
    return n


def source_statuses(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM source_status ORDER BY company")]


def last_ok(conn, company: str, source: str) -> str | None:
    row = conn.execute("SELECT last_ok FROM source_status WHERE company_key=? AND source=?",
                       (company_key(company), source)).fetchone()
    return row[0] if row else None


# --- HTTP conditional-request cache ---------------------------------------------------------------
def get_http_cache(conn, url: str) -> dict | None:
    row = conn.execute("SELECT * FROM http_cache WHERE url=?", (url,)).fetchone()
    return dict(row) if row else None


def set_http_cache(conn, url: str, etag: str | None, last_modified: str | None) -> None:
    conn.execute("INSERT INTO http_cache (url, etag, last_modified, fetched_at) VALUES (?,?,?,?) "
                 "ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, last_modified=excluded.last_modified, "
                 "fetched_at=excluded.fetched_at", (url, etag, last_modified, utcnow()))


def clear_http_cache(conn, url: str) -> None:
    conn.execute("DELETE FROM http_cache WHERE url=?", (url,))


# --- ATS detection cache (My Companies) ------------------------------------------------------------
def get_company_source(conn, company: str) -> dict | None:
    row = conn.execute("SELECT * FROM company_sources WHERE company_key=?", (company_key(company),)).fetchone()
    return dict(row) if row else None


def set_company_source(conn, company: str, source: str, detected_via: str, board: str | None = None,
                       host: str | None = None, site: str | None = None, careers_url: str | None = None) -> None:
    conn.execute(
        "INSERT INTO company_sources (company_key, company, source, board, host, site, careers_url, detected_via, "
        "detected_at, fail_count) VALUES (?,?,?,?,?,?,?,?,?,0) ON CONFLICT(company_key) DO UPDATE SET "
        "company=excluded.company, source=excluded.source, board=excluded.board, host=excluded.host, "
        "site=excluded.site, careers_url=excluded.careers_url, detected_via=excluded.detected_via, "
        "detected_at=excluded.detected_at, fail_count=0",
        (company_key(company), company, source, board, host, site, careers_url, detected_via, utcnow()))


def bump_company_source_failure(conn, company: str) -> int:
    conn.execute("UPDATE company_sources SET fail_count = fail_count + 1 WHERE company_key=?", (company_key(company),))
    row = conn.execute("SELECT fail_count FROM company_sources WHERE company_key=?", (company_key(company),)).fetchone()
    return row[0] if row else 0


def drop_company_source(conn, company: str) -> None:
    conn.execute("DELETE FROM company_sources WHERE company_key=?", (company_key(company),))


# --- page cache (sitemap/JSON-LD pages) ---------------------------------------------------------------
def get_page(conn, url: str) -> dict | None:
    row = conn.execute("SELECT * FROM page_cache WHERE url=?", (url,)).fetchone()
    return dict(row) if row else None


def set_page(conn, url: str, lastmod: str | None, payload: str | None) -> None:
    conn.execute("INSERT INTO page_cache (url, lastmod, fetched_at, payload) VALUES (?,?,?,?) ON CONFLICT(url) "
                 "DO UPDATE SET lastmod=excluded.lastmod, fetched_at=excluded.fetched_at, payload=excluded.payload",
                 (url, lastmod, utcnow(), payload))


# --- dead-link checks -------------------------------------------------------------------------------
def get_link_check(conn, url: str) -> dict | None:
    row = conn.execute("SELECT * FROM link_checks WHERE url=?", (url,)).fetchone()
    return dict(row) if row else None


def set_link_check(conn, url: str, http_status: int | None, ok: bool | None, final_url: str | None,
                   note: str | None) -> None:
    conn.execute("INSERT INTO link_checks (url, checked_at, http_status, ok, final_url, note) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(url) DO UPDATE SET checked_at=excluded.checked_at, http_status=excluded.http_status, "
                 "ok=excluded.ok, final_url=excluded.final_url, note=excluded.note",
                 (url, utcnow(), http_status, None if ok is None else int(ok), final_url, note))


def get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


def update_extracted(conn, pk: int, rec: "JobRecord") -> None:
    """Overwrite derived fields (re-extraction); identity, status and history are untouched."""
    fields = [f for f in _EXTRACTED if f not in ("jd_path", "jd_hash", "source_url", "apply_url", "posted_date",
                                                 "source_official", "canonical_key")]
    conn.execute(f"UPDATE jobs SET {','.join(f'{f}=?' for f in fields)} WHERE id=?",
                 [*(_enc(f, getattr(rec, f)) for f in fields), pk])


def record_resume_version(conn, hash_: str, path: str) -> bool:
    """Returns True if this resume version is new."""
    cur = conn.execute("INSERT OR IGNORE INTO resume_versions (hash, path, first_parsed) VALUES (?,?,?)",
                       (hash_, path, utcnow()))
    return cur.rowcount == 1


def close_interrupted_runs(conn) -> int:
    """Runs that never finished (process killed, machine slept / crashed) get finished_at = started_at and no
    summary, so nothing shows them as still running and nothing mistakes them for a completed run (the Run Log and the
    last-run lookups only read runs with a summary). Called by the next run, under the run lock."""
    with conn:
        cur = conn.execute("UPDATE runs SET finished_at = started_at WHERE finished_at IS NULL")
    return cur.rowcount


def start_run(conn, dry_run: bool) -> int:
    with conn:
        return conn.execute("INSERT INTO runs (started_at, dry_run) VALUES (?, ?)", (utcnow(), int(dry_run))).lastrowid


def finish_run(conn, run_id: int, summary: dict) -> None:
    with conn:
        conn.execute("UPDATE runs SET finished_at=?, summary=? WHERE id=?", (utcnow(), json.dumps(summary), run_id))


def recent_job_counts(conn, company: str, source: str, runs: int = 5) -> list[int]:
    """Job counts for (company, source) from recent run summaries (for source-health checks)."""
    out = []
    for (summary,) in conn.execute("SELECT summary FROM runs WHERE summary IS NOT NULL AND dry_run=0 "
                                   "ORDER BY id DESC LIMIT ?", (runs,)):
        for o in (json.loads(summary).get("per_source") or []):
            if o.get("company") == company and o.get("source") == source and o.get("ok"):
                out.append(o.get("jobs", 0))
    return out


def open_jobs(conn) -> list[dict]:
    return [decode_row(r) for r in conn.execute("SELECT * FROM jobs WHERE job_status != 'closed'")]


def labeled_jobs(conn) -> list[dict]:
    """Every job (open or closed) you gave a status, for feedback learning."""
    return [decode_row(r) for r in conn.execute("SELECT * FROM jobs WHERE user_status IS NOT NULL")]


def recent_runs(conn, n: int = 14) -> list[dict]:
    out = []
    for r in conn.execute("SELECT id, started_at, finished_at, summary FROM runs WHERE dry_run = 0 AND summary IS NOT "
                          "NULL ORDER BY id DESC LIMIT ?", (n,)):
        s = json.loads(r["summary"])
        out.append({"id": r["id"], "started_at": r["started_at"], "finished_at": r["finished_at"],
                    **{k: s.get(k) for k in ("scope", "sources_checked", "sources_ok", "sources_failed",
                                             "sources_manual", "sources_partial", "jobs_found", "new", "updated",
                                             "closed", "strong", "scored", "filtered")}})
    return out


def merge_canonical_duplicates(conn, key_of) -> int:
    """Recompute canonical keys (key_of(url) -> key) for open jobs and merge jobs that turn out to be the same
    posting: their source listings move to one job (the one you acted on, else the oldest). Returns jobs merged."""
    groups: dict[str, list] = {}
    for r in conn.execute("SELECT id, apply_url, source_url, canonical_key, user_status_at FROM jobs WHERE job_status != 'closed'"):
        k = key_of(r["apply_url"]) or key_of(r["source_url"]) or r["canonical_key"]
        if k and k != r["canonical_key"]:
            conn.execute("UPDATE jobs SET canonical_key=? WHERE id=?", (k, r["id"]))
        if k:
            groups.setdefault(k, []).append(r)
    merged = 0
    for k, rows in groups.items():
        if len(rows) < 2:
            continue
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))
        has = lambda table: {x[0] for x in conn.execute(  # noqa: E731
            f"SELECT DISTINCT job_pk FROM {table} WHERE job_pk IN ({marks})", ids)}
        acted, built, bucketed = has("applications"), has("resume_status"), has("bucket")
        # the job you applied to, else the one with a resume, in the Bucket or with a status, else the oldest
        keep = sorted(rows, key=lambda r: (r["id"] not in acted, r["id"] not in built, r["id"] not in bucketed,
                                           r["user_status_at"] is None, r["id"]))[0]["id"]
        for r in rows:
            if r["id"] == keep:
                continue
            old = r["id"]
            conn.execute("UPDATE job_sources SET job_pk=?, canonical_key=? WHERE job_pk=?", (keep, k, old))
            conn.execute("UPDATE applications SET job_pk=? WHERE job_pk=?", (keep, old))
            conn.execute("UPDATE resume_queue SET job_pk=? WHERE job_pk=?", (keep, old))
            for table in ("bucket", "resume_questions"):     # the kept job's own rows win
                conn.execute(f"UPDATE OR IGNORE {table} SET job_pk=? WHERE job_pk=?", (keep, old))
                conn.execute(f"DELETE FROM {table} WHERE job_pk=?", (old,))
            if old in built and keep not in built:            # (keep has a resume whenever any of them does)
                for table in ("resume_status", "resume_builds"):
                    conn.execute(f"UPDATE {table} SET job_pk=? WHERE job_pk=?", (keep, old))
            conn.execute("DELETE FROM jobs WHERE id=?", (old,))
            merged += 1
    conn.commit()
    return merged


def touch_source_id(conn, source: str, source_job_id: str, now: str | None = None) -> None:
    """touch_job for a multi-company feed listing known only by its id."""
    for (ck,) in conn.execute("SELECT company_key FROM job_sources WHERE source=? AND source_job_id=?",
                              (source, source_job_id)).fetchall():
        touch_job(conn, source, ck, source_job_id, now)


def repair_moved_paths(conn, jd_dir: Path, apps_dir: Path) -> int:
    """Point stored JD / application-JD paths at their current location after the repo or data folder moved or was
    reorganized (the stored absolute path is gone; the same file name exists under the folder or one of its
    subfolders, e.g. jd/<Company>/). Returns rows fixed."""
    fixed = 0
    for table, base in (("jobs", jd_dir), ("applications", apps_dir)):
        rows = conn.execute(f"SELECT rowid, jd_path FROM {table} WHERE jd_path IS NOT NULL").fetchall()
        missing = [(rowid, p) for rowid, p in rows if not Path(p).exists()]
        if not missing or not base.exists():
            continue
        index = {f.name: f for f in base.rglob("*.txt")}
        updates = [(str(index[Path(p).name]), rowid) for rowid, p in missing if Path(p).name in index]
        if updates:
            with conn:
                conn.executemany(f"UPDATE {table} SET jd_path=? WHERE rowid=?", updates)
            fixed += len(updates)
    return fixed
