"""DEMO environment for Resume Builder tests: inputs, config, a HireScope DB with DEMO jobs, data dir — all in
tmp_path. Nothing here reads your real inputs or data."""
import json
import shutil
from pathlib import Path

import pytest
import yaml

from jobsearch import paths as hs_paths
from jobsearch.storage import db
from resume_builder import config as rc

FIX = Path(__file__).parent / "fixtures"
REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "resume_builder.yaml"

DEMO_JD = """DEMO Corp builds payment infrastructure.

What you'll do:
• Build and operate REST APIs in Java and Spring Boot.
• Work with PostgreSQL and AWS.

Requirements:
• Experience with Java and Spring Boot
• Experience with GCP or Kubernetes
• Strong communication skills
"""


def insert_job(conn, jd_dir: Path, company="DemoCorp", title="Backend Engineer Intern", track="coop_intern",
               job_id="demo-1", jd=DEMO_JD, min_reqs=None, pref_reqs=None, keywords=None, pk=None):
    jd_dir.mkdir(parents=True, exist_ok=True)
    p = jd_dir / f"greenhouse_{company.lower()}_{job_id}.txt"
    p.write_text(jd, encoding="utf-8")
    kw = keywords if keywords is not None else {"coverage": 60.0, "required_hit": 1, "required_total": 2, "keywords": [
        {"canonical": "Java", "term": "Java", "section": "required", "count": 2, "weight": 6.0, "category": "languages",
         "status": "matched", "resume_term": "Java", "evidence": None, "evidence_section": None},
        {"canonical": "Google Cloud", "term": "GCP", "section": "required", "count": 1, "weight": 3.0,
         "category": "cloud", "status": "missing", "resume_term": None, "evidence": None, "evidence_section": None},
        {"canonical": "PostgreSQL", "term": "PostgreSQL", "section": "mentioned", "count": 1, "weight": 1.0,
         "category": "databases", "status": "matched", "resume_term": None, "evidence": None, "evidence_section": None}]}
    cols = dict(company=company, company_key=company.lower(), title=title, normalized_title=title.lower(),
                job_id=job_id, location="Boston, MA", first_seen="2026-10-01T00:00:00+00:00",
                last_checked="2026-10-01T00:00:00+00:00", source="greenhouse", apply_url="https://example.com/apply",
                jd_path=str(p), jd_hash="x", track=track, level="intern" if track == "coop_intern" else "entry",
                label="Strong", score=88.0, job_status="new",
                min_reqs=json.dumps(min_reqs if min_reqs is not None else
                                    ["Experience with Java and Spring Boot", "Experience with GCP or Kubernetes",
                                     "Strong communication skills"]),
                pref_reqs=json.dumps(pref_reqs or []), skills=json.dumps(["Java", "Spring Boot"]),
                keywords=json.dumps(kw), flags=json.dumps({}), grad_years=json.dumps([]), extra=json.dumps({}))
    if pk:
        cols["id"] = pk
    q = f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    cur = conn.execute(q, list(cols.values()))
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def rb_env(tmp_path, monkeypatch):
    inputs, data, cfgdir = tmp_path / "inputs", tmp_path / "data", tmp_path / "config"
    for d in (inputs, data, cfgdir):
        d.mkdir()
    shutil.copy(FIX / "demo_master_resume.md", inputs / "master_resume.md")
    shutil.copy(FIX / "demo_template.tex", inputs / "resume_template.tex")
    shutil.copy(FIX / "demo_rules.md", inputs / "resume_rules.md")
    cfg = yaml.safe_load(REPO_CONFIG.read_text())
    # naming left empty: the name comes from the DEMO master resume heading ("# Demo Candidate")
    (cfgdir / "resume_builder.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(rc, "MASTER_RESUME", inputs / "master_resume.md")
    monkeypatch.setattr(rc, "TEMPLATE", inputs / "resume_template.tex")
    monkeypatch.setattr(rc, "RULES", inputs / "resume_rules.md")
    monkeypatch.setattr(rc, "ADDITIONS", inputs / "evidence_additions.md")
    monkeypatch.setattr(rc, "CONFIG_FILE", cfgdir / "resume_builder.yaml")
    monkeypatch.setattr(rc, "ROOT", tmp_path)
    monkeypatch.setattr(hs_paths, "DATA", data)
    monkeypatch.setattr(hs_paths, "DB", data / "jobs.db")
    monkeypatch.setattr(hs_paths, "JD_DIR", data / "jd")
    monkeypatch.setattr(hs_paths, "DASHBOARD", tmp_path / "dashboard")
    conn = db.connect(data / "jobs.db")
    pk = insert_job(conn, data / "jd")
    ft = insert_job(conn, data / "jd", company="Full Time Co", title="Software Engineer I", track="full_time",
                    job_id="demo-2")
    conn.close()

    class Env:
        root = tmp_path
        job = pk
        ft_job = ft
        jd_dir = data / "jd"
        db_path = data / "jobs.db"
        inputs_dir = inputs
    Env.inputs = inputs
    Env.data = data
    yield Env
    # every test must still be inside the DEMO sandbox at the end (a monkeypatch.undo() would silently point the
    # rest of the test at real data)
    assert hs_paths.DB == data / "jobs.db" and rc.MASTER_RESUME == inputs / "master_resume.md", \
        "a test left the DEMO sandbox (monkeypatch.undo?)"


def needs_tex():
    return pytest.mark.skipif(shutil.which("tectonic") is None or shutil.which("pdftotext") is None,
                              reason="tectonic / poppler not installed")


def reviewed_flow(rb_env, work: Path, edit=None, job=None):
    """DEMO drafter → reviewer: draft = baseline (optionally edited), reviewer packet, reviewed content + review.json."""
    from resume_builder import pipeline
    from resume_builder.content_schema import dump, parse
    c = parse((work / "baseline.json").read_text())
    if edit:
        edit(c)
    c.notes.stage = "draft"
    (work / "draft.json").write_text(dump(c))
    pipeline.review_draft(str(job or rb_env.job))
    c = parse((work / "draft.json").read_text())
    c.notes.stage = "reviewed"
    c.notes.reviewer.append("DEMO reviewer: no changes needed")
    (work / "content.json").write_text(dump(c))
    (work / "review.json").write_text(json.dumps({"reviewer": "DEMO", "critique": []}))
    return c
