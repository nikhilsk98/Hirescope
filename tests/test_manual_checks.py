"""Manual Checks (DEMO): due rule (Python and dashboard view.js agree), merge from Export statuses (format 4,
newer wins, idempotent), sync back to the page, and the weekly digest section."""
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from jobsearch.ops import digest
from jobsearch.storage import db
from jobsearch.tracker import manual_checks as M
from jobsearch.tracker import statuses

VIEW = Path(__file__).resolve().parents[1] / "dashboard" / "view.js"
NODE = shutil.which("node")
TODAY = date(2026, 10, 13)


def _manual(conn, *companies):
    for c in companies:
        db.set_source_status(conn, c, "none", "manual", f"{c}: unsupported job system (DEMO)", None,
                             origin="my_companies", tier=1, url=f"https://careers.{c.lower()}.example/jobs", detected_via="manual")
    conn.commit()


def test_state_rule():
    assert M.state(None, TODAY, 7) == {"state": "never", "days": None}
    assert M.state("2026-10-10", TODAY, 7) == {"state": "ok", "days": 3}
    assert M.state("2026-10-06", TODAY, 7) == {"state": "due", "days": 7}      # exactly remind_days -> due
    assert M.state("garbage", TODAY, 7)["state"] == "never"


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_view_js_matches_python_rule():
    dates = [None, "2026-10-13", "2026-10-10", "2026-10-06", "2026-09-01"]
    out = subprocess.run([NODE, "-e", f"""const V = require({json.dumps(str(VIEW))});
const d = {json.dumps(dates)};
const marked = V.markChecked({{checked_at: "2026-10-01", history: ["2026-09-01", "2026-10-01"]}}, "2026-10-13", "2026-10-13T12:00:00Z");
console.log(JSON.stringify({{s: d.map((x) => V.manualState(x, "2026-10-13", 7)), marked}}));"""], capture_output=True, text=True, timeout=30)
    js = json.loads(out.stdout)
    assert js["s"] == [M.state(x, TODAY, 7) for x in dates]
    assert js["marked"] == {"checked_at": "2026-10-13", "history": ["2026-09-01", "2026-10-01", "2026-10-13"],
                            "updated_at": "2026-10-13T12:00:00Z"}


def _payload(checks, exported="2026-10-13T15:00:00Z"):
    return {"exported_at": exported, "format": 4, "statuses": {}, "track_overrides": {}, "applications": {},
            "manual_checks": checks}


def test_merge_from_export_newer_wins_and_sync(conn, tmp_path):
    p = _payload({"AcmeCo": {"checked_at": "2026-10-12", "history": ["2026-10-01", "2026-10-12"], "updated_at": "2026-10-12T20:00:00Z"},
                  "BadCo": {"checked_at": "12/10/2026", "updated_at": "2026-10-12T20:00:00Z"}})
    f = tmp_path / "jobsearch-statuses.json"
    f.write_text(json.dumps(p))
    r = statuses.merge(conn, statuses.load_export(f), None)                 # format 4 loads
    assert r.manual_checks_applied == 1 and any("BadCo" in e for e in r.errors)
    assert statuses.merge(conn, p, None).manual_checks_applied == 0         # idempotent
    older = _payload({"AcmeCo": {"checked_at": "2026-09-01", "updated_at": "2026-09-01T00:00:00Z"}})
    assert statuses.merge(conn, older, None).manual_checks_applied == 0     # an older export never wins
    sync = statuses.sync_payload(conn)["manual_checks"]
    assert sync == {"AcmeCo": {"checked_at": "2026-10-12", "history": ["2026-10-01", "2026-10-12"],
                               "updated_at": "2026-10-12T20:00:00+00:00"}}     # normalized like statuses


def test_report_and_digest_section(conn):
    _manual(conn, "AcmeCo", "BetaCo", "GammaCo")
    statuses.merge(conn, _payload({"AcmeCo": {"checked_at": "2026-10-12", "updated_at": "2026-10-12T20:00:00Z"},
                                   "BetaCo": {"checked_at": "2026-09-20", "updated_at": "2026-09-20T20:00:00Z"}}), None)
    rep = M.report(conn, TODAY, 7)
    assert [(x["company"], x["state"]) for x in rep] == [("GammaCo", "never"), ("BetaCo", "due"), ("AcmeCo", "ok")]
    html = digest.build(conn, TODAY, remind_days=7)
    assert "Manual checks due (2)" in html and "GammaCo" in html and "never checked" in html
    assert "BetaCo" in html and "23 days ago" in html and "AcmeCo</a> <span" not in html
    assert "https://careers.gammaco.example/jobs" in html
    assert "Manual checks due (1)" in digest.build(conn, TODAY, remind_days=60)      # only the never-checked one


def test_company_that_moved_to_an_adapter_is_no_longer_manual(conn):
    """AMD was manual, then got a Jibe source: only its newest row counts (dashboard Coverage + digest)."""
    from jobsearch.output.dashboard import latest_per_company
    db.set_source_status(conn, "AMD", "none", "manual", "iCIMS (DEMO)", None, "2026-10-12T03:00:00+00:00", origin="my_companies")
    db.set_source_status(conn, "AMD", "jibe", "ok", None, 659, "2026-10-13T03:00:00+00:00", origin="my_companies")
    db.set_source_status(conn, "Cimpress", "jsonld", "failed", "no JobPosting (DEMO)", None, "2026-10-12T03:00:00+00:00", origin="my_companies")
    db.set_source_status(conn, "Cimpress", "none", "manual", "SuccessFactors (DEMO)", None, "2026-10-13T03:00:00+00:00", origin="my_companies")
    conn.commit()
    assert [m["company"] for m in M.report(conn, TODAY, 7)] == ["Cimpress"]
    rows = [{"company": "AMD", "source": "none", "status": "manual", "last_checked": "2026-10-12T03:00"},
            {"company": "AMD", "source": "jibe", "status": "ok", "last_checked": "2026-10-13T03:00"},
            {"company": "Cimpress", "source": "jsonld", "status": "failed", "last_checked": "2026-10-12T03:00"},
            {"company": "Cimpress", "source": "none", "status": "manual", "last_checked": "2026-10-13T03:00"}]
    assert [(r["company"], r["status"]) for r in latest_per_company(rows)] == [("AMD", "ok"), ("Cimpress", "manual")]


def test_renamed_or_removed_watchlist_companies_are_not_manual(conn):
    _manual(conn, "Juniper Networks", "Tesla")      # Juniper now aliases to Hewlett Packard Enterprise
    assert [m["company"] for m in M.report(conn, TODAY, 7, {"Tesla", "Hewlett Packard Enterprise"})] == ["Tesla"]
    assert "Juniper" not in digest.build(conn, TODAY, remind_days=7, companies={"Tesla"})
