"""Questions for you: JD keywords a build could not use because your evidence doesn't confirm them (rule 61).

A queue build (`/resume-builder <id> --queue`, no one at the keyboard) never waits for an answer: `prepare --queue`
saves the brief's "Ask Nikhil" questions here (SQLite resume_questions, one per job and keyword), the build goes on
without those keywords, and the result is "Ready (questions pending)". Answer them on the dashboard (Questions for
you) or with `python -m resume_builder questions`; an answer is saved exactly like add-evidence / add-tool / decline
(only what you explicitly confirm) and covers that keyword in every job that asked. Then Regenerate the jobs that
asked: the new version can use the answer.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from jobsearch import paths as hs_paths
from jobsearch.storage import db

from .config import InputError
from .evidence import canonical

ANSWERS = ("yes", "no")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(keyword: str) -> str:
    return canonical(keyword).casefold()


def record(job_pk: int, qs: list[dict], db_path=None) -> int:
    """Save a prepare's keyword questions for the job (already-answered keywords are skipped). Returns how many are
    open for the job now."""
    from . import keywords
    answered = keywords.answers()
    conn = db.connect(db_path or hs_paths.DB)
    try:
        with conn:
            for q in qs:
                kw = str(q.get("keyword") or "").strip()
                if not kw or _norm(kw) in answered:
                    continue
                conn.execute(
                    "INSERT INTO resume_questions (job_pk, kind, keyword, question, options, asked_at) VALUES "
                    "(?,?,?,?,?,?) ON CONFLICT(job_pk, keyword) DO UPDATE SET question=excluded.question, "
                    "options=excluded.options, kind=excluded.kind WHERE resume_questions.answered_at IS NULL",
                    (int(job_pk), str(q.get("kind") or "keyword")[:40], kw[:120], str(q.get("question") or "")[:1000],
                     json.dumps(q.get("options") or []), _now()))
        return open_count(conn, job_pk)
    finally:
        conn.close()


def mark_answered(keyword: str, answer: str, db_path=None) -> int:
    """A keyword answered anywhere (dashboard, add-evidence, add-tool, decline, an interactive session) closes its
    question in every job. Returns how many questions it closed."""
    try:
        conn = db.connect(db_path or hs_paths.DB)
    except (OSError, sqlite3.Error):
        return 0
    try:
        n = 0
        with conn:
            for r in conn.execute("SELECT id, keyword FROM resume_questions WHERE answered_at IS NULL").fetchall():
                if _norm(r["keyword"]) == _norm(keyword):
                    conn.execute("UPDATE resume_questions SET answered_at=?, answer=? WHERE id=?",
                                 (_now(), answer, r["id"]))
                    n += 1
        return n
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def open_count(conn, job_pk: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM resume_questions WHERE job_pk=? AND answered_at IS NULL",
                        (int(job_pk),)).fetchone()[0]


def open_counts(conn) -> dict[int, int]:
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT job_pk, COUNT(*) FROM resume_questions WHERE answered_at IS NULL GROUP BY job_pk")}
    except sqlite3.OperationalError:          # an old database before migration 9
        return {}


def dashboard_list(conn) -> list[dict]:
    """Open questions grouped by keyword (one answer covers every job that asked), plus recently answered keywords
    whose jobs may want a Regenerate."""
    try:
        rows = conn.execute("SELECT q.*, j.company, j.title FROM resume_questions q LEFT JOIN jobs j ON j.id = q.job_pk "
                            "ORDER BY q.asked_at DESC, q.id DESC").fetchall()
    except sqlite3.OperationalError:
        return []
    groups: dict[str, dict] = {}
    for r in rows:
        k = _norm(r["keyword"])
        g = groups.setdefault(k, {"keyword": r["keyword"], "kind": r["kind"], "question": r["question"],
                                  "options": json.loads(r["options"] or "[]"), "ids": [], "jobs": [], "open": False,
                                  "answer": None, "answered_at": None, "asked_at": r["asked_at"]})
        g["ids"].append(r["id"])
        g["jobs"].append({"job_pk": r["job_pk"], "company": r["company"] or "", "title": r["title"] or "",
                          "open": r["answered_at"] is None})
        if r["answered_at"] is None:
            g["open"] = True
        elif not g["answered_at"] or r["answered_at"] > g["answered_at"]:
            g["answer"], g["answered_at"] = r["answer"], r["answered_at"]
    return sorted(groups.values(), key=lambda g: (not g["open"], g["asked_at"]), reverse=False)


def answer_entries() -> list[dict]:
    """Roles and projects an answer can belong to (the dashboard's "Where" list): ids from your master resume."""
    try:
        from .evidence import load_bank
        bank = load_bank()
    except Exception:  # noqa: BLE001 — inputs not readable here: the dashboard falls back to "skills"
        return [{"id": "skills", "label": "Skills (no specific role)"}]
    out = [{"id": eid, "label": (e.heading or e.organization or e.role or eid)[:90]} for eid, e in bank.entries.items()]
    return out + [{"id": "skills", "label": "Skills (no specific role)"}]


def get(conn, qid: int):
    return conn.execute("SELECT * FROM resume_questions WHERE id=?", (int(qid),)).fetchone()


def answer(qid: int, yes: bool, entry: str | None = None, level: str | None = None, text: str | None = None,
           where: str | None = None, db_path=None) -> dict:
    """Save your answer to one question (it covers the keyword everywhere). yes → add-evidence (a tool → the tool
    checklist), no → decline. You confirmed it by submitting it; nothing is saved without that."""
    from . import interview
    from .evidence import load_bank
    conn = db.connect(db_path or hs_paths.DB)
    try:
        q = get(conn, qid)
        if not q:
            raise InputError(f"no question {qid}")
        jobs = [r[0] for r in conn.execute("SELECT DISTINCT job_pk FROM resume_questions WHERE keyword=?",
                                           (q["keyword"],))]
    finally:
        conn.close()
    kw = q["keyword"]
    if yes:
        if not entry:
            raise InputError("say where you used it (a role or project, or skills)")
        p = interview.save_answer(entry, q["question"], text or "", True, load_bank(), keyword=kw, level=level,
                                  where=where)
        mark_answered(kw, "yes", db_path)
        return {"keyword": kw, "answer": "yes", "saved_to": str(p), "jobs": jobs}
    from . import keywords
    keywords.record_answer(kw, "no")
    p = interview.save_answer("skills", f"Have you used {kw}?", "no", True, load_bank(), keyword=kw, where="—")
    mark_answered(kw, "no", db_path)
    return {"keyword": kw, "answer": "no", "saved_to": str(p), "jobs": jobs}
