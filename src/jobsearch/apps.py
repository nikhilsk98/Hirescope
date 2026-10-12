"""My Applications from the command line (SQLite copy, i.e. as of the last imported dashboard export).

    python -m jobsearch.apps list            # stage, days since applied, follow-up state
    python -m jobsearch.apps followups       # follow-ups due / overdue and "ghosted?" suggestions
    python -m jobsearch.apps stats           # per track: response / OA / interview rates, offers
    python -m jobsearch.apps csv [FILE]      # export CSV (stdout when FILE is omitted)
    python -m jobsearch.apps import FILE     # merge a jobsearch-statuses.json now
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import paths
from .config import load_preferences
from .storage import db
from .tracker import applications as A
from .tracker import statuses


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="jobsearch.apps", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["list", "followups", "stats", "csv", "import"])
    p.add_argument("file", nargs="?", type=Path)
    args = p.parse_args(argv)
    prefs = load_preferences()
    conn = db.connect(paths.DB)
    today = datetime.now(ZoneInfo(prefs.timezone)).date()
    window = tuple(prefs.applications.follow_up_days)
    if args.command == "import":
        if not args.file:
            p.error("import needs a FILE")
        res = statuses.merge(conn, statuses.load_export(args.file), paths.APPS_DIR)
        print(json.dumps({k: v for k, v in res.as_dict().items() if v}, indent=2))
        return 0
    apps = [a for a in statuses.applications(conn, include_deleted=False, with_jd=False)]
    if args.command == "csv":
        out = A.to_csv(apps, today, window)
        if args.file:
            args.file.write_text(out, encoding="utf-8")
            print(f"wrote {len(apps)} applications to {args.file}")
        else:
            sys.stdout.write(out)
    elif args.command == "list":
        for a in apps:
            fu = A.follow_up(a, today, window)
            print(f"{A.STAGE_LABELS[a['stage']]:<18} {a['applied_date'] or '?':<10} {a['company']} — {a['title']}"
                  + (f"  [follow-up {fu['state']} {fu['due']}]" if fu and fu["state"] != "upcoming" else ""))
    elif args.command == "followups":
        for a in apps:
            fu = A.follow_up(a, today, window)
            if fu and fu["state"] != "upcoming":
                print(f"Follow-up {fu['state']:<8} since {fu['due']}: {a['company']} — {a['title']}")
            if A.ghost_suggestion(a, today, prefs.applications.ghosted_after_days):
                print(f"Ghosted?  no update in {prefs.applications.ghosted_after_days}+ days: {a['company']} — {a['title']}")
    else:
        for track in ("coop_intern", "full_time"):
            print(track, json.dumps(A.track_stats([a for a in apps if a.get("track") == track], today)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
