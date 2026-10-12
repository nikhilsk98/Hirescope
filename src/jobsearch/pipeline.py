"""Fetch -> extract -> store -> filter/score. Each source is isolated: one failure never stops the run."""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .adapters.base import HttpClient, RawJob
from .adapters.registry import get_adapter
from .config import Company, Config
from .extract.flags import extract_flags
from .extract.normalize import (clean_location, employment_type_from_title, normalize_title, parse_salary, seniority,
                                work_mode)
from .extract.requirements import parse_degree, parse_grad_years, parse_yoe, split_requirements
from .extract.resume import Resume
from .extract.skills import extract_skills
from .match.rules import apply_rules
from .match.score import score_job
from .match.semantic import Embedder, jd_chunks, semantic_match
from .storage import db

log = logging.getLogger(__name__)


@dataclass
class SourceOutcome:
    company: str
    source: str
    ok: bool
    jobs: int = 0
    new: int = 0
    updated: int = 0
    closed: int = 0
    error: str | None = None


@dataclass
class RunSummary:
    outcomes: list[SourceOutcome] = field(default_factory=list)
    scored: int = 0
    filtered: int = 0
    strong: int = 0
    new_strong: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        ok = [o for o in self.outcomes if o.ok]
        failed = [o for o in self.outcomes if not o.ok]
        return {
            "sources_checked": len(self.outcomes),
            "sources_ok": len(ok),
            "sources_failed": len(failed),
            "failures": [{"company": o.company, "source": o.source, "error": o.error} for o in failed],
            "jobs_found": sum(o.jobs for o in ok),
            "new": sum(o.new for o in ok),
            "updated": sum(o.updated for o in ok),
            "closed": sum(o.closed for o in ok),
            "scored": self.scored,
            "filtered": self.filtered,
            "strong": self.strong,
            "new_strong": self.new_strong,
            "complete": not failed,
        }


def jd_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:20]


def to_record(raw: RawJob, cfg: Config, jd_dir: Path | None) -> db.JobRecord:
    rules = cfg.title_rules
    norm = normalize_title(raw.title, rules)
    text = raw.description or ""
    reqs = split_requirements(text)
    yoe = parse_yoe(reqs.minimum or text)
    sal = parse_salary(text)
    h = jd_hash(text)
    jd_path = None
    if jd_dir is not None:
        jd_dir.mkdir(parents=True, exist_ok=True)
        p = jd_dir / f"{raw.source}_{db.company_key(raw.company).replace(' ', '-')}_{raw.job_id}.txt"
        p.write_text(text, encoding="utf-8")
        jd_path = str(p)
    return db.JobRecord(
        company=raw.company, title=raw.title, normalized_title=norm, job_id=raw.job_id, source=raw.source,
        source_url=raw.source_url, apply_url=raw.apply_url, location=clean_location(raw.location),
        work_mode=work_mode(raw.location, text),
        employment_type=raw.employment_type or employment_type_from_title(norm, rules),
        seniority=seniority(norm, rules),
        posted_date=raw.posted_date.isoformat() if raw.posted_date else None,
        salary_min=sal.min, salary_max=sal.max, salary_currency=sal.currency,
        jd_path=jd_path, jd_hash=h,
        min_reqs=reqs.minimum, pref_reqs=reqs.preferred,
        skills=extract_skills(text, cfg.synonyms),
        yoe_required=yoe.min, yoe_max=yoe.max, yoe_evidence=yoe.evidence,
        degree=parse_degree("\n".join(reqs.minimum) or text),
        grad_years=parse_grad_years(f"{raw.title}\n{text}"),
        flags=extract_flags(text),
        extra={**raw.extra, **({"yoe_preferred": y.min, "yoe_preferred_evidence": y.evidence}
                               if (y := parse_yoe(reqs.preferred)).min is not None else {})},
    )


def ingest_company(conn: sqlite3.Connection, client: HttpClient, company: Company, cfg: Config,
                   jd_dir: Path | None) -> SourceOutcome:
    out = SourceOutcome(company.name, company.source, ok=False)
    try:
        adapter = get_adapter(company.source, client)
        raws = adapter.fetch(company)
    except Exception as e:  # noqa: BLE001 - isolate every source failure
        out.error = f"{type(e).__name__}: {e}"
        log.error("source failed: %s/%s: %s", company.source, company.name, out.error)
        db.set_source_status(conn, company.name, company.source, "failed", out.error, None)
        return out

    now = db.utcnow()
    seen: set[str] = set()
    for raw in raws:
        try:
            rec = to_record(raw, cfg, jd_dir)
            _, status = db.upsert_job(conn, rec, now)
        except Exception as e:  # noqa: BLE001 - one bad posting must not sink the company
            log.warning("skipping %s/%s job %s: %s", company.source, company.name, raw.job_id, e)
            continue
        seen.add(raw.job_id)
        out.new += status == "new"
        out.updated += status == "updated"
    out.closed = db.mark_missing(conn, company.source, company.name, seen, now)
    out.ok, out.jobs = True, len(seen)
    db.set_source_status(conn, company.name, company.source, "ok", None, len(seen), now)
    log.info("source ok: %s/%s: %d jobs (%d new, %d updated, %d closed)",
             company.source, company.name, out.jobs, out.new, out.updated, out.closed)
    return out


def score_all(conn: sqlite3.Connection, cfg: Config, resume: Resume, embedder: Embedder,
              summary: RunSummary) -> None:
    by_name = {db.company_key(c.name): c for c in cfg.companies}
    resume_lines = {s: resume.bullets(s) for s in ("experience", "projects", "coursework")}
    for job in db.open_jobs(conn):
        rules = apply_rules(job, cfg.prefs, cfg.title_rules)
        if rules.hidden:
            db.save_scores(conn, job["id"], filtered=1, filter_reasons=rules.reasons, score=None,
                           qual_score=None, pref_score=None, label=None, breakdown=None,
                           explanation=None, gaps=None)
            summary.filtered += 1
            continue
        sem = semantic_match(embedder, resume_lines, jd_chunks(
            Path(job["jd_path"]).read_text(encoding="utf-8") if job.get("jd_path") and Path(job["jd_path"]).exists() else "",
            job.get("min_reqs") or [], job.get("pref_reqs") or []))
        res = score_job(job, resume, cfg.prefs, rules, sem, cfg.synonyms, by_name.get(job["company_key"]))
        db.save_scores(conn, job["id"], filtered=0, filter_reasons=rules.soft_issues, score=res.overall,
                       qual_score=res.qualification, pref_score=res.preference, label=res.label,
                       breakdown=res.breakdown, explanation=res.explanation, gaps=res.gaps)
        summary.scored += 1
        if res.label == "Strong":
            summary.strong += 1
            if job["job_status"] == "new":
                summary.new_strong.append(f"{job['company']}: {job['title']}")


def run(conn: sqlite3.Connection, cfg: Config, resume: Resume, companies: list[Company],
        client: HttpClient, embedder: Embedder, jd_dir: Path | None = paths.JD_DIR) -> RunSummary:
    summary = RunSummary()
    for company in companies:
        summary.outcomes.append(ingest_company(conn, client, company, cfg, jd_dir))
        conn.commit()
    score_all(conn, cfg, resume, embedder, summary)
    return summary
