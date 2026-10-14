"""External jobs (jobs found outside HireScope): every import path (paste, file .txt/.md/.pdf/.docx, drop folder,
dashboard JSON, CLI, URL only), duplicate detection, scoring + package + badge, and applied tracking incl. Applied
Companies (Python and dashboard/core.js). All data is DEMO."""
import base64
import io
import json
import shutil
import subprocess
import zipfile

import httpx
import pytest

from jobsearch import external as X
from jobsearch import paths
from jobsearch.output.dashboard import build_entry, select_visible
from jobsearch.pipeline import RunSummary, score_all
from jobsearch.storage import db
from jobsearch.tracker import applications as A
from jobsearch.tracker import statuses

from .conftest import FIXTURES, FakeEmbedder, mock_client
from .test_cli import _tmp_paths

JD = """About the role
DemoCorp builds robots for warehouses (DEMO). You will build backend services.

Requirements
- Pursuing a BS or MS in Computer Science, graduating 2027
- Experience with Python, SQL and REST APIs
- Familiarity with Docker and AWS

Nice to have
- Kubernetes, React

Co-op term: Spring 2027 (January - June 2027). Visa sponsorship is not available for this position.
"""


def _pdf(lines: list[str], drawn_bullets: bool = False) -> bytes:
    """A minimal one-page PDF with a text layer (Helvetica), xref offsets computed. With drawn_bullets, lines starting
    "* " are list items whose bullet is a small filled square (as browsers and word processors draw them) and lines
    starting "  " continue the item above (wrapped)."""
    ops, y = [], 760
    for ln in lines:
        x = 50
        if drawn_bullets and ln.startswith(("* ", "  ")):
            if ln.startswith("* "):
                ops.append(f"62 {y + 2} 3 3 re f")
            ln, x = ln[2:], 70
        ops.append(f"BT /F1 10 Tf {x} {y} Td ({ln.replace('(', '[').replace(')', ']')}) Tj ET")
        y -= 12
    stream = "\n".join(ops).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return bytes(out)


def _docx(paragraphs: list[str]) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p>{'<w:pPr><w:numPr/></w:pPr>' if p.startswith('- ') else ''}<w:r><w:t>{p.removeprefix('- ')}</w:t></w:r></w:p>"
                   for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    return tmp_path / "data"


def _job(conn, pk):
    return db.decode_row(conn.execute("SELECT * FROM jobs WHERE id=?", (pk,)).fetchone())


def _score(conn, cfg, resume):
    score_all(conn, cfg, resume, FakeEmbedder(conn), RunSummary())


# --- reading ---------------------------------------------------------------------------------------------------------
def test_header_filename_and_text_rules():
    fields, body = X.split_header("Company: DemoCorp\nRole: Software Engineer Co-op\nURL: https://demo.example/j/1\n"
                                  "Track: co-op\n\n" + JD)
    assert fields == {"company": "DemoCorp", "role": "Software Engineer Co-op", "url": "https://demo.example/j/1",
                      "track": "co-op"} and body.startswith("About the role")
    assert X.split_header(JD) == ({}, JD)                                   # no header: text unchanged
    assert X.name_fields("DemoCorp - Backend Engineer.pdf") == {"company": "DemoCorp", "role": "Backend Engineer"}
    assert X.name_fields("Demo_Corp__Backend_Engineer.docx") == {"company": "Demo Corp", "role": "Backend Engineer"}
    assert X.name_fields("jd.pdf") == {}
    j = X.from_text("Company: DemoCorp\nTitle: SWE Co-op\n\n" + JD, role="Software Engineer Co-op (DEMO)")
    assert (j.company, j.role, j.track) == ("DemoCorp", "Software Engineer Co-op (DEMO)", None)   # argument wins
    assert X.norm_track("fulltime") == "full_time" and X.norm_track("Co-op") == "coop_intern"
    with pytest.raises(X.ExternalError, match="company"):
        X.from_text(JD, role="SWE")
    with pytest.raises(X.ExternalError, match="Company - Role"):
        X.from_text(JD, file_name="jd.txt")
    with pytest.raises(X.ExternalError, match="too short"):
        X.from_text("Python", company="DemoCorp", role="SWE")
    with pytest.raises(X.ExternalError, match="http"):
        X.from_text(JD, company="DemoCorp", role="SWE", url="demo.example/1")
    with pytest.raises(X.ExternalError, match="track"):
        X.norm_track("part-time")
    assert X.norm_url("https://WWW.Demo.example/jobs/1/?utm_source=x&id=7#apply") == "https://demo.example/jobs/1?id=7"


def test_file_text_pdf_docx_md():
    pdf = X.file_text("jd.pdf", _pdf(["Software Engineer Co-op (DEMO)", "Requirements", "- Python and SQL"]))
    assert "Software Engineer Co-op" in pdf and "Python and SQL" in pdf
    docx = X.file_text("jd.docx", _docx(["Requirements", "- Python and SQL", "- Docker"]))
    assert docx.splitlines() == ["Requirements", "- Python and SQL", "- Docker"]
    assert X.file_text("jd.md", "• Python\r\n• SQL".encode()) == "- Python\n- SQL"        # bullets normalized
    # a browser-printed JD: bullets drawn as shapes, items wrapped across lines, a word split at the line end
    wrapped = X.file_text("jd.pdf", _pdf([
        "We build developer tools (DEMO) and are hiring engineers who like to ship product to real users every week of",
        "the year.",
        "Requirements:",
        "* Experience with Python and SQL in production services that serve many customers across several regions and",
        "  time zones",
        "* Familiarity with React and TypeScript for product work on the web, mobile and desktop clients we ship to self-",
        "  serve customers",
        "* BS in Computer Science"], drawn_bullets=True))
    assert wrapped.splitlines()[-3:] == [
        "- Experience with Python and SQL in production services that serve many customers across several regions and time zones",
        "- Familiarity with React and TypeScript for product work on the web, mobile and desktop clients we ship to self-serve customers",
        "- BS in Computer Science"]
    assert "every week of the year." in wrapped.splitlines()[0]
    from jobsearch.extract.requirements import split_requirements
    assert len(split_requirements(wrapped).minimum) == 3
    with pytest.raises(X.ExternalError, match="unsupported"):
        X.file_text("jd.rtf", b"x")
    with pytest.raises(X.ExternalError, match="PDF"):
        X.file_text("jd.pdf", b"%PDF-1.4 broken")
    with pytest.raises(X.ExternalError, match="docx"):
        X.file_text("jd.docx", b"not a zip")
    with pytest.raises(X.ExternalError, match="text layer"):
        X.file_text("scan.pdf", _pdf([]))


# --- paste / file import → score → package → dashboard ---------------------------------------------------------------
def test_paste_import_scores_packages_and_badges(conn, cfg, resume, data_dir):
    ext = X.from_text(JD, company="DemoCorp", role="Software Engineer Co-op (DEMO)", url="https://demo.example/jobs/1",
                      location="Boston, MA", track="coop", origin="paste")
    o = X.import_external(conn, cfg, ext)
    assert o.status == "new"
    _score(conn, cfg, resume)
    j = _job(conn, o.job_pk)
    assert j["source"] == "external" and j["job_id"].startswith("ext-") and j["track_override"] == "coop_intern"
    assert j["apply_url"] == "https://demo.example/jobs/1" and j["score"] is not None and not j["filtered"]
    assert j["flags"]["sponsorship"]["value"] == "No sponsorship"                     # sponsorship flag extracted
    assert j["keywords"]["keywords"] and j["ats_coverage"] is not None               # ATS keywords
    assert j["level"] in ("intern", "entry", "unknown") and j["extra"]["external"]["origin"] == "paste"
    assert "feed" not in (j["track_evidence"] or "")          # your track is an override; evidence is from the JD
    assert paths.stored_file(j["jd_path"], paths.JD_DIR).read_text().startswith("About the role")   # JD saved
    assert X.write_packages(conn, resume.version_hash) == 1
    pkg = next((data_dir / "job_packages" / "DemoCorp").glob("*.md")).read_text()
    assert "package_format: 2" in pkg and "## ATS keywords" in pkg and "source: external" in pkg
    e = build_entry(j, "America/New_York", None, "2026-10-14", "2026-10-14", set())
    assert "external" in e["badges"] and e["track"] == "coop_intern"


def test_external_job_always_visible_even_if_rules_would_hide(conn, cfg, resume, data_dir):
    o = X.import_external(conn, cfg, X.from_text(JD, company="DemoCorp", role="Warehouse Operations Associate (DEMO)"))
    _score(conn, cfg, resume)
    j = _job(conn, o.job_pk)
    assert not j["filtered"] and j["signals"]["rules_would_hide"]          # shown, with the rules' reasons as a note
    assert [x["id"] for x in select_visible([j], top_n=0)] == [j["id"]]    # beyond top_n too


def test_file_imports_pdf_and_docx_keep_original(conn, cfg, tmp_path, data_dir):
    pdf = tmp_path / "DemoCorp - Platform Engineer.pdf"
    pdf.write_bytes(_pdf(["Platform Engineer, New Grad 2027 (DEMO)", "Requirements", "- Python, Go and Kubernetes",
                          "- BS in Computer Science", "Full-time role starting July 2027"]))
    o = X.import_external(conn, cfg, X.from_file(pdf))
    j = _job(conn, o.job_pk)
    assert (j["company"], j["title"]) == ("DemoCorp", "Platform Engineer")
    orig = j["extra"]["external"]["original"]
    assert orig.endswith(".pdf") and open(orig, "rb").read() == pdf.read_bytes()         # the original kept
    dx = tmp_path / "jd.docx"
    dx.write_bytes(_docx(["Company: DemoCorp", "Role: Data Engineer Intern (DEMO)", "", "Requirements",
                          "- SQL and Spark", "- Pursuing a degree in CS, Summer and Fall 2027 internships"]))
    o2 = X.import_external(conn, cfg, X.from_file(dx))
    assert o2.status == "new" and _job(conn, o2.job_pk)["title"] == "Data Engineer Intern (DEMO)"


# --- duplicates ------------------------------------------------------------------------------------------------------
def test_duplicates_url_jd_and_role(conn, cfg, data_dir):
    from jobsearch.pipeline import to_record
    from jobsearch.adapters.base import RawJob
    rec = to_record(RawJob(company="DemoCorp", job_id="4001", title="Software Engineer Co-op (DEMO)", source="greenhouse",
                           source_url="https://boards.greenhouse.io/democorp/jobs/4001",
                           apply_url="https://boards.greenhouse.io/democorp/jobs/4001", description=JD), cfg, paths.JD_DIR)
    pk, _ = db.upsert_job(conn, rec)
    same_url = X.from_text("Totally different text " * 5, company="Other", role="Other",
                           url="https://boards.greenhouse.io/democorp/jobs/4001?gh_src=abc")
    o = X.import_external(conn, cfg, same_url)
    assert o.status == "duplicate" and o.job_pk == pk and o.duplicate["kind"] == "same_url"
    o = X.import_external(conn, cfg, X.from_text(JD, company="democorp", role="Anything"))   # same JD text
    assert o.status == "duplicate" and o.duplicate["kind"] == "same_jd" and "job " + str(pk) in o.message
    similar = JD.replace("warehouses", "factories").replace("Docker", "Docker and Terraform")
    o = X.import_external(conn, cfg, X.from_text(similar, company="DemoCorp", role="Software Engineer Co-op (DEMO)"))
    assert o.status == "duplicate" and o.duplicate["kind"] in ("same_role", "similar_jd")
    other = ("Requirements\n- Five years of embedded C and RTOS experience\n- PCB bring-up, oscilloscopes, JTAG\n"
             "We build satellite firmware (DEMO).")
    o = X.import_external(conn, cfg, X.from_text(other, company="DemoCorp", role="Software Engineer Co-op (DEMO)"))
    assert o.status == "new"                                               # same title, different job: added
    again = X.import_external(conn, cfg, X.from_text(other, company="DemoCorp", role="Software Engineer Co-op (DEMO)"))
    assert again.status == "duplicate" and again.job_pk == o.job_pk          # re-importing the same external job
    forced = X.import_external(conn, cfg, X.from_text(similar, company="DemoCorp", role="Software Engineer Co-op (DEMO)"),
                               force=True)
    assert forced.status == "new" and forced.job_pk not in (pk, o.job_pk)    # --force: a separate record


# --- drop folder + dashboard JSON ------------------------------------------------------------------------------------
def test_drop_folder_and_dashboard_json(conn, cfg, tmp_path, data_dir):
    inbox, downloads = tmp_path / "inbox", tmp_path / "Downloads"
    inbox.mkdir(), downloads.mkdir()
    (inbox / "README.example.md").write_text("instructions, not a JD")
    (inbox / "a.txt").write_text("Company: DemoCorp\nRole: Backend Engineer (DEMO)\nTrack: fulltime\n\n" + JD)
    (inbox / "DemoBank - QA Automation Co-op.md").write_text(JD.replace("DemoCorp", "DemoBank"))
    (inbox / "nothing.txt").write_text(JD.replace("DemoCorp", "NoName"))          # no company / role: stays put
    payload = {"kind": "hirescope_external_job", "format": 1, "company": "DemoAir", "role": "Software Engineer (DEMO)",
               "track": "full_time", "url": "https://demoair.example/careers/77", "jd_text": "",
               "file": {"name": "jd.docx", "b64": base64.b64encode(_docx(["Requirements", "- Java and Spring Boot",
                                                                           "- New grad 2027, BS in CS", "- AWS"])).decode()}}
    (downloads / "hirescope-external-job-DemoAir-20261014T1200.json").write_text(json.dumps(payload))
    (downloads / "unrelated.json").write_text("{}")
    outs = X.import_pending(conn, cfg, inbox, [downloads])
    by = {o.company or o.file.split("/")[-1]: o for o in outs}
    assert {o.status for o in outs} == {"new", "error"} and len(outs) == 4
    assert by["DemoCorp"].status == "new" and _job(conn, by["DemoCorp"].job_pk)["track_override"] == "full_time"
    assert _job(conn, by["DemoBank"].job_pk)["title"] == "QA Automation Co-op"
    air = _job(conn, by["DemoAir"].job_pk)
    assert air["extra"]["external"]["origin"] == "dashboard" and air["apply_url"].endswith("/77")
    assert sorted(p.name for p in inbox.iterdir()) == ["README.example.md", "nothing.txt"]   # imported ones moved
    assert not list(downloads.glob("hirescope-external-job*")) and (downloads / "unrelated.json").exists()
    assert len(list((data_dir / "imports" / "external").iterdir())) == 1
    assert "missing company and role" in next(o for o in outs if o.status == "error").message
    # the same file again (e.g. could not be moved): imported once
    (inbox / "b.txt").write_text((data_dir / "external_jobs" / "_imported").glob("*a.txt").__next__().read_text())
    assert X.import_pending(conn, cfg, inbox, []) [0].status == "error"     # nothing.txt still failing
    assert not (inbox / "b.txt").exists()                                    # duplicate content: archived, not re-added
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE source='external'").fetchone()[0] == 3
    # a forced dashboard file adds a look-alike anyway
    (downloads / "hirescope-external-job-2.json").write_text(json.dumps({**payload, "force": True, "role": "Software Engineer II (DEMO)"}))
    assert X.import_pending(conn, cfg, tmp_path / "empty", [downloads])[0].status == "new"
    with pytest.raises(X.ExternalError):
        X.from_json({"kind": "something else"})


def test_blocked_download_folder_is_reported(conn, cfg, tmp_path, monkeypatch, data_dir):
    """macOS can deny a scheduled run ~/Downloads (e.g. after a Python upgrade): say so instead of skipping silently."""
    from jobsearch.tracker import statuses as S
    blocked = tmp_path / "Downloads"
    monkeypatch.setattr(S, "access", lambda d: "blocked" if d == blocked else "ok" if d.exists() else "missing")
    (tmp_path / "inbox").mkdir()
    outs = X.import_pending(conn, cfg, tmp_path / "inbox", [blocked])
    assert [o.status for o in outs] == ["error"] and "import_pending.sh" in outs[0].message
    assert "import_pending.sh" in S.import_newest(conn, [blocked], None).skipped_reason


# --- CLI -------------------------------------------------------------------------------------------------------------
def _cli_env(monkeypatch, tmp_path, cfg):
    from jobsearch import add_job, add_url
    from jobsearch import run as R
    _tmp_paths(monkeypatch, tmp_path)
    (tmp_path / "inputs" / "companies").mkdir(parents=True)
    (tmp_path / "inputs" / "companies" / "my_companies.csv").write_text("company,careers_url,notes\n")
    for m in (add_job, add_url):
        monkeypatch.setattr(m, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(add_url, "Embedder", FakeEmbedder)
    monkeypatch.setattr(R, "Embedder", FakeEmbedder)
    monkeypatch.setattr(add_url, "HttpClient", lambda **k: mock_client(lambda r: httpx.Response(404)))
    return add_job, ["--resume", str(FIXTURES / "demo_resume.md"), "--prefs", str(FIXTURES / "demo_preferences.yaml")]


def test_cli_file_paste_duplicate_pending_and_url(monkeypatch, tmp_path, capsys, cfg):
    add_job, common = _cli_env(monkeypatch, tmp_path, cfg)
    jd = tmp_path / "jd.pdf"
    jd.write_bytes(_pdf(["Software Engineer Co-op, Spring 2027 (DEMO)", "Requirements", "- Python and SQL",
                         "- Pursuing a BS or MS in Computer Science", "- REST APIs, Docker"]))
    rc = add_job.main(["--company", "DemoCorp", "--role", "Software Engineer Co-op (DEMO)", "--file", str(jd),
                       "--track", "coop", "--url", "https://demo.example/j/9", *common])
    out = capsys.readouterr().out
    assert rc == 0 and "added: DemoCorp — Software Engineer Co-op (DEMO)" in out and "track coop_intern" in out
    assert "/resume-builder" in out and "job package:" in out
    data = (tmp_path / "dashboard" / "data.js").read_text()
    assert "Software Engineer Co-op (DEMO)" in data and '"external"' in data
    rc = add_job.main(["--company", "DemoCorp", "--role", "SWE Co-op", "--file", str(jd), "--url", "https://demo.example/j/9/", *common])
    assert rc == add_job.EXIT_DUPLICATE and "duplicate, not added" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO(JD))
    assert add_job.main(["--company", "DemoBank", "--role", "Backend Engineer (DEMO)", "--paste", *common]) == 0
    assert "DemoBank" in capsys.readouterr().out
    paths.EXTERNAL_INBOX.mkdir(parents=True)
    (paths.EXTERNAL_INBOX / "DemoAir - Cloud Engineer.txt").write_text(JD.replace("DemoCorp", "DemoAir"))
    assert add_job.main(["--pending", *common]) == 0 and "added: DemoAir — Cloud Engineer" in capsys.readouterr().out
    assert add_job.main(["--company", "X", "--role", "Y", "--text", "short", *common]) == 2
    assert add_job.main([*common]) == 2
    # URL only: the Add job by URL path (here the page can't be read -> its own error, exit 2)
    assert add_job.main(["--url", "https://coop.school.example/postings/1", *common]) == 2
    with sqlite_conn(tmp_path) as conn:
        assert {r[0] for r in conn.execute("SELECT company FROM jobs WHERE source='external'")} == {"DemoCorp", "DemoBank", "DemoAir"}


def test_next_run_imports_drop_folder_and_dashboard_file(monkeypatch, tmp_path, cfg, greenhouse_payload):
    """python -m jobsearch.run: a JD in inputs/external_jobs/ and a dashboard-saved file in the download folder are
    imported, scored, packaged and on the dashboard with the External badge; the run summary lists them."""
    from jobsearch import run as R
    from .test_pipeline import gh_handler
    _tmp_paths(monkeypatch, tmp_path)
    (tmp_path / "inputs" / "companies").mkdir(parents=True)
    (tmp_path / "inputs" / "companies" / "my_companies.csv").write_text("company,careers_url,notes\n")
    (tmp_path / "downloads").mkdir()
    prefs = cfg.prefs.model_copy(update={"statuses_import_dir": str(tmp_path / "downloads"),
                                         "my_companies_csv": "inputs/companies/my_companies.csv"})
    monkeypatch.setattr(R, "load_config", lambda *a, **k: cfg.model_copy(update={"prefs": prefs}))
    monkeypatch.setattr(R, "HttpClient", lambda **k: mock_client(gh_handler({"democo": greenhouse_payload})))
    monkeypatch.setattr(R, "Embedder", FakeEmbedder)
    monkeypatch.setattr(R.alerts, "online", lambda: True)
    from jobsearch.adapters.aggregator import AggregatorAdapter
    from jobsearch.adapters.linkedin_alerts import LinkedInAlertsAdapter
    for cls, attr in ((LinkedInAlertsAdapter, "disabled"), (AggregatorAdapter, "enabled"), (AggregatorAdapter, "date_window"),
                      (R.notify, "ENABLED")):
        monkeypatch.setattr(cls, attr, getattr(cls, attr))      # run.main sets these process-wide: restore after
    assert paths.EXTERNAL_INBOX == tmp_path / "inputs" / "external_jobs"          # follows the repo root (sandboxed)
    paths.EXTERNAL_INBOX.mkdir()
    (paths.EXTERNAL_INBOX / "DemoRail - Embedded Software Co-op.txt").write_text(JD.replace("DemoCorp", "DemoRail"))
    (tmp_path / "downloads" / "hirescope-external-job-DemoAir.json").write_text(json.dumps(
        {"kind": "hirescope_external_job", "format": 1, "company": "DemoAir", "role": "Software Engineer (DEMO)",
         "track": "full_time", "jd_text": JD.replace("DemoCorp", "DemoAir")}))
    args = ["--no-email", "--no-notify", "--no-backup", "--no-link-check",
            "--resume", str(FIXTURES / "demo_resume.md"), "--prefs", str(FIXTURES / "demo_preferences.yaml")]
    assert R.main(args) == 0
    data = (tmp_path / "dashboard" / "data.js").read_text()
    jobs = {j["company"]: j for j in json.loads(data.split("window.JOBS = ", 1)[1].rstrip().rstrip(";"))}
    assert "external" in jobs["DemoRail"]["badges"] and "external" in jobs["DemoAir"]["badges"]
    assert jobs["DemoAir"]["track"] == "full_time" and jobs["DemoRail"]["score"] is not None
    assert len(list((tmp_path / "job_packages").glob("*/*.md"))) == 2
    summary = (tmp_path / "logs" / "last_run_summary.txt").read_text()
    assert "external job added: DemoRail — Embedded Software Co-op" in summary
    assert not list(paths.EXTERNAL_INBOX.iterdir()) and not list((tmp_path / "downloads").iterdir())
    assert R.main(args) == 0                                                       # the next run: nothing re-added
    with sqlite_conn(tmp_path) as c:
        assert c.execute("SELECT COUNT(*) FROM jobs WHERE source='external'").fetchone()[0] == 2


def test_import_pending_script_imports_dashboard_files_now(monkeypatch, tmp_path, capsys, cfg):
    """./scripts/import_pending.sh: the newest Export statuses + "Add external job" files, then the dashboard, once."""
    from jobsearch import import_pending as IP
    from jobsearch.ops.lock import RunLock
    _, common = _cli_env(monkeypatch, tmp_path, cfg)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    prefs = cfg.prefs.model_copy(update={"statuses_import_dir": [str(downloads)],
                                         "my_companies_csv": "inputs/companies/my_companies.csv"})
    monkeypatch.setattr(IP, "load_config", lambda *a, **k: cfg.model_copy(update={"prefs": prefs}))
    (downloads / "hirescope-external-job-DemoAir.json").write_text(json.dumps(
        {"kind": "hirescope_external_job", "format": 1, "company": "DemoAir", "role": "Software Engineer (DEMO)",
         "track": "full_time", "jd_text": JD.replace("DemoCorp", "DemoAir")}))
    (downloads / "jobsearch-statuses.json").write_text(json.dumps({
        "exported_at": "2026-10-14T20:00:00Z", "format": 5, "statuses": {}, "applications": {
            "a1": {"company": "Elsewhere (DEMO)", "title": "SWE Co-op", "track": "coop_intern", "source": "Career fair",
                   "applied_date": "2026-10-13", "updated_at": "2026-10-13T12:00:00Z"}}}))
    assert IP.main(common) == 0
    out = capsys.readouterr().out
    assert "statuses: imported" in out and "apps_new 1" in out
    assert "external job added: DemoAir — Software Engineer (DEMO)" in out and "dashboard updated" in out
    assert '"DemoAir"' in (tmp_path / "dashboard" / "data.js").read_text()
    assert "Elsewhere (DEMO)" in (tmp_path / "dashboard" / "sync.js").read_text()
    assert not list(downloads.glob("hirescope-external-job*"))                 # moved out after import
    before = (tmp_path / "dashboard" / "data.js").stat().st_mtime_ns
    assert IP.main(common) == 0                                                 # again: nothing waiting
    out = capsys.readouterr().out
    assert "already imported" in out and "nothing waiting" in out and "dashboard updated" not in out
    assert (tmp_path / "dashboard" / "data.js").stat().st_mtime_ns == before
    with RunLock(paths.STATE_DIR / "run.lock", what="run"):                      # a run in progress: leave it to the run
        assert IP.main(common) == 3


class sqlite_conn:
    def __init__(self, tmp_path):
        self.c = db.connect(tmp_path / "jobs.db")

    def __enter__(self):
        return self.c

    def __exit__(self, *a):
        self.c.close()


# --- applied tracking -------------------------------------------------------------------------------------------------
def test_mark_applied_creates_application_with_resume_jd_link(conn, cfg, resume, data_dir, monkeypatch):
    o = X.import_external(conn, cfg, X.from_text(JD, company="DemoCorp", role="Software Engineer Co-op (DEMO)",
                                                 url="https://demo.example/jobs/1", track="coop"))
    _score(conn, cfg, resume)
    conn.execute("INSERT INTO resume_builds (job_pk, version, status, pdf_path, created_at, summary) VALUES "
                 "(?, 2, 'ready', '/r/DemoCorp_v2.pdf', '2026-10-14T10:00:00+00:00', '{\"pdf_sha\": \"abc\"}')", (o.job_pk,))
    conn.execute("INSERT INTO resume_status (job_pk, status, current_version, latest_version, updated_at) VALUES "
                 "(?, 'ready', 2, 2, '2026-10-14T10:00:00+00:00')", (o.job_pk,))
    app_id = statuses.mark_applied(conn, o.job_pk, paths.APPS_DIR, "2026-10-14")
    a = next(x for x in statuses.applications(conn) if x["id"] == app_id)
    assert (a["company"], a["title"], a["track"], a["source"], a["applied_date"], a["stage"]) == \
        ("DemoCorp", "Software Engineer Co-op (DEMO)", "coop_intern", "External", "2026-10-14", "applied")
    assert a["link"] == "https://demo.example/jobs/1" and a["jd"].startswith("About the role")
    assert a["job"]["resume"]["version"] == 2 and a["job"]["resume"]["pdf_sha"] == "abc"
    assert a["job"]["resume"]["pdf_name"] == "DemoCorp_v2.pdf" and a["job"]["resume"]["linked"] == "applied"
    assert a["job_key"] == statuses.job_key(_job(conn, o.job_pk))
    assert statuses.mark_applied(conn, o.job_pk, paths.APPS_DIR) == app_id                 # idempotent
    with pytest.raises(ValueError):
        statuses.mark_applied(conn, 99999, paths.APPS_DIR)


def test_any_job_marked_applied_gets_an_application(conn, cfg, data_dir):
    from jobsearch.adapters.base import RawJob
    from jobsearch.pipeline import to_record
    pk, _ = db.upsert_job(conn, to_record(RawJob(company="DemoCo", job_id="1", title="Software Engineer (DEMO)",
                                                 source="greenhouse", source_url="https://x.example/1",
                                                 apply_url="https://x.example/1", description=JD), cfg, paths.JD_DIR))
    conn.execute("UPDATE jobs SET user_status='interviewing', user_status_at='2026-10-01T12:00:00+00:00' WHERE id=?", (pk,))
    made = statuses.ensure_applications(conn, paths.APPS_DIR)
    a = next(x for x in statuses.applications(conn) if x["id"] == made[0])
    assert (a["source"], a["stage"], a["applied_date"]) == ("HireScope", "interviewing", "2026-10-01") and a["jd"]
    assert statuses.ensure_applications(conn, paths.APPS_DIR) == []                      # once
    # an application made on the dashboard (same job key) is not duplicated
    pk2, _ = db.upsert_job(conn, to_record(RawJob(company="DemoCo", job_id="2", title="Data Engineer (DEMO)",
                                                  source="greenhouse", source_url="", description=JD + " data"), cfg, None))
    conn.execute("UPDATE jobs SET user_status='applied', user_status_at='2026-10-02T12:00:00+00:00' WHERE id=?", (pk2,))
    statuses.merge(conn, {"exported_at": "2026-10-03T00:00:00Z", "format": 5, "statuses": {}, "applications": {
        "a1": {"job_key": "DemoCo::greenhouse::2", "company": "DemoCo", "title": "Data Engineer (DEMO)",
               "applied_date": "2026-10-02", "updated_at": "2026-10-02T12:00:00Z"}}}, None)
    assert statuses.ensure_applications(conn, None) == []


def _apps():
    return [{"id": "1", "company": "DemoCorp", "title": "SWE Co-op", "stage": "rejected", "applied_date": "2026-09-01",
             "updated_at": "2026-09-20", "source": "HireScope", "track": "coop_intern", "link": "https://d.example/1"},
            {"id": "2", "company": "democorp ", "title": "Backend Co-op", "stage": "oa", "applied_date": "2026-10-05",
             "updated_at": "2026-10-06", "source": "External", "track": "coop_intern", "link": ""},
            {"id": "3", "company": "Acme (DEMO)", "title": "SWE", "stage": "applied", "applied_date": "2026-10-05",
             "updated_at": "2026-10-05", "source": "External", "track": "full_time", "link": "https://a.example"},
            {"id": "4", "company": "Gone (DEMO)", "title": "SWE", "stage": "applied", "applied_date": "2026-10-09",
             "deleted": True}]


def test_applied_companies():
    rows = A.applied_companies(_apps())
    assert [r["company"] for r in rows] == ["Acme (DEMO)", "democorp "]          # newest first, A–Z on ties; deleted out
    d = rows[1]
    assert (d["count"], d["active"], d["latest_stage"], d["latest_applied"], d["first_applied"]) == (2, 1, "oa", "2026-10-05", "2026-09-01")
    assert d["sources"] == ["External", "HireScope"] and [x["id"] for x in d["applications"]] == ["2", "1"]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_core_js_applied_companies_and_external_duplicate_match_python():
    core = json.dumps(str(paths.DASHBOARD / "core.js"))
    js = f"""const C = require({core});
const jobs = [{{id: 7, company: "DemoCorp", title: "Software Engineer Co-op (DEMO)", apply_url: "https://www.demo.example/jobs/1/"}}];
console.log(JSON.stringify({{rows: C.appliedCompanies({json.dumps(_apps())}),
  url: C.externalDuplicate(jobs, {{company: "x", role: "y", url: "https://demo.example/jobs/1?utm_source=li"}}),
  role: C.externalDuplicate(jobs, {{company: "democorp", role: "Software Engineer Co-op (DEMO)"}}),
  none: C.externalDuplicate(jobs, {{company: "DemoCorp", role: "Data Analyst"}})}}));"""
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out["rows"] == A.applied_companies(_apps())
    assert out["url"]["kind"] == "same_url" and out["role"]["kind"] == "same_role" and out["role"]["id"] == 7
    assert out["none"] is None
