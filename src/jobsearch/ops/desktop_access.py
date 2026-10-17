"""Desktop / Downloads work that can never hang a background job.

macOS asks once per program before it may open files in ~/Desktop or ~/Downloads that another program wrote. A
background job (a scheduled run, the resume worker, the launcher app) has no window for that question, and the open()
or rename() then waits forever (seen 2026-10-17: a quick check sat an hour on the applications Excel file; the worker
and the launcher hung the same way). A program may always touch files it created itself, so no probe can tell in
advance. Instead the real work runs in a child process with a timeout:

    place <src> <target>     put a finished file (the Excel workbook) in place: atomic replace, or "<name> (pending)"
                             beside it while Excel has it open; prints a JSON result
    sync-desktop [job_dir]   mirror READY resume PDFs to the Desktop folder (one job, or all)
    read <folder> [name]     may this process list the folder and open a file in it (Downloads)

A child that fails or hangs is killed; the caller skips that folder, records why (dashboard notice) and leaves a marker
so the next sync tries again. Everything always stays in data.nosync.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from .. import paths

log = logging.getLogger(__name__)
_cache: dict[str, tuple[bool, str]] = {}
TIMEOUT_PLACE = 20.0
TIMEOUT_SYNC = 90.0


def how_to_allow(folder: Path) -> str:
    return (f"macOS did not let this background job use {folder}. To allow it: System Settings → Privacy & Security → "
            "Files and Folders → turn on Desktop Folder (and Downloads Folder) for Python and HireScope Launcher, or "
            "answer the macOS prompt if one is showing. Everything is still saved in data.nosync; the next sync after "
            "that copies it over.")


def _child(args: list[str], timeout: float) -> tuple[bool, str]:
    env = {**os.environ, "PYTHONPATH": str(paths.ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "JOBSEARCH_DATA_DIR": str(paths.DATA), "JOBSEARCH_DASHBOARD_DIR": str(paths.DASHBOARD),
           "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run([sys.executable, "-m", "jobsearch.ops.desktop_access", *args], capture_output=True,
                           text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, "the write was waiting for macOS permission"
    except OSError as e:
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or f"exit {r.returncode}").strip()[-300:]
    return True, r.stdout.strip()


def place(src: Path, target: Path) -> dict:
    """In a child: the Excel file goes in place (or beside it, "(pending)", while Excel has it open)."""
    ok, out = _child(["place", str(src), str(target)], TIMEOUT_PLACE)
    if ok:
        try:
            return json.loads(out.splitlines()[-1])
        except (ValueError, IndexError):
            return {"status": "error", "message": out[-200:]}
    log.warning("Desktop write skipped", extra={"target": str(target), "reason": out[-200:]})
    mark_pending(out)
    return {"status": "error", "message": f"{how_to_allow(target.parent)} ({out[-120:]})"}


def sync_desktop(job_dir: Path | None = None) -> tuple[bool, str]:
    """In a child: mirror READY resume PDFs to the Desktop folder (one job or all). (ok, last line)."""
    from resume_builder import desktop
    enabled, folder = desktop.settings_of()
    if not enabled:
        return True, ""
    ok, out = _child(["sync-desktop", *([str(job_dir)] if job_dir else [])], TIMEOUT_SYNC)
    if not ok:
        log.warning("Desktop copy skipped", extra={"reason": out[-200:]})
        mark_pending(out)
        return False, f"Desktop copy skipped: {how_to_allow(folder)} ({out[-120:]})"
    return True, (out.splitlines() or [""])[-1]


def readable(folder: Path, name: str | None = None, timeout: float = 8.0) -> tuple[bool, str]:
    """(ok, reason): may this process list `folder` and open a file in it (macOS asks per program for ~/Downloads)."""
    folder = Path(folder)
    key = f"read:{folder}"
    if key in _cache:
        return _cache[key]
    ok, out = _child(["read", str(folder), *([name] if name else [])], timeout)
    res = (True, "") if ok else (False, f"macOS is not letting this background job read {folder} ({out[-100:]}). To "
                                        "allow it: System Settings → Privacy & Security → Files and Folders → turn on "
                                        "Downloads Folder for Python (or answer the macOS prompt if one is showing).")
    if not ok:
        log.warning("protected folder not readable from this process", extra={"folder": str(folder)})
    _cache[key] = res
    return res


def forget() -> None:
    _cache.clear()


def pending_marker() -> Path:
    return paths.STATE_DIR / "desktop_pending"


def mark_pending(why: str) -> None:
    """A background job couldn't use the Desktop: remember it (the next sync retries), and — from the worker or a run,
    once — ask the launcher app to try now. Never from inside the launcher (that would loop)."""
    m = pending_marker()
    first = not m.exists()
    try:
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(str(why)[:500], encoding="utf-8")
    except OSError:
        return
    if first and not os.environ.get("HIRESCOPE_NO_KICK") and not os.environ.get("HIRESCOPE_IN_LAUNCHER"):
        try:
            subprocess.run(["open", "-g", "hirescope://sync"], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass


# --- the child side ------------------------------------------------------------------------------------------------
def _place_main(src: str, target: str) -> int:
    import shutil
    from .applications_excel import open_in_excel, pending_path
    s, t = Path(src), Path(target)
    t.parent.mkdir(parents=True, exist_ok=True)
    pend = pending_path(t)
    tmp = t.with_name(f".{t.stem}.{os.getpid()}.tmp{t.suffix}")
    shutil.copyfile(s, tmp)
    if open_in_excel(t):
        tmp.replace(pend)
        print(json.dumps({"status": "pending", "pending_path": str(pend),
                          "message": f"{t.name} is open in Excel: this update was saved as {pend.name}; it replaces "
                                     "the file at the next sync after you close it"}))
        return 0
    try:
        tmp.replace(t)
    except OSError as e:
        try:
            tmp.replace(pend)
        except OSError:
            tmp.unlink(missing_ok=True)
        print(json.dumps({"status": "pending", "pending_path": str(pend),
                          "message": f"{t.name} couldn't be replaced ({e.strerror or e}); the update is in {pend.name}"}))
        return 0
    pend.unlink(missing_ok=True)
    print(json.dumps({"status": "ok"}))
    return 0


def _sync_main(job_dir: str | None) -> int:
    from resume_builder import desktop
    if job_dir:
        _enabled, folder = desktop.settings_of()
        res = desktop.sync_job(Path(job_dir), folder)
        print(f"Desktop copy: {res['dir']} ({len(res['copied'])} copied, {len(res['removed'])} removed)")
        return 0
    code, lines = desktop.sync_all(direct=True)
    print("\n".join(lines))
    return code


def _read_main(folder: str, name: str | None) -> int:
    names = [n for n in os.listdir(folder) if not n.startswith(".")]
    f = os.path.join(folder, name) if name else (os.path.join(folder, names[0]) if names else None)
    if f and os.path.isfile(f):
        open(f, "rb").read(1)
    return 0


def main(argv=None) -> int:
    a = sys.argv[1:] if argv is None else argv
    if not a:
        return 2
    if a[0] == "place" and len(a) == 3:
        return _place_main(a[1], a[2])
    if a[0] == "sync-desktop":
        return _sync_main(a[1] if len(a) > 1 else None)
    if a[0] == "read" and len(a) in (2, 3):
        return _read_main(a[1], a[2] if len(a) == 3 else None)
    return 2


if __name__ == "__main__":
    sys.exit(main())
