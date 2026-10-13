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
                 manifest: dict, font_files: list[str]) -> Path:
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
