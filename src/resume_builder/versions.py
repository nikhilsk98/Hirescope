"""Versions of a job's resume: data/resumes/<Company>_<id>/v<N>/ with the employer-facing PDF, the .tex sources,
the content JSON, reports, page images, the compile log and a source bundle that rebuilds the same PDF.

current.json points at the last version that passed every required check (a failed build never replaces it).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from . import config as rc
from .config import sha256_file

MANIFEST_VERSION = 1


class LockedError(Exception):
    pass


class JobLock:
    """data/resumes/<job>/.lock (pid + time). A second run on the same job stops with a clear message; a lock whose
    process is gone or that is older than `stale_s` is replaced."""

    def __init__(self, job_dir: Path, stale_s: int = 3600):
        self.path = job_dir / ".lock"
        self.stale_s = stale_s
        self.fd = None

    def _stale(self) -> bool:
        try:
            pid, t = self.path.read_text().split()[:2]
            pid, t = int(pid), float(t)
        except (OSError, ValueError):
            return True
        if time.time() - t > self.stale_s:
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.write(self.fd, f"{os.getpid()} {time.time()}".encode())
                return self
            except FileExistsError:
                if self._stale():
                    self.path.unlink(missing_ok=True)
                    continue
                raise LockedError(f"another Resume Builder run is working on this job ({self.path.parent.name}); "
                                  "wait for it to finish (or delete the .lock file if no run is active)")
        raise LockedError(f"could not lock {self.path}")

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.path.unlink(missing_ok=True)
        return False


def version_dirs(job_dir: Path) -> list[Path]:
    out = [p for p in job_dir.glob("v*") if p.is_dir() and re.fullmatch(r"v\d+", p.name)]
    return sorted(out, key=lambda p: int(p.name[1:]))


def next_version(job_dir: Path) -> int:
    v = version_dirs(job_dir)
    return int(v[-1].name[1:]) + 1 if v else 1


def read_manifest(vdir: Path) -> dict:
    return json.loads((vdir / "manifest.json").read_text(encoding="utf-8"))


def current(job_dir: Path) -> dict | None:
    p = job_dir / "current.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def set_current(job_dir: Path, version: int, reason: str) -> dict:
    vdir = job_dir / f"v{version}"
    m = read_manifest(vdir)
    if m.get("status") not in ("ready", "reviewed"):
        raise ValueError(f"v{version} is '{m.get('status')}'; only a version that passed every check can be current")
    cur = {"version": version, "status": m["status"], "pdf": m["files"]["pdf"], "set_at": m.get("generated_at"),
           "reason": reason}
    tmp = job_dir / "current.json.tmp"
    tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    tmp.replace(job_dir / "current.json")
    return cur


def save_version(job_dir: Path, build_dir: Path, pdf_name: str, content_json: str, reports: dict[str, str],
                 manifest: dict, font_files: list[str], evidence_used: dict | None = None) -> Path:
    """Copy one build into v<N> (atomically: written as .v<N>.tmp, then renamed). Returns the version folder."""
    n = next_version(job_dir)
    tmp = job_dir / f".v{n}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "reports").mkdir(parents=True)
    src = tmp / "source"
    src.mkdir()
    files = {}
    pdf = build_dir / "main.pdf"
    if pdf.exists():
        shutil.copyfile(pdf, tmp / pdf_name)
        files["pdf"] = pdf_name
    for name in ("main.tex", "content.tex"):
        if (build_dir / name).exists():
            shutil.copyfile(build_dir / name, tmp / name)
            shutil.copyfile(build_dir / name, src / name)
    files["tex"] = "main.tex"
    files["content_tex"] = "content.tex"
    if (build_dir / "compile.log").exists():
        shutil.copyfile(build_dir / "compile.log", tmp / "compile.log")
        files["log"] = "compile.log"
    (tmp / "content.json").write_text(content_json, encoding="utf-8")
    (tmp / ".generated").mkdir()
    (tmp / ".generated" / "content.json").write_text(content_json, encoding="utf-8")   # untouched copy (edit detection)
    if (build_dir / "content.tex").exists():
        shutil.copyfile(build_dir / "content.tex", tmp / ".generated" / "content.tex")
    (tmp / ".generated" / "evidence_used.json").write_text(json.dumps(evidence_used or {}, indent=1,
                                                                      ensure_ascii=False), encoding="utf-8")
    files["content"] = "content.json"
    pages = build_dir / "pages"
    if pages.exists():
        shutil.copytree(pages, tmp / "pages")
        files["pages"] = sorted(f"pages/{p.name}" for p in (tmp / "pages").glob("*.png"))
    for name, text in reports.items():
        (tmp / "reports" / name).write_text(text, encoding="utf-8")
    files["reports"] = sorted(f"reports/{n_}" for n_ in reports)
    fonts = src / "fonts"
    copied = []
    for f in font_files:
        fp = Path(f)
        if fp.exists() and fp.suffix.lower() in (".ttf", ".otf", ".ttc"):
            fonts.mkdir(exist_ok=True)
            shutil.copyfile(fp, fonts / fp.name)
            copied.append({"file": f"source/fonts/{fp.name}", "sha256": sha256_file(fp)})
    (src / "BUILD.md").write_text(
        "Rebuild this PDF:\n\n    cd source && tectonic -X compile --untrusted main.tex\n\n"
        "Needs Tectonic (" + manifest.get("tools", {}).get("tectonic", "?") + ") and the fonts in fonts/ installed "
        "(the template selects its main font by name).\n", encoding="utf-8")
    manifest = {**manifest, "manifest_version": MANIFEST_VERSION, "version": n, "files": files, "fonts": copied}
    manifest["file_hashes"] = {k: sha256_file(tmp / v) for k, v in
                               (("pdf", files.get("pdf")), ("tex", "main.tex"), ("content_tex", "content.tex"),
                                ("content", "content.json")) if v and (tmp / v).exists()}
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    final = job_dir / f"v{n}"
    tmp.rename(final)
    return final


def versions_summary(job_dir: Path) -> list[dict]:
    out = []
    cur = current(job_dir) or {}
    for v in version_dirs(job_dir):
        try:
            m = read_manifest(v)
        except (OSError, ValueError):
            continue
        out.append({"version": m["version"], "status": m.get("status"), "generated_at": m.get("generated_at"),
                    "profile": m.get("profile"), "current": cur.get("version") == m["version"],
                    "reasons": m.get("failure_reasons") or []})
    return out


def resumes_root() -> Path:
    return rc.resumes_dir()


# --- dedupe ------------------------------------------------------------------------------------------------------
def dedupe_key(job_pk: int, package_hash: str, input_hashes: dict, settings_hash: str, profile: str | None,
               locks_hash: str = "") -> str:
    import hashlib
    blob = json.dumps({"job": job_pk, "package": package_hash, "inputs": input_hashes, "settings": settings_hash,
                       "profile": profile or "auto", "locks": locks_hash}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def reusable(job_dir: Path, key: str) -> dict | None:
    """The current version when it was built from the same job, inputs and settings, passed every check, and has
    not been edited by hand since (an edited version must be rebuilt to match its PDF)."""
    cur = current(job_dir)
    if not cur:
        return None
    vdir = job_dir / f"v{cur['version']}"
    try:
        m = read_manifest(vdir)
    except (OSError, ValueError):
        return None
    if m.get("dedupe_key") != key or m.get("status") not in ("ready", "reviewed"):
        return None
    if detect_edits(vdir)["edited"]:
        return None
    return {"version": cur["version"], "status": m["status"], "pdf": str(vdir / m["files"]["pdf"])}


# --- rollback / history -------------------------------------------------------------------------------------------
def rollback(job_dir: Path, version: int) -> dict:
    vdir = job_dir / f"v{int(version)}"
    if not vdir.exists():
        raise ValueError(f"v{version} does not exist (versions: {[p.name for p in version_dirs(job_dir)]})")
    before = (current(job_dir) or {}).get("version")
    cur = set_current(job_dir, int(version), f"rollback from v{before}" if before else "rollback")
    _history(job_dir, {"at": _now(), "action": "rollback", "from": before, "to": int(version)})
    return cur


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _history(job_dir: Path, event: dict) -> None:
    p = job_dir / "history.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def mark_reviewed(job_dir: Path, version: int | None = None, at: str | None = None) -> dict:
    """You reviewed/approved a version: status 'reviewed' (only a version that passed every check)."""
    v = int(version or (current(job_dir) or {}).get("version") or 0)
    vdir = job_dir / f"v{v}"
    if not v or not vdir.exists():
        raise ValueError("no version to mark reviewed")
    m = read_manifest(vdir)
    if m.get("status") not in ("ready", "reviewed"):
        raise ValueError(f"v{v} is '{m.get('status')}': only a ready version can be marked reviewed")
    m["status"], m["reviewed_at"] = "reviewed", at or _now()
    (vdir / "manifest.json").write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
    if (current(job_dir) or {}).get("version") == v:
        set_current(job_dir, v, "marked reviewed")
    _history(job_dir, {"at": m["reviewed_at"], "action": "reviewed", "version": v})
    return m


# --- diff -----------------------------------------------------------------------------------------------------
def _content_of(vdir: Path):
    from .content_schema import parse
    return parse((vdir / "content.json").read_text(encoding="utf-8"))


def diff(job_dir: Path, a: int | None = None, b: int | None = None) -> str:
    """Bullet-level and line-level diff between two versions (default: the previous version vs the latest)."""
    import difflib
    vs = version_dirs(job_dir)
    if len(vs) < 2 and (a is None or b is None):
        raise ValueError("only one version so far: nothing to compare")
    va = job_dir / f"v{a}" if a else vs[-2]
    vb = job_dir / f"v{b}" if b else vs[-1]
    for v in (va, vb):
        if not v.exists():
            raise ValueError(f"{v.name} does not exist")
    ca, cb = _content_of(va), _content_of(vb)
    ba = {bl.id: (e, bl.text) for _s, e, bl in ca.all_bullets()}
    bb = {bl.id: (e, bl.text) for _s, e, bl in cb.all_bullets()}
    L = [f"# Diff {va.name} → {vb.name}", ""]
    removed = [k for k in ba if k not in bb]
    added = [k for k in bb if k not in ba]
    # same evidence + entry but new text counts as "changed"
    ev_a = {tuple(bl.evidence_ids) + (e,): bl.id for _s, e, bl in ca.all_bullets()}
    changed = [(k, k) for k in ba if k in bb and ba[k][1] != bb[k][1]]     # same bullet, new wording
    for k in list(added):
        bl = next(x for _s, e, x in cb.all_bullets() if x.id == k)
        key_ = tuple(bl.evidence_ids) + (bb[k][0],)
        if key_ in ev_a and ev_a[key_] in removed:
            changed.append((ev_a[key_], k))
            removed.remove(ev_a[key_])
            added.remove(k)
    for old, new in changed:
        L.append(f"~ {bb[new][0]}: {ba[old][1]}\n  → {bb[new][1]}")
    L += [f"- {ba[k][0]}: {ba[k][1]}" for k in removed]
    L += [f"+ {bb[k][0]}: {bb[k][1]}" for k in added]
    if ca.section_order != cb.section_order:
        L.append(f"section order: {ca.section_order} → {cb.section_order}")
    sa = {c.category: [i.name for i in c.items] for c in ca.skills}
    sb = {c.category: [i.name for i in c.items] for c in cb.skills}
    if sa != sb:
        L.append(f"skills: {sa} → {sb}")
    for v in (va, vb):
        ed = detect_edits(v)
        if ed["edited"]:
            L.append(f"note: {v.name} was edited by hand after it was generated "
                     f"({'content.json' if ed['json'] else ''}{' + ' if ed['json'] and ed['tex'] else ''}"
                     f"{'content.tex' if ed['tex'] else ''}); compared as edited")
    if len(L) == 2 or all(x.startswith("note:") for x in L[2:]):
        L.append("no content changes")
    ta = (va / "content.tex").read_text(encoding="utf-8").splitlines()
    tb = (vb / "content.tex").read_text(encoding="utf-8").splitlines()
    L += ["", "## content.tex", *difflib.unified_diff(ta, tb, va.name, vb.name, lineterm="", n=0)]
    return "\n".join(L) + "\n"


# --- locks -----------------------------------------------------------------------------------------------------
LOCKABLE_SECTIONS = ("skills", "education", "summary")


def load_locks(job_dir: Path) -> dict:
    p = job_dir / "locks.json"
    if not p.exists():
        return {"bullets": {}, "entries": {}, "sections": {}}
    d = json.loads(p.read_text(encoding="utf-8"))
    return {"bullets": d.get("bullets", {}), "entries": d.get("entries", {}), "sections": d.get("sections", {})}


def save_locks(job_dir: Path, locks: dict) -> None:
    tmp = job_dir / "locks.json.tmp"
    tmp.write_text(json.dumps(locks, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(job_dir / "locks.json")


def locks_hash(locks: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(locks, sort_keys=True).encode()).hexdigest()[:16] if any(locks.values()) else ""


def lock(job_dir: Path, target: str, version: int | None = None) -> str:
    """Lock a bullet (its id, e.g. exp-accenture:1a2b3c), an entry (exp-accenture: all its bullets, as they are) or a
    section (section:skills / section:education / section:summary) from the current (or given) version."""
    v = int(version or (current(job_dir) or {}).get("version") or 0)
    if not v:
        raise ValueError("no version yet: build one, then lock what you approve")
    c = _content_of(job_dir / f"v{v}")
    locks = load_locks(job_dir)
    if target.startswith("section:"):
        sec = target.split(":", 1)[1]
        if sec not in LOCKABLE_SECTIONS:
            raise ValueError(f"lockable sections: {', '.join(LOCKABLE_SECTIONS)}")
        val = getattr(c, sec)
        locks["sections"][sec] = ([x.model_dump(mode="json") for x in val] if isinstance(val, list)
                                  else (val.model_dump(mode="json") if val else None))
        what = f"section {sec}"
    elif ":" in target:
        hit = next(((s, e, b) for s, e, b in c.all_bullets() if b.id == target), None)
        if not hit:
            raise ValueError(f"no bullet '{target}' in v{v} (python -m resume_builder show <job> --bullets)")
        s, e, b = hit
        locks["bullets"][target] = {"section": s, "entry_id": e, **b.model_dump(mode="json")}
        what = f"bullet {target}"
    else:
        ent = next((x for x in [*c.experience, *c.projects] if x.entry_id == target), None)
        if not ent:
            raise ValueError(f"no entry '{target}' in v{v}")
        locks["entries"][target] = ent.model_dump(mode="json")
        what = f"entry {target}"
    save_locks(job_dir, locks)
    _history(job_dir, {"at": _now(), "action": "lock", "target": target, "version": v})
    return what


def unlock(job_dir: Path, target: str) -> bool:
    locks = load_locks(job_dir)
    hit = False
    for k in ("bullets", "entries"):
        hit = locks[k].pop(target, None) is not None or hit
    if target.startswith("section:"):
        hit = locks["sections"].pop(target.split(":", 1)[1], None) is not None or hit
    save_locks(job_dir, locks)
    return hit


def apply_locks(content, locks: dict) -> tuple[object, list[str]]:
    """Put locked items back exactly as approved (regeneration never changes them). Returns notes."""
    from .content_schema import Bullet, EducationEntry, ExperienceEntry, ProjectEntry, SkillCategory, Summary
    notes = []
    c = content
    for sec, val in locks.get("sections", {}).items():
        if sec == "skills" and val:
            new = [SkillCategory.model_validate(x) for x in val]
            if [x.model_dump() for x in c.skills] != [x.model_dump() for x in new]:
                notes.append("locked section skills restored")
            c.skills = new
        elif sec == "education" and val:
            new = [EducationEntry.model_validate(x) for x in val]
            if [x.model_dump() for x in c.education] != [x.model_dump() for x in new]:
                notes.append("locked section education restored")
            c.education = new
        elif sec == "summary":
            new = Summary.model_validate(val) if val else None
            if (c.summary.model_dump() if c.summary else None) != (new.model_dump() if new else None):
                notes.append("locked summary restored")
            c.summary = new
    for eid, val in locks.get("entries", {}).items():
        cls, lst = (ExperienceEntry, c.experience) if eid.startswith("exp-") else (ProjectEntry, c.projects)
        new = cls.model_validate(val)
        i = next((n for n, x in enumerate(lst) if x.entry_id == eid), None)
        if i is None:
            lst.append(new)
            notes.append(f"locked entry {eid} restored (it was removed)")
        elif lst[i].model_dump() != new.model_dump():
            lst[i] = new
            notes.append(f"locked entry {eid} restored")
    for bid, val in locks.get("bullets", {}).items():
        v = {k: x for k, x in val.items() if k not in ("section", "entry_id")}
        new = Bullet.model_validate(v)
        lst = c.experience if val["section"] == "experience" else c.projects
        ent = next((x for x in lst if x.entry_id == val["entry_id"]), None)
        if ent is None:
            continue    # its whole role/project was left out; the locked bullet can't stand alone
        i = next((n for n, x in enumerate(ent.bullets) if x.id == bid), None)
        if i is None:
            ent.bullets.insert(0, new)
            notes.append(f"locked bullet {bid} restored (it was removed)")
        else:
            if (ent.bullets[i].text, ent.bullets[i].evidence_ids) != (new.text, new.evidence_ids):
                notes.append(f"locked bullet {bid} restored")
            ent.bullets[i] = new
    return c, notes


def locked_ids(locks: dict) -> set[str]:
    ids = set(locks.get("bullets", {}))
    for val in locks.get("entries", {}).values():
        ids |= {b.get("id") for b in val.get("bullets", []) if b.get("id")}
    return ids


# --- manual edits ------------------------------------------------------------------------------------------------
def _tex_items(tex: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^\\item\s+(.*)$", tex, re.M)]


def unescape_tex(s: str) -> str:
    rep = {r"\textbackslash{}": "\\", r"\textasciitilde{}": "~", r"\textasciicircum{}": "^", r"\textquotedbl{}": '"'}
    for k, v in rep.items():
        s = s.replace(k, v)
    s = re.sub(r"\\([&%$#_{}])", r"\1", s)
    s = re.sub(r"\\textbf\{([^}]*)\}", r"\1", s)
    return s.strip()


def detect_edits(vdir: Path) -> dict:
    """Your edits to a version's content.json or content.tex since it was generated.
    bullets: {bullet id: edited text}; removed: [bullet ids]; conflicts: [explanations]."""
    gen = vdir / ".generated"
    out = {"edited": False, "json": False, "tex": False, "bullets": {}, "removed": [], "added": [], "conflicts": []}
    if not gen.exists():
        return out
    from .content_schema import ContentError, parse
    gj, cj = gen / "content.json", vdir / "content.json"
    json_bullets = {}
    if cj.exists() and gj.exists() and cj.read_bytes() != gj.read_bytes():
        out["edited"] = out["json"] = True
        try:
            g, c = parse(gj.read_text(encoding="utf-8")), parse(cj.read_text(encoding="utf-8"))
        except ContentError as e:
            out["conflicts"].append(f"content.json was edited but is no longer valid: {str(e)[:200]}")
            g = c = None
        if g and c:
            gb = {b.id: b for _s, _e, b in g.all_bullets()}
            cb = {b.id: (e, b) for _s, e, b in c.all_bullets()}
            for bid, (e, b) in cb.items():
                if bid not in gb:
                    out["added"].append({"entry_id": e, **b.model_dump(mode="json")})
                elif b.text != gb[bid].text or b.evidence_ids != gb[bid].evidence_ids:
                    json_bullets[bid] = b.text
            out["removed"] += [bid for bid in gb if bid not in cb]
    gt, ct = gen / "content.tex", vdir / "content.tex"
    if ct.exists() and gt.exists() and ct.read_bytes() != gt.read_bytes():
        out["edited"] = out["tex"] = True
        gi, ci = _tex_items(gt.read_text(encoding="utf-8")), _tex_items(ct.read_text(encoding="utf-8"))
        g = parse(gj.read_text(encoding="utf-8")) if gj.exists() else None
        skill_n = len(g.skills) if g else 0
        order = _bullet_order_in_tex(g) if g else []
        struct_g = re.sub(r"^\\item.*$", "", gt.read_text(encoding="utf-8"), flags=re.M)
        struct_c = re.sub(r"^\\item.*$", "", ct.read_text(encoding="utf-8"), flags=re.M)
        if len(gi) != len(ci) or struct_g != struct_c:
            out["conflicts"].append("content.tex was edited beyond bullet text (items added/removed or other lines "
                                    "changed): those edits can't be carried into a new version automatically; make "
                                    "them in content.json instead (the edited file stays in this version)")
        else:
            for n, (a, b) in enumerate(zip(gi, ci)):
                if a != b and order and n < len(order) and order[n]:
                    bid = order[n]
                    txt = unescape_tex(b)
                    if bid in json_bullets and json_bullets[bid] != txt:
                        out["conflicts"].append(f"bullet {bid} was edited differently in content.json and content.tex")
                    json_bullets.setdefault(bid, txt)
                elif a != b and n < skill_n:
                    out["conflicts"].append("a Technical Skills line was edited in content.tex; edit the skills "
                                            "in content.json instead")
    out["bullets"] = json_bullets
    return out


def _bullet_order_in_tex(c) -> list[str | None]:
    """\\item order in content.tex: skills lines (None), then bullets in section order."""
    order: list[str | None] = []
    for sec in c.section_order:
        if sec == "skills":
            order += [None] * len(c.skills)
        elif sec == "experience":
            order += [b.id for e in c.experience for b in e.bullets]
        elif sec == "projects":
            order += [b.id for p in c.projects for b in p.bullets]
    return order
