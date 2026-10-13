"""Tectonic wrapper: isolated build directory, no shell escape (--untrusted), timeout, full log captured.

Tectonic downloads the TeX packages it needs on first use and caches them
(~/Library/Caches/TectonicProject.Tectonic); later builds are offline. The main font is a system font (Calibri, or
Carlito when Calibri isn't installed); the font files used are copied into the version's source bundle so the PDF can
be rebuilt elsewhere.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


class CompileError(Exception):
    pass


@dataclass
class CompileResult:
    ok: bool
    pdf: Path | None
    log_text: str
    seconds: float
    errors: list[str] = field(default_factory=list)
    overfull: list[str] = field(default_factory=list)      # "Overfull \hbox (3.2pt too wide) in paragraph at lines 12--13"
    underfull: int = 0
    missing_glyphs: list[str] = field(default_factory=list)
    font_used: str | None = None
    font_files: list[str] = field(default_factory=list)
    timed_out: bool = False


def tectonic_path() -> str | None:
    return shutil.which("tectonic")


def tectonic_version() -> str:
    exe = tectonic_path()
    if not exe:
        return "missing"
    try:
        return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def redact(text: str) -> str:
    """Logs never keep contact details (overfull-box messages quote the header line)."""
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text))


def parse_log(text: str) -> dict:
    errors = [ln.strip() for ln in text.splitlines() if ln.startswith("! ") or ln.startswith("error:")]
    over = re.findall(r"Overfull \\hbox \(([\d.]+)pt too wide\)[^\n]*", text)
    over_lines = [m.group(0) for m in re.finditer(r"Overfull \\hbox \([\d.]+pt too wide\)[^\n]*", text)]
    missing = sorted(set(re.findall(r"Missing character: There is no (.+?) in font", text)))
    font = re.search(r"RB-FONT: (\S+)", text)
    files = sorted(set(re.findall(r"accessing absolute path `([^`]+\.(?:ttf|otf|ttc))`", text)))
    if "cannot be found" in text and "fontspec" in text:
        errors.append("a font the template asks for is not installed (see fonts in config/resume_builder.yaml)")
    return {"errors": errors, "overfull": [ln for ln, w in zip(over_lines, over)], "overfull_pt": [float(w) for w in over],
            "underfull": len(re.findall(r"Underfull \\[hv]box", text)), "missing": missing,
            "font": font.group(1) if font else None, "font_files": files}


def compile_tex(build_dir: Path, main: str = "main.tex", timeout_s: int = 120) -> CompileResult:
    exe = tectonic_path()
    if not exe:
        raise CompileError("Tectonic is not installed: brew install tectonic")
    if not (build_dir / main).exists():
        raise CompileError(f"{main} not found in the build directory")
    t0 = time.monotonic()
    cmd = [exe, "-X", "compile", "--untrusted", "--keep-logs", "--outdir", str(build_dir), str(build_dir / main)]
    try:
        p = subprocess.run(cmd, cwd=build_dir, capture_output=True, text=True, timeout=timeout_s,
                           stdin=subprocess.DEVNULL)
        out = (p.stdout or "") + "\n" + (p.stderr or "")
        code = p.returncode
        timed_out = False
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) + \
              "\nTIMEOUT after %ss" % timeout_s
        code, timed_out = -1, True
    texlog = build_dir / Path(main).with_suffix(".log").name
    full = out + ("\n--- TeX log ---\n" + texlog.read_text(encoding="utf-8", errors="replace") if texlog.exists() else "")
    info = parse_log(full)
    pdf = build_dir / Path(main).with_suffix(".pdf").name
    ok = code == 0 and pdf.exists() and not timed_out
    if timed_out:
        info["errors"].append(f"Tectonic timed out after {timeout_s}s")
    elif code != 0 and not info["errors"]:
        info["errors"].append(f"Tectonic exited with code {code}")
    return CompileResult(ok, pdf if pdf.exists() else None, redact(full), round(time.monotonic() - t0, 2),
                         info["errors"], info["overfull"], info["underfull"], info["missing"], info["font"],
                         info["font_files"], timed_out)
