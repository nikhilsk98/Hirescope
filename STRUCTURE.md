# What lives where

All locations are defined in `src/jobsearch/paths.py`. Folder names are ASCII words joined by hyphens
(`Clēnera` → `Clenera`, `AT&T / Mobility` → `AT-T-Mobility`).

```
hirescope/
├── README.md  PROGRESS.md  STRUCTURE.md  requirements.txt  pyproject.toml  .env.example
├── config/          tuning and source lists (companies.yaml, title_rules.yaml, resume_builder.yaml, …)
├── dashboard/       the static dashboard (index.html, app.js, …) + generated data.js, details/, sync.js, resumes.js
├── docs/            notes; docs/prompts/ = the build prompts this project was built from
├── scripts/         run.sh, setup.sh, schedule.sh (+ launchd/ templates), add_url.sh, migrate_layout.py
├── src/jobsearch/   HireScope (discovery → scoring → dashboard)
├── src/resume_builder/   the Resume Builder (/resume-builder)
├── tests/           DEMO data only
├── .claude/skills/resume-builder/   the Claude Code skill
│
├── inputs/          YOUR files (gitignored except *.example.*)
│   ├── preferences.yaml (+ .example)       what you're looking for; the paths below are set here
│   ├── resume/      master_resume.md (+ .example), evidence_additions.md, resume_template.tex, resume_rules.md
│   ├── companies/   my_companies.csv (+ .example), coop_leads.txt
│   ├── linkedin/    Connections.csv (LinkedIn data export, optional)
│   ├── statuses/    dashboard exports (scheduled runs can always read this folder)
│   └── h1b/         h1b_employer_data.csv (USCIS Employer Data Hub, optional)
│
└── data.nosync/     GENERATED, never synced by iCloud (".nosync"), gitignored
    ├── db/          jobs.db (+ -wal/-shm while a run is open)
    ├── jd/<Company>/            full JD text per posting (_unassigned/: no job refers to it any more)
    ├── job_packages/<Company>/  job packages saved on disk
    ├── applications/            saved JD copies for your applications
    ├── imports/                 dashboard exports that were imported
    ├── generated_resumes/
    │   ├── <Company>/<Role>_<JobID>/
    │   │   ├── v1/, v2/, …      Nikhil_<Last>_Resume_<Company>.pdf, main.tex, content.tex, content.json,
    │   │   │                    manifest.json, compile.log, pages/ (previews), source/ (rebuild bundle)
    │   │   ├── reports/v1/, …   truth, coverage, changes, defense notes, review, validation, extracted text
    │   │   ├── latest → vN      the current version (symlink)
    │   │   ├── current.json  locks.json  history.jsonl
    │   │   └── _work/           the generation in progress (brief, drafts, build folder)
    │   └── _cache/              evidence bank, batch queue
    ├── logs/        jobsearch.log (rotated), launchd logs, last_run_summary.txt
    ├── backups/     daily SQLite snapshots (last 14)
    ├── digests/  exports/  screenshots/
    └── state/       run.lock (while a run is active)
```

- Running against a copy: `JOBSEARCH_DATA_DIR=/copy JOBSEARCH_DASHBOARD_DIR=/copy-dashboard …` leaves the real
  data and dashboard untouched.
- The dashboard links resumes relative to `dashboard/` (`../data.nosync/generated_resumes/…`), so it works from
  `file://` with no server.
- Moving from the old layout (`data/` symlink, flat `inputs/`): `scripts/migrate_layout.py --dry-run`, then without.
