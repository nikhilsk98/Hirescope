"""Every file location HireScope and the Resume Builder use (see STRUCTURE.md)."""
import os
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"

# --- your inputs (gitignored except *.example.*) -------------------------------------------------------------
INPUTS = ROOT / "inputs"
PREFERENCES = INPUTS / "preferences.yaml"
RESUME_INPUTS = INPUTS / "resume"            # master_resume.md, evidence_additions.md, resume_template.tex, resume_rules.md
RESUME = RESUME_INPUTS / "master_resume.md"
COMPANY_INPUTS = INPUTS / "companies"        # my_companies.csv, coop_leads.txt
LINKEDIN_INPUTS = INPUTS / "linkedin"        # Connections.csv (LinkedIn data export)
STATUSES_INPUTS = INPUTS / "statuses"        # dashboard exports that scheduled runs can always read
H1B_INPUTS = INPUTS / "h1b"                  # h1b_employer_data.csv (USCIS Employer Data Hub)

COMPANIES = CONFIG / "companies.yaml"
SYNONYMS = CONFIG / "skills_synonyms.yaml"
TITLE_RULES = CONFIG / "title_rules.yaml"
ENG_BLOGS = CONFIG / "eng_blogs.yaml"

# --- runtime data: data.nosync/ (never synced by iCloud; gitignored). Override with JOBSEARCH_DATA_DIR. ------------
DATA = Path(os.environ["JOBSEARCH_DATA_DIR"]).expanduser() if os.environ.get("JOBSEARCH_DATA_DIR") else ROOT / "data.nosync"
# Generated dashboard data goes here; override with JOBSEARCH_DASHBOARD_DIR (with JOBSEARCH_DATA_DIR, e.g. to run against
# a copy of your data without touching the real dashboard).
DASHBOARD = (Path(os.environ["JOBSEARCH_DASHBOARD_DIR"]).expanduser() if os.environ.get("JOBSEARCH_DASHBOARD_DIR")
             else ROOT / "dashboard")

# Everything under DATA / DASHBOARD is derived when read (module __getattr__), so redirecting DATA (tests,
# JOBSEARCH_DATA_DIR) redirects all of it. Setting one of these names explicitly still overrides it.
_UNDER_DATA = {
    "DB": ("db", "jobs.db"),
    "JD_DIR": ("jd",),                    # jd/<Company>/<source>_<job id>.txt
    "PACKAGES_DIR": ("job_packages",),    # job_packages/<Company>/
    "LOG_DIR": ("logs",),
    "APPS_DIR": ("applications",),
    "EXTERNAL_DIR": ("external_jobs",),   # external_jobs/<Company>/: the original files of imported external jobs        # saved JD copies for your applications (kept after postings close)
    "IMPORTS_DIR": ("imports",),          # archived copies of imported dashboard exports
    "BACKUPS_DIR": ("backups",),
    "DIGESTS_DIR": ("digests",),
    "EXPORTS_DIR": ("exports",),
    "SCREENSHOTS_DIR": ("screenshots",),
    "STATE_DIR": ("state",),              # run.lock
    "RESUMES_DIR": ("generated_resumes",),   # <Company>/<Role>_<JobID>/v1, v2, reports/vN, latest
}


def __getattr__(name: str):
    if name in _UNDER_DATA:
        return DATA.joinpath(*_UNDER_DATA[name])
    if name == "SYNC_JS":                 # SQLite -> dashboard: your statuses and applications
        return DASHBOARD / "sync.js"
    if name == "EXTERNAL_INBOX":          # drop folder: JD files of jobs found outside HireScope (the next run imports)
        return ROOT / "inputs" / "external_jobs"
    raise AttributeError(name)


def slug(name: str, max_len: int = 60) -> str:
    """Folder-safe name: ASCII words joined by hyphens ("Clēnera" → "Clenera", "AT&T / Mobility" → "AT-T-Mobility")."""
    ascii_ = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    parts = re.findall(r"[A-Za-z0-9]+", ascii_)
    out = ""
    for p in parts:
        if len(out) + len(p) + (1 if out else 0) > max_len:
            break
        out = f"{out}-{p}" if out else p
    return out or "Unknown"


def jd_dir_for(company: str, base: Path | None = None) -> Path:
    return (base or JD_DIR) / slug(company)


def stored_file(p: str | None, base: Path) -> Path | None:
    """A file path saved in SQLite, found again after the repo or data folder moved: the stored absolute path if it
    still exists, else the same file under `base` (directly or in its company subfolder). None when neither exists."""
    if not p:
        return None
    path = Path(p)
    if path.exists():
        return path
    for moved in (base / path.parent.name / path.name, base / path.name):
        if moved.exists():
            return moved
    # saved before files were grouped into company folders: look one level down (rare; each run repairs paths)
    return next(base.glob(f"*/{glob_escape(path.name)}"), None) if base.exists() else None


def glob_escape(name: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", name)
