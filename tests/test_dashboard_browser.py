"""The dashboard in headless Chrome (tests/browser/*.mjs) on a DEMO dashboard built by the real pipeline: no console
errors, the new controls, keyboard navigation. Skipped when Chrome or Node is missing."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jobsearch import live
from jobsearch import paths
from jobsearch.output.dashboard import write_data_js
from jobsearch.storage import db
from jobsearch.tracker import statuses

from .conftest import mock_client
from .test_pipeline import gh_handler, run

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
NODE = shutil.which("node")
BROWSER = Path(__file__).parent / "browser"
pytestmark = pytest.mark.skipif(not (CHROME.exists() and NODE), reason="needs Google Chrome and node")
STATIC = ("index.html", "app.js", "core.js", "view.js", "styles.css")


def coop_job(n: int) -> dict:
    return {"id": 2000 + n, "internal_job_id": 2000 + n, "title": f"Software Engineer Co-op {n} (Spring 2027)",
            "updated_at": "2026-10-16T12:00:00-04:00", "absolute_url": f"https://boards.example.com/democo/{2000 + n}",
            "location": {"name": "Boston, MA"},
            "content": "Software engineering co-op, January to June 2027. Python, Java, SQL, AWS, React. "
                       "Pursuing a degree in computer science. Build REST APIs and data pipelines."}


EXT_JD = """About the role
DemoExt builds developer platforms (DEMO). Co-op, January to June 2027.

Requirements
- Pursuing a BS or MS in Computer Science
- Python, Go, Kubernetes, AWS
"""


@pytest.fixture
def demo_dashboard(conn, cfg, resume, embedder, greenhouse_payload, tmp_path):
    from jobsearch.config import Company
    payload = {"jobs": greenhouse_payload["jobs"] + [coop_job(1), coop_job(2)]}
    run(conn, cfg, resume, [Company(name="DemoCo", source="greenhouse", board="democo")],
        mock_client(gh_handler({"democo": payload})), embedder, tmp_path / "pkg")
    dash = paths.DASHBOARD
    dash.mkdir(parents=True, exist_ok=True)
    for f in STATIC:
        shutil.copy(paths.ROOT / "dashboard" / f, dash / f)
    write_data_js(dash / "data.js", db.open_jobs(conn), {"summary": {}, "repo_root": str(paths.ROOT)},
                  "America/New_York", resume.version_hash, 200, lambda j: 1)
    # an external job (Resume Builder tab) whose resume is READY → "Ready to apply" in My Applications
    from jobsearch import external
    from jobsearch.tracker import ready_apps
    from resume_builder import hirescope_bridge
    ext = external.from_text(EXT_JD, company="DemoExt", role="Platform Engineer Co-op (DEMO)", track="coop_intern",
                             origin="resume_builder")
    pk = external.import_external(conn, cfg, ext).job_pk
    from jobsearch.pipeline import RunSummary, score_all
    score_all(conn, cfg, resume, embedder, RunSummary())
    conn.execute("INSERT INTO resume_builds (job_pk, version, status, pdf_path, manifest_path, created_at, summary) VALUES "
                 "(?,1,'ready',?,?,'2026-10-17T00:00:00+00:00','{}')", (pk, str(tmp_path / "v1" / "R.pdf"), str(tmp_path / "v1" / "manifest.json")))
    conn.execute("INSERT INTO resume_status (job_pk, status, current_version, latest_version, updated_at) VALUES "
                 "(?, 'ready', 1, 1, '2026-10-17T00:00:00+00:00')", (pk,))
    conn.commit()
    ready_apps.ensure(conn, auto_applied=False)
    conn.execute("INSERT INTO resume_questions (job_pk, kind, keyword, question, options, asked_at) VALUES "
                 "(?, 'keyword', 'Kafka', 'Have you used Kafka? If yes, where?', '[]', '2026-10-17T00:00:00+00:00')", (pk,))
    conn.commit()
    write_data_js(dash / "data.js", db.open_jobs(conn), {"summary": {}, "repo_root": str(paths.ROOT)},
                  "America/New_York", resume.version_hash, 200, lambda j: 1)
    statuses.write_sync_js(dash / "sync.js", statuses.sync_payload(conn))
    hirescope_bridge.write_dashboard_data(conn, dash / "resumes.js")
    live.write(conn, dash / "live.js")
    live.write_launcher_js(True, dash / "launcher.js")

    class D:
        dir = dash
        url = (dash / "index.html").as_uri()
        db_conn = conn
    return D


def scenario(d, name: str, width: int = 1440, dark: bool = False) -> dict:
    out = subprocess.run([NODE, str(BROWSER / "dashboard.mjs"), d.url, name, str(width), "1" if dark else ""],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    res = json.loads(out.stdout.strip().splitlines()[-1])
    failed = [c for c in res["checks"] if not c[1]]
    assert not failed, failed
    assert res["errors"] == [], res["errors"]
    return res


def test_generate_resume_queues_through_the_launcher(demo_dashboard):
    scenario(demo_dashboard, "smoke")


def test_setup_banner_without_the_launcher(demo_dashboard):
    live.write_launcher_js(False, demo_dashboard.dir / "launcher.js")
    scenario(demo_dashboard, "setup_banner")


def test_bucket_tab_add_remove_mark_applied_undo_generate_all(demo_dashboard):
    scenario(demo_dashboard, "bucket")


def test_resume_builder_tab_form_external_list_ready_to_apply(demo_dashboard):
    scenario(demo_dashboard, "builder")


def test_questions_view_and_queue_panel(demo_dashboard):
    scenario(demo_dashboard, "queue_questions")


def test_stage_and_note_edits_sync_and_excel_notice(demo_dashboard):
    scenario(demo_dashboard, "excel")
