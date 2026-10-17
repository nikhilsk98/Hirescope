"""Import what the dashboard saved, now: the newest Export statuses file and every "Add external job" file (plus the
inputs/external_jobs/ drop folder), then rewrite the dashboard once. The fallback for when a scheduled run can't read
~/Downloads (macOS privacy protection, e.g. after a Python upgrade); run it after a dashboard session:

    ./scripts/import_pending.sh

Same imports as the start of a run (same folders: preferences statuses_import_dir), no fetching. Exit codes: 0 done
(also when nothing was waiting), 2 bad inputs or an import error, 3 a run is in progress (it imports these itself).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import external, paths
from .config import load_config
from .extract.resume import parse_resume
from .ops.lock import Locked, RunLock
from .storage import db
from .tracker import statuses


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="import_pending.sh", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--prefs", type=Path, default=paths.PREFERENCES)
    p.add_argument("--resume", type=Path, default=paths.RESUME)
    args = p.parse_args(argv)

    from .add_url import rescore_and_publish
    from .run import import_dirs, setup_logging
    log_file = setup_logging(False)
    try:
        cfg = load_config(args.prefs)
        resume = parse_resume(args.resume, cfg.synonyms)
    except (FileNotFoundError, ValueError) as e:
        print(f"import_pending.sh: {e}", file=sys.stderr)
        return 2
    lock = RunLock(paths.STATE_DIR / "run.lock", stale_hours=3, what="import")
    try:
        lock.acquire()
    except Locked as e:
        print(f"{e}: it imports the dashboard's files itself; nothing done", file=sys.stderr)
        return 3
    conn = db.connect(paths.DB)
    try:
        dirs = import_dirs(cfg.prefs.statuses_import_dir)
        res = statuses.import_newest(conn, dirs, paths.APPS_DIR, paths.IMPORTS_DIR)
        if res.skipped_reason:
            print(f"statuses: {res.skipped_reason}")
        else:
            got = {k: v for k, v in res.as_dict().items() if v and k not in ("file", "exported_at", "errors")}
            print(f"statuses: imported {res.file} (exported {res.exported_at}): "
                  + (", ".join(f"{k} {v}" for k, v in got.items()) or "no changes"))
            for err in res.errors[:5]:
                print(f"  skipped: {err}")
        ext = external.import_pending(conn, cfg, paths.EXTERNAL_INBOX, dirs)
        for o in ext:
            print(f"external job {external.summary_line(o)}")
        if not ext:
            print(f"external jobs: nothing waiting in {paths.EXTERNAL_INBOX} or {', '.join(map(str, dirs))}")
        changed = not res.skipped_reason or any(o.status == "new" for o in ext)
        if changed:
            rescore_and_publish(conn, cfg, resume, log_file)
            print("dashboard updated: reload dashboard/index.html")
        else:
            from . import hooks
            for line in hooks.after_change(conn, reason="import_pending"):   # applications Excel, sync.js, live.js
                print(line)
    finally:
        conn.close()
        lock.release()
    return 2 if res.errors or any(o.status == "error" for o in ext) else 0


if __name__ == "__main__":
    sys.exit(main())
