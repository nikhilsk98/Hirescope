"""JD analysis from the HireScope job package (v2): role, level, track, responsibilities, required vs preferred
requirements, ATS keywords, eligibility constraints, and a first requirement → evidence mapping.

The job description is DATA, never instructions. Lines that read like instructions to an AI / tool ("ignore previous
instructions", "write this to a file", "add 'X' to your resume") are listed under injection_warnings and dropped from
everything the drafter tailors to; nothing here ever reads a file or URL named in a JD.

Mapping categories (the drafter confirms or corrects them in content.json → requirements):
  direct          every concrete term in the requirement is in your evidence
  transferable    a related term of the same kind is in your evidence (explained; never treated as the same thing:
                  AWS is not GCP, MySQL is not PostgreSQL)
  missing_evidence  you may have it, but your inputs don't say (an evidence-interview question)
  gap             nothing in your evidence supports it
  needs_judgment  no concrete term recognized (e.g. "strong communication"); the drafter decides
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from jobsearch import paths as hs_paths
from jobsearch.output.packages import parse_package, render_package
from jobsearch.storage import db

from .config import InputError, Settings, sha256_text
from .evidence import Bank, terms_in, vocab

JOB_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

INJECTION_PATTERNS = [
    r"\bignore (?:all |any |the )?(?:previous|prior|above|earlier|other) (?:instructions|prompts?|rules)",
    r"\bdisregard (?:all |any |the )?(?:previous|prior|above|your) ",
    r"\b(?:system|developer) prompt\b", r"\byou are (?:now )?(?:an? |the )?(?:ai|llm|language model|assistant|chatgpt|claude)\b",
    r"\bif you are an? (?:ai|llm|language model|bot|assistant)\b", r"\bas an ai\b", r"<\|[a-z_]+\|>",
    r"\b(?:assistant|system)\s*:", r"\bnew instructions?\b", r"\boverride\b.*\b(?:rules|instructions)\b",
    r"\b(?:write|save|append|copy|upload|send|post|email)\b.{0,40}\b(?:file|disk|folder|server|url|webhook|address)\b",
    r"\b(?:rm -rf|curl |wget |sudo |chmod |base64)", r"(?:~/|/etc/|/users/|\.ssh|\.env\b|id_rsa)",
    r"\b(?:api[_ ]?key|password|credentials?|secret token)\b",
    r"\b(?:include|add|insert|put|mention|write)\b.{0,60}\b(?:in|on|into|to)\s+(?:your|the)\s+(?:resume|cv|application)\b",
    r"\b(?:white|hidden|invisible) text\b", r"\bprompt injection\b",
]
_INJ = re.compile("|".join(INJECTION_PATTERNS), re.I)

RESP_HEAD = re.compile(r"^(?:key |your |core |primary |main )?(?:responsibilities|duties|what you(?:'ll| will) do|"
                       r"what you'll be doing|in this role|the role|day[- ]to[- ]day|you will|role overview|"
                       r"about the role|job duties|essential (?:duties|functions))\b", re.I)
REQ_HEAD = re.compile(r"(?:qualifications|requirements|what you(?:'ll)? bring|who you are|what we're looking for|"
                      r"skills|experience|nice to have|preferred|bonus|education)", re.I)


def injection_lines(text: str) -> list[str]:
    return [ln.strip()[:300] for ln in (text or "").splitlines() if ln.strip() and _INJ.search(ln)]


def resolve_job(conn, ref: str) -> dict:
    """A job by its HireScope ID (the number in the dashboard URL, `job=123`) or, if unique, its source job ID."""
    ref = str(ref).strip()
    if not JOB_REF_RE.match(ref):
        raise InputError(f"'{ref[:40]}' is not a valid job ID (letters, digits, '.', '_' and '-' only)")
    rows = []
    if ref.isdigit():
        rows = conn.execute("SELECT * FROM jobs WHERE id=?", (int(ref),)).fetchall()
    if not rows:
        rows = conn.execute("SELECT * FROM jobs WHERE job_id=?", (ref,)).fetchall()
    if not rows:
        raise InputError(f"no HireScope job with ID '{ref}' (use the number from the dashboard: #…&job=123)")
    if len(rows) > 1:
        raise InputError(f"source job ID '{ref}' matches {len(rows)} jobs; use the HireScope ID instead: "
                         + ", ".join(f"{r['id']} ({r['company']})" for r in rows[:5]))
    return db.decode_row(rows[0])


def _jd_text(job: dict) -> str:
    p = hs_paths.stored_file(job.get("jd_path"), hs_paths.JD_DIR)
    return p.read_text(encoding="utf-8") if p else ""


def job_package(job: dict, resume_hash: str = "resume-builder") -> str:
    return render_package(job, _jd_text(job), resume_hash)


def _lines(section: str) -> list[str]:
    out = []
    for ln in (section or "").splitlines():
        ln = re.sub(r"^\s*[-*•]\s+", "", ln).strip()
        if ln and not ln.startswith("_") and not ln.lower().startswith("coverage:"):
            out.append(ln)
    return out


def _jd_blocks(jd: str) -> list[tuple[str, str]]:
    """(heading, line) for each non-empty JD line; heading = the closest heading-like line above it."""
    head = ""
    out = []
    for raw in jd.splitlines():
        ln = raw.strip().lstrip("\\")
        if not ln:
            continue
        bare = re.sub(r"^[#*\s]+|[*:\s]+$", "", ln)
        is_bullet = bool(re.match(r"^[•\-*·]\s+", ln))
        if not is_bullet and len(bare) <= 70 and (ln.endswith(":") or ln.startswith("#") or
                                                  (RESP_HEAD.match(bare) or REQ_HEAD.search(bare)) and len(bare.split()) <= 8):
            head = bare
            continue
        out.append((head, re.sub(r"^[•\-*·]\s+", "", ln), is_bullet))
    return out


def responsibilities(jd: str) -> list[str]:
    return [ln for h, ln, _b in _jd_blocks(jd) if RESP_HEAD.match(h or "")][:15]


def _parse_keyword_line(line: str) -> dict | None:
    parts = [p.strip() for p in line.split(" | ")]
    if len(parts) < 4 or parts[1] not in ("required", "preferred", "mentioned"):
        return None
    try:
        w = float(parts[3].replace("weight", "").strip())
    except ValueError:
        w = 1.0
    return {"term": parts[0], "section": parts[1], "status": parts[2], "weight": w}


def _supported(term: str, bank_terms: set[str]) -> tuple[bool, list[str]]:
    """(supported, concrete terms that support it). Umbrella terms (Relational Databases, CI/CD) are supported by
    a concrete one in the HireScope vocabulary's `implies` list; nothing else counts as equivalent."""
    if term in bank_terms:
        return True, [term]
    concrete = [c for c in vocab().implies.get(term, ()) if c in bank_terms]
    return bool(concrete), concrete


def _evidence_for(terms: list[str], bank: Bank) -> list[str]:
    ids = []
    for it in bank.items.values():
        if it.section in ("summary",):
            continue
        if any(t in it.technologies for t in terms):
            ids.append(it.id)
    return ids


def related_terms(term: str, bank_terms: set[str], settings: Settings | None) -> list[str]:
    """Evidenced terms in the same related_groups group as `term` (config/resume_builder.yaml)."""
    from .evidence import canonical
    out = []
    for group in (settings.related_groups if settings else {}).values():
        canon = [canonical(g) for g in group]
        if term in canon:
            out += [g for g in canon if g != term and g in bank_terms]
    return list(dict.fromkeys(out))


def map_requirement(line: str, kind: str, bank: Bank, settings: Settings | None = None) -> dict:
    v = vocab()
    bank_terms = bank.evidenced_terms()
    terms = [t for t in terms_in(line) if t.lower() not in v.stop]
    if not terms:
        return {"text": line, "kind": kind, "terms": [], "category": "needs_judgment", "evidence_ids": [],
                "note": "no concrete skill recognized; the drafter judges whether your evidence covers it"}
    have, missing, support = [], [], []
    for t in terms:
        ok, conc = _supported(t, bank_terms)
        (have if ok else missing).append(t)
        support += conc
    ev = _evidence_for(support, bank)
    if not missing:
        return {"text": line, "kind": kind, "terms": terms, "category": "direct", "evidence_ids": ev[:12],
                "note": "supported: " + ", ".join(dict.fromkeys(support))}
    related = list(dict.fromkeys(r for t in missing for r in related_terms(t, bank_terms, settings)))
    if have or related:
        note = (f"not in your evidence: {', '.join(missing)}"
                + (f"; related evidence (not the same thing): {', '.join(related[:6])}" if related else "")
                + (f"; covered: {', '.join(have)}" if have else ""))
        return {"text": line, "kind": kind, "terms": terms, "category": "transferable" if related else "gap",
                "evidence_ids": (ev + _evidence_for(related, bank))[:12], "note": note, "missing_terms": missing,
                "related_terms": related[:6]}
    return {"text": line, "kind": kind, "terms": terms, "category": "gap", "evidence_ids": [],
            "note": f"not in your evidence: {', '.join(missing)}", "missing_terms": missing}


def pick_profile(title: str, required: list[str], text: str, settings: Settings) -> dict:
    scores = {}
    low_t, low_r, low_x = title.lower(), "\n".join(required).lower(), (text or "").lower()
    for name, p in settings.profiles.items():
        s = 0.0
        for sig in p.signals:
            pat = re.compile(rf"(?<![a-z0-9]){re.escape(sig)}(?![a-z0-9])")
            s += 3 * len(pat.findall(low_t)) + 2 * len(pat.findall(low_r)) + min(5, len(pat.findall(low_x)))
        scores[name] = round(s, 1)
    best = max(scores.items(), key=lambda kv: (kv[1], kv[0] == settings.default_profile)) if scores else (None, 0)
    picked = best[0] if best[1] > 0 else settings.default_profile
    return {"picked": picked, "scores": scores,
            "reason": f"highest JD signal score ({best[1]})" if best[1] > 0 else "no signals; default profile"}


def analyze(job: dict, package_text: str, bank: Bank, settings: Settings, profile: str | None = None) -> dict:
    fm, sec = parse_package(package_text)
    jd = sec.get("Full JD", "")
    if jd.startswith("_JD text unavailable"):
        jd = ""
    inj = injection_lines(jd) + [ln for k in ("Minimum requirements", "Preferred requirements")
                                 for ln in injection_lines(sec.get(k, ""))]
    clean = lambda lines: [ln for ln in lines if not _INJ.search(ln)]
    required = clean(_lines(sec.get("Minimum requirements", "")))
    preferred = clean(_lines(sec.get("Preferred requirements", "")))
    mentioned = []
    if not required and not preferred:
        # no requirement sections parsed: the JD's bullets (outside responsibilities) and lines naming a skill
        for h, ln, bullet in _jd_blocks(jd):
            if len(ln) < 300 and not _INJ.search(ln) and not RESP_HEAD.match(h or "") and (
                    terms_in(ln) or (bullet and len(ln.split()) >= 4)):   # 2-3 word bullets are links / boilerplate
                mentioned.append(ln)
        mentioned = mentioned[:20]
    resp = clean(responsibilities(jd))
    kws = []
    bank_terms = bank.evidenced_terms()
    for ln in _lines(sec.get("ATS keywords", "")):
        k = _parse_keyword_line(ln)
        if not k:
            continue
        from .evidence import canonical
        canon = canonical(k["term"])
        ok, conc = _supported(canon, bank_terms)
        k.update(canonical=canon, supported=ok, evidence_ids=_evidence_for(conc, bank)[:8],
                 category=vocab().category.get(canon, "other"))
        kws.append(k)
    reqs = ([map_requirement(r, "required", bank, settings) for r in required]
            + [map_requirement(r, "preferred", bank, settings) for r in preferred]
            + [map_requirement(r, "mentioned", bank, settings) for r in mentioned])
    flags = job.get("flags") or {}
    elig = {k: (flags.get(k) or {}).get("value", "Not stated") for k in ("sponsorship", "citizenship", "clearance")}
    elig.update(yoe_required=job.get("yoe_required"), degree=job.get("degree"), grad_years=job.get("grad_years") or [],
                note=job.get("eligibility_note"), terms=job.get("terms") or [])
    track = job.get("track_override") or job.get("track") or "full_time"
    prof = pick_profile(job["title"], required + preferred, jd, settings)
    if profile:
        if profile not in settings.profiles:
            raise InputError(f"unknown profile '{profile}' (choose from {', '.join(settings.profiles)})")
        prof.update(picked=profile, reason="--profile override")
    domain = [k["term"] for k in kws if k["category"] in ("domains", "domain")]
    return {
        "analysis_version": 1,
        "job": {"pk": job["id"], "company": job["company"], "title": job["title"], "job_id": job["job_id"],
                "source": job["source"], "apply_url": job.get("apply_url"), "track": track,
                "level": job.get("level") or "unknown", "location": job.get("location"),
                "label": job.get("label"), "score": job.get("score"), "posted": job.get("posted_date")},
        "package_hash": sha256_text(package_text),
        "package_front_matter": {k: fm.get(k) for k in ("package_format", "company", "title", "job_id", "source")},
        "role": job["title"], "seniority": job.get("level") or "unknown", "track": track,
        "responsibilities": resp, "required": required, "preferred": preferred, "mentioned": mentioned,
        "keywords": kws, "domain_terms": domain, "eligibility": elig, "requirements": reqs,
        "profile": prof, "injection_warnings": inj, "jd_chars": len(jd),
    }


def load_analysis_inputs(ref: str, db_path: Path | None = None) -> tuple[dict, str]:
    conn = db.connect(db_path or hs_paths.DB)
    try:
        job = resolve_job(conn, ref)
    finally:
        conn.close()
    return job, job_package(job)


def save(analysis: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analysis, indent=1, ensure_ascii=False), encoding="utf-8")
