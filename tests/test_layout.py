"""Folder layout (STRUCTURE.md): derived paths, folder names, JD company folders, the one-time migration. DEMO data."""
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from jobsearch import paths
from jobsearch.storage import db

ROOT = Path(__file__).resolve().parents[1]


def test_data_paths_follow_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    assert paths.DB == tmp_path / "db" / "jobs.db" and paths.JD_DIR == tmp_path / "jd"
    assert paths.STATE_DIR == tmp_path / "state" and paths.RESUMES_DIR == tmp_path / "generated_resumes"
    assert paths.PACKAGES_DIR == tmp_path / "job_packages" and paths.BACKUPS_DIR == tmp_path / "backups"
    assert paths.RESUME == paths.INPUTS / "resume" / "master_resume.md"
    assert paths.DATA.name != "data" and (ROOT / "data.nosync").name.endswith(".nosync")


def test_folder_names_are_clean():
    assert paths.slug("Clēnera") == "Clenera"
    assert paths.slug("AT&T / Mobility, Inc.") == "AT-T-Mobility-Inc"
    assert paths.slug("") == "Unknown"
    long = paths.slug("Software Engineer I, Entry-Level (Graduation Date: Fall 2026-Summer 2027) - US", 50)
    assert len(long) <= 50 and "-" in long and not long.endswith("-")


def test_stored_file_finds_files_in_company_subfolders(tmp_path):
    (tmp_path / "jd" / "DemoCo").mkdir(parents=True)
    f = tmp_path / "jd" / "DemoCo" / "greenhouse_democo_1.txt"
    f.write_text("DEMO")
    assert paths.stored_file("/old/place/data/jd/greenhouse_democo_1.txt", tmp_path / "jd") == f
    assert paths.stored_file(str(f), tmp_path / "jd") == f


def test_repair_finds_jd_files_in_company_folders(tmp_path, conn):
    jd = tmp_path / "jd"
    (jd / "DemoCo").mkdir(parents=True)
    (jd / "DemoCo" / "greenhouse_democo_9.txt").write_text("DEMO")
    conn.execute("INSERT INTO jobs (company, company_key, title, normalized_title, job_id, first_seen, last_checked, "
                 "source, jd_path) VALUES ('DemoCo','democo','DEMO','demo','9','x','x','greenhouse',?)",
                 ("/old/data/jd/greenhouse_democo_9.txt",))
    assert db.repair_moved_paths(conn, jd, tmp_path / "apps") == 1
    assert conn.execute("SELECT jd_path FROM jobs").fetchone()[0] == str(jd / "DemoCo" / "greenhouse_democo_9.txt")


def test_migration_script_moves_an_old_layout(tmp_path):
    """DEMO copy of the old layout → the new one: files moved (not regenerated), SQLite and manifests updated."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "migrate_layout.py", root / "scripts")
    shutil.copytree(ROOT / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
    data = root / "data.nosync"
    (data / "jd").mkdir(parents=True)
    (data / "jd" / "greenhouse_democo_1.txt").write_text("DEMO JD")
    (data / "jd" / "greenhouse_orphan_2.txt").write_text("DEMO orphan")
    jobdir = data / "resumes" / "DemoCo_1"
    (jobdir / "v1" / "reports").mkdir(parents=True)
    (jobdir / "work").mkdir()
    (jobdir / "v1" / "Demo_Candidate_Resume_DemoCo.pdf").write_bytes(b"%PDF DEMO")
    (jobdir / "v1" / "reports" / "truth.md").write_text("DEMO")
    (jobdir / "v1" / "manifest.json").write_text(json.dumps({"files": {"reports": ["reports/truth.md"]}}))
    (jobdir / "current.json").write_text(json.dumps({"version": 1}))
    (root / "data").symlink_to("data.nosync")
    inputs = root / "inputs"
    inputs.mkdir()
    (inputs / "master_resume.md").write_text("# Demo Candidate\n")
    (inputs / "my_companies.csv").write_text("company\nDemoCo\n")
    (inputs / "preferences.yaml").write_text("my_companies_csv: inputs/my_companies.csv\n")
    c = sqlite3.connect(data / "jobs.db")
    db.migrate(c)
    c.execute("INSERT INTO jobs (id, company, company_key, title, normalized_title, job_id, first_seen, last_checked, "
              "source, jd_path) VALUES (1, 'DemoCo','democo','Backend Engineer (DEMO)','x','1','x','x','greenhouse',?)",
              (str(data / "jd" / "greenhouse_democo_1.txt"),))
    c.execute("INSERT INTO resume_builds (job_pk, version, status, pdf_path, created_at) VALUES (1, 1, 'ready', ?, 'x')",
              (str(root / "data" / "resumes" / "DemoCo_1" / "v1" / "Demo_Candidate_Resume_DemoCo.pdf"),))
    c.commit()
    c.close()
    run = lambda *a: subprocess.run([sys.executable, str(root / "scripts" / "migrate_layout.py"), *a],
                                    capture_output=True, text=True, cwd=root)
    dry = run("--dry-run")
    assert dry.returncode == 0 and (data / "jobs.db").exists()            # dry run moves nothing
    res = run()
    assert res.returncode == 0, res.stderr
    new_job = data / "generated_resumes" / "DemoCo" / "Backend-Engineer-DEMO_1"
    assert (new_job / "v1" / "Demo_Candidate_Resume_DemoCo.pdf").read_bytes() == b"%PDF DEMO"
    assert (new_job / "reports" / "v1" / "truth.md").exists() and (new_job / "_work").is_dir()
    assert (new_job / "latest").is_symlink() and (new_job / "latest").resolve() == (new_job / "v1").resolve()
    assert json.loads((new_job / "v1" / "manifest.json").read_text())["files"]["reports"] == ["../reports/v1/truth.md"]
    assert (data / "jd" / "DemoCo" / "greenhouse_democo_1.txt").exists()
    assert (data / "jd" / "_unassigned" / "greenhouse_orphan_2.txt").exists()
    assert not (root / "data").exists() and (data / "db" / "jobs.db").exists()
    assert (inputs / "resume" / "master_resume.md").exists() and (inputs / "companies" / "my_companies.csv").exists()
    assert "inputs/companies/my_companies.csv" in (inputs / "preferences.yaml").read_text()
    c = sqlite3.connect(data / "db" / "jobs.db")
    assert c.execute("SELECT jd_path FROM jobs").fetchone()[0] == str(data / "jd" / "DemoCo" / "greenhouse_democo_1.txt")
    assert c.execute("SELECT pdf_path FROM resume_builds").fetchone()[0] == str(new_job / "v1" /
                                                                                "Demo_Candidate_Resume_DemoCo.pdf")
    c.close()
    again = run()
    assert again.returncode == 0 and "already" in again.stdout                # idempotent
