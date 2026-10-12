from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "inputs"
CONFIG = ROOT / "config"
DATA = ROOT / "data"
DASHBOARD = ROOT / "dashboard"

RESUME = INPUTS / "master_resume.md"
PREFERENCES = INPUTS / "preferences.yaml"
COMPANIES = CONFIG / "companies.yaml"
SYNONYMS = CONFIG / "skills_synonyms.yaml"
TITLE_RULES = CONFIG / "title_rules.yaml"

DB = DATA / "jobs.db"
JD_DIR = DATA / "jd"
PACKAGES_DIR = DATA / "packages"
LOG_DIR = DATA / "logs"
