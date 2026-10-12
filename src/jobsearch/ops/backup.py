"""Daily backups of your data (keep the last N) and a restore command.

    data/backups/YYYY-MM-DD/jobs.db.gz        SQLite snapshot (online backup API; the embeddings cache is dropped,
                                              it is rebuilt automatically)
    data/backups/YYYY-MM-DD/imports/          the dashboard exports imported so far (statuses + applications)
    data/backups/YYYY-MM-DD/applications/     saved JD copies for your applications

    python -m jobsearch.ops.backup list | now | restore YYYY-MM-DD
`backups.dir` in preferences moves them (e.g. to iCloud Drive). JD files under data/jd are re-fetchable and not
backed up.
"""
from __future__ import annotations

import argparse
import gzip
import logging
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .. import paths

log = logging.getLogger(__name__)


def backups_dir(cfg_dir: str | None) -> Path:
    if not cfg_dir:
        return paths.DATA / "backups"
    p = Path(cfg_dir).expanduser()
    return p if p.is_absolute() else paths.ROOT / p


def list_backups(root: Path) -> list[Path]:
    return sorted((p for p in root.glob("20??-??-??") if (p / "jobs.db.gz").exists()), key=lambda p: p.name)


def backup(db_path: Path, root: Path, day: str | None = None, keep: int = 14, force: bool = False,
           extra_dirs: tuple[Path, ...] = ()) -> Path | None:
    """One backup per day (later runs that day skip unless force). Returns the backup folder, or None if skipped."""
    day = day or datetime.now().strftime("%Y-%m-%d")
    dest = root / day
    if (dest / "jobs.db.gz").exists() and not force:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "jobs.db"
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(snap))
        with dst:
            src.backup(dst)
        src.close()
        dst.execute("DELETE FROM embeddings") if dst.execute(
            "SELECT 1 FROM sqlite_master WHERE name='embeddings'").fetchone() else None
        dst.commit()
        dst.execute("PRAGMA journal_mode=DELETE")   # a self-contained file (no -wal side file)
        dst.execute("VACUUM")
        dst.close()
        with open(snap, "rb") as f, gzip.open(dest / "jobs.db.gz.tmp", "wb", compresslevel=6) as g:
            shutil.copyfileobj(f, g, 1 << 20)
    (dest / "jobs.db.gz.tmp").replace(dest / "jobs.db.gz")
    for d in extra_dirs:
        if d.is_dir():
            shutil.copytree(d, dest / d.name, dirs_exist_ok=True)
    for old in list_backups(root)[:-keep] if keep > 0 else []:
        shutil.rmtree(old, ignore_errors=True)
    log.info("backup written: %s (%.1f MB)", dest, (dest / "jobs.db.gz").stat().st_size / 1e6)
    return dest


def restore(root: Path, day: str, db_path: Path) -> Path:
    """Replace the live DB with a backup. The current DB is kept as jobs.db.before-restore-<time>."""
    src = root / day / "jobs.db.gz"
    if not src.exists():
        raise FileNotFoundError(f"no backup for {day} in {root} (have: {', '.join(p.name for p in list_backups(root))})")
    keep = None
    if db_path.exists():
        keep = db_path.with_name(f"{db_path.name}.before-restore-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}")
        shutil.copyfile(db_path, keep)
    tmp = db_path.with_suffix(".restoring")
    with gzip.open(src, "rb") as g, open(tmp, "wb") as f:
        shutil.copyfileobj(g, f, 1 << 20)
    for side in ("-wal", "-shm"):
        Path(str(db_path) + side).unlink(missing_ok=True)
    tmp.replace(db_path)
    sqlite3.connect(str(db_path)).execute("PRAGMA integrity_check").fetchone()
    for name in ("imports", "applications"):
        if (root / day / name).is_dir():
            shutil.copytree(root / day / name, paths.DATA / name, dirs_exist_ok=True)
    return keep or db_path


def main(argv=None) -> int:
    from ..config import load_preferences
    p = argparse.ArgumentParser(prog="jobsearch.ops.backup", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["list", "now", "restore"])
    p.add_argument("day", nargs="?")
    a = p.parse_args(argv)
    prefs = load_preferences()
    root = backups_dir(prefs.backups.dir)
    if a.command == "list":
        for b in list_backups(root):
            print(f"{b.name}  {(b / 'jobs.db.gz').stat().st_size / 1e6:7.1f} MB  {b}")
    elif a.command == "now":
        out = backup(paths.DB, root, keep=prefs.backups.keep, force=True,
                     extra_dirs=(paths.IMPORTS_DIR, paths.APPS_DIR))
        print(f"backup written to {out}")
    else:
        if not a.day:
            p.error("restore needs a day (see `list`)")
        kept = restore(root, a.day, paths.DB)
        print(f"restored {a.day}; previous database kept at {kept}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
