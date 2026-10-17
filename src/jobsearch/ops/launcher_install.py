"""One-time setup for zero-CLI resumes (scripts/install_launcher.sh / uninstall_launcher.sh):

  ~/Applications/HireScope Launcher.app   an AppleScript applet registered for hirescope:// links (LSUIElement: no
                                          window, no Dock icon). It passes each link, quoted, to scripts/launcher.sh.
  ~/Library/LaunchAgents/com.nikhil.hirescope.worker.plist
                                          the background resume worker (RunAtLoad, restarted after a crash, started
                                          by the launcher through launchctl kickstart and a WatchPaths trigger file)
  dashboard/launcher.js                   tells the dashboard the launcher is installed (no setup banner)

    python -m jobsearch.ops.launcher_install install [--dry-run] | uninstall | status

Re-running install replaces both. The first time the dashboard opens a hirescope:// link the browser asks "Open
HireScope Launcher?": choose Always allow (the dashboard says so once).
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .. import live, paths

APP_NAME = "HireScope Launcher.app"
BUNDLE_ID = "com.nikhil.hirescope.launcher"
WORKER_LABEL = "com.nikhil.hirescope.worker"
SCHEME = "hirescope"
SOURCE = paths.ROOT / "scripts" / "launcher" / "HireScopeLauncher.applescript"
TEMPLATE = paths.ROOT / "scripts" / "launchd" / f"{WORKER_LABEL}.plist.template"
APPS = Path.home() / "Applications"
AGENTS = Path.home() / "Library" / "LaunchAgents"
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/"
              "Versions/A/Support/lsregister")
WORKER_PATH = ["~/.local/bin", "/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/bin", "/bin",
               "/usr/sbin", "/sbin", "/Library/TeX/texbin"]


def _esc_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def applescript(repo: Path = paths.ROOT) -> str:
    r = str(repo)
    if '"' in r or "\\" in r:
        raise ValueError(f"the repository path can't contain quotes or backslashes: {r}")
    return SOURCE.read_text(encoding="utf-8").replace("{{REPO}}", r)


def worker_plist(repo: Path = paths.ROOT, log_dir: Path | None = None) -> str:
    log_dir = log_dir or paths.LOG_DIR
    out = TEMPLATE.read_text(encoding="utf-8")
    path = ":".join(os.path.expanduser(p) for p in WORKER_PATH)
    for k, v in (("{{LABEL}}", WORKER_LABEL), ("{{REPO}}", str(repo)), ("{{LOG_DIR}}", str(log_dir)),
                 ("{{TRIGGER}}", str(paths.STATE_DIR / "queue.trigger")), ("{{PATH}}", path)):
        out = out.replace(k, _esc_xml(v))
    plistlib.loads(out.encode())             # must be a valid plist
    return out


def patch_info_plist(app: Path) -> dict:
    """The applet's Info.plist: its own bundle id, the hirescope:// URL scheme, and no Dock icon / window."""
    p = app / "Contents" / "Info.plist"
    d = plistlib.loads(p.read_bytes())
    d.update({"CFBundleIdentifier": BUNDLE_ID, "CFBundleName": "HireScope Launcher",
              "CFBundleDisplayName": "HireScope Launcher", "CFBundleShortVersionString": "1.0", "LSUIElement": True,
              "CFBundleURLTypes": [{"CFBundleURLName": "HireScope dashboard links", "CFBundleURLSchemes": [SCHEME]}]})
    p.write_bytes(plistlib.dumps(d))
    return d


def build_app(dest_dir: Path, run=subprocess.run) -> Path:
    """Compile the applet into a temporary folder, patch it, sign it (ad hoc), then move it into dest_dir."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "launcher.applescript"
        src.write_text(applescript(), encoding="utf-8")
        built = Path(tmp) / APP_NAME
        run(["osacompile", "-o", str(built), str(src)], check=True, capture_output=True)
        patch_info_plist(built)
        run(["codesign", "--force", "--deep", "--sign", "-", str(built)], check=True, capture_output=True)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / APP_NAME
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(built), dest)
    return dest


def install(dry: bool = False, apps: Path = APPS, agents: Path = AGENTS, run=subprocess.run) -> list[str]:
    uid = os.getuid()
    lines = []
    plist = worker_plist()
    if dry:
        return [f"--- {apps / APP_NAME} (Info.plist: {BUNDLE_ID}, URL scheme {SCHEME}://, LSUIElement)",
                applescript(), f"--- {agents / (WORKER_LABEL + '.plist')}", plist]
    app = build_app(apps, run)
    run([LSREGISTER, "-f", str(app)], check=False, capture_output=True)
    lines.append(f"launcher app: {app} (handles {SCHEME}:// links)")
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (paths.STATE_DIR / "queue.trigger").touch()
    agents.mkdir(parents=True, exist_ok=True)
    target = agents / f"{WORKER_LABEL}.plist"
    run(["launchctl", "bootout", f"gui/{uid}/{WORKER_LABEL}"], check=False, capture_output=True)
    target.write_text(plist, encoding="utf-8")
    r = run(["launchctl", "bootstrap", f"gui/{uid}", str(target)], check=False, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed for {target}: {(r.stderr or '').strip()}")
    lines.append(f"resume worker: {target} (launchd {WORKER_LABEL}; runs only while the queue has work)")
    live.write_launcher_js(True)
    lines += ["dashboard: reload it — Generate Resume, Generate resumes for all and the Resume Builder tab now queue "
              "builds in the background.",
              "First click: your browser asks \"Open HireScope Launcher?\" — tick Always allow, then Open."]
    return lines


def uninstall(apps: Path = APPS, agents: Path = AGENTS, run=subprocess.run) -> list[str]:
    uid = os.getuid()
    run(["launchctl", "bootout", f"gui/{uid}/{WORKER_LABEL}"], check=False, capture_output=True)
    (agents / f"{WORKER_LABEL}.plist").unlink(missing_ok=True)
    app = apps / APP_NAME
    if app.exists():
        run([LSREGISTER, "-u", str(app)], check=False, capture_output=True)
        shutil.rmtree(app)
    live.write_launcher_js(False)
    return [f"removed {app} and the {WORKER_LABEL} agent; queued builds stay in SQLite (they run again after "
            "./scripts/install_launcher.sh, or with `python -m resume_builder queue worker`)"]


def status(apps: Path = APPS, agents: Path = AGENTS) -> list[str]:
    app = apps / APP_NAME
    loaded = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{WORKER_LABEL}"],
                            capture_output=True).returncode == 0
    return [f"launcher app: {'installed' if app.exists() else 'not installed'} ({app})",
            f"resume worker: {'loaded' if loaded else 'not loaded'} ({agents / (WORKER_LABEL + '.plist')})"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="install_launcher.sh", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["install", "uninstall", "status"])
    p.add_argument("--dry-run", action="store_true", help="install: print what would be installed, change nothing")
    a = p.parse_args(argv)
    if sys.platform != "darwin":
        print("The hirescope:// launcher is macOS only.", file=sys.stderr)
        return 2
    try:
        out = (install(a.dry_run) if a.command == "install" else uninstall() if a.command == "uninstall"
               else status())
    except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError) as e:
        print(f"install_launcher: {e}", file=sys.stderr)
        return 1
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
