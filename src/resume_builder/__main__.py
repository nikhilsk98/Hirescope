"""python -m resume_builder <command> — the deterministic half of /resume-builder (see SKILL.md).

  check                      dependencies and inputs
  default                    the master's Default one-page resume, unchanged (no job description; master v4), checked
                             against your template compiled as written → generated_resumes/_default/v<N>
  prepare <job> [--profile X] [--regenerate] [--force] [--discard-edits]
                             evidence bank, JD analysis, baseline + brief for the drafter (work/); reuses the
                             current version when nothing changed (unless --regenerate)
  review <job>               reviewer packet for _work/draft.json (rule findings + truth check), once per generation
  verify <job> [--content F] truth check of a content file against the evidence it cites
  build <job> [--content F]  reviewed content → truth check, render, compile, check the PDF, repair (max 3), save v<N>
  interview <job>            evidence-interview questions for this job (answers are saved only with add-evidence)
  add-evidence --entry ID --question Q --answer A [--keyword K --level L] --confirm
                             save one answer you confirmed to inputs/resume/evidence_additions.md (a tool answer
                             goes to inputs/resume/tool_checklist.md instead)
  add-tool --tool T --where W --level L [--proficiency P] --confirm
                             tick a tool you used in tool_checklist.md (where: role/project; level: production |
                             project | coursework | tutorial)
  decline --keyword K        you haven't used K: don't ask again (it stays a known gap)
  tags                       status of every master-resume item (rule 1): what still needs a [confirmed] tag
  gaps                       learn-it list: JD keywords missing from your evidence in several jobs
  show <job> [--bullets]     versions, the current one, locks (and bullet ids to lock)
  diff <job> [A B]           what changed between two versions (default: previous → latest)
  rollback <job> N           make v<N> current again (it must have passed every check)
  lock <job> TARGET          keep a bullet id, an entry (exp-…/proj-…) or section:skills|education|summary
  unlock <job> TARGET        as approved in the current version, unchanged on regeneration
  mark-reviewed <job> [N]    you reviewed/approved the current (or given) version
  refresh                    re-sync resume statuses into HireScope and rewrite dashboard/resumes.js
  batch shortlisted [--yes]  queue every shortlisted job (shows the count and a usage warning; --yes to start)
  schema                     the content JSON schema
Job = the HireScope job ID (the number in the dashboard URL: #…&job=123).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as rc
from . import pipeline, versions
from .compile import CompileError
from .config import InputError
from .content_schema import ContentError, json_schema


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


def cmd_check(args) -> int:
    missing = pipeline.check_dependencies()
    try:
        hashes = pipeline.check_inputs()
        settings = rc.load_settings()
        from .evidence import load_bank
        bank = load_bank()
    except InputError as e:
        print(f"Input problem: {e}")
        return 2
    if missing:
        print("Missing dependencies:\n  - " + "\n  - ".join(missing))
        return 2
    print(f"OK: tools present ({pipeline.tool_versions()}); inputs: {', '.join(hashes)}; "
          f"{len(bank.items)} confirmed evidence items in {len(bank.entries)} entries "
          f"({sum(1 for i in bank.items.values() if i.section == 'tool')} ticked tools); "
          f"profiles: {', '.join(settings.profiles)}")
    if bank.excluded:
        print(f"note: {len(bank.excluded)} master-resume item(s) are not confirmed and are never used (rule 1): "
              "`python -m resume_builder tags`")
    for f in bank.metric_flags:
        print(f"check metric (rule 3): {f['detail']}")
    for w in bank.warnings:
        print(f"note: {w}")
    return 0


def cmd_default(args) -> int:
    out = pipeline.build_default()
    rep = out["report"]
    print(f"{out['status'].upper()}: v{out['version']} → {out['version_dir']}\n  PDF: {out['pdf']}")
    print(f"  matches the template compiled as written: {'yes' if out['matches_template'] else 'NO'}")
    for x in rep.errors():
        print(f"  ✗ {x.name}: {x.detail}")
    for x in rep.warnings():
        if not x.ok:
            print(f"  ! {x.name}: {x.detail}")
    return 0 if out["status"] == "ready" else 1


def cmd_tags(args) -> int:
    from .evidence import status_report
    rep = status_report()
    print("Status tags (rule 1): only [confirmed] items are used. Put a tag at the end of a line; on a '## Section'\n"
          "or '### Entry' heading it covers everything below it unless a line has its own tag:\n"
          "  ## Experience [confirmed]\n"
          "  ### Associate Software Developer — Accenture Technology, India (Feb 2021 – Dec 2021) [confirmed]\n"
          "  - Redesigned internal messaging … [needs confirmation]\n"
          "  - Languages: JavaScript, Java, Kotlin [demo], Python   (a tag after an item covers that item)\n"
          "  - Databases [needs confirmation]: Cassandra, Redis     (a tag after the category covers the line)\n"
          "  ### Shipment Tracker | React, Node.js [confirmed] [client work: Freelance]   (rule 62)\n")
    print("Counts: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["counts"].items())))
    todo = [r for r in rep["rows"] if r["status"] != "confirmed"]
    if not todo:
        print("Every item is confirmed.")
    for r in todo:
        print(f"  line {r['line']:>3} [{r['status']}] {r['section']}: {r['text'][:110]}")
    for w in rep["additions_warnings"]:
        print(f"evidence_additions.md: {w}")
    return 0


def cmd_add_tool(args) -> int:
    from . import keywords, tools
    if not args.confirm:
        print("Error: not saved — add --confirm once you're sure you used it", file=sys.stderr)
        return 2
    state = tools.confirm(rc.TOOL_CHECKLIST, args.tool, args.where, args.level, args.proficiency or "")
    keywords.record_answer(args.tool, "saved")
    print(f"{state}: {args.tool} in {rc.TOOL_CHECKLIST} (Where: {args.where}, Level: {args.level})")
    return 0


def cmd_decline(args) -> int:
    from . import interview, keywords
    from .evidence import load_bank
    keywords.record_answer(args.keyword, "no")
    p = interview.save_answer("skills", f"Have you used {args.keyword}?", "no", True, load_bank(), keyword=args.keyword,
                              where="—")
    print(f"Noted in {p}: you haven't used {args.keyword}. It won't be asked again and is never claimed (a known gap).")
    return 0


def cmd_gaps(args) -> int:
    from . import keywords
    from .evidence import load_bank
    rows = keywords.learn_it(load_bank(), args.min_jobs)
    p = keywords.write_learn_it(load_bank())
    if not rows:
        print(f"No keyword is missing from {args.min_jobs}+ prepared jobs yet. ({p})")
    for r in rows:
        print(f"{r['jobs']:>3} jobs  {r['keyword']}" + ("  (you said you haven't used it)" if r["declined"] else ""))
    return 0


def _bridge(pk, status=None, message=None) -> bool:
    """HireScope status + dashboard data; a failure here never loses the resume, and says so."""
    from .hirescope_bridge import BridgeError, update_job
    try:
        update_job(pk, status, message)
        return True
    except BridgeError as e:
        print(f"Warning: {e}", file=sys.stderr)
        return False


def cmd_prepare(args) -> int:
    out = pipeline.prepare(args.job, args.profile, regenerate=args.regenerate, force=args.force,
                           discard_edits=args.discard_edits)
    a = out["analysis"]
    # reused: back to the current version's status (it may have been "queued" by a batch)
    _bridge(a["job"]["pk"], out["reused"]["status"] if out.get("reused") else "generating")
    if out.get("reused"):
        r = out["reused"]
        print(f"REUSED: v{r['version']} ({r['status']}) was built from the same job, inputs, settings and locks; "
              f"nothing to regenerate.\n  PDF: {r['pdf']}\n  (--regenerate builds a new version anyway)")
        return 0
    cats = {}
    for r in a["requirements"]:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    print(f"Prepared {a['job']['company']} — {a['job']['title']} (job {a['job']['pk']}, {a['track']}, "
          f"profile {a['profile']['picked']})")
    print(f"  requirements: {len(a['requirements'])} {cats}; keywords: {len(a['keywords'])}; "
          f"injection warnings: {len(a['injection_warnings'])}")
    print(f"  brief:    {out['work']}/brief.md\n  baseline: {out['baseline']}\n  write:    {out['work']}/content.json")
    kp = a.get("keyword_plan") or {}
    for q in kp.get("questions", []):
        print(f"  ❓ ask Nikhil ({q['kind']}): {q['question']}")
    for g in kp.get("known_gaps", []):
        print(f"  gap: {g}")
    for w in out["bank_warnings"]:
        print(f"  note: {w}")
    car = out.get("carried") or {}
    if car.get("manual"):
        print(f"  hand edits carried from v{car['from_version']}: {len(car['manual'])} bullet(s)")
    if car.get("discarded"):
        print(f"  hand edits in v{car['from_version']} discarded (--discard-edits); that version keeps them")
    for c in car.get("conflicts") or []:
        print(f"  ⚠ conflict: {c}")
    locks = out.get("locks") or {}
    n = sum(len(v) for v in locks.values())
    if n:
        print(f"  locks applied: {n}")
    return 0


def cmd_build(args) -> int:
    from jobsearch.storage import db as hs_db
    from jobsearch import paths as hs_paths
    from .jd_analysis import resolve_job
    conn = hs_db.connect(hs_paths.DB)
    try:
        job = resolve_job(conn, args.job)
    finally:
        conn.close()
    jd = pipeline.job_dir(job["company"], job["id"], job["title"])
    _bridge(job["id"], "validating")
    try:
        with versions.JobLock(jd):
            res = pipeline.build(args.job, Path(args.content) if args.content else None)
            out = pipeline.finalize(res)
    except pipeline.LimitReached:
        cur = versions.current(jd)  # nothing was built: the status goes back to what the current version is
        _bridge(job["id"], cur["status"] if cur else "failed",
                None if cur else "build attempts used up before any version passed")
        raise
    except (ContentError, CompileError, InputError) as e:
        cur = versions.current(jd)
        _bridge(job["id"], "failed", f"build stopped: {str(e)[:300]}"
                + (f" (v{cur['version']} is still current)" if cur else ""))
        raise
    msg = None
    if out["status"] != "ready":
        cur = versions.current(jd)
        msg = (f"v{out['version']} failed" + (f" (v{cur['version']} is still current)" if cur else "") + ": "
               + "; ".join(out["reasons"]))[:480]
    bridged = _bridge(job["id"], out["status"], msg)
    rep = res["report"]
    print(f"{out['status'].upper()}: v{out['version']} → {out['version_dir']}")
    if out["pdf"]:
        print(f"  PDF: {out['pdf']}")
    for a in res["attempts"]:
        print(f"  attempt {a['attempt']}: {'ok' if a['ok'] else 'failed'} ({a['pages']} page(s))")
    for r in res["repairs"]:
        print(f"  repair: {r}")
    for r in out["reasons"]:
        print(f"  ✗ {r}")
    for w in (rep.warnings() if rep else []):
        print(f"  ! {w.name}: {w.detail}")
    if out["status"] != "ready":
        cur = versions.current(jd)
        print(f"  current version stays v{cur['version']}" if cur else "  no version is current yet")
    if bridged:
        print("  HireScope updated (refresh the dashboard)")
    return 0 if out["status"] == "ready" and bridged else 1


def _job_dir(ref):
    from jobsearch.storage import db as hs_db
    from jobsearch import paths as hs_paths
    from .jd_analysis import resolve_job
    conn = hs_db.connect(hs_paths.DB)
    try:
        job = resolve_job(conn, ref)
    finally:
        conn.close()
    return job, pipeline.job_dir(job["company"], job["id"], job["title"])


def cmd_review(args) -> int:
    out = pipeline.review_draft(args.job)
    print(f"Reviewer packet: {out['packet']}")
    print(f"  rule findings: {len(out['findings'])}; truth: "
          + ("every claim supported" if out["truth"].ok else f"{len(out['truth'].findings)} unsupported"))
    for f in out["findings"][:15]:
        print(f"  - [{f['rule']}] {f['where']}: {f['detail']}")
    for f in out["truth"].findings[:15]:
        print(f"  ✗ {f.where}: {f.detail}")
    return 0


def cmd_verify(args) -> int:
    from . import verify
    from .content_schema import parse
    from .evidence import load_bank
    job, jd = _job_dir(args.job)
    src = Path(args.content) if args.content else jd / pipeline.WORK / "content.json"
    c = pipeline.assign_ids(parse(src.read_text(encoding="utf-8")))
    bank = load_bank()
    st, analysis = pipeline.load_work(jd) if (jd / pipeline.WORK / "state.json").exists() else ({}, None)
    probs = pipeline.structure_problems(c, bank, rc.load_settings(), analysis)
    rep = verify.verify(c, bank, verify.load_llm_review(jd / pipeline.WORK / "truth_review.json"))
    (jd / pipeline.WORK).mkdir(parents=True, exist_ok=True)
    (jd / pipeline.WORK / "truth.md").write_text(verify.truth_md(rep, bank), encoding="utf-8")
    print(f"{'SUPPORTED' if rep.ok and not probs else 'UNSUPPORTED'}: {len(rep.checked)} item(s) checked; "
          f"report {jd / pipeline.WORK / 'truth.md'}")
    for p_ in probs:
        print(f"  ✗ structure: {p_}")
    for f in rep.findings:
        print(f"  ✗ {f.where} [{f.kind}]: {f.detail}")
    return 0 if rep.ok and not probs else 1


def cmd_interview(args) -> int:
    from . import interview
    from .content_schema import parse
    from .evidence import load_bank
    job, jd = _job_dir(args.job)
    work = jd / pipeline.WORK
    if not (work / "analysis.json").exists():
        pipeline.prepare(args.job)
    analysis = json.loads((work / "analysis.json").read_text())
    src = next((work / n for n in ("content.json", "draft.json", "baseline.json") if (work / n).exists()))
    qs = interview.questions(analysis, parse(src.read_text()), load_bank())
    (work / "interview.json").write_text(json.dumps(qs, indent=1, ensure_ascii=False), encoding="utf-8")
    kp = json.loads((work / "questions.json").read_text()) if (work / "questions.json").exists() else {}
    for q in kp.get("questions", []):
        print(f"keyword {q['keyword']} [{q['kind']}]: {q['question']}")
    if kp.get("questions"):
        print("  save a yes: add-evidence --entry <role id> --keyword K --question Q --answer A [--level L] --confirm; "
              "a no: decline --keyword K")
    if not qs and not kp.get("questions"):
        print("No questions: your evidence already states scope and outcomes for this job's strongest matches.")
    for q in qs:
        print(f"{q['id']} ({q['entry_id'] or 'which role?'}): {q['question']}\n    why: {q['why']}")
    return 0


def cmd_add_evidence(args) -> int:
    from . import interview
    from .evidence import load_bank
    p = interview.save_answer(args.entry, args.question, args.answer, args.confirm, load_bank(), keyword=args.keyword,
                              level=args.level, where=args.where)
    print(f"Saved to {p} (dated, confirmed). The evidence bank rebuilds on the next run.")
    return 0


def cmd_diff(args) -> int:
    _job, jd = _job_dir(args.job)
    print(versions.diff(jd, args.a, args.b))
    return 0


def cmd_rollback(args) -> int:
    _job, jd = _job_dir(args.job)
    with versions.JobLock(jd):
        cur = versions.rollback(jd, args.version)
    _bridge_refresh(_job)
    print(f"Current: v{cur['version']} ({cur['status']}) — {cur['pdf']}")
    return 0


def cmd_lock(args) -> int:
    _job, jd = _job_dir(args.job)
    with versions.JobLock(jd):
        what = versions.lock(jd, args.target, args.version)
    print(f"Locked {what}: regeneration keeps it unchanged.")
    return 0


def cmd_unlock(args) -> int:
    _job, jd = _job_dir(args.job)
    with versions.JobLock(jd):
        hit = versions.unlock(jd, args.target)
    print("Unlocked." if hit else f"'{args.target}' was not locked.")
    return 0 if hit else 1


def cmd_mark_reviewed(args) -> int:
    job, jd = _job_dir(args.job)
    with versions.JobLock(jd):
        m = versions.mark_reviewed(jd, args.version)
    _bridge_refresh(job)
    print(f"v{m['version']} marked reviewed ({m['reviewed_at']}).")
    return 0


def _bridge_refresh(job) -> None:
    _bridge(job["id"])


def cmd_refresh(args) -> int:
    from .hirescope_bridge import refresh_all
    n = refresh_all()
    print(f"HireScope updated for {n} job(s) with resumes; dashboard/resumes.js rewritten.")
    return 0


def cmd_batch(args) -> int:
    from . import batch
    return batch.run(args.which, args.yes, args.limit)


def cmd_show(args) -> int:
    from jobsearch.storage import db as hs_db
    from jobsearch import paths as hs_paths
    from .jd_analysis import resolve_job
    conn = hs_db.connect(hs_paths.DB)
    try:
        job = resolve_job(conn, args.job)
    finally:
        conn.close()
    jd = pipeline.job_dir(job["company"], job["id"], job["title"])
    out = {"job_dir": str(jd), "current": versions.current(jd), "versions": versions.versions_summary(jd),
           "locks": sorted(versions.locked_ids(versions.load_locks(jd))) + sorted(
               f"section:{k}" for k in versions.load_locks(jd)["sections"])}
    cur = versions.current(jd)
    if cur:
        ed = versions.detect_edits(jd / f"v{cur['version']}")
        out["hand_edits"] = {k: ed[k] for k in ("edited", "json", "tex", "bullets", "removed", "conflicts")}
        if args.bullets:
            c = versions._content_of(jd / f"v{cur['version']}")
            out["bullets"] = [{"id": b.id, "text": b.text} for _s, _e, b in c.all_bullets()]
    _print(out)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m resume_builder", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("default")
    p = sub.add_parser("prepare")
    p.add_argument("job")
    p.add_argument("--profile")
    p.add_argument("--regenerate", action="store_true", help="build a new version even if nothing changed")
    p.add_argument("--force", action="store_true", help="start over even if a generation is in progress")
    p.add_argument("--discard-edits", action="store_true", help="don't carry hand edits of the current version")
    p = sub.add_parser("build")
    p.add_argument("job")
    p.add_argument("--content")
    p = sub.add_parser("review")
    p.add_argument("job")
    p = sub.add_parser("verify")
    p.add_argument("job")
    p.add_argument("--content")
    p = sub.add_parser("interview")
    p.add_argument("job")
    p = sub.add_parser("add-evidence")
    p.add_argument("--entry", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--answer", required=True)
    p.add_argument("--keyword", help="the JD keyword the question was about (tools go to tool_checklist.md)")
    p.add_argument("--level", help="production | project | coursework | tutorial (tools, skills)")
    p.add_argument("--where", help="where it was used (role, project or course), when --entry is skills")
    p.add_argument("--confirm", action="store_true", help="you confirm the answer is true and may be used")
    p = sub.add_parser("add-tool")
    p.add_argument("--tool", required=True)
    p.add_argument("--where", required=True, help="role or project where you used it")
    p.add_argument("--level", required=True, help="production | project | coursework | tutorial")
    p.add_argument("--proficiency")
    p.add_argument("--confirm", action="store_true")
    p = sub.add_parser("decline")
    p.add_argument("--keyword", required=True)
    sub.add_parser("tags")
    p = sub.add_parser("gaps")
    p.add_argument("--min-jobs", type=int, default=2)
    p = sub.add_parser("show")
    p.add_argument("job")
    p.add_argument("--bullets", action="store_true")
    p = sub.add_parser("diff")
    p.add_argument("job")
    p.add_argument("a", nargs="?", type=int)
    p.add_argument("b", nargs="?", type=int)
    p = sub.add_parser("rollback")
    p.add_argument("job")
    p.add_argument("version", type=int)
    for name in ("lock", "unlock"):
        p = sub.add_parser(name)
        p.add_argument("job")
        p.add_argument("target")
        if name == "lock":
            p.add_argument("--version", type=int)
    sub.add_parser("refresh")
    p = sub.add_parser("batch")
    p.add_argument("which", choices=["shortlisted"])
    p.add_argument("--yes", action="store_true", help="you confirmed the job count and the usage warning")
    p.add_argument("--limit", type=int)
    p = sub.add_parser("mark-reviewed")
    p.add_argument("job")
    p.add_argument("version", nargs="?", type=int)
    sub.add_parser("schema")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "schema":
            _print(json_schema())
            return 0
        return {"check": cmd_check, "default": cmd_default, "prepare": cmd_prepare, "build": cmd_build, "show": cmd_show,
                "review": cmd_review, "verify": cmd_verify, "interview": cmd_interview,
                "add-evidence": cmd_add_evidence, "add-tool": cmd_add_tool, "decline": cmd_decline, "tags": cmd_tags,
                "gaps": cmd_gaps, "diff": cmd_diff, "rollback": cmd_rollback, "lock": cmd_lock,
                "unlock": cmd_unlock, "mark-reviewed": cmd_mark_reviewed, "refresh": cmd_refresh,
                "batch": cmd_batch}[args.cmd](args)
    except (InputError, ContentError, CompileError, versions.LockedError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
