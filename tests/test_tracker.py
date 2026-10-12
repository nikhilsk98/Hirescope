"""Phase 4 (DEMO data): status import/merge, applications, follow-ups, duplicate guard, feedback learning,
referrals, CSV export, and parity between the Python tracker and dashboard/core.js."""
import json
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobsearch import paths
from jobsearch.tracker import applications as A
from jobsearch.tracker import feedback, referrals, statuses
from jobsearch.storage import db

from .test_storage import rec  # DEMO JobRecord factory

TODAY = date(2026, 10, 12)
CORE = paths.DASHBOARD / "core.js"
NODE = shutil.which("node")


def demo_apps() -> list[dict]:
    """DEMO applications covering every stage and helper rule."""
    def ev(at, kind, frm=None, to=None, note=None):
        return {"at": at, "kind": kind, "from": frm, "to": to, "note": note}
    return [
        {"id": "a1", "job_key": "DemoCo::greenhouse::1", "company": "DemoCo", "title": "Software Engineer, New Grad",
         "track": "full_time", "applied_date": "2026-10-03", "stage": "applied", "followed_up": "", "deleted": False,
         "source": "HireScope", "notes": "=SUM(A1)", "events": [ev("2026-10-03T14:00:00Z", "created", to="applied")]},
        {"id": "a2", "job_key": "", "company": "DemoCo", "title": "Software Engineer - New Grad (2027)",
         "track": "full_time", "applied_date": "2026-09-01", "stage": "oa", "deleted": False, "source": "Referral",
         "events": [ev("2026-09-01T10:00:00Z", "created", to="applied"), ev("2026-09-05T10:00:00Z", "stage", "applied", "oa")]},
        {"id": "a3", "company": "Acme Robotics", "title": "Software Engineering Co-op", "track": "coop_intern",
         "applied_date": "2026-09-20", "stage": "interviewing", "deleted": False, "source": "School board",
         "events": [ev("2026-09-20T10:00:00Z", "created", to="applied"), ev("2026-09-24T10:00:00Z", "stage", "applied", "oa"),
                    ev("2026-09-30T10:00:00Z", "stage", "oa", "interviewing")]},
        {"id": "a4", "company": "Beta Labs", "title": "Backend Engineer", "track": "full_time",
         "applied_date": "2026-09-28", "stage": "rejected", "deleted": False, "source": "HireScope",
         "events": [ev("2026-09-28T10:00:00Z", "created", to="applied"), ev("2026-10-06T10:00:00Z", "stage", "applied", "rejected")]},
        {"id": "a5", "company": "Gamma", "title": "SWE Intern, Spring 2027", "track": "coop_intern",
         "applied_date": "2026-09-30", "stage": "applied", "followed_up": "2026-10-01", "deleted": False,
         "events": [ev("2026-09-30T10:00:00Z", "created", to="applied")]},
        {"id": "a6", "company": "Delta", "title": "Software Engineer I", "track": "full_time", "applied_date": "2026-08-01",
         "stage": "offer", "deleted": False, "source": "HireScope",
         "events": [ev("2026-08-01T10:00:00Z", "created", to="applied"), ev("2026-08-11T10:00:00Z", "stage", "applied", "interviewing"),
                    ev("2026-09-01T10:00:00Z", "stage", "interviewing", "offer")]},
        {"id": "a7", "company": "Old Corp", "title": "Developer", "track": "full_time", "applied_date": "2026-07-01",
         "stage": "applied", "deleted": True, "events": []},
    ]


# --- follow-ups, ghosted, duplicates ------------------------------------------------------------
def test_follow_up_window():
    a1, a5 = demo_apps()[0], demo_apps()[4]
    assert A.follow_up(a1, date(2026, 10, 9), (7, 10)) == {"due": "2026-10-10", "state": "upcoming", "days": 6}
    assert A.follow_up(a1, TODAY, (7, 10))["state"] == "due"
    assert A.follow_up(a1, date(2026, 10, 14), (7, 10))["state"] == "overdue"
    # counts from your last follow-up, not the application
    assert A.follow_up(a5, TODAY, (7, 10)) == {"due": "2026-10-08", "state": "overdue", "days": 11}
    assert A.follow_up(demo_apps()[1], TODAY) is None        # OA stage: no follow-up reminder


def test_ghost_suggestion_is_only_a_suggestion():
    a2 = demo_apps()[1]  # OA since Sep 5
    assert A.ghost_suggestion(a2, date(2026, 10, 4), 30) is False
    assert A.ghost_suggestion(a2, date(2026, 10, 5), 30) is True
    assert a2["stage"] == "oa"                              # never changed automatically
    assert A.ghost_suggestion(demo_apps()[2], TODAY, 1) is False   # interviewing: not suggested


def test_duplicate_guard():
    apps = demo_apps()
    w = A.duplicate_warnings("DemoCo", "Software Engineer, New Grad", "DemoCo::greenhouse::1", apps, TODAY)
    kinds = [x["kind"] for x in w]
    assert kinds[0] == "same_job" and "near_duplicate" in kinds  # a2 is the same role reposted
    w = A.duplicate_warnings("democo", "Data Engineer", None, apps, TODAY, window_days=60)
    assert [x["kind"] for x in w] == ["same_company"] and "2 other applications" in w[0]["message"]
    assert A.duplicate_warnings("Old Corp", "Developer", None, apps, TODAY) == []   # deleted apps don't count


def test_stats_and_outcomes():
    s = A.track_stats(demo_apps(), TODAY)
    assert s["total"] == 6 and s["active"] == 5
    assert s["response_rate"] == 66.7       # a2 OA, a3 interview, a4 rejected, a6 offer
    assert s["oa_rate"] == 50.0 and s["interview_rate"] == 33.3 and s["offers"] == 1
    assert s["median_days_to_response"] == 6.0   # 4, 4, 8, 10
    assert sum(s["per_week"]) == 6 and s["week_start"] == "2026-07-27"
    by_source = {r["group"]: r for r in A.outcome_breakdown(demo_apps(), lambda a: a.get("source"))}
    assert by_source["HireScope"]["n"] == 3 and by_source["HireScope"]["offers"] == 1
    assert A.furthest(demo_apps()[3]) == "rejected" and A.furthest(demo_apps()[2]) == "interview"


def test_csv_export_neutralizes_formulas():
    out = A.to_csv(demo_apps(), TODAY)
    lines = out.strip().splitlines()
    assert lines[0].startswith("company,title,track,stage")
    assert len(lines) == 7                   # 6 live applications + header
    assert "'=SUM(A1)" in out and "Online Assessment" in out
    assert "due 2026-10-10" in out


# --- status import / merge -------------------------------------------------------------------------
def _job(conn, jid="1", company="DemoCo", title="Software Engineer, New Grad"):
    pk, _ = db.upsert_job(conn, rec(job_id=jid, title=title, normalized_title=title.lower(), company=company,
                                    location_us=True), "2026-10-10T12:00:00+00:00")
    conn.commit()
    return pk


def _export(**kw):
    return {"exported_at": "2026-10-12T15:00:00Z", "format": 3, "statuses": {}, "track_overrides": {},
            "applications": {}, **kw}


def test_status_merge_newer_wins(conn):
    pk = _job(conn)
    key = "DemoCo::greenhouse::1"
    r = statuses.merge(conn, _export(statuses={key: {"status": "shortlisted", "updated_at": "2026-10-12T10:00:00Z"}}), None)
    assert r.statuses_applied == 1
    row = conn.execute("SELECT user_status, user_status_at FROM jobs WHERE id=?", (pk,)).fetchone()
    assert row["user_status"] == "shortlisted"
    # an older export never overwrites SQLite
    r = statuses.merge(conn, _export(statuses={key: {"status": "skipped", "skip_reason": "location",
                                                     "updated_at": "2026-10-11T10:00:00Z"}}), None)
    assert r.statuses_kept_db == 1 and r.statuses_applied == 0
    assert conn.execute("SELECT user_status FROM jobs WHERE id=?", (pk,)).fetchone()[0] == "shortlisted"
    # a newer one does, including the skip reason
    statuses.merge(conn, _export(statuses={key: {"status": "skipped", "skip_reason": "location",
                                                 "updated_at": "2026-10-12T11:00:00Z"}}), None)
    row = conn.execute("SELECT user_status, skip_reason FROM jobs WHERE id=?", (pk,)).fetchone()
    assert tuple(row) == ("skipped", "location")
    # unknown jobs and bad statuses are counted, never crash
    r = statuses.merge(conn, _export(statuses={"Nope::greenhouse::9": {"status": "applied"},
                                               key: {"status": "<script>"}}), None)
    assert r.statuses_unmatched == 1 and len(r.errors) == 1


def test_status_merge_format2_overrides_and_idempotence(conn):
    pk = _job(conn)
    payload = {"exported_at": "2026-10-12T15:00:00Z", "format": 2,
               "statuses": {"DemoCo::greenhouse::1": {"status": "applied", "updated_at": "2026-10-12T09:00:00Z"}},
               "track_overrides": {"DemoCo::greenhouse::1": "coop_intern"}}
    statuses.merge(conn, payload, None)
    r2 = statuses.merge(conn, payload, None)
    assert r2.statuses_applied == 0 and r2.overrides_applied == 0
    assert conn.execute("SELECT track_override FROM jobs WHERE id=?", (pk,)).fetchone()[0] == "coop_intern"


def test_applications_merge_from_export(conn, tmp_path):
    pk = _job(conn)
    apps = {a["id"]: a for a in demo_apps()}
    apps["a1"]["updated_at"] = "2026-10-03T14:00:00Z"
    apps["a1"]["jd"] = "DEMO JD text kept with the application"
    r = statuses.merge(conn, _export(applications=apps), tmp_path)
    assert r.apps_new == 7 and r.events_added == 12
    row = conn.execute("SELECT job_pk, jd_path, stage FROM applications WHERE id='a1'").fetchone()
    assert row["job_pk"] == pk and Path(row["jd_path"]).read_text() == "DEMO JD text kept with the application"
    # stage change in a newer export: updated + new timeline event appended; re-import adds nothing
    a1 = dict(apps["a1"], stage="oa", updated_at="2026-10-12T12:00:00Z",
              events=[*apps["a1"]["events"], {"at": "2026-10-12T12:00:00Z", "kind": "stage", "from": "applied", "to": "oa"}])
    r = statuses.merge(conn, _export(applications={"a1": a1}), tmp_path)
    assert (r.apps_updated, r.events_added) == (1, 1)
    r = statuses.merge(conn, _export(applications={"a1": a1}), tmp_path)
    assert (r.apps_updated, r.apps_kept_db, r.events_added) == (0, 1, 0)
    got = {a["id"]: a for a in statuses.applications(conn)}
    assert got["a1"]["stage"] == "oa" and [e["kind"] for e in got["a1"]["events"]] == ["created", "stage"]
    assert got["a1"]["jd"].startswith("DEMO JD") and got["a7"]["deleted"] is True
    # an older copy of a1 (another browser) never rolls it back, but its events still merge
    stale = dict(apps["a1"], stage="applied", updated_at="2026-10-04T00:00:00Z",
                 events=[{"at": "2026-10-04T00:00:00Z", "kind": "note", "note": "called recruiter"}])
    statuses.merge(conn, _export(applications={"a1": stale}), tmp_path)
    got = {a["id"]: a for a in statuses.applications(conn)}["a1"]
    assert got["stage"] == "oa" and len(got["events"]) == 3


def test_application_jd_copied_from_job_when_not_in_export(conn, tmp_path):
    jd = tmp_path / "jd.txt"
    jd.write_text("DEMO stored JD")
    pk = _job(conn)
    conn.execute("UPDATE jobs SET jd_path=? WHERE id=?", (str(jd), pk))
    statuses.merge(conn, _export(applications={"b1": {"company": "DemoCo", "title": "SWE", "job_key": "DemoCo::greenhouse::1",
                                                      "applied_date": "2026-10-12", "updated_at": "2026-10-12T00:00:00Z"}}),
                   tmp_path / "apps")
    jd.unlink()                                  # the posting (and its JD file) disappears later
    a = statuses.applications(conn)[0]
    assert a["jd"] == "DEMO stored JD"


def test_bad_applications_rejected(conn):
    r = statuses.merge(conn, _export(applications={"x1": {"company": "", "title": "SWE"},
                                                   "x2": {"company": "A", "title": "B", "stage": "hired?"},
                                                   "../etc": {"company": "A", "title": "B"}}), None)
    assert r.apps_new == 0 and len(r.errors) == 2


def test_import_newest_file_once(conn, tmp_path):
    _job(conn)
    d = tmp_path / "Downloads"
    d.mkdir()
    old = d / "jobsearch-statuses.json"
    old.write_text(json.dumps(_export(statuses={"DemoCo::greenhouse::1": {"status": "skipped", "skip_reason": "company",
                                                                          "updated_at": "2026-10-11T00:00:00Z"}})))
    new = d / "jobsearch-statuses (1).json"
    new.write_text(json.dumps(_export(statuses={"DemoCo::greenhouse::1": {"status": "applied",
                                                                          "updated_at": "2026-10-12T00:00:00Z"}})))
    import os
    os.utime(old, (1_700_000_000, 1_700_000_000))
    r = statuses.import_newest(conn, d, None, tmp_path / "imports")
    assert r.file.endswith("(1).json") and r.statuses_applied == 1
    assert list((tmp_path / "imports").iterdir())
    assert statuses.import_newest(conn, d, None).skipped_reason == "already imported"
    assert statuses.import_newest(conn, tmp_path / "missing", None).skipped_reason.startswith("no ")
    (d / "jobsearch-statuses (2).json").write_text("{not json")
    assert statuses.import_newest(conn, d, None).skipped_reason.startswith("unreadable")


def test_sync_payload_round_trip(conn, tmp_path):
    _job(conn)
    statuses.merge(conn, _export(statuses={"DemoCo::greenhouse::1": {"status": "shortlisted", "updated_at": "2026-10-12T01:00:00Z"}},
                                 applications={"a1": demo_apps()[0] | {"updated_at": "2026-10-12T01:00:00Z"}}), None)
    p = statuses.sync_payload(conn)
    assert p["statuses"]["DemoCo::greenhouse::1"]["status"] == "shortlisted"
    assert p["applications"][0]["id"] == "a1" and p["job_state"]["DemoCo::greenhouse::1"][0] == "new"
    statuses.write_sync_js(tmp_path / "sync.js", p)
    assert (tmp_path / "sync.js").read_text().startswith("// Generated")


def test_reopened_shortlisted(conn):
    pk = _job(conn)
    statuses.merge(conn, _export(statuses={"DemoCo::greenhouse::1": {"status": "shortlisted", "updated_at": "2026-10-11T00:00:00Z"}}), None)
    db.mark_missing(conn, "greenhouse", "DemoCo", set(), "2026-10-11T01:00:00+00:00")
    db.mark_missing(conn, "greenhouse", "DemoCo", set(), "2026-10-11T02:00:00+00:00")
    assert conn.execute("SELECT job_status FROM jobs WHERE id=?", (pk,)).fetchone()[0] == "closed"
    db.upsert_job(conn, rec(job_id="1", title="Software Engineer, New Grad", normalized_title="software engineer, new grad",
                            location_us=True), "2026-10-12T08:00:00+00:00")
    got = statuses.reopened_shortlisted(conn, "2026-10-12T00:00:00+00:00")
    assert [r["id"] for r in got] == [pk]


# --- feedback learning ------------------------------------------------------------------------------
def _labeled(company, status, reason=None, skills=("Python",), loc="Boston, MA", level="entry", filtered=False):
    return {"company": company, "user_status": status, "skip_reason": reason, "skills": list(skills),
            "location": loc, "level": level, "remote_us": False, "filtered": filtered,
            "filter_reasons": ["Title does not match: x"] if filtered else [], "keywords": {}, "breakdown": {}}


def test_feedback_bounded_and_reason_specific(cfg):
    prefs = cfg.prefs
    jobs = [_labeled("Stripe", "shortlisted"), _labeled("Stripe", "applied"), _labeled("Stripe", "interviewing"),
            _labeled("Oracle", "skipped", "company"), _labeled("Oracle", "skipped", "company"),
            _labeled("Oracle", "skipped", "compensation"),       # teaches nothing
            _labeled("Zed", "skipped", "location", loc="Austin, TX"), _labeled("Zed2", "skipped", "location", loc="Austin, TX"),
            _labeled("Stripe", "rejected")]                     # the company's decision: ignored
    L = feedback.learn(jobs, prefs)
    assert L.adjustments["company"]["Stripe"].points == pytest.approx(3 * 3 / 5)
    assert L.adjustments["company"]["Oracle"].points == pytest.approx(-3 * 2 / 4)
    assert L.adjustments["location"]["TX"].points < 0
    assert "Python" in L.adjustments["skills"]                # positives teach skills
    assert L.ignored == {"compensation": 1}
    # per-group and per-job caps
    adj = L.for_job({"company": "Stripe", "skills": ["Python"], "location": "Boston, MA", "level": "entry", "keywords": {}}, prefs)
    assert all(abs(p) <= prefs.feedback.max_per_feature for _, p in adj)
    assert abs(sum(p for _, p in adj)) <= prefs.feedback.max_total + 1e-9
    many = [_labeled("Stripe", "shortlisted") for _ in range(200)]
    L2 = feedback.learn(many, prefs)
    assert L2.adjustments["company"]["Stripe"].points <= prefs.feedback.max_per_feature
    d = L.describe()
    assert d["adjustments"] and "Capped" in d["status"]


def test_feedback_needs_evidence_and_can_be_disabled(cfg):
    prefs = cfg.prefs
    L = feedback.learn([_labeled("Solo", "shortlisted", skills=())], prefs)
    assert "company" not in L.adjustments
    off = prefs.model_copy(deep=True)
    off.feedback.enabled = False
    assert not feedback.learn([_labeled("A", "shortlisted")] * 5, off).adjustments


def test_feedback_weight_nudges_bounded(cfg):
    prefs = cfg.prefs
    def bd(loc):
        return {"preference": {"parts": {"location": {"score": loc}}}, "qualification": {"parts": {}}}
    jobs = [dict(_labeled(f"P{i}", "shortlisted"), breakdown=bd(100)) for i in range(6)] + \
           [dict(_labeled(f"N{i}", "skipped", "location"), breakdown=bd(0)) for i in range(6)]
    L = feedback.learn(jobs, prefs)
    assert L.weight_nudges["location"] == prefs.feedback.weight_nudge_max
    p2 = L.apply_weights(prefs)
    assert p2.weights.preference_parts["location"] == pytest.approx(prefs.weights.preference_parts["location"] * 1.25)
    assert prefs.weights.preference_parts["location"] == cfg.prefs.weights.preference_parts["location"]  # copy


def test_feedback_suggests_rule_change_for_filtered_positives(cfg):
    L = feedback.learn([_labeled("A", "shortlisted", filtered=True)], cfg.prefs)
    assert L.suggestions and "never un-hides" in L.suggestions[0]


def test_learned_points_reach_the_score(conn, cfg, resume, embedder):
    from jobsearch.pipeline import RunSummary, score_all
    for i in range(3):
        _job(conn, str(i), title="Software Engineer")
    conn.execute("UPDATE jobs SET user_status='shortlisted', user_status_at='2026-10-12T00:00:00+00:00' WHERE job_id IN ('0','1')")
    score_all(conn, cfg, resume, embedder, RunSummary())
    before = conn.execute("SELECT score FROM jobs WHERE job_id='2'").fetchone()[0]
    L = feedback.learn(db.labeled_jobs(conn), cfg.prefs)
    score_all(conn, cfg, resume, embedder, RunSummary(), L)
    row = db.decode_row(conn.execute("SELECT * FROM jobs WHERE job_id='2'").fetchone())
    assert row["score"] > before
    assert row["breakdown"]["learned"] and any(b["reason"].startswith("Learned") for b in row["breakdown"]["bonuses"])


# --- referrals ---------------------------------------------------------------------------------------
CONNECTIONS = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Ada,Demo,https://www.linkedin.com/in/ada-demo,,"Stripe, Inc.",Software Engineer,01 Jan 2026
Bo,Demo,https://www.linkedin.com/in/bo-demo,,Google Cloud,Product Manager,02 Feb 2026
Cy,Demo,,,Google,SWE II,03 Mar 2026
Di,Demo,,,Apple Hospitality REIT,Analyst,04 Apr 2026
,NoFirst,,,Stripe,,05 May 2026
"""


def test_referrals_parse_and_match():
    people = referrals.parse_connections(CONNECTIONS)
    assert len(people) == 5 and people[0]["company"] == "Stripe, Inc."
    m = referrals.match(["Stripe", "Google", "Apple", "Nobody"], people, paths.CONFIG / "company_aliases.yaml")
    assert m["Stripe"]["count"] == 2              # "Stripe, Inc." and "Stripe"
    assert m["Google"]["count"] == 1 and m["Google"]["possible_count"] == 1   # Google Cloud: possible only
    assert m["Apple"]["count"] == 0 and m["Apple"]["possible_count"] == 1     # never a confident match
    assert "Nobody" not in m
    with pytest.raises(ValueError):
        referrals.parse_connections("a,b\n1,2\n")


# --- dashboard/core.js parity ---------------------------------------------------------------------
def _node(script: str):
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_core_js_matches_python():
    apps = demo_apps()
    js = _node(f"""
const C = require({json.dumps(str(CORE))});
const apps = {json.dumps(apps)};
const today = "{TODAY.isoformat()}";
console.log(JSON.stringify({{
  fu: apps.map((a) => C.followUp(a, today, [7, 10])),
  ghost: apps.map((a) => C.ghostSuggestion(a, "2026-10-05", 30)),
  dup: C.duplicateWarnings("DemoCo", "Software Engineer, New Grad", "DemoCo::greenhouse::1", apps, today, 30),
  dup2: C.duplicateWarnings("democo", "Data Engineer", null, apps, today, 60),
  stats: C.trackStats(apps, today, 12),
  by: C.outcomeBreakdown(apps, (a) => a.source),
  csv: C.toCsv(apps, today, [7, 10]),
  tsr: C.tokenSortRatio("new grad software engineer", "software engineer new grad 2027"),
}}));""")
    assert js["fu"] == [A.follow_up(a, TODAY, (7, 10)) for a in apps]
    assert js["ghost"] == [A.ghost_suggestion(a, date(2026, 10, 5), 30) for a in apps]
    assert js["dup"] == A.duplicate_warnings("DemoCo", "Software Engineer, New Grad", "DemoCo::greenhouse::1", apps, TODAY, 30)
    assert js["dup2"] == A.duplicate_warnings("democo", "Data Engineer", None, apps, TODAY, 60)
    assert js["stats"] == A.track_stats(apps, TODAY)
    assert js["by"] == A.outcome_breakdown(apps, lambda a: a.get("source"))
    assert js["csv"] == A.to_csv(apps, TODAY, (7, 10))
    from rapidfuzz import fuzz
    assert js["tsr"] == pytest.approx(fuzz.token_sort_ratio("new grad software engineer", "software engineer new grad 2027"))


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_core_js_mark_applied_stage_transitions_and_merge():
    js = _node(f"""
const C = require({json.dumps(str(CORE))});
let a = C.newApp({{company: "DemoCo", title: "SWE", job_key: "DemoCo::greenhouse::1", track: "full_time"}}, "2026-10-01T10:00:00Z");
const created = JSON.parse(JSON.stringify(a));
a = C.moveStage(a, "oa", "2026-10-03T10:00:00Z");
a = C.moveStage(a, "oa", "2026-10-04T10:00:00Z");          // no-op
a = C.moveStage(a, "interviewing", "2026-10-05T10:00:00Z");
a = C.logFollowUp(a, "2026-10-06", "2026-10-06T10:00:00Z");
let bad = null; try {{ C.moveStage(a, "hired", "x"); }} catch (e) {{ bad = e.message; }}
const other = C.edit(created, {{notes: "older copy"}}, "2026-10-02T00:00:00Z");
const merged = C.mergeApp(a, other);
console.log(JSON.stringify({{a, created, bad, merged}}));""")
    a = js["a"]
    assert js["created"]["applied_date"] == "2026-10-01" and js["created"]["stage"] == "applied"
    assert [(e["kind"], e["to"]) for e in a["events"]] == [("created", "applied"), ("stage", "oa"),
                                                           ("stage", "interviewing"), ("follow_up", None)]
    assert a["events"][2]["from"] == "oa" and a["stage"] == "interviewing" and a["followed_up"] == "2026-10-06"
    assert js["bad"] == "unknown stage hired"
    m = js["merged"]
    assert m["stage"] == "interviewing" and m["notes"] == "" and len(m["events"]) == 4
    # the merged application is what the pipeline then imports
    assert A.furthest(m) == "interview" and A.first_response(m) == ("oa", date(2026, 10, 3))
