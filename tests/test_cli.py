"""Command-line entry points and the dead-link check, end to end on DEMO data in a temp folder: python -m
jobsearch.apps, python -m jobsearch.add_url (manual JD entry), output/links.check_links (cached 24h)."""
import json

import httpx

from jobsearch import paths
from jobsearch.output.links import check_links
from jobsearch.storage import db

from .conftest import FIXTURES, FakeEmbedder, mock_client
from .test_storage import rec
from .test_tracker import _export, demo_apps


def test_check_links_marks_broken_and_caches(conn):
    ids = [db.upsert_job(conn, rec(job_id=str(i), apply_url=u))[0] for i, u in enumerate(
        ["https://ok.example/job", "https://gone.example/job", "https://boards.greenhouse.io/x/jobs/1"], 1)]
    for pk in ids:
        conn.execute("UPDATE jobs SET filtered=0, score=80 WHERE id=?", (pk,))

    def handler(request):
        u = str(request.url)
        if "gone.example" in u:
            return httpx.Response(404)
        if "greenhouse.io/x/jobs/1" in u:
            return httpx.Response(302, headers={"location": "https://boards.greenhouse.io/x?error=true"})
        return httpx.Response(200, text="ok")
    calls = []
    client = mock_client(lambda r: (calls.append(str(r.url)), handler(r))[1])
    jobs = db.open_jobs(conn)
    res = check_links(conn, client, jobs)
    assert res == {"checked": 3, "broken": 2, "cached": 0}
    status = {j["apply_url"]: j["link_status"] for j in db.open_jobs(conn)}
    assert status == {"https://ok.example/job": "ok", "https://gone.example/job": "broken",
                      "https://boards.greenhouse.io/x/jobs/1": "broken"}
    n = len(calls)
    assert check_links(conn, client, db.open_jobs(conn))["cached"] == 3 and len(calls) == n    # within 24h: no requests


def _tmp_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DB", tmp_path / "jobs.db")
    monkeypatch.setattr(paths, "APPS_DIR", tmp_path / "applications")
    monkeypatch.setattr(paths, "IMPORTS_DIR", tmp_path / "imports")
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(paths, "DASHBOARD", tmp_path / "dashboard")
    monkeypatch.setattr(paths, "SYNC_JS", tmp_path / "dashboard" / "sync.js")


def test_apps_cli(monkeypatch, tmp_path, capsys, cfg):
    from jobsearch import apps
    _tmp_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(apps, "load_preferences", lambda *a, **k: cfg.prefs)
    export = tmp_path / "jobsearch-statuses.json"
    export.write_text(json.dumps(_export(applications={a["id"]: {**a, "updated_at": "2026-10-12T10:00:00Z"} for a in demo_apps()})))
    assert apps.main(["import", str(export)]) == 0
    assert '"apps_new"' in capsys.readouterr().out
    assert apps.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "DemoCo" in out and "Acme Robotics" in out
    assert apps.main(["stats"]) == 0 and "full_time" in capsys.readouterr().out.replace("Full-time", "full_time")
    assert apps.main(["followups"]) == 0
    capsys.readouterr()
    csv_file = tmp_path / "apps.csv"
    assert apps.main(["csv", str(csv_file)]) == 0
    text = csv_file.read_text()
    assert text.startswith("company,title,track,stage") and "'=SUM(A1)" in text      # formula-safe


def test_add_url_cli_manual_entry(monkeypatch, tmp_path, capsys, cfg):
    """A page that can't be read (school co-op board): added with a pasted JD file, scored, and on the dashboard."""
    from jobsearch import add_url
    from jobsearch import run as R
    _tmp_paths(monkeypatch, tmp_path)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "my_companies.csv").write_text("company,careers_url,notes\n")
    prefs = cfg.prefs.model_copy(update={"my_companies_csv": "inputs/companies/my_companies.csv"})
    monkeypatch.setattr(add_url, "load_config", lambda *a, **k: cfg.model_copy(update={"prefs": prefs}))
    monkeypatch.setattr(add_url, "Embedder", FakeEmbedder)
    monkeypatch.setattr(R, "Embedder", FakeEmbedder)
    monkeypatch.setattr(add_url, "HttpClient", lambda **k: mock_client(lambda r: httpx.Response(404)))
    jd = tmp_path / "jd.txt"
    jd.write_text("Software Engineering Co-op, Spring 2027 (DEMO)\n\nRequirements\n- Python and SQL\n- Pursuing a BS/MS in CS\n")
    rc = add_url.main(["https://coop.school.example/postings/123", "--company", "DemoCo", "--title",
                       "Software Engineering Co-op (DEMO)", "--location", "Boston, MA", "--jd-file", str(jd),
                       "--resume", str(FIXTURES / "demo_resume.md"), "--prefs", str(FIXTURES / "demo_preferences.yaml")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DemoCo — Software Engineering Co-op (DEMO)" in out and "[coop_intern]" in out and "manual" in out
    assert "Software Engineering Co-op (DEMO)" in (tmp_path / "dashboard" / "data.js").read_text()
