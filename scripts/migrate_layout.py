"""One-time move to the 2026-10-13 folder layout (see STRUCTURE.md). Moves files, never regenerates them; idempotent
(safe to re-run); --dry-run prints what it would do.

  data/ (symlink) → removed; everything lives in data.nosync/
  data.nosync/jobs.db*            → data.nosync/db/
  data.nosync/jd/*.txt            → data.nosync/jd/<Company>/            (SQLite jobs.jd_path updated)
  data.nosync/packages/           → data.nosync/job_packages/
  data.nosync/run.lock            → data.nosync/state/run.lock
  data.nosync/resumes/<Co>_<id>/  → data.nosync/generated_resumes/<Company>/<Role>_<id>/
        vN/reports/ → reports/vN/,  work/ → _work/,  latest → current version,  manifests updated
        _evidence/bank.json → generated_resumes/_cache/evidence_bank.json (rebuilt on next use anyway)
  inputs/*                         → inputs/resume/, inputs/companies/, inputs/linkedin/, inputs/h1b/
                                     (preferences.yaml paths updated; preferences stay at the top of inputs/)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobsearch.paths import slug  # noqa: E402

DATA = ROOT / "data.nosync"
DRY = False
LOG: list[str] = []


def say(msg: str) -> None:
    LOG.append(msg)
    print(("[dry-run] " if DRY else "") + msg)


def move(src: Path, dst: Path) -> None:
    if not src.exists() and not src.is_symlink():
        return
    if dst.exists():
        raise SystemExit(f"refusing to overwrite {dst}")
    if not DRY:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


def migrate_db() -> Path:
    db = DATA / "db" / "jobs.db"
    if (DATA / "jobs.db").exists():
        if not DRY:
            c = sqlite3.connect(DATA / "jobs.db")
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
        for name in ("jobs.db", "jobs.db-wal", "jobs.db-shm"):
            move(DATA / name, DATA / "db" / name)
        say("db: jobs.db → db/jobs.db")
    return db if db.exists() or not DRY else DATA / "jobs.db"


def migrate_jd(conn) -> None:
    jd = DATA / "jd"
    flat = [p for p in jd.glob("*.txt")] if jd.exists() else []
    if not flat:
        say("jd: already grouped by company")
        return
    company_of = {}
    for path, company in conn.execute("SELECT jd_path, company FROM jobs WHERE jd_path IS NOT NULL"):
        company_of[Path(path).name] = company
    updates, moved, unassigned = [], 0, 0
    for p in flat:
        company = company_of.get(p.name)
        folder = slug(company) if company else "_unassigned"
        unassigned += company is None
        dst = jd / folder / p.name
        move(p, dst)
        moved += 1
    say(f"jd: {moved} files → jd/<Company>/ ({unassigned} not referenced by any job → jd/_unassigned/)")
    if not DRY:
        rows = conn.execute("SELECT rowid, jd_path, company FROM jobs WHERE jd_path IS NOT NULL").fetchall()
        for rowid, path, company in rows:
            new = jd / slug(company) / Path(path).name
            if new.exists() and str(new) != path:
                updates.append((str(new), rowid))
        with conn:
            conn.executemany("UPDATE jobs SET jd_path=? WHERE rowid=?", updates)
        say(f"sqlite: {len(updates)} jobs.jd_path rows updated")


def migrate_resumes(conn) -> None:
    old_root, new_root = DATA / "resumes", DATA / "generated_resumes"
    if not old_root.exists():
        say("resumes: already in generated_resumes/")
        return
    mapping = {}
    for d in sorted(old_root.iterdir()):
        if d.name == "_evidence":
            move(d / "bank.json", new_root / "_cache" / "evidence_bank.json")
            if not DRY:
                shutil.rmtree(d, ignore_errors=True)
            continue
        m = re.match(r"^(.+)_(\d+)$", d.name)
        if not d.is_dir() or not m:
            continue
        pk = int(m.group(2))
        r = conn.execute("SELECT company, title FROM jobs WHERE id=?", (pk,)).fetchone()
        company, title = (r if r else (m.group(1), "Job"))
        dst = new_root / slug(company) / f"{slug(title, 50)}_{pk}"
        mapping[str(d)] = str(dst)
        mapping[str(ROOT / "data" / "resumes" / d.name)] = str(dst)    # paths stored through the old data symlink
        move(d, dst)
        say(f"resume: {d.name} → generated_resumes/{dst.parent.name}/{dst.name}")
        if DRY:
            continue
        if (dst / "work").exists():
            move(dst / "work", dst / "_work")
        for v in sorted(dst.glob("v*")):
            if not re.fullmatch(r"v\d+", v.name):
                continue
            if (v / "reports").exists():
                move(v / "reports", dst / "reports" / v.name)
            mf = v / "manifest.json"
            if mf.exists():
                man = json.loads(mf.read_text())
                files = man.get("files", {})
                files["reports"] = [f"../reports/{v.name}/{Path(x).name}" for x in files.get("reports", [])]
                mf.write_text(json.dumps(man, indent=1, ensure_ascii=False))
        cur = dst / "current.json"
        if cur.exists():
            ver = json.loads(cur.read_text())["version"]
            link = dst / "latest"
            link.unlink(missing_ok=True)
            link.symlink_to(f"v{ver}")
    if not DRY:
        shutil.rmtree(old_root, ignore_errors=True) if not any(old_root.iterdir()) else None
        # SQLite: resume folders and files; application resume links
        for row in conn.execute("SELECT job_pk, job_dir FROM resume_status WHERE job_dir IS NOT NULL").fetchall():
            if row[1] in mapping:
                conn.execute("UPDATE resume_status SET job_dir=? WHERE job_pk=?", (mapping[row[1]], row[0]))
        for job_pk, version, pdf, tex, man in conn.execute(
                "SELECT job_pk, version, pdf_path, tex_path, manifest_path FROM resume_builds").fetchall():
            def remap(p):
                for o, n in mapping.items():
                    if p and p.startswith(o + "/"):
                        return n + p[len(o):]
                return p
            conn.execute("UPDATE resume_builds SET pdf_path=?, tex_path=?, manifest_path=? WHERE job_pk=? AND "
                         "version=?", (remap(pdf), remap(tex), remap(man), job_pk, version))
        for app_id, ref in conn.execute("SELECT id, resume FROM applications WHERE resume IS NOT NULL").fetchall():
            r = json.loads(ref)
            for o, n in mapping.items():
                for key in ("pdf",):
                    if isinstance(r.get(key), str):
                        rel_o = "../data/resumes/" + Path(o).name + "/"
                        if r[key].startswith(o + "/"):
                            r[key] = n + r[key][len(o):]
                        elif r[key].startswith(rel_o):
                            r[key] = "../data.nosync/generated_resumes/" + str(Path(n).relative_to(new_root)) + "/" + \
                                r[key][len(rel_o):]
            conn.execute("UPDATE applications SET resume=? WHERE id=?", (json.dumps(r), app_id))
        conn.commit()
        say(f"sqlite: resume_status / resume_builds / applications.resume updated for {len(mapping)} job folder(s)")


def migrate_misc() -> None:
    if (DATA / "packages").exists():
        if any((DATA / "packages").iterdir()):
            move(DATA / "packages", DATA / "job_packages")
        elif not DRY:
            (DATA / "packages").rmdir()
            (DATA / "job_packages").mkdir(exist_ok=True)
        say("packages/ → job_packages/")
    if (DATA / "run.lock").exists():
        move(DATA / "run.lock", DATA / "state" / "run.lock")
        say("run.lock → state/run.lock")
    for name in ("applications", "imports", "state", "job_packages"):
        if not DRY:
            (DATA / name).mkdir(exist_ok=True)


INPUT_MOVES = {
    "master_resume.md": "resume", "master_resume.example.md": "resume", "evidence_additions.md": "resume",
    "resume_template.tex": "resume", "resume_rules.md": "resume",
    "my_companies.csv": "companies", "my_companies.example.csv": "companies", "coop_leads.txt": "companies",
    "Connections.csv": "linkedin", "h1b_employer_data.csv": "h1b",
}
PREF_PATHS = [("inputs/my_companies.csv", "inputs/companies/my_companies.csv"),
              ("inputs/Connections.csv", "inputs/linkedin/Connections.csv"),
              ("inputs/h1b_employer_data.csv", "inputs/h1b/h1b_employer_data.csv")]


def migrate_inputs() -> None:
    inp = ROOT / "inputs"
    for name, sub in INPUT_MOVES.items():
        if (inp / name).exists():
            move(inp / name, inp / sub / name)
            say(f"inputs: {name} → inputs/{sub}/")
    for sub in ("resume", "companies", "linkedin", "statuses", "h1b"):
        if not DRY:
            (inp / sub).mkdir(exist_ok=True)
    for name in ("preferences.yaml", "preferences.example.yaml"):
        p = inp / name
        if p.exists():
            text = p.read_text(encoding="utf-8")
            new = text
            for o, n in PREF_PATHS:
                new = re.sub(rf"(?<![/\w]){re.escape(o)}", n, new)
            if new != text:
                say(f"inputs/{name}: file paths updated")
                if not DRY:
                    p.write_text(new, encoding="utf-8")


def tidy_root() -> None:
    for o, n in (("BUILD_PROMPT.md", "docs/prompts/BUILD_PROMPT.md"),
                 ("RESUME_BUILDER_PROMPT.md", "docs/prompts/RESUME_BUILDER_PROMPT.md")):
        if (ROOT / o).exists():
            move(ROOT / o, ROOT / n)
            say(f"{o} → {n}")
    for stray in (ROOT / ".coverage", ROOT / ".DS_Store", ROOT / "inputs" / ".DS_Store", ROOT / "inputs" / ".Rhistory"):
        if stray.exists():
            say(f"removed stray {stray.relative_to(ROOT)}")
            if not DRY:
                stray.unlink()
    link = ROOT / "data"
    if link.is_symlink():
        say("removed the data → data.nosync symlink")
        if not DRY:
            link.unlink()


def main() -> int:
    global DRY
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    DRY = ap.parse_args().dry_run
    if (ROOT / "data").exists() and not (ROOT / "data").is_symlink():
        raise SystemExit("data/ is a real folder, not the data.nosync symlink: move its contents by hand first")
    lock = DATA / "run.lock"
    if lock.exists():
        import os
        try:
            os.kill(json.loads(lock.read_text())["pid"], 0)
            raise SystemExit("a HireScope run is in progress; try again when it's done")
        except (ProcessLookupError, ValueError, KeyError):
            pass
    db_path = migrate_db()
    conn = sqlite3.connect(db_path) if db_path.exists() else None
    if conn:
        migrate_jd(conn)
        migrate_resumes(conn)
        conn.close()
    migrate_misc()
    migrate_inputs()
    tidy_root()
    return 0


if __name__ == "__main__":
    sys.exit(main())
