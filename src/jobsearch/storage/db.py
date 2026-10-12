"""SQLite storage with versioned migrations, dedupe, update and closed-job detection.

Presence per source lives in `job_sources`; a `jobs` row is one real-world posting that may be
listed by several sources. A job is closed when every source listing it has missed it on
CLOSE_AFTER_MISSES consecutive *successful* fetches (a failed fetch never closes anything).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def company_key(name: str) -> str:
    return " ".join(name.casefold().split())


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
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


_JSON_FIELDS = {"min_reqs", "pref_reqs", "skills", "grad_years", "flags", "extra",
                "breakdown", "gaps", "filter_reasons"}
_EXTRACTED = [f for f in JobRecord.__dataclass_fields__ if f not in ("company", "job_id", "source")]


def _enc(field: str, value):
    return json.dumps(value) if field in _JSON_FIELDS and value is not None else value


def decode_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in _JSON_FIELDS & d.keys():
        if d[f] is not None:
            d[f] = json.loads(d[f])
    return d


def _location_key(loc: str | None) -> str:
    return " ".join((loc or "").casefold().replace(",", " ").split())


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
        same_loc = not loc or not r["location"] or _location_key(r["location"]) == loc \
            or fuzz.token_set_ratio(_location_key(r["location"]), loc) >= 90
        if same_loc and fuzz.token_sort_ratio(r["normalized_title"], rec.normalized_title) >= FUZZY_TITLE_THRESHOLD:
            return r["id"]
    return None


def upsert_job(conn: sqlite3.Connection, rec: JobRecord, now: str | None = None) -> tuple[int, str]:
    """Insert or update one job. Returns (job pk, 'new' | 'updated' | 'seen')."""
    now = now or utcnow()
    ck = company_key(rec.company)
    row = conn.execute(
        "SELECT job_pk FROM job_sources WHERE source=? AND company_key=? AND source_job_id=?",
        (rec.source, ck, rec.job_id),
    ).fetchone()
    pk = row["job_pk"] if row else find_fuzzy(conn, rec)

    if pk is None:
        cols = ["company", "company_key", "job_id", "source", "first_seen", "last_checked",
                "job_status", "status_changed_at", *_EXTRACTED]
        vals = [rec.company, ck, rec.job_id, rec.source, now, now, "new", now,
                *(_enc(f, getattr(rec, f)) for f in _EXTRACTED)]
        cur = conn.execute(f"INSERT INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        pk = cur.lastrowid
        conn.execute(
            "INSERT INTO job_sources (job_pk, source, company_key, source_job_id, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
            (pk, rec.source, ck, rec.job_id, now, now),
        )
        return pk, "new"

    existing = conn.execute("SELECT * FROM jobs WHERE id=?", (pk,)).fetchone()
    if row is None:  # fuzzy cross-source match: attach this source, keep the primary record
        conn.execute(
            "INSERT INTO job_sources (job_pk, source, company_key, source_job_id, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
            (pk, rec.source, ck, rec.job_id, now, now),
        )
        conn.execute("UPDATE jobs SET last_checked=? WHERE id=?", (now, pk))
        return pk, "seen"

    conn.execute(
        "UPDATE job_sources SET last_seen=?, miss_count=0 WHERE source=? AND company_key=? AND source_job_id=?",
        (now, rec.source, ck, rec.job_id),
    )
    changed = existing["jd_hash"] != rec.jd_hash
    reopened = existing["job_status"] == "closed"
    if existing["source"] == rec.source:
        sets = {f: _enc(f, getattr(rec, f)) for f in _EXTRACTED}
    else:  # a secondary source never overwrites the primary's data
        sets = {}
    sets["last_checked"] = now
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


def mark_missing(conn: sqlite3.Connection, source: str, company: str, seen_ids: set[str], now: str | None = None) -> int:
    """After a *successful* fetch: bump misses for unseen listings and close jobs missing everywhere.

    Returns the number of jobs newly closed.
    """
    now = now or utcnow()
    ck = company_key(company)
    rows = conn.execute(
        "SELECT job_pk, source_job_id FROM job_sources WHERE source=? AND company_key=?", (source, ck)
    ).fetchall()
    missing = [r for r in rows if r["source_job_id"] not in seen_ids]
    for r in missing:
        conn.execute(
            "UPDATE job_sources SET miss_count = miss_count + 1 WHERE source=? AND company_key=? AND source_job_id=?",
            (source, ck, r["source_job_id"]),
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


def save_scores(conn: sqlite3.Connection, pk: int, **fields) -> None:
    conn.execute(
        f"UPDATE jobs SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
        [*(_enc(k, v) for k, v in fields.items()), pk],
    )


def set_source_status(conn, company: str, source: str, status: str, error: str | None, job_count: int | None,
                      now: str | None = None) -> None:
    conn.execute(
        "INSERT INTO source_status (company_key, company, source, last_checked, status, error, job_count) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(company_key, source) DO UPDATE SET company=excluded.company, "
        "last_checked=excluded.last_checked, status=excluded.status, error=excluded.error, job_count=excluded.job_count",
        (company_key(company), company, source, now or utcnow(), status, error, job_count),
    )


def record_resume_version(conn, hash_: str, path: str) -> bool:
    """Returns True if this resume version is new."""
    cur = conn.execute("INSERT OR IGNORE INTO resume_versions (hash, path, first_parsed) VALUES (?,?,?)",
                       (hash_, path, utcnow()))
    return cur.rowcount == 1


def start_run(conn, dry_run: bool) -> int:
    with conn:
        return conn.execute("INSERT INTO runs (started_at, dry_run) VALUES (?, ?)", (utcnow(), int(dry_run))).lastrowid


def finish_run(conn, run_id: int, summary: dict) -> None:
    with conn:
        conn.execute("UPDATE runs SET finished_at=?, summary=? WHERE id=?", (utcnow(), json.dumps(summary), run_id))


def open_jobs(conn) -> list[dict]:
    return [decode_row(r) for r in conn.execute("SELECT * FROM jobs WHERE job_status != 'closed'")]
