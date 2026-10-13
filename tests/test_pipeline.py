"""Adapter, retries, error isolation, end-to-end run, job package format, data.js + day tabs. (DEMO data)"""
import json
import re

import httpx
import pytest

from jobsearch.adapters.base import FetchContext, SourceError
from jobsearch.adapters.greenhouse import GreenhouseAdapter
from jobsearch.config import Company
from jobsearch.output.dashboard import local_day, write_data_js
from jobsearch.output.packages import FRONT_MATTER_KEYS, SECTIONS, package_filename, parse_package, render_package
from jobsearch.pipeline import run
from jobsearch.storage import db

from .conftest import mock_client


def gh_handler(payloads: dict, calls: list | None = None):
    """payloads: board -> JSON dict, or an int HTTP status to return."""
    def handler(request: httpx.Request):
        board = request.url.path.split("/")[3]
        if calls is not None:
            calls.append(board)
        p = payloads.get(board, 404)
        return httpx.Response(p, json={}) if isinstance(p, int) else httpx.Response(200, json=p)
    return handler


def test_greenhouse_adapter_parses_jobs(greenhouse_payload):
    client = mock_client(gh_handler({"democo": greenhouse_payload}))
    jobs = GreenhouseAdapter(client).fetch(Company(name="DemoCo", source="greenhouse", board="democo"),
                                           FetchContext()).jobs
    assert [j.job_id for j in jobs] == ["1001", "1002", "1003"]
    assert jobs[0].canonical_key == "greenhouse:1001"
    j = jobs[0]
    assert j.apply_url == "https://example.com/demo/1001"
    assert "- Experience with Python, Go and SQL" in j.description
    assert j.posted_date.year == 2026


def test_greenhouse_bad_shape_is_source_error():
    client = mock_client(lambda r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(SourceError):
        GreenhouseAdapter(client).fetch(Company(name="X", source="greenhouse", board="x"), FetchContext())


def test_retries_then_succeeds(greenhouse_payload):
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(503) if len(attempts) < 3 else httpx.Response(200, json=greenhouse_payload)
    jobs = GreenhouseAdapter(mock_client(handler)).fetch(Company(name="DemoCo", source="greenhouse", board="democo"),
                                                         FetchContext()).jobs
    assert len(attempts) == 3 and len(jobs) == 3


def test_404_not_retried():
    calls = []
    client = mock_client(gh_handler({}, calls))
    with pytest.raises(SourceError):
        GreenhouseAdapter(client).fetch(Company(name="X", source="greenhouse", board="x"), FetchContext())
    assert len(calls) == 1


def test_end_to_end_and_error_isolation(conn, cfg, resume, embedder, greenhouse_payload, tmp_path):
    companies = [Company(name="DemoCo", source="greenhouse", board="democo", tier=1),
                 Company(name="BrokenCo", source="greenhouse", board="broken", tier=1),
                 Company(name="NoAdapterCo", source="doesnotexist", tier=3)]
    client = mock_client(gh_handler({"democo": greenhouse_payload, "broken": 500}))
    s = run(conn, cfg, resume, companies, client, embedder, tmp_path).as_dict()
    assert s["sources_ok"] == 1 and s["sources_failed"] == 2 and not s["complete"]
    assert {f["company"] for f in s["failures"]} == {"BrokenCo", "NoAdapterCo"}
    assert s["jobs_found"] == 3 and s["new"] == 3

    rows = {r["job_id"]: r for r in db.open_jobs(conn)}
    ng = rows["1001"]
    assert not ng["filtered"] and ng["label"] in ("Strong", "Stretch", "Weak")
    assert ng["grad_years"] == [2026, 2027] and ng["salary_min"] == 120000
    assert ng["location"] == "Boston, MA · New York, NY" and ng["location_us"] == 1
    assert ng["track"] == "full_time" and ng["level"] == "entry"
    # senior asking <= 5 years: kept, but capped at Stretch (Candidate focus)
    assert not rows["1002"]["filtered"] and rows["1002"]["label"] in ("Stretch", "Weak")
    assert rows["1002"]["breakdown"]["stretch_reasons"]
    assert rows["1002"]["flags"]["sponsorship"]["value"] == "No sponsorship"
    assert rows["1003"]["filtered"]
    st = {r["company"]: r["status"] for r in conn.execute("SELECT company, status FROM source_status")}
    assert st == {"DemoCo": "ok", "BrokenCo": "failed", "NoAdapterCo": "failed"}


def test_failed_fetch_never_closes_jobs(conn, cfg, resume, embedder, greenhouse_payload, tmp_path):
    co = [Company(name="DemoCo", source="greenhouse", board="democo")]
    run(conn, cfg, resume, co, mock_client(gh_handler({"democo": greenhouse_payload})), embedder, tmp_path)
    for _ in range(3):
        run(conn, cfg, resume, co, mock_client(gh_handler({"democo": 500})), embedder, tmp_path)
    assert len(db.open_jobs(conn)) == 3
    # two successful fetches without job 1003 -> closed
    trimmed = {"jobs": greenhouse_payload["jobs"][:2]}
    for _ in range(2):
        s = run(conn, cfg, resume, co, mock_client(gh_handler({"democo": trimmed})), embedder, tmp_path).as_dict()
    assert s["closed"] == 1 and {r["job_id"] for r in db.open_jobs(conn)} == {"1001", "1002"}


def test_job_package_format(conn, cfg, resume, embedder, greenhouse_payload, tmp_path):
    run(conn, cfg, resume, [Company(name="DemoCo", source="greenhouse", board="democo")],
        mock_client(gh_handler({"democo": greenhouse_payload})), embedder, tmp_path)
    j = next(r for r in db.open_jobs(conn) if r["job_id"] == "1001")
    jd = open(j["jd_path"]).read()
    text = render_package(j, jd + "\n## Gaps\nfake heading inside JD", resume.version_hash)
    fm, sections = parse_package(text)
    assert list(fm) == FRONT_MATTER_KEYS
    assert fm["job_id"] == "1001" and fm["resume_version_hash"] == resume.version_hash and fm["score"] == j["score"]
    assert list(sections) == SECTIONS                           # JD heading was escaped, not a new section
    assert "Python" in sections["Minimum requirements"]
    assert "Kafka" in sections["Preferred requirements"]
    assert "Sponsorship: Not stated" in sections["Flags"]
    assert package_filename("Demo Co", "Software Engineer, New Grad 2027 (DEMO)", "1001") == \
        "Demo-Co_Software-Engineer-New-Grad-2027-DEMO_1001.md"


def load_data_js(path):
    text = path.read_text()
    meta = json.loads(re.search(r"window\.META = (.*);\n", text).group(1))
    jobs = json.loads(re.search(r"window\.JOBS = (.*);\n", text).group(1))
    return meta, jobs


def test_two_runs_same_day_one_tab_no_duplicates(conn, cfg, resume, embedder, greenhouse_payload, tmp_path):
    co = [Company(name="DemoCo", source="greenhouse", board="democo")]
    out = tmp_path / "data.js"
    for _ in range(2):  # e.g. the 08:00 and 18:00 runs
        run(conn, cfg, resume, co, mock_client(gh_handler({"democo": greenhouse_payload})), embedder, tmp_path)
        write_data_js(out, db.open_jobs(conn), {"summary": {}}, "America/New_York", resume.version_hash, 200,
                      lambda j: 1)
    meta, jobs = load_data_js(out)
    assert sorted(j["job_id"] for j in jobs) == ["1001", "1002", "1003"]
    assert len({j["day"] for j in jobs}) == 1
    visible = [j for j in jobs if not j["filtered"]]
    assert all("chunk" in j for j in visible) and all("chunk" not in j for j in jobs if j["filtered"])
    assert meta["visible_count"] == len(visible) and "not an interview" in meta["disclaimer"]
    # details (JD, package, keywords) live in lazily loaded chunk files, keeping data.js small
    chunk = (tmp_path / "details" / f"d_{visible[0]['chunk']}.js").read_text()
    body = json.loads(re.search(r"window\.__details\((.*)\);", chunk, re.S).group(1))
    d = body[str(visible[0]["id"])]
    assert d["package"].startswith("---") and "## ATS keywords" in d["package"] and "reqs" in d


def test_local_day_uses_timezone():
    # 02:00 UTC on Oct 12 is still Oct 11 in New York
    assert local_day("2026-10-12T02:00:00+00:00", "America/New_York") == "2026-10-11"


def test_discovered_label_only_for_jobs_new_after_the_first_scan(conn, cfg, resume, embedder, greenhouse_payload, tmp_path):
    """No posting date: first_seen stands in (labeled "Discovered … (posting date unknown)") only for jobs that
    appeared after earlier runs of their source; jobs from the first scan get no freshness bonus at all."""
    import copy
    co = [Company(name="DemoCo", source="greenhouse", board="democo")]
    undated = copy.deepcopy(greenhouse_payload)
    for j in undated["jobs"]:
        j.pop("first_published", None)
    first = {**undated, "jobs": [j for j in undated["jobs"] if str(j["id"]) != "1001"]}     # 1001 appears later
    run(conn, cfg, resume, co, mock_client(gh_handler({"democo": first})), embedder, tmp_path)
    conn.execute("UPDATE jobs SET first_seen = '2026-01-01T00:00:00+00:00'")            # the first scan was long ago
    conn.commit()
    run(conn, cfg, resume, co, mock_client(gh_handler({"democo": undated})), embedder, tmp_path)
    by_id = {j["job_id"]: j for j in db.open_jobs(conn)}
    new, old = by_id["1001"], by_id["1002"]
    assert not new["filtered"] and not old["filtered"]
    assert new["posted_date"] is None and old["posted_date"] is None
    assert new["signals"]["discovered_proxy"] is True and old["signals"]["discovered_proxy"] is False
    assert "discovered (posting date unknown)" in new["breakdown"]["freshness"]["detail"]
    assert "no freshness bonus" in old["breakdown"]["freshness"]["detail"]
    out = tmp_path / "data.js"
    write_data_js(out, db.open_jobs(conn), {"summary": {}}, "America/New_York", resume.version_hash, 200, lambda j: 1)
    _, jobs = load_data_js(out)
    e = {j["job_id"]: j for j in jobs}
    assert e["1001"]["discovered"] is True and e["1002"]["discovered"] is False
