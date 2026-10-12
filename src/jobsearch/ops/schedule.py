"""launchd scheduling: two full runs a day (default 08:00 and 18:00) plus quick checks every 2–3 hours.

    python -m jobsearch.ops.schedule install [--times 08:00,18:00] [--quick-hours 2.5] [--dry-run]
    python -m jobsearch.ops.schedule uninstall | status
    python -m jobsearch.ops.schedule change-time 07:30,19:00 [--quick-hours 3]
(`scripts/schedule.sh …` is the same.) Plists are rendered from scripts/launchd/*.plist.template into
~/Library/LaunchAgents and loaded with `launchctl bootstrap gui/<uid>`. Sleep: launchd starts a missed
calendar run once on wake. Powered off: skipped. Offline: the run logs it and exits; the next run retries.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

from .. import paths

LABEL = "com.nikhil.jobsearch"
QUICK_LABEL = "com.nikhil.jobsearch.quick"
TEMPLATES = paths.ROOT / "scripts" / "launchd"
AGENTS = Path.home() / "Library" / "LaunchAgents"
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_times(spec) -> list[tuple[int, int]]:
    items = spec.split(",") if isinstance(spec, str) else list(spec)
    out = []
    for t in items:
        m = _TIME.match(str(t).strip())
        if not m:
            raise ValueError(f"bad time {t!r} (use HH:MM, 24-hour)")
        out.append((int(m.group(1)), int(m.group(2))))
    if not out:
        raise ValueError("at least one run time is needed")
    return sorted(set(out))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_full(times, repo: Path = paths.ROOT, log_dir: Path = paths.LOG_DIR, args: list[str] | None = None) -> str:
    cal = "\n".join(f"    <dict><key>Hour</key><integer>{h}</integer><key>Minute</key><integer>{m}</integer></dict>"
                    for h, m in parse_times(times))
    out = (TEMPLATES / f"{LABEL}.plist.template").read_text()
    out = out.replace("{{LABEL}}", LABEL).replace("{{REPO}}", _esc(str(repo))).replace("{{LOG_DIR}}", _esc(str(log_dir)))
    out = out.replace("{{CALENDAR}}", cal).replace("{{ARGS}}", "".join(f"<string>{_esc(a)}</string>" for a in args or []))
    plistlib.loads(out.encode())       # must be a valid plist
    return out


def render_quick(hours: float, repo: Path = paths.ROOT, log_dir: Path = paths.LOG_DIR) -> str:
    if not 0.5 <= hours <= 12:
        raise ValueError("quick-check interval must be between 0.5 and 12 hours")
    out = (TEMPLATES / f"{LABEL}.quick.plist.template").read_text()
    out = out.replace("{{LABEL}}", QUICK_LABEL).replace("{{REPO}}", _esc(str(repo))).replace("{{LOG_DIR}}", _esc(str(log_dir)))
    out = out.replace("{{INTERVAL}}", str(int(hours * 3600)))
    plistlib.loads(out.encode())
    return out


def _launchctl(*args, run=subprocess.run, dry=False) -> int:
    cmd = ["launchctl", *args]
    if dry:
        print("would run:", " ".join(cmd))
        return 0
    return run(cmd, capture_output=True, text=True).returncode


def install(times, quick_hours: float, dry: bool = False, agents: Path = AGENTS, run=subprocess.run) -> list[Path]:
    uid = os.getuid()
    agents.mkdir(parents=True, exist_ok=True)
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    plan = [(LABEL, render_full(times))]
    if quick_hours:
        plan.append((QUICK_LABEL, render_quick(quick_hours)))
    written = []
    for label in (LABEL, QUICK_LABEL):            # replace whatever was installed before
        _launchctl("bootout", f"gui/{uid}/{label}", run=run, dry=dry)
        if not dry:
            (agents / f"{label}.plist").unlink(missing_ok=True)
    for label, text in plan:
        p = agents / f"{label}.plist"
        if dry:
            print(f"--- {p}\n{text}")
        else:
            p.write_text(text)
            if _launchctl("bootstrap", f"gui/{uid}", str(p), run=run) != 0:
                raise RuntimeError(f"launchctl bootstrap failed for {p}")
        written.append(p)
    return written


def uninstall(agents: Path = AGENTS, run=subprocess.run, dry: bool = False) -> None:
    uid = os.getuid()
    for label in (LABEL, QUICK_LABEL):
        _launchctl("bootout", f"gui/{uid}/{label}", run=run, dry=dry)
        if not dry:
            (agents / f"{label}.plist").unlink(missing_ok=True)


def status(agents: Path = AGENTS) -> str:
    lines = []
    for label in (LABEL, QUICK_LABEL):
        p = agents / f"{label}.plist"
        if not p.exists():
            lines.append(f"{label}: not installed")
            continue
        d = plistlib.loads(p.read_bytes())
        when = (", ".join(f"{c['Hour']:02d}:{c['Minute']:02d}" for c in d.get("StartCalendarInterval", []))
                or f"every {d.get('StartInterval', 0) / 3600:g} h")
        loaded = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True).returncode == 0
        lines.append(f"{label}: {when} ({'loaded' if loaded else 'NOT loaded'})")
    return "\n".join(lines)


def main(argv=None) -> int:
    from ..config import load_preferences
    p = argparse.ArgumentParser(prog="jobsearch.ops.schedule", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["install", "uninstall", "status", "change-time"])
    p.add_argument("times_arg", nargs="?", help="change-time: HH:MM[,HH:MM…]")
    p.add_argument("--times", help="full-run times, e.g. 08:00,18:00 (default: preferences schedule.full_times)")
    p.add_argument("--quick-hours", type=float, help="quick-check interval in hours (0 = no quick checks)")
    p.add_argument("--dry-run", action="store_true", help="print the plists and commands, change nothing")
    a = p.parse_args(argv)
    sched = load_preferences().schedule
    quick = sched.quick_hours if a.quick_hours is None else a.quick_hours
    if a.command == "status":
        print(status())
    elif a.command == "uninstall":
        uninstall(dry=a.dry_run)
        print("schedule removed")
    else:
        times = a.times_arg if a.command == "change-time" else (a.times or sched.full_times)
        if a.command == "change-time" and not times:
            p.error("change-time needs HH:MM[,HH:MM]")
        written = install(times, quick, dry=a.dry_run)
        if not a.dry_run:
            print("installed:", ", ".join(str(w) for w in written))
            print(status())
            if a.command == "change-time" or a.times:
                print("Tip: also update schedule.full_times in inputs/preferences.yaml so it stays the default.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
