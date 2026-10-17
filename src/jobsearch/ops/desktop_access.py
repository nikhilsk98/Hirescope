"""May this process write to / read from a folder macOS protects (Desktop, Downloads)? Asked without ever hanging.

macOS asks once per program before it may write to ~/Desktop. A background job (a scheduled run, the resume worker)
has no window for that question, so a write can block forever inside a rename() (seen 2026-10-17: a quick check sat
an hour on the applications Excel file). Every Desktop write first runs this probe in a child process with a timeout;
a probe that fails or hangs is killed, and the caller skips the Desktop (everything stays in data.nosync) and says how
to allow it. The answer is cached for the life of the process.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_PROBE = ("import os, pathlib, sys\n"
          "d = pathlib.Path(sys.argv[1]); d.mkdir(parents=True, exist_ok=True)\n"
          "p, t = d / '.hirescope-probe', d / '.hirescope-probe.tmp'\n"
          "p.write_text('x'); t.write_text('y'); os.replace(t, p); p.unlink()\n")
_cache: dict[str, tuple[bool, str]] = {}


def how_to_allow(folder: Path) -> str:
    return (f"macOS did not let this background job write to {folder}. To allow it: System Settings → Privacy & "
            "Security → Files and Folders (or Full Disk Access) → turn on Desktop for Python (or the app listed), or "
            "answer the macOS prompt if one is showing. Everything is still saved in data.nosync; the next sync after "
            "that copies it over.")


def writable(folder: Path, timeout: float = 8.0) -> tuple[bool, str]:
    """(ok, reason). Runs the write + rename probe in a child Python that is killed after `timeout` seconds."""
    folder = Path(folder)
    key = str(folder)
    if key in _cache:
        return _cache[key]
    try:
        r = subprocess.run([sys.executable, "-c", _PROBE, key], capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        res = (True, "") if r.returncode == 0 else (False, how_to_allow(folder) + f" ({(r.stderr or '').strip()[-160:]})")
    except subprocess.TimeoutExpired:
        res = (False, how_to_allow(folder) + " (the write was waiting for permission)")
    except OSError as e:
        res = (False, f"{folder}: {e}")
    _cache[key] = res
    return res


def forget() -> None:
    _cache.clear()


_READ = ("import os, sys\n"
         "d = sys.argv[1]; names = [n for n in os.listdir(d) if not n.startswith('.')]\n"
         "f = os.path.join(d, sys.argv[2]) if len(sys.argv) > 2 else (os.path.join(d, names[0]) if names else None)\n"
         "if f and os.path.isfile(f):\n    open(f, 'rb').read(1)\n")


def readable(folder: Path, name: str | None = None, timeout: float = 8.0) -> tuple[bool, str]:
    """(ok, reason): may this process list `folder` and open a file in it (macOS asks per program for ~/Downloads)."""
    folder = Path(folder)
    key = f"read:{folder}"
    if key in _cache:
        return _cache[key]
    try:
        r = subprocess.run([sys.executable, "-c", _READ, str(folder), *([name] if name else [])], capture_output=True,
                           text=True, timeout=timeout)
        res = (True, "") if r.returncode == 0 else (False, f"can't read {folder} ({(r.stderr or '').strip()[-120:]})")
    except subprocess.TimeoutExpired:
        res = (False, f"macOS is asking whether this background job may read {folder} (the read was waiting for "
                      "permission)")
    except OSError as e:
        res = (False, f"{folder}: {e}")
    if not res[0]:
        res = (False, res[1] + ". To allow it: System Settings → Privacy & Security → Files and Folders → turn on "
               "Downloads Folder for Python (or answer the macOS prompt if one is showing).")
    _cache[key] = res
    return res


def pending_marker():
    from .. import paths
    return paths.STATE_DIR / "desktop_pending"


def mark_pending(why: str) -> None:
    """A background job couldn't write the Desktop: ask the launcher app (which may) to catch up, right away."""
    try:
        m = pending_marker()
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(why[:500], encoding="utf-8")
    except OSError:
        return
    if not os.environ.get("HIRESCOPE_NO_KICK"):
        try:
            subprocess.run(["open", "-g", "hirescope://sync"], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
