"""Jobs found outside HireScope ("External"): a JD you paste or a file (.txt, .md, .pdf, .docx), imported into a
normal job record with source "external", scored like any other job and given a job package.

Ways in (all end in import_external):
  - CLI: ./scripts/add_job.sh --company X --role Y --file jd.pdf [--url …] [--track coop|fulltime]   (now)
  - Dashboard "Add external job": saves hirescope-external-job-*.json (to ~/Downloads); each run imports it, or
    ./scripts/import_pending.sh now (with the newest Export statuses; or ./scripts/add_job.sh --pending)
  - Drop folder inputs/external_jobs/: any JD file is imported on the next run. Company / role / URL come from a
    small header at the top of the file ("Company: …", "Role: …", "URL: …", "Location: …", "Track: …") or from the
    file name ("Company - Role.pdf"). Imported files move to data.nosync/external_jobs/<Company>/ (the saved
    original); files that can't be imported stay put and the reason is logged.

Duplicates: an external job that matches an existing job (same posting URL, the same JD, or the same company + role
with a very similar JD) is not created again; you get a warning naming the existing job instead (--force adds it
anyway). The JD is data, never instructions: it is stored as text and nothing in it is followed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

from rapidfuzz import fuzz

from . import paths
from .adapters.base import RawJob
from .adapters.canonical import canonical_key
from .extract.normalize import normalize_title
from .extract.text import normalize_bullets
from .pipeline import jd_hash, to_record
from .storage import db

log = logging.getLogger(__name__)

SOURCE = "external"
EXTERNAL_SOURCES = (SOURCE, "added_url")      # both are jobs you added yourself (badge "External")
FILE_TYPES = (".txt", ".md", ".pdf", ".docx")
JSON_GLOB = "hirescope-external-job*.json"    # what the dashboard's "Add external job" form saves
MAX_FILE = 15_000_000
MAX_JD = 200_000
TRACKS = {"coop": "coop_intern", "co-op": "coop_intern", "coop_intern": "coop_intern", "intern": "coop_intern",
          "internship": "coop_intern", "fulltime": "full_time", "full-time": "full_time", "full_time": "full_time",
          "full time": "full_time", "newgrad": "full_time", "new grad": "full_time"}
SAME_ROLE = 90          # token_sort_ratio of normalized titles
SIMILAR_JD = 90         # token_set_ratio of the JD texts (same company)
SAME_ROLE_JD = 75       # a looser JD match is enough when the role is the same too
HEADER_KEYS = {"company": "company", "employer": "company", "role": "role", "title": "role", "position": "role",
               "job title": "role", "url": "url", "link": "url", "job url": "url", "apply url": "url",
               "location": "location", "track": "track"}
_HEADER = re.compile(r"^\s*(?:[#*>-]\s*)?\**\s*(" + "|".join(sorted(HEADER_KEYS, key=len, reverse=True))
                     + r")\s*\**\s*[:=]\s*(.+?)\s*$", re.I)


class ExternalError(ValueError):
    """The job can't be imported (missing company / role / JD, unreadable file)."""


@dataclass
class ExternalJob:
    company: str
    role: str
    jd_text: str
    url: str | None = None
    location: str | None = None
    track: str | None = None          # coop_intern | full_time | None (detected from the JD)
    origin: str = "cli"               # paste | file | drop | dashboard | cli
    file_name: str | None = None
    file_bytes: bytes | None = field(default=None, repr=False)   # the original upload, kept as your saved copy

    def job_id(self) -> str:
        """Stable id: the same company + role + URL (or JD, without a URL) is the same external job."""
        basis = "|".join([db.company_key(self.company), " ".join(self.role.casefold().split()),
                          norm_url(self.url) or jd_hash(self.jd_text)])
        return "ext-" + hashlib.sha256(basis.encode()).hexdigest()[:12]


@dataclass
class Outcome:
    status: str                     # new | duplicate | error
    job_pk: int | None = None
    company: str = ""
    title: str = ""
    message: str = ""
    duplicate: dict | None = None   # {job_pk, company, title, source, kind, message}
    file: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


# --- reading ---------------------------------------------------------------------------------------------------------
def norm_track(v: str | None) -> str | None:
    if not v:
        return None
    t = TRACKS.get(" ".join(str(v).casefold().replace("/", " ").split()))
    if t is None:
        raise ExternalError(f"unknown track {v!r} (use coop or fulltime)")
    return t


def norm_url(u: str | None) -> str | None:
    """Comparable form of a posting URL: lower-case host, no fragment, no tracking parameters, no trailing slash."""
    if not u or not re.match(r"^https?://", u.strip(), re.I):
        return None
    s = urlsplit(u.strip())
    q = "&".join(p for p in s.query.split("&") if p and not re.match(r"(?:utm_[a-z]+|gh_src|source|ref|src|lever-source[^=]*)=", p, re.I))
    return urlunsplit((s.scheme.lower(), s.netloc.lower().removeprefix("www."), s.path.rstrip("/"), q, ""))


def _docx_text(data: bytes) -> str:
    """Paragraph text of a .docx (stdlib only: the document is a zip of XML)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ExternalError(f"not a readable .docx file ({e})") from e
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ElementTree.fromstring(xml)
    out = []
    for p in root.iter(f"{ns}p"):
        parts = []
        for n in p.iter():
            if n.tag == f"{ns}t" and n.text:
                parts.append(n.text)
            elif n.tag == f"{ns}tab":
                parts.append("\t")
            elif n.tag in (f"{ns}br", f"{ns}cr"):
                parts.append("\n")
        bullet = p.find(f"{ns}pPr/{ns}numPr") is not None
        line = "".join(parts).strip()
        out.append(f"- {line}" if bullet and line else line)
    return "\n".join(out)


def _pdf_text(data: bytes) -> str:
    try:
        import pdfplumber    # the Resume Builder's PDF reader (requirements.txt)
    except ImportError as e:   # pragma: no cover - installed by setup.sh
        raise ExternalError("reading PDFs needs pdfplumber (./scripts/setup.sh installs it)") from e
    logging.getLogger("pdfminer").setLevel(logging.WARNING)
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n".join(_pdf_page_text(p) for p in pdf.pages)
    except ExternalError:
        raise
    except Exception as e:  # noqa: BLE001 - pdfminer raises many types for broken files
        raise ExternalError(f"not a readable PDF ({type(e).__name__})") from e
    if not text.strip():
        raise ExternalError("the PDF has no text layer (a scan?): paste the JD text instead")
    return text


_BULLET_CHARS = "•·◦▪▫‣⁃●○■□➢➤►–—-*"


def _pdf_page_text(page) -> str:
    """One page as paragraphs: wrapped lines are joined again, and list items become "- item" lines. Browsers and word
    processors draw list bullets as small shapes (not text), so a small mark just left of a line starts an item."""
    lines = page.extract_text_lines() or []
    if not lines:
        return ""
    marks = [o for o in (page.curves + page.rects) if o["x1"] - o["x0"] < 9 and o["bottom"] - o["top"] < 9]
    right = max(ln["x1"] for ln in lines)
    out: list[str] = []
    prev = None
    for ln in lines:
        t = ln["text"].strip()
        if not t:
            continue
        drawn = any(m["x1"] <= ln["x0"] + 1 and m["x0"] >= ln["x0"] - 30 and m["top"] >= ln["top"] - 3
                    and m["bottom"] <= ln["bottom"] + 3 for m in marks)
        typed = t[0] in _BULLET_CHARS and len(t) > 2 and t[1] == " "
        height = ln["bottom"] - ln["top"]
        new = (prev is None or drawn or typed or ln["top"] - prev["bottom"] > 0.8 * height
               or prev["x1"] < right - 0.12 * (right - prev["x0"]) or abs(ln["x0"] - prev["x0"]) > 6
               or prev["text"].rstrip().endswith(":"))
        if drawn and not typed:
            t = f"- {t}"
        if new:
            if prev is not None and ln["top"] - prev["bottom"] > 0.8 * height and not (drawn or typed):
                out.append("")
            out.append(t)
        elif out[-1].endswith("-") and not out[-1].endswith(" -"):
            out[-1] += t                       # "self-" + "organize" (a word split at the line end)
        else:
            out[-1] += " " + t
        prev = ln
    return "\n".join(out)


def file_text(name: str, data: bytes) -> str:
    """JD text from a .txt / .md / .pdf / .docx file's bytes."""
    ext = Path(name).suffix.lower()
    if ext not in FILE_TYPES:
        raise ExternalError(f"unsupported file type {ext or '(none)'} (use {', '.join(FILE_TYPES)})")
    if len(data) > MAX_FILE:
        raise ExternalError(f"file too large ({len(data) // 1_000_000} MB)")
    if ext == ".pdf":
        text = _pdf_text(data)
    elif ext == ".docx":
        text = _docx_text(data)
    else:
        text = data.decode("utf-8", errors="replace")
    return clean_text(text)


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return normalize_bullets(text.strip())[:MAX_JD]


def split_header(text: str) -> tuple[dict, str]:
    """Optional "Key: value" lines at the top (Company, Role/Title, URL, Location, Track), ended by a blank line or
    '---'. Returns (fields, the JD without the header). Without a recognized header the text is returned whole."""
    lines = text.splitlines()
    fields: dict[str, str] = {}
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    start = i
    while i < len(lines):
        ln = lines[i]
        if not ln.strip() or re.match(r"^\s*-{3,}\s*$", ln):
            break
        m = _HEADER.match(ln)
        if not m:
            break
        fields.setdefault(HEADER_KEYS[m.group(1).casefold()], m.group(2).strip().strip("*").strip())
        i += 1
    if not fields:
        return {}, text
    if i < len(lines) and re.match(r"^\s*-{3,}\s*$", lines[i]):
        i += 1
    rest = "\n".join(lines[i:]).strip()
    return (fields, rest) if rest or i > start else ({}, text)


def name_fields(file_name: str) -> dict:
    """"Acme - Software Engineer Co-op.pdf" / "Acme__Backend_Engineer.docx" -> company + role."""
    stem = Path(file_name).stem
    for sep in (" - ", " – ", " — ", "__"):
        if sep in stem:
            co, role = stem.split(sep, 1)
            co, role = co.replace("_", " ").strip(), role.replace("_", " ").strip()
            if co and role:
                return {"company": co, "role": role}
    return {}


def from_text(text: str, *, company: str | None = None, role: str | None = None, url: str | None = None,
              location: str | None = None, track: str | None = None, origin: str = "paste",
              file_name: str | None = None, file_bytes: bytes | None = None) -> ExternalJob:
    """Build an ExternalJob. Explicit arguments win over a header in the text, which wins over the file name."""
    hdr, body = split_header(clean_text(text or ""))
    byname = name_fields(file_name) if file_name else {}
    pick = lambda k, v: (v or "").strip() or hdr.get(k) or byname.get(k) or None  # noqa: E731
    co, ti = pick("company", company), pick("role", role)
    missing = [n for n, v in (("company", co), ("role", ti)) if not v]
    if missing:
        hint = (" (add 'Company: …' / 'Role: …' lines at the top of the file, or name it 'Company - Role"
                f"{Path(file_name).suffix}')") if file_name else ""
        raise ExternalError(f"missing {' and '.join(missing)}{hint}")
    body = body.strip()
    if len(body) < 40:
        raise ExternalError("the job description is empty or too short to score (paste the full JD)")
    u = pick("url", url)
    if u and not re.match(r"^https?://", u, re.I):
        raise ExternalError(f"the job URL must start with http:// or https:// (got {u[:60]!r})")
    return ExternalJob(company=co[:200], role=ti[:300], jd_text=body, url=u, location=pick("location", location),
                       track=norm_track(pick("track", track)), origin=origin, file_name=file_name,
                       file_bytes=file_bytes)


def from_file(path: Path, origin: str = "file", **kw) -> ExternalJob:
    data = path.read_bytes()
    return from_text(file_text(path.name, data), origin=origin, file_name=path.name,
                     file_bytes=data if path.suffix.lower() in (".pdf", ".docx") else None, **kw)


def from_json(payload: dict, origin: str = "dashboard") -> tuple[ExternalJob, bool]:
    """A dashboard export (hirescope-external-job-*.json). Returns (job, force)."""
    if not isinstance(payload, dict) or payload.get("kind") != "hirescope_external_job":
        raise ExternalError("not a HireScope external-job file")
    if payload.get("format", 1) != 1:
        raise ExternalError(f"unknown external-job format {payload.get('format')!r}")
    s = lambda k: str(payload.get(k) or "")[:2000].strip() or None  # noqa: E731
    text, name, data = str(payload.get("jd_text") or "")[:MAX_JD], None, None
    f = payload.get("file")
    if isinstance(f, dict) and f.get("name") and f.get("b64"):
        name = Path(str(f["name"])).name[:200]
        try:
            data = base64.b64decode(str(f["b64"]), validate=True)
        except ValueError as e:
            raise ExternalError("the attached file is corrupt") from e
        file_jd = file_text(name, data)
        text = f"{text}\n\n{file_jd}" if text.strip() else file_jd
        if Path(name).suffix.lower() not in (".pdf", ".docx"):
            data = None
    job = from_text(text, company=s("company"), role=s("role") or s("title"), url=s("url"),
                    location=s("location"), track=s("track"), origin=origin, file_name=name, file_bytes=data)
    return job, bool(payload.get("force"))


# --- duplicates ------------------------------------------------------------------------------------------------------
def _jd_of(row) -> str:
    p = paths.stored_file(row["jd_path"], paths.JD_DIR)
    try:
        return p.read_text(encoding="utf-8") if p else ""
    except OSError:
        return ""


def _jd_sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(" ".join(a[:6000].split()), " ".join(b[:6000].split()))


def find_duplicate(conn, ext: ExternalJob, rules=None) -> dict | None:
    """An existing job that is the same posting: same URL (or ATS posting), the same JD text, or the same company and
    role with a similar JD. Closed jobs count too (you'd want the history, not a second record)."""
    def hit(r, kind, why):
        return {"job_pk": r["id"], "company": r["company"], "title": r["title"], "source": r["source"],
                "job_status": r["job_status"], "kind": kind,
                "message": f"Already in HireScope as job {r['id']}: {r['company']} — {r['title']} ({why})"}
    u, ck = norm_url(ext.url), canonical_key(ext.url) if ext.url else None
    if ck:
        r = conn.execute("SELECT j.* FROM jobs j WHERE j.canonical_key=? OR j.id IN (SELECT job_pk FROM job_sources "
                         "WHERE canonical_key=?) LIMIT 1", (ck, ck)).fetchone()
        if r:
            return hit(r, "same_url", "same posting URL")
    if u:
        for r in conn.execute("SELECT * FROM jobs WHERE apply_url IS NOT NULL OR source_url IS NOT NULL"):
            if u in (norm_url(r["apply_url"]), norm_url(r["source_url"])):
                return hit(r, "same_url", "same posting URL")
    h = jd_hash(ext.jd_text)
    norm = normalize_title(ext.role, rules) if rules is not None else " ".join(ext.role.casefold().split())
    rows = conn.execute("SELECT * FROM jobs WHERE company_key=?", (db.company_key(ext.company),)).fetchall()
    for r in rows:
        if r["jd_hash"] == h:
            return hit(r, "same_jd", "same job description")
    best = None
    for r in rows:
        role_sim = fuzz.token_sort_ratio(r["normalized_title"], norm)
        jd = _jd_sim(_jd_of(r), ext.jd_text)
        if jd >= SIMILAR_JD or (role_sim >= SAME_ROLE and jd >= SAME_ROLE_JD):
            why = (f"same role, {round(jd)}% similar JD" if role_sim >= SAME_ROLE else f"{round(jd)}% similar JD")
            score = jd + role_sim
            if best is None or score > best[0]:
                best = (score, hit(r, "similar_jd" if role_sim < SAME_ROLE else "same_role", why))
    return best[1] if best else None


# --- import ----------------------------------------------------------------------------------------------------------
def _save_original(ext: ExternalJob, job_id: str) -> str | None:
    if not ext.file_bytes or not ext.file_name:
        return None
    d = paths.EXTERNAL_DIR / paths.slug(ext.company)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{job_id}{Path(ext.file_name).suffix.lower()}"
    p.write_bytes(ext.file_bytes)
    return str(p)


def import_external(conn, cfg, ext: ExternalJob, jd_dir: Path | None = None, *, force: bool = False,
                    now: str | None = None) -> Outcome:
    """Create the job record (not scored yet: call pipeline.score_all, then write_packages). Duplicates are reported,
    not created, unless force."""
    jd_dir = paths.JD_DIR if jd_dir is None else jd_dir
    now = now or db.utcnow()
    jid = ext.job_id()
    mine = conn.execute("SELECT j.* FROM job_sources s JOIN jobs j ON j.id = s.job_pk WHERE s.source=? AND "
                        "s.company_key=? AND s.source_job_id=?", (SOURCE, db.company_key(ext.company), jid)).fetchone()
    if mine:
        return Outcome("duplicate", mine["id"], mine["company"], mine["title"],
                       f"Already imported as job {mine['id']}: {mine['company']} — {mine['title']}",
                       {"job_pk": mine["id"], "company": mine["company"], "title": mine["title"], "source": SOURCE,
                        "job_status": mine["job_status"], "kind": "same_external", "message": "already imported"})
    if not force and (dup := find_duplicate(conn, ext, cfg.title_rules)):
        return Outcome("duplicate", dup["job_pk"], dup["company"], dup["title"], dup["message"], dup)
    raw = RawJob(company=ext.company, job_id=jid, title=ext.role, source=SOURCE, source_url=ext.url or "",
                 apply_url=ext.url, location=ext.location, description=ext.jd_text,
                 canonical_key=canonical_key(ext.url) if ext.url else None,   # your track is an override (below);
                 extra={"posted_date_unknown": True})                       # detection stays from the JD itself
    rec = to_record(raw, cfg, jd_dir, official=True)
    original = _save_original(ext, jid)
    rec.extra["external"] = {"origin": ext.origin, "added_at": now, "file_name": ext.file_name,
                             "original": original, "url_given": bool(ext.url)}
    # find_duplicate already decided this is a different job: no fuzzy title merge into an existing posting
    pk, _ = db.upsert_job(conn, rec, now=now, fuzzy=False)
    if ext.track:
        db.save_scores(conn, pk, track_override=ext.track, track_override_at=now)
    conn.commit()
    log.info("external job added", extra={"job_pk": pk, "company": ext.company, "origin": ext.origin})
    return Outcome("new", pk, ext.company, ext.role, f"Added job {pk}: {ext.company} — {ext.role}")


def write_packages(conn, resume_hash: str, base: Path | None = None) -> int:
    """Job packages (v2) for open external jobs: job_packages/<Company>/<Company>_<Role>_<JobID>.md."""
    from .output.packages import package_filename, render_package
    base = paths.PACKAGES_DIR if base is None else base
    n = 0
    for r in conn.execute(f"SELECT * FROM jobs WHERE source IN ({','.join('?' * len(EXTERNAL_SOURCES))}) "
                          "AND job_status != 'closed'", EXTERNAL_SOURCES):
        job = db.decode_row(r)
        d = base / paths.slug(job["company"])
        d.mkdir(parents=True, exist_ok=True)
        (d / package_filename(job["company"], job["title"], job["job_id"])).write_text(
            render_package(job, _jd_of(r), resume_hash), encoding="utf-8")
        n += 1
    return n


# --- pending files (drop folder + dashboard exports) -----------------------------------------------------------------
def _sig(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:24]


def _seen(conn) -> list[str]:
    try:
        return json.loads(db.get_meta(conn, "external_imported") or "[]")
    except ValueError:
        return []


def pending_files(drop_dir: Path, json_dirs: list[Path]) -> list[Path]:
    from .tracker.statuses import access
    out = []
    if access(drop_dir) == "ok":
        out += sorted(p for p in drop_dir.iterdir() if p.is_file() and not p.name.startswith(".")
                      and ".example." not in p.name and not p.name.upper().startswith("README")
                      and (p.suffix.lower() in FILE_TYPES or p.suffix.lower() == ".json"))
    for d in json_dirs:
        if d != drop_dir and access(d) == "ok":
            out += sorted(p for p in d.glob(JSON_GLOB) if p.is_file())
    return out


def import_pending(conn, cfg, drop_dir: Path | None = None, json_dirs: list[Path] | None = None,
                   jd_dir: Path | None = None, archive: bool = True) -> list[Outcome]:
    """Import every pending external job: files in the drop folder and dashboard-saved JSON files. Each file is
    imported once (by content hash); imported files are moved out of the way (drop folder → the saved original
    under data.nosync/external_jobs/, JSON → data.nosync/imports/external/). Not scored here."""
    drop_dir = paths.EXTERNAL_INBOX if drop_dir is None else drop_dir
    from .tracker.statuses import access
    seen = _seen(conn)
    out = [Outcome("error", message=f"can't read {d} (macOS privacy protection for this Python, e.g. after an "
                   "upgrade): files the dashboard saved there wait — run ./scripts/import_pending.sh yourself, "
                   "or save them to inputs/external_jobs/", file=str(d))
           for d in json_dirs or [] if access(d) == "blocked"]
    for p in pending_files(drop_dir, json_dirs or []):
        try:
            sig = _sig(p)
        except OSError as e:
            out.append(Outcome("error", message=f"{p.name}: can't read ({e})", file=str(p)))
            continue
        if sig in seen:
            _archive(p, archive, drop_dir)
            continue
        try:
            if p.suffix.lower() == ".json":
                payload = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and payload.get("kind") != "hirescope_external_job" and p.parent == drop_dir:
                    raise ExternalError("not a HireScope external-job file")
                ext, force = from_json(payload, origin="dashboard")
            else:
                ext, force = from_file(p, origin="drop"), False
            res = import_external(conn, cfg, ext, jd_dir, force=force)
        except (ExternalError, OSError, ValueError) as e:
            out.append(Outcome("error", message=f"{p.name}: {e}", file=str(p)))
            log.warning("external job not imported: %s: %s", p, e)
            continue
        res.file = str(p)
        out.append(res)
        seen = [*seen, sig][-1000:]
        db.set_meta(conn, "external_imported", json.dumps(seen))
        conn.commit()
        _archive(p, archive, drop_dir)
    return out


def _archive(p: Path, archive: bool, drop_dir: Path) -> None:
    if not archive:
        return
    dest = (paths.EXTERNAL_DIR / "_imported") if p.parent == drop_dir else (paths.IMPORTS_DIR / "external")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        shutil.move(str(p), dest / f"{stamp}_{p.name}")
    except OSError as e:     # e.g. ~/Downloads not writable for a scheduled job: the content hash prevents re-import
        log.info("left %s in place (%s)", p, e)


def summary_line(o: Outcome) -> str:
    if o.status == "new":
        return f"added: {o.company} — {o.title} (job {o.job_pk})"
    if o.status == "duplicate":
        return f"duplicate, not added: {o.message}"
    return f"not imported: {o.message}"
