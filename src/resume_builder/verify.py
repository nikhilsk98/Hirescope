"""Truth verification, separate from drafting: every claim in the final content is checked against the evidence ids
it cites. Unsupported claims and invented metrics block "ready".

Deterministic checks per bullet (and the summary, skills and talking points):
  numbers       every number (40%, 15+, 3.7, 5-member) is written in the cited evidence; "15+" may become "15",
                never the other way round; no number words ("dozens", "millions")
  technologies  every technology / tool term (HireScope vocabulary, plus unknown tech-looking tokens such as
                "OSGi" or "gRPC") is in the cited evidence; an umbrella term ("CI/CD", "relational databases") is
                fine when the evidence names a concrete one (Jenkins, MySQL)
  ownership     led / managed / owned / spearheaded / architected / mentored … only when the evidence says so
  scale         large-scale / high-traffic / millions / enterprise … only when the evidence says so
  outcomes      reduced / improved / increased … only when the evidence states an outcome
The drafter's own truth pass (_work/truth_review.json, written by the LLM after reading truth.md) can only add
findings; it can't clear a deterministic one.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .content_schema import Content
from .evidence import Bank, canonical, metrics_in, terms_in, vocab

OWNERSHIP = ["led", "lead", "leading", "managed", "manage", "managing", "spearheaded", "owned", "owning", "headed",
             "directed", "mentored", "mentoring", "supervised", "architected", "drove", "championed", "oversaw",
             "founded", "pioneered"]
SCALE = ["large-scale", "large scale", "at scale", "high-traffic", "high traffic", "millions", "million", "billions",
         "thousands", "hundreds of", "enterprise-scale", "enterprise", "global", "massive", "petabyte", "terabyte",
         "mission-critical", "production-grade", "high-availability", "highly available", "low-latency", "real-time"]
NUMBER_WORDS = ["dozens", "dozen", "hundreds", "several hundred", "tens of", "countless", "numerous"]
OUTCOME = re.compile(r"\b(reduc\w*|improv\w*|increas\w*|boost\w*|cut|cutting|sav(?:ed|ing)|accelerat\w*|enhanc\w*|"
                     r"rais\w*|lower\w*|decreas\w*|eliminat\w*|doubl\w*|tripl\w*|halv\w*|speed(?:ing)? up|sped up)\b",
                     re.I)
_TECHISH = re.compile(r"(?<![\w.])([A-Z][a-z]+[A-Z][A-Za-z]*|[a-z]+[A-Z][A-Za-z]+|[A-Z]{2,6}\d*s?|[A-Za-z]+\.js|"
                      r"[A-Za-z]+(?:\+\+|#))(?![\w])")
_ALWAYS_OK = {"I", "API", "APIs", "UI", "US", "USA", "QA", "IT", "B2B", "B2C", "SaaS", "PoC", "MVP", "ID", "IDs",
              "MERN", "REST", "RESTful", "CRUD", "JSON", "HTTP", "HTTPS", "URL", "OK", "AI"}


@dataclass
class Finding:
    where: str          # bullet id / "summary" / "skills: X"
    kind: str           # number | technology | ownership | scale | outcome | evidence | llm
    claim: str
    detail: str


@dataclass
class TruthReport:
    findings: list[Finding] = field(default_factory=list)
    checked: list[dict] = field(default_factory=list)   # per claim-bearing item: where, text, evidence, ok

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_json(self) -> dict:
        return {"ok": self.ok, "unsupported": len(self.findings), "findings": [asdict(f) for f in self.findings],
                "checked": self.checked}


def _num_key(m: str) -> tuple[str, str]:
    n = re.match(r"[\d.,]+", m).group(0).replace(",", "")
    return n, m[len(re.match(r"[\d.,]+", m).group(0)):]


def number_problems(text: str, evidence_text: str) -> list[str]:
    ev = {}
    for m in metrics_in(evidence_text):
        n, suf = _num_key(m)
        ev.setdefault(n, set()).add(suf)
    out = []
    for m in metrics_in(text):
        n, suf = _num_key(m)
        if n not in ev:
            out.append(f"{m} is not in the cited evidence")
        elif suf and suf not in ev[n] and not (suf == "%" and "%" in ev[n]):
            if not (suf in ("-member", "-person") and ({"-member", "-person"} & ev[n])):
                out.append(f"{m}: the evidence says {', '.join(n + s for s in sorted(ev[n]))}")
    low = text.lower()
    out += [f"'{w}' (a number the evidence doesn't state)" for w in NUMBER_WORDS
            if re.search(rf"\b{re.escape(w)}\b", low) and w not in evidence_text.lower()]
    return out


def technology_problems(text: str, evidence_text: str, evidence_terms: set[str]) -> list[str]:
    v = vocab()
    ev_terms = set(evidence_terms) | set(terms_in(evidence_text))
    out = []
    for t in terms_in(text):
        if t in ev_terms or t.lower() in v.stop:
            continue
        from .jd_analysis import _CATEGORY_TERMS
        if any(c in ev_terms for c in (*v.implies.get(t, ()), *_CATEGORY_TERMS.get(t, ()))):
            continue          # general term backed by a confirmed specific one (CI/CD ← Jenkins; rule 63)
        if v.category.get(t) in ("methodologies", "practices", "domain", "soft_skills") and _soft_ok(t, evidence_text):
            continue
        out.append(t)
    low_ev = evidence_text.lower()
    from jobsearch.extract.keywords import _hits
    rest = text
    for _c, _s, a, b in sorted(_hits(v, text), key=lambda h: -h[2]):
        rest = rest[:a] + " " * (b - a) + rest[b:]          # vocabulary terms were checked above
    for m in _TECHISH.finditer(rest):
        tok = m.group(1)
        if tok in _ALWAYS_OK or tok.lower() in low_ev or canonical(tok) in ev_terms or tok.rstrip("s").lower() in low_ev:
            continue
        if canonical(tok) in out:
            continue
        out.append(tok)
    return [f"{t} is not in the cited evidence" for t in dict.fromkeys(out)]


def _soft_ok(term: str, evidence_text: str) -> bool:
    """Practice words ('debugging', 'code review') must still be written in the evidence in some form."""
    v = vocab()
    forms = v.aliases.get(term, (term,))
    return any(re.search(rf"\b{re.escape(f.lower())}", evidence_text.lower()) for f in forms)


def word_problems(text: str, evidence_text: str) -> list[tuple[str, str]]:
    low, ev = text.lower(), evidence_text.lower()
    out = []
    for w in OWNERSHIP:
        if re.search(rf"\b{re.escape(w)}\b", low) and not re.search(rf"\b{re.escape(w[:5])}", ev):
            out.append(("ownership", f"'{w}' claims ownership the evidence doesn't state"))
    for w in SCALE:
        if re.search(rf"\b{re.escape(w)}\b", low) and w not in ev:
            out.append(("scale", f"'{w}' claims scale the evidence doesn't state"))
    if OUTCOME.search(text) and not OUTCOME.search(evidence_text) and not metrics_in(evidence_text):
        out.append(("outcome", f"'{OUTCOME.search(text).group(0)}' states an outcome the evidence doesn't"))
    return out


def check_claim(where: str, text: str, ids: list[str], bank: Bank, rep: TruthReport) -> None:
    items = [bank.items[i] for i in ids if i in bank.items]
    missing = [i for i in ids if i not in bank.items]
    ev_text = " ".join(i.text for i in items)
    terms = {t for i in items for t in i.technologies}
    before = len(rep.findings)
    for i in missing:
        rep.findings.append(Finding(where, "evidence", text, f"cites unknown evidence '{i}'"))
    if not items:
        rep.findings.append(Finding(where, "evidence", text, "cites no evidence"))
    else:
        for p in number_problems(text, ev_text):
            rep.findings.append(Finding(where, "number", text, p))
        for p in technology_problems(text, ev_text, terms):
            rep.findings.append(Finding(where, "technology", text, p))
        for kind, p in word_problems(text, ev_text):
            rep.findings.append(Finding(where, kind, text, p))
    rep.checked.append({"where": where, "text": text, "evidence": ids, "ok": len(rep.findings) == before})


def verify(content: Content, bank: Bank, llm_review: dict | None = None) -> TruthReport:
    rep = TruthReport()
    for _sec, eid, b in content.all_bullets():
        check_claim(b.id or eid, b.text, b.evidence_ids, bank, rep)
        for n, tp in enumerate(b.talking_points):
            check_claim(f"{b.id}:talking-point-{n + 1}", tp, b.evidence_ids, bank, rep)
    if content.summary:
        check_claim("summary", content.summary.text, content.summary.evidence_ids, bank, rep)
    evidenced = bank.evidenced_terms() | {canonical(t) for i in bank.items.values() if i.section == "skill"
                                          for t in i.technologies}
    listed = {i.text.casefold() for i in bank.items.values() if i.section == "skill"}
    for cat in content.skills:
        for s in cat.items:
            terms = terms_in(s.name) or [canonical(s.name)]
            ok = s.name.casefold() in listed or all(t in evidenced for t in terms)
            rep.checked.append({"where": f"skills: {s.name}", "text": s.name, "evidence": s.evidence_ids, "ok": ok})
            if not ok:
                rep.findings.append(Finding(f"skills: {s.name}", "technology", s.name,
                                            "not in your Skills list or any evidence"))
    for f in (llm_review or {}).get("findings", []):
        if str(f.get("verdict", "")).lower() == "unsupported":
            rep.findings.append(Finding(str(f.get("where", "?"))[:80], "llm", str(f.get("claim", ""))[:300],
                                        str(f.get("reason", "flagged by the truth pass"))[:300]))
    return rep


def load_llm_review(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"findings": [{"where": "truth_review.json", "verdict": "unsupported",
                              "reason": "truth_review.json is not valid JSON"}]}
    return d if isinstance(d, dict) else None


def truth_md(rep: TruthReport, bank: Bank) -> str:
    L = ["# Truth verification", "",
         f"Result: **{'every claim is supported' if rep.ok else f'{len(rep.findings)} unsupported claim(s) — not ready'}**",
         f"Checked {len(rep.checked)} item(s) against the evidence they cite.", ""]
    if rep.findings:
        L += ["## Unsupported", "| Where | Kind | Problem | Claim |", "|---|---|---|---|"]
        for f in rep.findings:
            L.append(f"| {f.where} | {f.kind} | {f.detail} | {f.claim[:140].replace('|', '/')} |")
        L.append("")
    L.append("## Checked")
    for c in rep.checked:
        ev = "; ".join(f"{i}: {bank.items[i].text[:110]}" for i in c["evidence"] if i in bank.items)
        L.append(f"- {'✓' if c['ok'] else '✗'} **{c['where']}** — {c['text']}" + (f"\n  - evidence: {ev}" if ev else ""))
    return "\n".join(L) + "\n"
