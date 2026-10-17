# What lives where

All locations are defined in `src/jobsearch/paths.py`. Folder names are ASCII words joined by hyphens
(`Clēnera` → `Clenera`, `AT&T / Mobility` → `AT-T-Mobility`).

```
hirescope/
├── README.md  PROGRESS.md  STRUCTURE.md  requirements.txt  pyproject.toml  .env.example
├── config/          tuning and source lists (companies.yaml, title_rules.yaml, resume_builder.yaml, …)
├── dashboard/       the static dashboard (index.html, app.js, …) + generated data.js, details/, sync.js, resumes.js,
│                    search.js (every job, for the ⌘K palette), live.js (queue / worker / Excel state, re-read every few
│                    seconds), launcher.js (is the hirescope:// launcher installed?)
├── docs/            notes; docs/prompts/ = the build prompts this project was built from
├── scripts/         run.sh, setup.sh, schedule.sh (+ launchd/ templates), add_url.sh, add_job.sh, import_pending.sh, sync_desktop_resumes.sh, migrate_layout.py,
│                    install_launcher.sh / uninstall_launcher.sh, launcher.sh (one hirescope:// link), queue_worker.sh,
│                    launcher/HireScopeLauncher.applescript (the launcher app's source)
├── src/jobsearch/   HireScope (discovery → scoring → dashboard)
├── src/resume_builder/   the Resume Builder (/resume-builder)
├── tests/           DEMO data only
├── .claude/skills/resume-builder/   the Claude Code skill
│
├── inputs/          YOUR files (gitignored except *.example.*)
│   ├── preferences.yaml (+ .example)       what you're looking for; the paths below are set here
│   ├── resume/      master_resume.md (v4 format: Default one-page snapshot, bullet banks, metrics with statuses,
│   │                Skills inventory tiers, Builder notes), evidence_additions.md (confirmed interview answers),
│   │                tool_checklist.md (ticks count only as far as the master has evidence), resume_template.tex,
│   │                resume_rules.md (v4.3; its numbers and layout spec drive the builder), archive/ (old versions)
│   ├── companies/   my_companies.csv (+ .example), coop_leads.txt
│   ├── linkedin/    Connections.csv (LinkedIn data export, optional)
│   ├── statuses/    dashboard exports (scheduled runs can always read this folder)
│   ├── external_jobs/   drop folder: JD files (.txt .md .pdf .docx) of jobs found outside HireScope; the next run
│   │                    imports them (company / role from a "Company: … / Role: …" header or "Company - Role.pdf")
│   └── h1b/         h1b_employer_data.csv (USCIS Employer Data Hub, optional)
│
└── data.nosync/     GENERATED, never synced by iCloud (".nosync"), gitignored
    ├── db/          jobs.db (+ -wal/-shm while a run is open)
    ├── jd/<Company>/            full JD text per posting (_unassigned/: no job refers to it any more)
    ├── job_packages/<Company>/  job packages saved on disk (external jobs' packages are rewritten each run)
    ├── external_jobs/<Company>/ original PDF / DOCX of imported external jobs (_imported/: drop-folder files after import)
    ├── applications/            saved JD copies for your applications
    ├── imports/                 dashboard exports that were imported (external/: imported "Add external job" files)
    ├── generated_resumes/
    │   ├── <Company>/<Role>_<JobID>/
    │   │   ├── v1/, v2/, …      Nikhil_<Last>_Resume_<Company>.pdf, main.tex, content.tex, content.json,
    │   │   │                    manifest.json, compile.log, pages/ (previews), source/ (rebuild bundle)
    │   │   ├── reports/v1/, …   truth, coverage, changes, defense notes, review, validation, extracted text
    │   │   ├── latest → vN      the current version (symlink)
    │   │   ├── current.json  locks.json  history.jsonl
    │   │   └── _work/           the generation in progress (brief, drafts, build folder)
    │   ├── _default/            the Default one-page resume (no job description): v1, v2, … like any job
    │   ├── learn_it.md          JD keywords missing from your evidence in several prepared jobs (rule 61)
    │   └── _cache/              evidence bank, batch queue, missing_keywords.json, keyword_answers.json (your "no"s)
    ├── logs/        jobsearch.log (rotated), launchd logs, last_run_summary.txt, launcher.log (every hirescope:// link),
    │                queue/ (one log per queued build attempt)
    ├── backups/     daily SQLite snapshots (last 14)
    ├── digests/  exports/  screenshots/
    └── state/       run.lock (while a run is active), queue_worker.lock, queue.trigger (starts the launchd worker),
                     launcher_spool/ (links that met a busy database), external_requests/, desktop_pending

~/Desktop/Generated Resumes/   COPIES of READY PDFs only (desktop_copy in config/resume_builder.yaml; outside the repo)
    ├── HireScope Applications.xlsx   My Applications as Excel (preferences excel_applications), rebuilt after each change
    ├── <Company>/<Role>_<JobID>/  Nikhil_Shivakumar_Resume_<Company>.pdf (current), …_<Company>_v<N>.pdf (older READY)
    └── Default/                   Nikhil_Shivakumar_Resume.pdf (+ _v<N> older)
```

- Resume Builder code for the v4 master: `master_v4.py` (parser), `v4build.py` (snapshot baseline, template-macro
  rendering, layout checks), `rules_v4.py` (rules v4.3 checks). HireScope's matcher reads the same v4 master through
  `jobsearch/extract/resume.py` (usable bullets and inventory skills only).
- Zero-CLI resumes: `~/Applications/HireScope Launcher.app` → `scripts/launcher.sh` → `src/jobsearch/launcher.py`
  (validates and runs each hirescope:// link); `src/resume_builder/queue.py` (SQLite `resume_queue`, the launchd worker
  `com.nikhil.hirescope.worker`); `src/resume_builder/questions.py` (Questions for you: `resume_questions`);
  `src/jobsearch/bucket.py` (the Bucket: `bucket`); `src/jobsearch/hooks.py` (what every change refreshes: sync.js,
  applications, Excel, live.js); `src/jobsearch/live.py` (dashboard/live.js, launcher.js);
  `src/jobsearch/ops/launcher_install.py` (install / uninstall); `src/jobsearch/external_resume.py` (Resume Builder
  submissions: the launcher leaves a request in data.nosync/state/external_requests/, the worker imports it);
  `src/jobsearch/ops/desktop_access.py` (Desktop / Downloads access checked in a child process with a timeout, so a
  background job never hangs on a macOS permission prompt); `src/jobsearch/ops/applications_excel.py` (the Desktop
  Excel file); `src/jobsearch/tracker/ready_apps.py` ("Ready to apply").
- Desktop copy: `src/resume_builder/desktop.py` mirrors each job's READY PDFs when a version becomes current
  (`versions.set_current`) and on `./scripts/sync_desktop_resumes.sh` (`python -m resume_builder sync-desktop`);
  tests point it at a temporary folder (`JOBSEARCH_DESKTOP_DIR`).
- Resume Builder inputs can come from a copy: `JOBSEARCH_RESUME_INPUTS_DIR=/copy-of-inputs-resume` (with
  `JOBSEARCH_DATA_DIR` / `JOBSEARCH_DASHBOARD_DIR` for a full test run that touches nothing real).
- External jobs (source `external`): `src/jobsearch/external.py` (reading, duplicates, import), `add_job.py` (CLI);
  their JD text goes to `jd/<Company>/external_<company>_ext-<id>.txt` like any other job's.
- Running against a copy: `JOBSEARCH_DATA_DIR=/copy JOBSEARCH_DASHBOARD_DIR=/copy-dashboard …` leaves the real
  data and dashboard untouched.
  `inputs/` (including the `external_jobs/` drop folder) is still the repo's: a run against a copy imports — and moves
  — files waiting there.
- The dashboard links resumes relative to `dashboard/` (`../data.nosync/generated_resumes/…`), so it works from
  `file://` with no server.
- Moving from the old layout (`data/` symlink, flat `inputs/`): `scripts/migrate_layout.py --dry-run`, then without.
