"""Desktop copy of the employer-facing PDFs (config/resume_builder.yaml → desktop_copy):

  <dir>/<Company>/<Role>_<JobID>/<First>_<Last>_Resume_<Company>.pdf      the current (last READY) version
  <dir>/<Company>/<Role>_<JobID>/<First>_<Last>_Resume_<Company>_v<N>.pdf  every other READY / reviewed version
  <dir>/Default/<First>_<Last>_Resume.pdf                                  the default resume (generated_resumes/_default)

Same folder names as data.nosync/generated_resumes. Only PDFs of versions that passed every check; never .tex,
reports or build files; failed versions never. Mirrored whenever a version becomes current (build, regenerate,
`default`, rollback, mark-reviewed) and by `python -m resume_builder sync-desktop` (scripts/sync_desktop_resumes.sh).
Only files named like the resume PDFs are ever replaced or removed there; anything else in the folder is left alone.
A Desktop that macOS won't let us write (privacy permission) never fails a build: the resume stays in data.nosync and
a message says how to allow it.
"""
from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
from pathlib import Path

from . import config as rc
from . import versions

DEFAULT_DIR = "~/Desktop/Generated Resumes"
DEFAULT_JOB = "_default"
OK_STATUS = ("ready", "reviewed")


def settings_of(settings=None) -> tuple[bool, Path]:
    """(enabled, folder). JOBSEARCH_DESKTOP_DIR overrides the folder (tests point it at a temporary one)."""
    cfg = {}
    try:
        settings = settings or rc.load_settings()
        cfg = settings.raw.get("desktop_copy") or {}
    except Exception:                     # broken settings never stop a build here; the build reports them itself
        pass
    folder = os.environ.get("JOBSEARCH_DESKTOP_DIR") or str(cfg.get("dir") or DEFAULT_DIR)
    return bool(cfg.get("enabled", True)), Path(os.path.expanduser(folder))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _copy(src: Path, dst: Path) -> bool:
    if dst.exists() and dst.stat().st_size == src.stat().st_size and _sha(dst) == _sha(src):
        return False
    tmp = dst.with_name(f".{dst.name}.tmp")
    shutil.copy2(src, tmp)
    tmp.replace(dst)
    return True


def target_dir(job_dir: Path, folder: Path) -> Path:
    rel = job_dir.resolve().relative_to(versions.resumes_root().resolve())
    return folder / ("Default" if rel.as_posix() == DEFAULT_JOB else rel)


def wanted(job_dir: Path) -> dict[str, Path]:
    """Desktop file name → source PDF, for one job folder."""
    cur = (versions.current(job_dir) or {}).get("version")
    if cur is None:
        return {}
    out = {}
    for vdir in versions.version_dirs(job_dir):
        try:
            m = versions.read_manifest(vdir)
        except (OSError, ValueError):
            continue
        pdf = vdir / ((m.get("files") or {}).get("pdf") or "")
        if m.get("status") not in OK_STATUS or not pdf.is_file() or pdf.suffix != ".pdf":
            continue
        n = int(vdir.name[1:])
        out[pdf.name if n == cur else f"{pdf.stem}_v{n}.pdf"] = pdf
    return out


def _managed(name: str, stems: set[str]) -> bool:
    return any(re.fullmatch(rf"{re.escape(s)}(?:_v\d+)?\.pdf", name) for s in stems)


def sync_job(job_dir: Path, folder: Path) -> dict:
    """Mirror one job's READY PDFs. {copied, removed, dir}; raises OSError (e.g. macOS privacy) to the caller."""
    want = wanted(job_dir)
    dest = target_dir(job_dir, folder)
    res = {"copied": [], "removed": [], "dir": dest}
    if not want:
        return res
    dest.mkdir(parents=True, exist_ok=True)
    for name, src in want.items():
        if _copy(src, dest / name):
            res["copied"].append(name)
    stems = {re.sub(r"_v\d+$", "", Path(n).stem) for n in want}
    for p in dest.glob("*.pdf"):
        if p.name not in want and _managed(p.name, stems):
            p.unlink()
            res["removed"].append(p.name)
    return res


def blocked_message(folder: Path, err: OSError) -> str:
    return (f"Desktop copy skipped: macOS did not allow writing to {folder} ({err.strerror or err}). The resume is "
            "saved in data.nosync/generated_resumes as usual. To allow it, run ./scripts/sync_desktop_resumes.sh "
            "in Terminal and click OK if macOS asks for access to the Desktop folder (or turn it on under System "
            "Settings → Privacy & Security → Files and Folders → Terminal → Desktop Folder); the script then "
            "copies everything. To turn the copy off: desktop_copy.enabled: false in config/resume_builder.yaml.")


def after_current(job_dir: Path, settings=None) -> str | None:
    """Called when a version becomes current: copy to the Desktop if enabled. Never raises; returns a line to print."""
    enabled, folder = settings_of(settings)
    if not enabled:
        return None
    try:
        res = sync_job(job_dir, folder)
    except OSError as e:
        if e.errno in (errno.EPERM, errno.EACCES):
            return blocked_message(folder, e)
        return f"Desktop copy skipped ({e}); the resume is saved in data.nosync/generated_resumes."
    except ValueError:                    # a job folder outside generated_resumes: nothing to mirror
        return None
    return f"Desktop copy: {res['dir']}" if res["copied"] or res["removed"] else None


def job_dirs() -> list[Path]:
    """Every job folder with a current version: generated_resumes/_default and <Company>/<Role>_<JobID>."""
    root = versions.resumes_root()
    if not root.is_dir():
        return []
    out = [root / DEFAULT_JOB] if (root / DEFAULT_JOB / "current.json").exists() else []
    for company in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
        out += sorted(p for p in company.iterdir() if p.is_dir() and (p / "current.json").exists())
    return out


def sync_all(settings=None, force: bool = False) -> tuple[int, list[str]]:
    """Mirror every job (backfill / re-sync). (exit code, lines to print). `force` runs even when disabled."""
    enabled, folder = settings_of(settings)
    if not (enabled or force):
        return 0, ["Desktop copy is off (desktop_copy.enabled: false in config/resume_builder.yaml); nothing copied."]
    lines, copied, removed = [], 0, 0
    try:
        for jd in job_dirs():
            res = sync_job(jd, folder)
            copied += len(res["copied"])
            removed += len(res["removed"])
            for n in res["copied"]:
                lines.append(f"  copied   {res['dir'].relative_to(folder) / n}")
            for n in res["removed"]:
                lines.append(f"  removed  {res['dir'].relative_to(folder) / n}")
    except OSError as e:
        if e.errno in (errno.EPERM, errno.EACCES):
            return 1, [blocked_message(folder, e)]
        return 1, [f"Desktop copy failed: {e}"]
    lines.append(f"Desktop folder {folder}: {copied} copied, {removed} removed, {len(job_dirs())} resume folder(s).")
    return 0, lines
