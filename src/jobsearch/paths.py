import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "inputs"
CONFIG = ROOT / "config"
# Runtime data (SQLite, JDs, logs). Override with JOBSEARCH_DATA_DIR, e.g. to keep it out of iCloud Drive.
DATA = Path(os.environ["JOBSEARCH_DATA_DIR"]).expanduser() if os.environ.get("JOBSEARCH_DATA_DIR") else ROOT / "data"
DASHBOARD = ROOT / "dashboard"

RESUME = INPUTS / "master_resume.md"
PREFERENCES = INPUTS / "preferences.yaml"
COMPANIES = CONFIG / "companies.yaml"
SYNONYMS = CONFIG / "skills_synonyms.yaml"
TITLE_RULES = CONFIG / "title_rules.yaml"
ENG_BLOGS = CONFIG / "eng_blogs.yaml"

DB = DATA / "jobs.db"
JD_DIR = DATA / "jd"
PACKAGES_DIR = DATA / "packages"
LOG_DIR = DATA / "logs"
APPS_DIR = DATA / "applications"      # saved JD copies for your applications (kept after postings close)
IMPORTS_DIR = DATA / "imports"        # archived copies of imported dashboard exports
SYNC_JS = DASHBOARD / "sync.js"       # SQLite -> dashboard: your statuses and applications


def stored_file(p: str | None, base: Path) -> Path | None:
    """A file path saved in SQLite, found again after the repo or data folder moved: the stored absolute path if it
    still exists, else the same file name under `base` (JD_DIR, APPS_DIR). None when neither exists."""
    if not p:
        return None
    path = Path(p)
    if path.exists():
        return path
    moved = base / path.name
    return moved if moved.exists() else None
