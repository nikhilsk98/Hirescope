"""Plan -> fetch (parallel) -> extract -> store -> filter/score.

Each company/source is isolated: one failure never stops the run. Fetches run in a thread pool;
all database writes happen on the calling thread as results arrive.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import paths
from .adapters.base import FetchContext, FetchResult, HttpClient, RawJob
from .adapters.detect import Detector
from .adapters.registry import ADAPTERS, get_adapter
from .config import Company, Config
from .extract.dates import experience_restriction, parse_deadline, parse_grad_windows, parse_start_windows, summer_only
from .extract.flags import extract_flags
from .extract.levels import map_level
from .extract.locations import normalize_location
from .extract.normalize import employment_type_from_title, normalize_title, parse_salary, work_mode
from .extract.requirements import parse_degree, parse_grad_years, parse_yoe, split_requirements
from .extract.resume import Resume
from .extract.skills import extract_skills
from .extract.text import normalize_bullets
from .extract.track import classify_track, normalize_employment_type, parse_terms
from .match.rules import apply_rules, quick_relevant
from .match.score import score_job
from .match.semantic import Embedder, jd_chunks, semantic_match
from .storage import db
from .watchlist import WatchItem

log = logging.getLogger(__name__)

# Bump when extraction logic changes; stored jobs are then re-extracted from their saved title/location/JD.
EXTRACT_VERSION = "2026-10-12.8"   # .8: keyword implications, title rule additions
EXTRACT_CONFIGS = ("title_rules.yaml", "levels.yaml", "locations.yaml", "skills_synonyms.yaml", "ats_keywords.yaml")
TIER_INTERVAL_HOURS = {1: 0, 2: 20, 3: 70}       # tier 1 every run, tier 2 daily, tier 3 every 3 days
TIER_MAX_PAGES = {1: 100, 2: 25, 3: 10}
DIRECTORY_KEY = "ats_directory:simplify"
WORKERS = 16
# Each source family gets its own worker pool so a slow shared host (e.g. api.smartrecruiters.com, where
# every request waits for the per-host rate limiter) can never hold every worker (head-of-line blocking).
# Workday tenants each have their own host, so that family gets the most workers.
POOL_SIZES = {"greenhouse": 3, "lever": 2, "ashby": 2, "smartrecruiters": 2, "workday": 6, "amazon": 1,
              "netflix": 1, "simplify": 2, "jsonld": 3, "jsonld_sitemap": 1}


@dataclass
class SourceOutcome:
    company: str
    source: str
    ok: bool
    status: str = "failed"          # ok | failed | manual | skipped
    origin: str = "seed"            # seed | my_companies | feed
    tier: int | None = None
    jobs: int = 0
    new: int = 0
    updated: int = 0
    closed: int = 0
    unchanged: bool = False
    partial: str | None = None
    detected_via: str | None = None
    url: str | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class RunSummary:
    outcomes: list[SourceOutcome] = field(default_factory=list)
    skipped: list[SourceOutcome] = field(default_factory=list)
    scored: int = 0
    filtered: int = 0
    strong: int = 0
    rescued: int = 0
    new_strong: list[str] = field(default_factory=list)
    by_track: dict[str, dict] = field(default_factory=dict)

    def as_dict(self) -> dict:
        ok = [o for o in self.outcomes if o.ok]
        failed = [o for o in self.outcomes if o.status == "failed"]
        manual = [o for o in self.outcomes if o.status == "manual"]
        partial = [o for o in ok if o.partial]
        return {
            "sources_checked": len(self.outcomes),
            "sources_ok": len(ok),
            "sources_failed": len(failed),
            "sources_manual": len(manual),
            "sources_partial": len(partial),
            "sources_skipped": len(self.skipped),
            "failures": [{"company": o.company, "source": o.source, "error": o.error, "origin": o.origin}
                         for o in failed],
            "manual": [{"company": o.company, "url": o.url, "error": o.error} for o in manual],
            "partial": [{"company": o.company, "source": o.source, "why": o.partial} for o in partial],
            "jobs_found": sum(o.jobs for o in ok),
            "new": sum(o.new for o in ok),
            "updated": sum(o.updated for o in ok),
            "closed": sum(o.closed for o in ok),
            "scored": self.scored,
            "filtered": self.filtered,
            "strong": self.strong,
            "rescued_by_meaning": self.rescued,
            "new_strong": self.new_strong,
            "by_track": self.by_track,
            "complete": not failed and not manual,
            "per_source": [{k: v for k, v in asdict(o).items() if k != "notes"} for o in self.outcomes],
        }


def jd_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:20]


def to_record(raw: RawJob, cfg: Config, jd_dir: Path | None, official: bool = True) -> db.JobRecord:
    rules = cfg.title_rules
    norm = normalize_title(raw.title, rules)
    text = normalize_bullets(raw.description or "")
    reqs = split_requirements(text)
    yoe = parse_yoe(reqs.minimum or text)
    sal = parse_salary(text)
    h = jd_hash(text)
    jd_path = None
    if jd_dir is not None and text:
        jd_dir.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw.job_id)[:80]
        p = jd_dir / f"{raw.source}_{db.company_key(raw.company).replace(' ', '-').replace('/', '-')}_{safe_id}.txt"
        p.write_text(text, encoding="utf-8")
        jd_path = str(p)
    loc = normalize_location(raw.location)
    et = normalize_employment_type(raw.employment_type) or employment_type_from_title(norm, rules)
    track = classify_track(raw.title, raw.employment_type, text, raw.track_hint)
    level = map_level(norm, raw.company, yoe.min)
    terms = list(dict.fromkeys([*raw.terms, *parse_terms(f"{raw.title}\n{text[:4000]}")]))
    deadline, deadline_ev = parse_deadline(text, raw.extra.get("deadline"))
    y = parse_yoe(reqs.preferred)
    return db.JobRecord(
        company=raw.company, title=raw.title, normalized_title=norm, job_id=raw.job_id, source=raw.source,
        source_url=raw.source_url, apply_url=raw.apply_url, location=loc.display if loc.places else None,
        work_mode=work_mode(raw.location, text),
        employment_type=et,
        seniority=level.level,
        posted_date=raw.posted_date.isoformat() if raw.posted_date else None,
        salary_min=sal.min, salary_max=sal.max, salary_currency=sal.currency,
        jd_path=jd_path, jd_hash=h,
        min_reqs=reqs.minimum, pref_reqs=reqs.preferred,
        skills=extract_skills(text, cfg.synonyms),
        yoe_required=yoe.min, yoe_max=yoe.max, yoe_evidence=yoe.evidence,
        degree=parse_degree("\n".join(reqs.minimum) or text),
        grad_years=parse_grad_years(f"{raw.title}\n{text}"),
        flags=extract_flags(text),
        extra={**raw.extra, **({"yoe_preferred": y.min, "yoe_preferred_evidence": y.evidence} if y.min is not None
                               else {}), **({"has_jd": False} if not text else {}),
               **({"employment_type_raw": raw.employment_type} if raw.employment_type else {})},
        track=track.track, track_evidence=track.evidence, track_confidence=track.confidence,
        level=level.level, level_evidence=level.evidence, terms=terms,
        canonical_key=raw.canonical_key, location_us=loc.us, remote_us=loc.remote_us,
        source_official=official,
        grad_windows=[w.as_dict() for w in parse_grad_windows(f"{raw.title}\n{text}")],
        start_windows=[w.as_dict() for w in parse_start_windows(text, terms)],
        summer_only=track.track == "coop_intern" and summer_only(terms, raw.title, text),
        eligibility_note=experience_restriction(text),
        deadline=deadline, deadline_evidence=deadline_ev,
    )


def extraction_fingerprint() -> str:
    h = hashlib.sha256(EXTRACT_VERSION.encode())
    for name in EXTRACT_CONFIGS:
        p = paths.CONFIG / name
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def reextract_all(conn, cfg: Config) -> int:
    """Recompute derived fields (track, level, location flags, terms, skills, requirements, flags) for every open
    job from what is stored, so parser/config fixes apply without re-downloading anything."""
    n = 0
    for job in db.open_jobs(conn):
        jd = ""
        if job.get("jd_path") and Path(job["jd_path"]).exists():
            jd = Path(job["jd_path"]).read_text(encoding="utf-8")
        hint = {"simplify_internships": "coop_intern", "simplify_newgrad": "full_time"}.get(job["source"])
        raw = RawJob(company=job["company"], job_id=job["job_id"], title=job["title"], source=job["source"],
                     source_url=job.get("source_url") or "", apply_url=job.get("apply_url"),
                     location=job.get("location"), description=jd,
                     employment_type=(job.get("extra") or {}).get("employment_type_raw") or job.get("employment_type"),
                     canonical_key=job.get("canonical_key"),
                     track_hint=hint, terms=[t for t in (job.get("terms") or []) if t],
                     extra={k: v for k, v in (job.get("extra") or {}).items() if k != "has_jd"})
        rec = to_record(raw, cfg, None, bool(job.get("source_official", 1)))
        db.update_extracted(conn, job["id"], rec)
        n += 1
    conn.commit()
    return n


def ensure_extraction_current(conn, cfg: Config, force: bool = False) -> int:
    fp = extraction_fingerprint()
    if not force and db.get_meta(conn, "extraction") == fp:
        return 0
    n = reextract_all(conn, cfg)
    if db.get_meta(conn, "extract_version") != EXTRACT_VERSION:
        # extraction code changed: let the next fetch re-download (no 304s) so values only the source has
        # (raw employment types etc.) are re-read too
        conn.execute("DELETE FROM http_cache")
        db.set_meta(conn, "extract_version", EXTRACT_VERSION)
    db.set_meta(conn, "extraction", fp)
    conn.commit()
    log.info("re-extracted %d stored jobs (extraction version/config changed)", n)
    return n


# --- planning ------------------------------------------------------------------------------------
@dataclass
class Planned:
    company: Company | None        # None -> needs detection (My Companies)
    watch: WatchItem | None = None
    reason: str = ""


def tier_of(company: Company, cfg: Config) -> int:
    return cfg.prefs.company_tiers.tier_of(company.name) or company.tier


def due(conn, company: Company, label: str, tier: int, now: datetime) -> bool:
    hours = TIER_INTERVAL_HOURS.get(tier, 70)
    if hours == 0:
        return True
    last = db.last_ok(conn, company.name, label)
    return not last or now - datetime.fromisoformat(last) >= timedelta(hours=hours)


def plan(conn, cfg: Config, watch: list[WatchItem], *, only_company: str | None = None,
         only_source: str | None = None, force_all: bool = False, now: datetime | None = None
         ) -> tuple[list[Planned], list[SourceOutcome]]:
    now = now or datetime.now(timezone.utc)
    seeds = [c for c in cfg.companies if not cfg.prefs.company_tiers.is_excluded(c.name)]
    watch_keys = {w.company.casefold() for w in watch}
    planned: list[Planned] = []
    skipped: list[SourceOutcome] = []
    if only_company:
        key = only_company.casefold()
        planned += [Planned(None, w, "My Companies") for w in watch if w.company.casefold() == key]
        if not planned:
            planned += [Planned(c, None, "requested") for c in seeds if c.name.casefold() == key]
        if not planned:  # not configured anywhere: try detection by name
            planned.append(Planned(None, WatchItem(only_company), "ad hoc"))
        return planned, skipped
    for w in watch:
        planned.append(Planned(None, w, "My Companies: every run"))
    for c in seeds:
        if c.name.casefold() in watch_keys:
            continue  # covered by My Companies
        if only_source and c.source != only_source:
            continue
        adapter = ADAPTERS.get(c.source)
        label = adapter(None).source_label(c) if adapter else c.source  # type: ignore[arg-type]
        t = tier_of(c, cfg)
        if force_all or only_source or due(conn, c, label, t, now):
            planned.append(Planned(c, None, f"tier {t}"))
        else:
            skipped.append(SourceOutcome(c.name, c.source, False, status="skipped", origin=c.origin, tier=t,
                                         notes=[f"tier {t}: not due (checked every {TIER_INTERVAL_HOURS[t]}h)"]))
    if only_source:
        planned = [p for p in planned if p.company is not None or p.watch is not None]
    return planned, skipped


# --- fetching ------------------------------------------------------------------------------------
@dataclass
class Fetched:
    planned: Planned
    company: Company | None
    detected_via: str | None
    result: FetchResult | None
    error: str | None
    label: str | None = None
    official: bool = True
    multi: bool = False


def _context(conn, cfg: Config, company: Company, label: str, multi: bool, tier: int, pages_cache: dict,
             validators: dict) -> FetchContext:
    full = tier == 1 or company.origin == "my_companies"
    return FetchContext(
        full=full,
        max_pages=TIER_MAX_PAGES.get(tier, 10),
        want=lambda title, loc: quick_relevant(title, loc, cfg.prefs, cfg.title_rules),
        known=db.known_listings(conn, label, None if multi else company.name),
        known_relevant=db.relevant_listings(conn, label, None if multi else company.name),
        validators=validators,
        pages=pages_cache,
        max_details=200 if multi else (400 if full else 150),
    )


def _fetch(client: HttpClient, company: Company, ctx: FetchContext) -> FetchResult:
    return get_adapter(company.source, client).fetch(company, ctx)


def run(conn: sqlite3.Connection, cfg: Config, resume: Resume, companies: list[Company] | None,
        client: HttpClient, embedder: Embedder, jd_dir: Path | None = paths.JD_DIR, *,
        watch: list[WatchItem] | None = None, only_company: str | None = None, only_source: str | None = None,
        force_all: bool = False, workers: int = WORKERS) -> RunSummary:
    """companies=None -> use cfg.companies with tier scheduling; a list -> check exactly those."""
    summary = RunSummary()
    watch = watch or []
    if companies is not None:
        planned = [Planned(c, None, "explicit") for c in companies]
        skipped: list[SourceOutcome] = []
    else:
        planned, skipped = plan(conn, cfg, watch, only_company=only_company, only_source=only_source,
                                force_all=force_all)
    summary.skipped = skipped

    directory = {}
    if row := db.get_page(conn, DIRECTORY_KEY):
        directory = json.loads(row["payload"] or "{}")
    detector = Detector(client, cfg.companies, directory)
    pages_cache = {r["url"]: {"lastmod": r["lastmod"], "payload": r["payload"]}
                   for r in conn.execute("SELECT url, lastmod, payload FROM page_cache WHERE url NOT LIKE 'ats_directory:%'")}

    # Resolve My Companies (detection may need network; cached detections are instant).
    jobs_to_run: list[tuple[Planned, Company, str | None]] = []
    for p in planned:
        if p.company is not None:
            jobs_to_run.append((p, p.company, None))
            continue
        w = p.watch
        assert w is not None
        det = detector.detect(w.company, w.careers_url, db.get_company_source(conn, w.company))
        origin = "ad_hoc" if p.reason == "ad hoc" else "my_companies"
        if det.company is None:
            summary.outcomes.append(SourceOutcome(w.company, "none", False, status="manual", origin=origin,
                                                  tier=1, detected_via="manual", url=w.careers_url, error=det.detail))
            db.set_source_status(conn, w.company, "none", "manual", det.detail, None, origin=origin, tier=1,
                                 url=w.careers_url, detected_via="manual")
            continue
        if det.via != "cache":
            c = det.company
            db.set_company_source(conn, w.company, c.source, det.via, c.board, c.host, c.site, w.careers_url)
        jobs_to_run.append((p, det.company, det.via))
    conn.commit()

    def work(item: tuple[Planned, Company, str | None]) -> Fetched:
        p, company, via = item
        adapter_cls = ADAPTERS.get(company.source)
        if adapter_cls is None:
            return Fetched(p, company, via, None, f"unknown source adapter '{company.source}'")
        adapter = adapter_cls(client)
        label = adapter.source_label(company)
        multi = adapter.capabilities.multi_company
        ctx = contexts[(company.name, company.source)]
        try:
            res = adapter.fetch(company, ctx)
            if res.partial or adapter.capabilities.query_based:
                adapter.verify_missing(company, ctx, res)
            return Fetched(p, company, via, res, None, label, adapter.capabilities.official, multi)
        except Exception as e:  # noqa: BLE001 - isolate every source failure
            if via == "cache" and p.watch is not None:  # cached detection failed: re-detect once now
                det = detector.detect(p.watch.company, p.watch.careers_url, None)
                if det.company is not None and (det.company.source, det.company.board) != (company.source, company.board):
                    try:
                        a2 = get_adapter(det.company.source, client)
                        res = a2.fetch(det.company, contexts.get((det.company.name, det.company.source), ctx))
                        return Fetched(p, det.company, f"re-detected: {det.via}", res, None,
                                       a2.source_label(det.company), a2.capabilities.official,
                                       a2.capabilities.multi_company)
                    except Exception as e2:  # noqa: BLE001
                        e = e2
            return Fetched(p, company, via, None, f"{type(e).__name__}: {e}", label, True, multi)

    contexts: dict[tuple[str, str], FetchContext] = {}
    validators = {r[0]: {"etag": r[1], "last_modified": r[2]}
                  for r in conn.execute("SELECT url, etag, last_modified FROM http_cache")}
    covered = {(c.source, (c.board or "").casefold()) for _, c, _ in jobs_to_run if c.board}
    for p, company, via in jobs_to_run:
        cls = ADAPTERS.get(company.source)
        if cls is None:
            continue
        a = cls(client)
        t = 1 if company.origin == "my_companies" else tier_of(company, cfg)
        ctx = _context(conn, cfg, company, a.source_label(company), a.capabilities.multi_company, t, pages_cache,
                       validators)
        ctx.covered = covered
        contexts[(company.name, company.source)] = ctx

    scale = max(1, workers) / WORKERS
    pools: dict[str, ThreadPoolExecutor] = {}
    try:
        futures = []
        for item in jobs_to_run:
            fam = item[1].source
            if fam not in pools:
                pools[fam] = ThreadPoolExecutor(max_workers=max(1, round(POOL_SIZES.get(fam, 2) * scale)),
                                                thread_name_prefix=fam)
            futures.append(pools[fam].submit(work, item))
        for fut in as_completed(futures):
            f = fut.result()
            summary.outcomes.append(_store(conn, cfg, f, jd_dir))
            conn.commit()
    finally:
        for pool in pools.values():
            pool.shutdown(wait=True)

    score_all(conn, cfg, resume, embedder, summary)
    return summary


def _store(conn, cfg: Config, f: Fetched, jd_dir: Path | None) -> SourceOutcome:
    company = f.company
    assert company is not None
    origin = ("ad_hoc" if f.planned.reason == "ad hoc" else "my_companies") if f.planned.watch else company.origin
    tier = 1 if origin == "my_companies" else tier_of(company, cfg)
    label = f.label or company.source
    name = f.planned.watch.company if f.planned.watch else company.name
    out = SourceOutcome(name, label, False, origin=origin, tier=tier, detected_via=f.detected_via,
                        url=company.careers_url or company.board)
    extra = {"origin": origin, "tier": tier, "url": out.url, "detected_via": f.detected_via}
    if f.result is None:
        out.error = f.error
        log.error("source failed: %s/%s: %s", label, name, out.error)
        if f.planned.watch is not None:
            db.bump_company_source_failure(conn, name)
        db.set_source_status(conn, name, label, "failed", out.error, None, **extra)
        return out

    res = f.result
    now = db.utcnow()
    out.notes = res.notes
    if res.validators:
        for url, v in res.validators.items():
            if v.get("etag") or v.get("last_modified"):
                db.set_http_cache(conn, url, v.get("etag"), v.get("last_modified"))
    for url, pg in (res.pages or {}).items():
        db.set_page(conn, url, pg.get("lastmod"), pg.get("payload"))
    if res.directory:
        db.set_page(conn, DIRECTORY_KEY, now, json.dumps(res.directory))

    scope = None if f.multi else name
    if res.unchanged:
        out.unchanged = True
        out.jobs = db.touch_all(conn, label, scope, now)
        out.ok, out.status = True, "ok"
        db.set_source_status(conn, name, label, "ok", None, out.jobs, now, partial=None, **extra)
        log.info("source ok: %s/%s: unchanged (304), %d listings", label, name, out.jobs)
        return out

    seen: set[str] = set(res.seen_ids or ())
    for raw in res.jobs:
        if f.planned.watch is not None and not f.multi:
            raw.company = name  # use the watchlist's spelling
        try:
            if raw.description is None:
                hit = db.touch_job(conn, label, raw.company, raw.job_id, now)
                if hit is None:   # unknown after all: store without a JD
                    raw.description = ""
                else:
                    seen.add(raw.job_id)
                    out.updated += hit[1] == "updated"
                    continue
            rec = to_record(raw, cfg, jd_dir, f.official)
            _, status = db.upsert_job(conn, rec, now)
        except Exception as e:  # noqa: BLE001 - one bad posting must not sink the company
            log.warning("skipping %s/%s job %s: %s", label, name, raw.job_id, e)
            continue
        seen.add(raw.job_id)
        out.new += status == "new"
        out.updated += status == "updated"
    out.partial = res.partial
    if res.partial:
        # Capped listing: a missing job is closed only if its own posting was verified gone.
        for jid in res.confirmed_open:
            db.touch_job(conn, label, name, jid, now)
        out.closed = db.mark_missing_ids(conn, label, scope, res.confirmed_closed, now)
        if res.confirmed_open or res.confirmed_closed:
            out.notes = [*out.notes, f"listing capped: verified {len(res.confirmed_open)} still open, "
                                     f"{len(res.confirmed_closed)} gone"]
    else:
        # search-based sources: relevant jobs we couldn't verify are kept as present (never closed on a guess)
        for jid in res.confirmed_open | res.protected:
            db.touch_job(conn, label, name, jid, now)
        out.closed = db.mark_missing(conn, label, scope, seen | res.confirmed_open | res.protected, now)
    out.ok, out.status, out.jobs = True, "ok", len(seen)
    db.set_source_status(conn, name, label, "ok", "; ".join(out.notes) or None, len(seen), now,
                         partial=res.partial, **extra)
    log.info("source ok: %s/%s: %d jobs (%d new, %d updated, %d closed)%s", label, name, out.jobs, out.new,
             out.updated, out.closed, f" PARTIAL: {res.partial}" if res.partial else "")
    return out


# --- scoring -------------------------------------------------------------------------------------
def _jd(job: dict) -> str:
    p = job.get("jd_path")
    return Path(p).read_text(encoding="utf-8") if p and Path(p).exists() else ""


def score_all(conn: sqlite3.Connection, cfg: Config, resume: Resume, embedder: Embedder,
              summary: RunSummary) -> None:
    from .extract.keywords import compare, extract_keywords, resume_terms, vocab_for
    from .match.prefilter import candidate, job_text, rescue

    prefs = cfg.prefs
    now = datetime.now(timezone.utc)
    by_name = {db.company_key(c.name): c for c in cfg.companies}
    resume_lines = {s: resume.bullets(s) for s in ("experience", "projects", "coursework")}
    jobs = db.open_jobs(conn)

    # first_seen may stand in for a missing posting date only for jobs that appeared after the source's first scan
    first_scan: dict[tuple, str] = {}
    for j in jobs:
        k = (j["company_key"], j["source"])
        first_scan[k] = min(first_scan.get(k, j["first_seen"]), j["first_seen"])

    results = {j["id"]: apply_rules(j, prefs, cfg.title_rules, now) for j in jobs}

    # meaning-based pre-filter: rescue relevant jobs with unusual titles
    rescued = 0
    if prefs.prefilter.enabled:
        cands = [j for j in jobs if results[j["id"]].hidden and candidate(j, results[j["id"]].reasons)]
        hits = rescue(embedder, [(j["id"], job_text(j, _jd(j))) for j in cands], prefs.prefilter.threshold,
                      prefs.prefilter.margin, extra_profile=resume.bullets("summary")[:3])
        for j in cands:
            if r := hits.get(j["id"]):
                j["_semantic_title"] = {"similarity": r.similarity, "profile": r.profile}
                results[j["id"]] = apply_rules(j, prefs, cfg.title_rules, now)
                rescued += not results[j["id"]].hidden
    summary.rescued = rescued

    vocab = vocab_for(cfg.synonyms)
    rt = resume_terms(resume)
    kw_fp = hashlib.sha256((resume.version_hash + extraction_fingerprint()).encode()).hexdigest()[:12]
    tracks: dict[str, dict] = {}
    for job in jobs:
        rules = results[job["id"]]
        t = tracks.setdefault(rules.track, {"open": 0, "visible": 0, "strong": 0, "stretch": 0, "weak": 0,
                                            "filtered": 0})
        t["open"] += 1
        if rules.hidden:
            db.save_scores(conn, job["id"], filtered=1, filter_reasons=rules.reasons, score=None,
                           qual_score=None, pref_score=None, label=None, breakdown=None,
                           explanation=None, gaps=None, signals=rules.signals or None)
            summary.filtered += 1
            t["filtered"] += 1
            continue
        jd = _jd(job)
        sem = semantic_match(embedder, resume_lines, jd_chunks(jd, job.get("min_reqs") or [], job.get("pref_reqs") or []))
        discovered_ok = job["first_seen"] > _shift(first_scan[(job["company_key"], job["source"])], hours=1)
        res = score_job(job, resume, prefs, rules, sem, cfg.synonyms, by_name.get(job["company_key"]), now,
                        discovered_ok=discovered_ok, jd_text=jd)
        key = f"{job.get('jd_hash')}:{kw_fp}"
        extra_fields = {}
        if job.get("keywords_key") != key:
            rep = compare(extract_keywords(jd, job.get("min_reqs") or [], job.get("pref_reqs") or [], vocab,
                                           job["company"]), rt, vocab)
            extra_fields = {"keywords": rep.as_dict(), "keywords_key": key, "ats_coverage": rep.coverage}
        signals = {**rules.signals, "ghost": res.breakdown.get("ghost") or [],
                   "fresh_hours": res.breakdown["freshness"]["age_hours"],
                   "discovered_proxy": not job.get("posted_date") and discovered_ok}
        db.save_scores(conn, job["id"], filtered=0, filter_reasons=rules.soft_issues, score=res.overall,
                       qual_score=res.qualification, pref_score=res.preference, label=res.label,
                       breakdown=res.breakdown, explanation=res.explanation, gaps=res.gaps, signals=signals,
                       **extra_fields)
        summary.scored += 1
        t["visible"] += 1
        t[res.label.lower()] += 1
        if res.label == "Strong":
            summary.strong += 1
            if job["job_status"] == "new":
                summary.new_strong.append(f"{job['company']}: {job['title']}")
    summary.by_track = tracks
    conn.commit()


def _shift(iso: str, hours: float) -> str:
    return (datetime.fromisoformat(iso) + timedelta(hours=hours)).isoformat(timespec="seconds")


def compute_similar(conn, embedder: Embedder, jobs: list[dict], k: int = 5) -> int:
    """'More like this' for the jobs shown on the dashboard (same track, local embeddings)."""
    from .match.prefilter import similar_jobs
    items = [(j["id"], j.get("track_override") or j.get("track") or "full_time",
              f"{j['title']}. {' '.join((j.get('min_reqs') or [])[:3])}") for j in jobs]
    sims = similar_jobs(embedder, items, k)
    for pk, ids in sims.items():
        db.save_scores(conn, pk, similar=ids)
    conn.commit()
    return len(sims)
