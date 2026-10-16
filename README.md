# HireScope — local job search

A local, single-user job-search tool for macOS: discovery from employer job boards and feeds → dedupe → rules +
scoring for two tracks (Spring 2027 co-op/intern and full-time from May 2027) → a static HTML dashboard with
My Applications → per-job "job packages" → the **Resume Builder** (`/resume-builder`, tailored one-page LaTeX resumes
from your own evidence, below). The core pipeline makes **zero LLM calls**,
never logs in, never scrapes LinkedIn, never applies or messages anyone, and sends no resume data anywhere.

> Status: Phases 1–7 complete, plus the Resume Builder (`/resume-builder`). 300 verified seed companies + Amazon, Netflix, SimplifyJobs, Hacker News Who's
> Hiring, LinkedIn alert emails (optional) and an optional aggregator; your My Companies watchlist; two-track rules;
> ATS keywords; feedback learning; My Applications; launchd schedule with quick checks, notifications, digest, backups.

## Quick start

1. `./scripts/setup.sh` — venv in `~/.venvs/hirescope`, dependencies, editable install.
2. Copy and edit your inputs: `inputs/preferences.yaml`, `inputs/resume/master_resume.md` (and optionally
   `inputs/companies/my_companies.csv`, `inputs/linkedin/Connections.csv`, `.env` for LinkedIn alert emails).
3. `./scripts/run.sh --all` — first full run (~25 min; later runs fetch only what changed).
4. Open `dashboard/index.html`. Shortlist / skip with a reason / **Mark Applied**; **Export statuses** now and then.
5. `./scripts/schedule.sh install` — full runs 08:00 + 18:00 and quick checks every 2.5 h.

## Daily use

1. Runs happen on their own (08:00, 18:00, plus a quick check every 2.5 h) once the schedule is installed. Run one
   by hand any time: `./scripts/run.sh` (or `--quick`, `--company "Stripe"`, `--dry-run`).
2. Open `dashboard/index.html`. Start in **Last 24h** / the pinned **Apply Today** strip of each track tab;
   open a job (`Enter`), check its Keywords and Full JD, **Generate Resume** (copies the `/resume-builder` command
   for Claude Code; see [Resume Builder](#resume-builder)), apply, then **Mark
   Applied** (`a`). Shortlist (`s`) or skip with a reason (`x`) as you go — that is what feedback learning learns from.
3. Click **Export statuses** at the end of a session. The next run imports it from `~/Downloads` into SQLite (and
   backups), so nothing you did lives only in the browser. To import right away (or if a run reports it can't read
   ~/Downloads): `./scripts/import_pending.sh`.
4. Found a job somewhere else? **+ Add external job** on the dashboard, a JD file in `inputs/external_jobs/`, or
   `./scripts/add_job.sh` (see [External jobs](#external-jobs-found-outside-hirescope)).
5. Weekly: skim **Missed Matches** (false negatives) and **Coverage** (companies to check by hand); follow up on
   **Follow-ups Due**. A Sunday digest lands in `data.nosync/digests/`.

## How it works (data flow)

| Stage | Reads | Writes |
|---|---|---|
| Inputs | `inputs/preferences.yaml`, `inputs/resume/master_resume.md`, `inputs/companies/my_companies.csv`, `config/*.yaml`, `.env` | — |
| Import your side | newest `~/Downloads/jobsearch-statuses*.json` (dashboard export) | SQLite `jobs.user_*`, `applications`, `application_events`; `data.nosync/imports/` |
| Plan | tier schedules, My Companies, `--quick` / `--source` / `--company` | which sources run now |
| Fetch (parallel, polite) | employer ATS APIs, feeds, HN API, IMAP (optional) | `data.nosync/jd/*.txt`, HTTP validators (ETag) |
| Extract | JD text | title/level/track/terms/location/YOE/degree/flags/deadline/keywords per job |
| Dedupe + lifecycle | same source id → canonical ATS key → fuzzy company+title+location | new / updated / seen / closed (2 misses) |
| Rules + score | preferences, resume, learned adjustments | filtered (with reasons) or scored: qualification + preference + bonuses − penalties |
| Publish | SQLite | `dashboard/data.js` + `details/`, `dashboard/sync.js` (your statuses & applications) |
| After the run | — | notifications, `data.nosync/logs/last_run_summary.txt`, daily backup, weekly digest, optional .xlsx |

## Setup

```sh
./scripts/setup.sh                       # venv at ~/.venvs/hirescope, dependencies, editable install
cp inputs/preferences.example.yaml inputs/preferences.yaml      # then edit
cp inputs/resume/master_resume.example.md inputs/resume/master_resume.md      # then replace with YOUR resume
```

`inputs/` is gitignored except the `*.example.*` files. `master_resume.md` is the only source of facts about you.
Keep its `## Experience / ## Projects / ## Coursework / ## Education / ## Skills` headings, `### ` entry headings
and `- ` bullets.

> **Where it lives**: the repo is at `~/Projects/hirescope`, outside the macOS-protected (and iCloud-synced)
> `~/Documents`, so scheduled runs need no Full Disk Access. The venv stays outside the repo at `~/.venvs/hirescope`
> (set `JOBSEARCH_VENV` to move it): inside an iCloud-synced folder macOS flags new `.pth` files hidden and Python
> 3.12 skips them, which silently breaks the editable install. If you ever move the repo, re-run
> `./scripts/setup.sh` (re-points the editable install) and `./scripts/schedule.sh install` (rewrites the plists).

## Usage

```sh
./scripts/run.sh                         # full run, writes dashboard/data.js
source ~/.venvs/hirescope/bin/activate && python -m jobsearch.run --verbose     # same thing
python -m jobsearch.run --company "Figma" --verbose               # one company
python -m jobsearch.run --source greenhouse --dry-run             # only that adapter; fetch + score, write nothing
python -m jobsearch.run --no-email                                # skip LinkedIn alert emails (IMAP) this run
python -m jobsearch.run --quick                                   # quick check: My Companies + tier 1 (what launchd runs every 2.5 h)
python -m jobsearch.run --export-excel --digest                   # also write an .xlsx and the weekly digest now
python -m jobsearch.run --rescore                                 # no fetching: re-score after editing preferences / resume
python -m jobsearch.run --all                                     # ignore tier schedules this run
python -m jobsearch.run --resume path.md --prefs path.yaml        # alternate inputs
```

Then double-click `dashboard/index.html` (opens via `file://`, no server). The exit code is `0` when no
source failed (companies marked "Manual check needed" and partial listings are reported, not errors), `1` when any
source failed (the run still completes and the dashboard flags the missing coverage), `2` for missing or invalid
inputs, `3` when another run holds the lock, and `75` when offline. A JSON API that answers with a non-JSON body (an HTML
error or bot-check page — Workday does this now and then) is retried with backoff; if it still fails, the run (quick or
full) logs a warning and exits `0`, unless the same source also failed on its previous check (then `1`).

### Two tracks
Every job gets a `track`: **Co-op/Intern**, **Full-time**, or **Unclear**, decided by (in order) the title,
the employment type the source states, an internships-only feed, then JD wording. The evidence is stored
and shown. The dashboard's track switch (Co-op/Intern · Full-time · Both) is on every view; Unclear jobs
appear in both with a "?" badge. Internship terms ("Spring 2027", "Jan 2027 start") are extracted too.

Level rules (`config/levels.yaml`, `preferences.yaml → levels / yoe`): entry and mid both match;
senior is a **Stretch** when it asks ≤ 5 years, otherwise filtered; 7+ years is filtered; staff, principal,
distinguished, director, manager, architect, head of are excluded. Company level names map to one scale
(Amazon SDE I/II/III, Google L3–L5 and SWE III, Microsoft 59–64, Meta E3–E5, Apple ICT2–4). Locations are
normalized with `config/locations.yaml` (NYC → New York, NY; "US Remote" → Remote (US)); non-US jobs are
filtered and unknown locations are left uncertain.

### Sources

| Source | How | Notes |
|---|---|---|
| Greenhouse, Lever, Ashby | official public job-board APIs | full JD in one call; ETag/304 skips unchanged boards |
| Workday | the `cxs` JSON every `*.myworkdayjobs.com` page uses | US location facet + role keyword searches; JD fetched only for relevant jobs, cached 7 days |
| SmartRecruiters | public Posting API (`country=us`) | JD fetched only for relevant jobs; ETag/304 skips unchanged listings |
| Rippling | public board API behind `ats.rippling.com/<slug>/jobs` | whole board in one call; JD fetched only for relevant jobs |
| Jobvite | the employer's hosted `jobs.jobvite.com/<slug>` pages (its JSON API needs an employer key) | listing pages + `JobPosting` JSON-LD on job pages; robots.txt respected |
| Eightfold | the PCSX JSON behind `<host>/careers?domain=<domain>` (e.g. Microsoft) | ≤300 US jobs: listed in full; bigger sites: role keyword searches, capped at 200 results each; robots.txt checked |
| Workable | the public widget API Workable provides for embedding (`apply.workable.com/api/v1/widget/accounts/<slug>?details=true`) | whole board with descriptions in one call |
| iCIMS | the portal's `sitemap.xml` + schema.org `JobPosting` on each job page (`?in_iframe=1`); iCIMS has no public job API | robots.txt respected (some portals disallow everything: those stay manual); pages cached by `lastmod` |
| Careers-site JSON feeds | Jibe career sites' own `/api/jobs` feed (AMD, Rivian, Schneider Electric, Publicis Sapient, N-able) | US jobs, full descriptions; detected automatically from a `careers_url` |
| Amazon | `amazon.jobs/en/search.json` (best effort) | software-development category + intern/co-op/new-grad searches; ETag/304 skips unchanged listings |
| Netflix | careers sitemap + schema.org `JobPosting` JSON-LD | its JSON search API is not public; pages cached by `lastmod` |
| SimplifyJobs | `listings.json` from `SimplifyJobs/Summer2027-Internships` (incl. off-season) and `SimplifyJobs/New-Grad-Positions` | aggregator; JD pulled from the linked employer ATS for new relevant listings (capped per run). Repo names change yearly — update the URLs in `config/companies.yaml` |
| Custom careers pages | `JobPosting` JSON-LD on the page and its job links | robots.txt respected; never logs in |
| Hacker News Who's Hiring | official HN API (`hacker-news.firebaseio.com/v0`): newest "Ask HN: Who is hiring?" thread | one job per post (`Company \| Role \| Location \| …`); non-software posts skipped and remembered; only new posts fetched; last month's posts age out |
| LinkedIn job alerts | **your own alert emails**, read-only over IMAP | needs `.env` (below); never opens LinkedIn; listings have no JD until an official ATS lists the same role |
| Aggregator (optional, off) | JSearch (RapidAPI) or SerpAPI Google Jobs | `aggregator.enabled: true` + API key; sends only your search queries |
| Add job by URL | any of the above, or a pasted JD | for school co-op boards etc. LinkedIn, Apple and Meta pages are never fetched (terms) — paste the JD |

Google / Apple / Meta careers sites can't be read automatically (robots.txt or terms); see
`docs/experimental_big_tech.md`. Their roles arrive via SimplifyJobs, LinkedIn alerts and Add job by URL.

### LinkedIn job alerts (email)
1. On LinkedIn, create job alerts (e.g. "Software Engineer", "Software Engineer Intern", per company if you like).
2. Gmail: turn on 2-Step Verification, create an **app password** (myaccount.google.com/apppasswords).
3. Copy `.env.example` to `.env` and set `IMAP_USER` and `IMAP_APP_PASSWORD` (`IMAP_HOST`, `IMAP_FOLDER` optional).
The mailbox is opened read-only (nothing marked read, moved or deleted); only messages from LinkedIn's job-alert
senders in the last 21 days are parsed (`board` on the feed entry in `config/companies.yaml`). Without `.env` the
source is skipped with a note in Coverage, never a failure.

### Aggregator (optional)
Set `aggregator.enabled: true` (provider `jsearch` or `serpapi`, your `queries`) in `inputs/preferences.yaml` and the
matching key (`JSEARCH_API_KEY` / `SERPAPI_API_KEY`) in `.env`. Results are aggregator listings; jobs missing from a
later search stay 30 days (searches aren't complete listings). Keys are masked in logs.

### Adding a company
`config/companies.yaml` holds ~300 companies generated by `scripts/build_seed_companies.py` from SimplifyJobs
employer links plus a curated list, **each verified live** (candidates that failed are in
`config/companies_unverified.yaml` with the reason). Add one entry to add a company:
```yaml
- {name: Stripe, source: greenhouse, board: stripe, tier: 2}
- {name: NVIDIA, source: workday, host: nvidia.wd5.myworkdayjobs.com, board: nvidia, site: NVIDIAExternalCareerSite, tier: 1}
```
Tiers: **1** every run (deeper pagination), **2** once a day, **3** every 3 days; `preferences.yaml →
company_tiers` overrides. Last-checked times are tracked per company. `--all` ignores the schedule.

### My Companies watchlist
`inputs/companies/my_companies.csv` (gitignored; see `inputs/companies/my_companies.example.csv`):
```csv
company,careers_url,notes
Stripe,,
NVIDIA,https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite,
```
Every row is checked **every run** with full pagination. The job system is auto-detected in this order:
`careers_url` is an ATS URL → cached detection (re-run when `careers_url` changes) → seed list → SimplifyJobs-derived directory → ATS links on the
careers page → company-name probes (Greenhouse, Lever, Ashby, SmartRecruiters) → JSON-LD on the careers page →
**Manual check needed** (shown with a direct link, never dropped). A cached detection that fails is re-detected.
Companies on job systems HireScope can't read (iCIMS, SuccessFactors, Workable, own sites whose terms forbid it…)
are listed under `manual:` in `config/company_aliases.yaml` with the reason; their `careers_url` is the page to check.
Test one company: `python -m jobsearch.run --company "Stripe" --verbose`.

### Add job by URL
```sh
./scripts/add_url.sh "https://jobs.lever.co/acme/…"                     # ATS API or JSON-LD
./scripts/add_url.sh "https://school.example/coop/123" --company Acme --title "Software Co-op" \
    --jd-file jd.txt --track coop_intern                                 # manual: paste the JD yourself
```
The dashboard's "Add job by URL" box copies this command for you. Added jobs are scored and tracked like any
other; if an ATS adapter later finds the same posting, the records merge.

### External jobs (found outside HireScope)
A job from a school co-op board, a referral, a career fair or any site, added from its **JD text or file** (.txt,
.md, .pdf, .docx). It becomes a normal job with source `external`: scored like the rest (tracks, level, sponsorship /
citizenship / clearance flags, ATS keywords), given a job package (`data.nosync/job_packages/<Company>/`), always shown
on the dashboard with an **External** badge (also when your rules would hide it — the Overview tab then lists the
rules it fails), and listed under **External Jobs** in the sidebar. **Generate Resume** and `/resume-builder <job_id>`
work exactly as for any other job. Three ways in:

```sh
./scripts/add_job.sh --company "Acme" --role "Software Engineer Co-op" --file jd.pdf \
    [--url https://…] [--track coop|fulltime] [--location "Boston, MA"]          # now: imported, scored, packaged
pbpaste | ./scripts/add_job.sh --company "Acme" --role "Backend Engineer" --paste  # a copied JD
./scripts/add_job.sh --pending        # import everything waiting (drop folder + files saved by the dashboard)
./scripts/add_job.sh --url https://…  # URL only: exactly ./scripts/add_url.sh (Add job by URL)
```
- **Dashboard → "+ Add external job"** (sidebar, or ⌘K): company, role, location, job URL (optional), track, and the
  JD pasted or as a file. The page can't write to disk, so it saves `hirescope-external-job-<Company>-<time>.json` to
  your Downloads; the next run imports it, or run the command it shows (`./scripts/import_pending.sh`). Scheduled
  runs read ~/Downloads through the venv's Python, which macOS allows per program (tested under launchd on
  2026-10-14: Python could list, read and move files there while `/bin/ls` in the same job was denied). After a Python
  upgrade or `setup.sh` the permission can stop applying; the run summary then says "can't read ~/Downloads" — run
  `./scripts/import_pending.sh` yourself (see [Statuses, sync and feedback learning](#statuses-sync-and-feedback-learning)),
  or save the file to `inputs/external_jobs/`.
  With only a URL it copies the Add job by URL command instead.
- **Drop folder `inputs/external_jobs/`**: put any JD file there; the next run imports it. Company / role / URL /
  location / track come from a small header at the top of a text file (`Company: …`, `Role: …`, `URL: …`, blank line,
  then the JD) or from the file name (`Acme - Software Engineer Co-op.pdf`). Imported files move out of the folder
  (PDF / DOCX originals are kept in `data.nosync/external_jobs/<Company>/`); a file that can't be imported stays, with
  the reason in the log and the run summary. Each file is imported once.
- **Duplicates**: an external job that matches a job HireScope already has — the same posting URL (tracking
  parameters ignored) or ATS posting, the same JD text, or the same company + role with a similar JD — is **not**
  created; you get a warning naming the existing job and a link to it (the dashboard form checks company + role and
  URL before saving; the import checks the JD text too). `--force` (or "add it anyway" in the form) adds it as a
  separate job. Exit codes: `0` added, `3` duplicate, `2` error.
- PDFs need a text layer (scans can't be read: paste the text). Browser-printed PDFs draw list bullets as shapes;
  the reader turns those back into `- item` lines and joins wrapped lines, so requirements are found.

### Coverage
The header shows "X of Y companies/sources checked successfully this run". The **Coverage** tab lists every
company with status (OK / Failed / Manual check / Not due), source, how it was detected, last checked time,
and open roles. Partial listings (page caps) are labeled and never close jobs. Apply links of visible jobs
are checked (rate-limited, cached 24h); broken ones get a badge instead of being hidden.

## Dashboard

`dashboard/index.html` (double-click; `file://`, no server, no network calls).
- **Co-op / Internships** and **Full-time** tabs at the top are separate workspaces (own view, filters, sort,
  search, Apply Today, stats); `#coop` / `#fulltime` are bookmarkable. Unclear jobs show in both with a "?" badge
  and **Move to Co-op / Move to Full-time** (saved in the browser and included in Export statuses).
- Day tabs (recent days first-seen; older days in a dropdown) and a **Log** link in the header.
- Sidebar views: Last 24h (default when it has jobs), Apply Today, All Jobs, My Companies, **My Applications**,
  **Applied / Tracker**, Shortlisted, **Follow-ups Due**, Closing Soon, Skill Gaps, **Stats**, Hiring Seasons, Missed
  Matches (weekly false-negative review), Filtered Out (with reasons), Coverage, **Manual Checks**, **Run Log**.
- **Manual Checks**: every company HireScope can't read, with the reason and its careers link. Open the page, look
  for roles, press **Mark checked** (saves today's date; Undo available). A company not checked in
  `manual_checks.remind_days` (default 7) — or never — gets a "Check due" badge, the sidebar shows how many are due,
  a banner links to the view, and the weekly digest lists them. Dates go out with **Export statuses** (format 4) and
  are kept in SQLite (`manual_checks`).
- **Apply Today** is pinned above the main list (collapsible). Cards (score ring, label, New / Seen and other badges,
  ATS meter, "More like this", Move to Co-op / Full-time on Unclear jobs) or a sortable table, remembered per view;
  Comfortable / Compact density. Lists are virtualized (only rows near the viewport exist), so there is no row cap.
- Filter chips (company, tier, label, level, work mode, location, status, flags, posted within, min score, **salary
  min–max range**); sort by Newest posted, Best JD match, Overall, ATS coverage, Deadline, Company (secondary: newest
  posted). **Saved views** keep named filter + sort combinations per track. Filters, search, sort and an open job live
  in the URL hash (`#fulltime/all?label=Strong&sort=newest&job=123&tab=Keywords`), so a view is bookmarkable.
- Detail panel: Overview (incl. "You know N people here"), Requirements (✓ / ✗ / ? per line), Keywords, Score
  breakdown (incl. learned adjustments), Full JD (collapsible sections, keywords highlighted, Jump to requirements;
  one click on a keyword chip highlights it there with its evidence), Similar jobs, Notes & History; actions Generate
  Resume, Open Application, Shortlist, **Mark Applied**, Skip with reason, status, LinkedIn search, Levels.fyi /
  Glassdoor / engineering blog (`config/eng_blogs.yaml`, verified URLs; a search link otherwise). The panel keeps
  keyboard focus inside while open; `Esc` closes it and focus returns to the job's card.
- **Bulk select** (checkbox on hover, or `Space`): shortlist, skip with a reason, or export CSV for many jobs at once.
- **Command palette** `⌘K`: jump to any job, company, application, view or action. Jobs are searched across **both
  tracks and every view** — the lists, Filtered Out, jobs below the top N per track, filtered jobs the Filtered Out
  list doesn't show, and closed jobs (`dashboard/search.js`, written by each run, loaded the first time you open the
  palette). Each result shows its track and view; opening one switches to that tab and view (clearing filters that
  would hide it) and opens the job; a job the dashboard holds no details for (closed, not listed, beyond the top N)
  opens a short info card with its status and filter reason.
- Keys: `j`/`k`, `Enter`, `s` shortlist, `x` skip (reason picker: `1`–`9`, `0`), `a` mark applied, `o` open, `g` Generate Resume (copy command), `/` search, `1`/`2` tabs,
  `Space` select, `⌘K` palette, `[` / `]` move a board card, `?` help.

### My Applications (`#applications`)
- **Mark Applied** (or `a`, or status → applied) on any job — HireScope or External — moves it here with today's date
  (editable), company, role, track, source (**HireScope** or **External**), its application link, the exact tailored
  resume version (if one is ready: version, PDF and the PDF's hash), the job package you generated, and a **saved local
  copy of the JD** (kept after the posting disappears). Every job whose status is applied / interviewing / offer gets
  an application even if it was set some other way (each run adds the missing ones). From the terminal:
  `python -m jobsearch.apps mark-applied <job_id> [--date YYYY-MM-DD]`. **Add application** covers jobs you applied
  to without adding them to HireScope (company, role, track, link, date, source, optional pasted JD).
- **Applied Companies** tab: one row per company — number of applications, latest status (the stage of your most
  recent application there), latest applied date, sources, and every application with its link (click one for its
  drawer). CLI: `python -m jobsearch.apps companies`.
- **Board**: Applied → Online Assessment → Interviewing → Offer, plus Rejected · Ghosted · Withdrawn. Drag cards, or
  focus one and press `[` / `]`. Every move is logged on the card's timeline with its date. Cards show the track, days
  since applying, next step, follow-up state, referral, and "Posting closed".
- **Table**: sortable, searchable, stage filter, **Export CSV** (formula-safe).
- **Drawer**: stage bar, timeline, next step + date, contact, referral (suggests your connections), notes, link,
  source, package used, saved JD.
- **Helpers**: follow-up reminders (`applications.follow_up_days`, default 7–10 days after applying or your last
  follow-up; you send them yourself), "Ghosted?" suggestions after `ghosted_after_days` (30) without an update — never
  set automatically — and an upcoming-steps list.
- **Duplicate guard**: before marking applied (and in Add application) you're warned about the same posting, a
  near-duplicate title at the same company, or other applications there in the last `same_company_window_days`.
- **Stats** per track: applications per week, response / OA / interview rates, offers, median days to first
  response (inline SVG), plus response rates by source, company tier, score label and level.
- CLI (SQLite copy, as of the last import): `python -m jobsearch.apps list | followups | stats | companies | csv [FILE] | import FILE | mark-applied JOB_ID`.

### Statuses, sync and feedback learning
- Statuses, notes, track overrides and applications save in the browser instantly. **Export statuses** downloads
  `jobsearch-statuses.json` (format 3). Each run imports the newest `jobsearch-statuses*.json` in
  `statuses_import_dir` (default `~/Downloads`; `--statuses FILE` for a specific one, `--no-import` to skip) and merges
  it into SQLite: per item, **SQLite wins unless the export's entry is newer**; application timelines are unioned.
  Imported files are archived in `data.nosync/imports/`.
- **Import now: `./scripts/import_pending.sh`** — the fallback after a dashboard session. It imports the newest
  Export statuses file and every "Add external job" file (plus the `inputs/external_jobs/` drop folder) from the same
  folders a run reads, then rewrites the dashboard once (no fetching; a few seconds). Each file is imported once, so
  running it before a scheduled run is harmless. Use it when you don't want to wait for the next run, or when a run
  reports "can't read ~/Downloads": scheduled runs read it through the venv's Python, which macOS allows per program
  (tested under launchd), and a Python upgrade can lose that permission; from Terminal it always works. Exit codes:
  `0` done (also when nothing was waiting), `2` an import error, `3` a run is in progress (the run imports the files).
- Each run writes `dashboard/sync.js` from SQLite, and the page merges it on load (newer wins), so clearing browser
  data or opening the dashboard in another browser loses nothing that was exported.
- **Feedback learning** (`feedback` in preferences): shortlist / package / applied / interviewing / offer are
  positive; a skip teaches only what its reason is about (company → company, location → location, too senior/junior
  → level, missing skills → those skills, not interested in role → skills and domain). Compensation, visa,
  start-date, duplicate and "other" skips teach nothing; rejections are ignored. Each company / skill / domain /
  location / level needs `min_evidence` labeled jobs, moves the score by at most `max_per_feature` per group and
  `max_total` per job, and component weights move at most ±25%. Everything learned is listed under **Learned
  adjustments** and in each job's Score tab. Learning never un-hides a filtered job; it suggests reviewing the rule.
- **Referral finder**: put LinkedIn's official connections export at `inputs/linkedin/Connections.csv` (LinkedIn → Settings →
  Data privacy → Get a copy of your data → Connections). Companies are matched by brand (aliases, legal suffixes
  stripped); "Google Cloud" for Google is shown only as a possible match. Cards get a "Referral · N" badge. Never
  scrapes LinkedIn, never contacts anyone.
- **Reopened roles**: a closed job you had shortlisted that reappears gets a banner, a badge and a log warning.
- Data: `dashboard/data.js` holds compact summaries; per-job details load on demand from `dashboard/details/`.

## Two-track rules

| | Co-op / Intern | Full-time |
|---|---|---|
| Titles | intern/co-op titles with a software word (`coop_role_words`) | core + related titles (`title_rules.yaml`), plus meaning-based rescues |
| Start | stated start must fall in Dec 2026 – Mar 2027 (`tracks.coop_intern.start_window`), else a soft penalty; unknown = uncertain | stated start before May 2027 is a soft penalty |
| Filtered | summer-only internships (`filter_summer_only`) | — |
| Boosts | Spring 2027 / Jan 2027 terms; extra freshness urgency (×1.5) | New Grad / University Grad / Class of 2027 (badge + boost), 2027-start terms |
| Both | graduation windows ("graduating between…", "Class of 2026") must include your date (±2 months) or the job is filtered with the quote; "no more than 1 year of experience" lines are kept with a **Check eligibility** flag; levels: entry and mid match, senior ≤ 5 years is Stretch, 7+ years / staff+ / managers filtered |

**Freshness** is an additive bonus (`freshness:` in preferences): biggest in the first 24h, smaller to 72h, then
decaying; capped so a fresh Weak job can never outrank a clearly stronger one by more than the cap. Missing posting
dates use the discovery time only for jobs that appeared after the source's first scan, labeled "Discovered …
(posting date unknown)"; otherwise there is no bonus. **Ghost-job signals** (old postings, reposts, talent-pool
wording) give a penalty and a badge.

**Meaning-based pre-filter**: jobs hidden only by the title rule whose titles look technical are compared (local
embeddings) with a target-role profile and contrast profiles (hardware, mechanical, chip design, sales, …); close
matches are kept with a "Meaning match" badge. Tune `prefilter.threshold` / `margin`.

**ATS keywords**: extracted with the JD's exact wording (`config/ats_keywords.yaml` + `skills_synonyms.yaml`, both
directions), tagged required / preferred / mentioned, weighted by section and frequency, compared with your resume as
matched / synonym / missing with the resume line as evidence. Coverage measures keyword overlap, not your chance of
passing an ATS; missing keywords are gaps, never suggestions to add something you don't have.

**Optional sponsorship history** (`sponsorship_history.enabled`, off by default): put a USCIS H-1B Employer Data
Hub CSV you downloaded at `inputs/h1b/h1b_employer_data.csv`; matched companies show past approvals as company history,
never as job-level eligibility.

## Where the settings live

**Changing your preferences**: edit `inputs/preferences.yaml` (every key is explained in
`inputs/preferences.example.yaml`), then `python -m jobsearch.run --rescore` to re-score stored jobs without
fetching; the next scheduled run uses the new values too. Typical edits: `preferred_locations`, `domains`, `salary`,
`work_authorization`, `tracks.*` windows, `yoe` / `years_experience`, `company_tiers` (tier1 = checked every run),
`schedule.full_times`. Your resume is `inputs/resume/master_resume.md`; it is re-read every run.

Nothing about your search is hardcoded. `inputs/preferences.yaml` (documented line by line in
`inputs/preferences.example.yaml`) holds your profile and every tuning value: graduation date, track windows and
preferred terms, levels and YOE limits, locations and work modes, work authorization (incl. the co-op
"no sponsorship" policy), company tiers and how often each tier is checked, score weights, component scores and
penalties (`scoring`), label thresholds, freshness and boosts, ghost signals, Apply Today weights, feedback
learning weights, application reminders and duplicate guard, source-health thresholds, notifications, schedule,
backups. `config/` holds the shared vocabularies: `title_rules.yaml` (titles, co-op words, New Grad patterns,
meaning-filter role profiles, role keywords searched on Workday / SmartRecruiters / Amazon, HN role words),
`levels.yaml` (level ladders and the years-of-experience fallbacks), `locations.yaml`, `skills_synonyms.yaml`,
`ats_keywords.yaml`, `companies.yaml`, `company_aliases.yaml`. Code defaults are neutral (no dates, terms or
queries); after editing, `python -m jobsearch.run --rescore` re-applies everything without fetching.

## How scoring works

1. **Hard filters** (`preferences.yaml → rules`): title must match a target/related title (as a phrase, or fuzzy
   ≥ `fuzzy_threshold`); no excluded seniority words or level numerals (III+); location/work mode; excluded
   companies; hard salary floor (only when salary is stated). Each rule is `hard` (hidden and listed in the
   **Filtered** tab with its reason) or `soft` (a penalty). Missing data never fails a rule.
2. **Qualification fit**: skills (required skills count 1, preferred-only 0.5, synonyms from
   `config/skills_synonyms.yaml`), semantic similarity (local `all-MiniLM-L6-v2` embeddings of resume
   experience/projects/coursework vs JD requirement lines), and level (title match, years of experience,
   graduation year, degree).
3. **Preference fit**: location, company tier, employment type, salary, and your `domains` (a job whose title,
   company or JD mentions one gets the domain component; silence is uncertainty, never a mismatch). Freshness is a
   separate capped bonus. `years_experience` sets the default YOE window (+1 match / +2 Stretch / +3 filtered) and,
   inside the match window, roles asking more than your own years fit slightly less (`scoring.yoe_fit.near`).
4. **Overall** = weighted qualification + preference − penalties (YOE above your max, soft-rule misses, explicit
   no-sponsorship/citizenship/clearance when relevant to your `work_authorization`). Components with no data
   are left out of the average and listed as *uncertain*. Labels: Strong ≥ `thresholds.strong`, Stretch ≥
   `thresholds.stretch`, else Weak.

The score ranks postings against your resume and preferences. **It is not an interview probability.**

Semantic similarity scale: cosine 0.15 → 0 and 0.60 → 100, linear and clipped; the score is the mean of the
top 8 JD lines' best resume match. This is a heuristic.

Flags (sponsorship, citizenship, clearance) are set only when the JD states them explicitly, and the dashboard
quotes the sentence. Otherwise they read "Not stated".

**U.S. person / export control** (`work_authorization.us_person`; unset follows `us_citizen`): a job is filtered
(both tracks, the sentence quoted as the reason) only when the JD explicitly limits it to U.S. citizens, permanent
residents / green card holders, protected individuals, or a "U.S. Person" as defined — "This job requires a U.S.
Person", "US person as defined in 22 CFR 120.62" (or 120.15), "protected individual … 8 U.S.C. 1324b(a)(3)", "U.S.
Person status (citizen or LPR) is required", "must be a U.S. citizen or permanent resident", the ITAR boilerplate
"U.S. citizen, lawful permanent resident … or eligible to obtain the required authorizations". Export control
without that wording ("must be eligible to access export-controlled information", "requires access to
export-controlled technology", "subject to ITAR"), scoped or hedged lines ("may require…", "certain positions…",
"should the position require…", "except U.S. persons … may be subject to a licence review") and sanctioned-country
boilerplate set the `export_control` flag: the job stays, with a **Check eligibility** chip quoting the line. Not
requirements at all: EEO statements listing citizenship status, "or otherwise authorized to work", sponsorship
statements that mention green cards (left to the sponsorship flag), export control named as a skill. Sentences are
read whole ("U.S." doesn't end one).

## Job package format (v2, stable contract for the Resume Builder)

The **Job package** button in a job's panel downloads `Company_Role_JobID.md`, copies it to the clipboard, and sets
the job's status to "package exported". The Resume Builder reads the same package straight from SQLite. Format:

```markdown
---
package_format: 2
company: ...
title: ...
job_id: '...'
apply_url: ...
source: greenhouse
posted_date: 2026-10-01T10:00:00-04:00 | Unknown
fetched_at: 2026-10-12T08:00:00+00:00
resume_version_hash: be8f29e2481e982f
score: 78.2
label: Strong
---

## Full JD
## Minimum requirements
## Preferred requirements
## Skills required
## ATS keywords
## My matching evidence
## Gaps
## Flags
```

The front-matter keys and section names/order are fixed (`output/packages.py`: `FRONT_MATTER_KEYS`,
`SECTIONS`). JD lines that look like Markdown headings are escaped (`\## ...`) so they can't be mistaken for
sections. `parse_package()` is the reference parser. v2 added **ATS keywords**: a `Coverage: N% (required a/b)` line,
then one line per keyword: `JD exact term | required|preferred|mentioned | matched|synonym|missing | weight w
[| resume says X] [| evidence (section): resume line]`.

## Resume Builder

A Claude Code skill (`.claude/skills/resume-builder/`) plus deterministic scripts (`src/resume_builder/`) that turn a
HireScope job into a tailored, validated **one-page PDF in your own LaTeX template**, using only facts from your
inputs. Claude does the judgment (requirement mapping, selection, rewriting, review) as structured JSON; the scripts
do everything else (evidence bank, JD analysis, schema checks, truth verification, LaTeX rendering, Tectonic, PDF
checks, reports, versions, HireScope statuses). It never submits applications and never writes cover letters.

### Setup
1. `brew install tectonic poppler` (LaTeX engine; `pdftotext` / `pdftoppm` / `pdffonts`).
2. Font: the template asks for **Calibri**. If it isn't installed system-wide (Microsoft Word's own copy isn't
   visible to other apps), the build uses **Carlito**, which is metric-compatible (same line breaks):
   `brew install --cask font-carlito`. Every validation report and manifest says which font was used.
3. `~/.venvs/hirescope/bin/pip install -r requirements.txt` (adds `pdfplumber`).
4. Inputs (all gitignored): `inputs/resume/master_resume.md` (the only source of facts; its `# First Last` heading names the
   PDF), `inputs/resume/resume_template.tex` (your template, never modified: the build copies it and replaces its body with
   `\input{content.tex}`), `inputs/resume/resume_rules.md` (your writing rules, v4.3: 84 rules + a layout specification; the builder reads its
   numbers — bullet totals, per-role allocations, project counts — directly), `inputs/resume/tool_checklist.md` (tick-box
   tool families with where and level; with a v4 master, a ticked tool counts only as far as the master has evidence —
   otherwise it is self-reported — and the master's Excluded list always wins). `inputs/resume/evidence_additions.md` holds confirmed
   interview answers.
5. **Master resume (v4 format)**: a `## Default one-page resume` snapshot, `## Contact`, `## Education` (`### School —
   Location` with `**Degree:**` / `**Graduation:**` / `**GPA:**` lines and a coursework table — only "Yes" rows are
   used), `## Experience` (per role: `- **Title:** …` fields, a `#### Metrics` table with a Status column, `#### Bullet
   bank` — core, and for internships an extended bank used only when a JD needs it — with `**A. …**` groups and
   "In my own words" quotes, `#### Legacy bullets` never used), `## Projects` (context, repo, component status),
   `## Skills inventory` (default rows; core / extended / self-reported / planned tiers; Concepts; Excluded),
   `## Certifications`. Status comes from the master's own labels: **verified** / **estimated** are usable;
   **unverified**, **pending**, **planned**, Legacy and Excluded never are (a bullet stating an unverified metric's
   number is left out too). Italic lines starting with **"Builder:"** are instructions to the builder, never resume
   content. (The older `### Title — Org (dates)` format still works; its `[confirmed]`-style tags are no longer required.)
6. `~/.venvs/hirescope/bin/python -m resume_builder check` — tools, inputs, evidence bank, and notes: unconfirmed
   items, **metrics to check** (rule 3: the same % in two bullets is flagged, never changed), **checklist conflicts**
   (a tool your master resume names but the checklist leaves unticked: kept out of Skills until you resolve it),
   template bullets that aren't in your master resume.

### Use
- Dashboard → open a job → **Generate Resume (copy command)** (or `g`) copies `claude "/resume-builder <id>"`. Paste it
  in Terminal in the HireScope folder (copy-the-command workflow: the page runs nothing). The job shows "queued".
- Or in Claude Code: `/resume-builder <job_id> [--regenerate] [--profile backend|fullstack|cloud|ml]`; the job ID is
  the number in the dashboard URL (`#…&job=123`). `/resume-builder --batch shortlisted` for every shortlisted job
  (shows the count and a usage warning and waits for your OK).
- What happens: `prepare` (evidence bank, JD analysis from the job package, baseline of your own bullets ranked for
  the JD, brief) → Claude drafts `content.json` → `review` (rule findings from your rules + truth check) → a separate
  reviewer pass (hiring-manager view, once) → Claude's truth pass → `build`: truth verification, render, Tectonic,
  PDF checks, automatic trimming if over a page (max 3 attempts), reports, `v<N>`.
- Refresh the dashboard: status chip, **Resume** section (Preview PDF, Download PDF, Download .tex, Validation
  report, All reports, a page preview, checks, coverage) and **Mark reviewed**; the **Resumes** view lists them.
  A ready resume moves the job to "package exported" unless you're further along. **Mark Applied** records the exact
  resume version on the application (shown in its drawer).
- Statuses: queued → generating → validating → **ready** (every required check passed) → **reviewed** (only when you
  mark it: dashboard button + Export statuses, or `mark-reviewed`), or **failed** (reasons listed; the last good
  version stays current).

### What gets checked
- **Truth** (blocks "ready"): every bullet, skill, summary and talking point against the evidence ids it cites:
  numbers exactly as written (15+ may become 15, never 20), technologies (no AWS for GCP, no new tools), no ownership
  (led, managed, spearheaded…) or scale (large-scale, millions, enterprise…) or outcomes the evidence doesn't state.
- **PDF** (blocks "ready"): page count, blank pages, compiler errors, overfull lines, missing glyphs, fonts embedded,
  minimum font size, margins, clipping, overlapping lines, bullets over 2 lines, pdftotext vs the intended content
  (reading order, headings, bullet characters, contact details, missing or garbled text), every link on its own text
  and pointing where intended, ink in the margins of the rendered page images. Warnings: one-word last lines,
  stretched lines, line spacing, bottom whitespace, hyphenation.
- **Reports** (internal, never in the PDF; `v<N>/reports/`): `truth.md`, `coverage.md` ("internal coverage measure,
  not an ATS score"; missing resume keywords kept apart from missing qualifications), `changes.md` (selection, before →
  after per bullet, what was left out, remaining gaps), `defense.md` (2–3 interview talking points per bullet),
  `review.md`, `validation.md`, `extracted_text.txt`.
- **Rules v4.3 enforced by the build** (with a v4 master; content outside them is refused, naming the rule): start from
  the master's Default one-page snapshot — with no job description it is reproduced unchanged (`python -m
  resume_builder default`, checked line by line against your template compiled as written) — and swap bullets only as
  the master's Builder notes allow; the notes override the general rules for style and selection (bullets per role:
  Freelance 4–5, Accenture 3–4, Rescho 2; courses: 2; overlapping bullets), never a truthfulness rule (rule 2).
  Education → Technical Skills → Experience → Projects for every level, never reordered (24–25); no summary (28); 8–11
  experience bullets (32); 2 projects, 3 only for co-op roles that need it, 1 for mid-level (46–48); one metric of each
  kind — latency/p95, throughput, deployment/pipeline duration, query time (8); team work stays "Contributed to" (11);
  skill-only facts (Oracle anywhere, Node.js at Accenture) stay out of bullets (12); no compliance claims (13); no
  performance numbers at Rescho, intern-sized claims (14); certifications as the last skills row, AWS always, Azure
  Fundamentals only for cloud roles or an Azure JD (29); "Boston, MA (Open to Relocation)" for remote / out-of-state
  jobs, without the GitHub link, dropping "+1" only if the contact line still wraps (31); the freelance title only as
  the master records it (37); sector
  labels and at most 2 bullets per client (38–39, review); in-progress VibeSpace bullets start with design verbs, no
  results, no planned tech in Skills (50); no student phrasing (78); the master's overlap pairs (A3/C2, B3/Material UI)
  at most one each. Skills (53): core always; extended when the JD mentions them; self-reported only when the JD asks,
  at most 4, never in a bullet; planned never; the master's Excluded list beats the tool checklist; a ticked checklist
  tool with no evidence in the master counts as self-reported. Overflow: rule 67's order — duplicates and weak bullets,
  then JD-irrelevant skills (not named by the JD, not in a bullet, only where a row loses a line), then coursework, then
  bullets and projects — spacing last (`\entrygap` 3 → 2 pt, then leading 12.0 → 11.8 pt), never below 10 pt or 0.5 in.
  **v4.3 selection quality (79–84)**: relevance counts the JD's responsibilities as well as its technologies (79);
  the reviewer is told when a JD skill sits only in Skills while an unused bullet on the page shows it (80) and when a
  JD area (frontend, backend/database, Python/data pipelines, testing, delivery) has no bullet or 3+ bullets show only
  one area (81); TDD is never claimed or mapped from test-writing evidence, and coverage counts it only where the resume
  names it (82, refused); every Skills / certification item must cite its own master entry, and `changes.md` lists the
  skills by tier, self-reported first (83, refused); a bullet that refers back to a client ("the distributor") needs an
  earlier bullet introducing it, and overflow trimming never removes that introduction (84, refused).
- **Layout specification as checks** (rules 16–23, 69 and the spec table): T1 + lmodern, 0.5 in margins, 10.5/12 pt
  body, hyphenation off, 12.5 pt headings, math bullets at 1.2 em, hidelinks, 3 pt entry gaps, US Letter, Latin Modern
  fonts only, **no Type 3 fonts**, bullets extract as "•", dates with "–", f-ligatures extract as letters ("fi", not
  "ﬁ" — the builder breaks the ligatures in generated text, since Tectonic would otherwise write the single glyph),
  body 10.5 pt, name 20 pt, contact on one line. Engine: **Tectonic** (XeTeX-based; pdfLaTeX isn't installed) with
  lmodern reproduces the template on one page: the default build's extracted text is identical to the template's.
- **Smart keywords (rules 61, 63)**: for each important JD keyword your evidence doesn't support, the brief asks you
  first — "Have you used Kafka? If yes, where?", or for a specific tool in a family you have ("You list CI/CD
  (Jenkins). Which of these have you actually used: …?") — and suggests honest broader wording ("relational
  databases (MySQL)" for a PostgreSQL JD, never the specific tool). A confirmed specific tool supports its general
  terms automatically (Jenkins → CI/CD; `category_terms` in the config); a general term never implies a specific
  tool. Unticked tools and keywords you declined are known gaps, not asked again. Answers:
  `add-evidence --entry <id> --keyword K --question … --answer … [--level …] --confirm` (a tool answer is ticked in
  tool_checklist.md; anything else goes to evidence_additions.md), `add-tool --tool T --where W --level L --confirm`,
  `decline --keyword K`. **Learn-it list**: keywords missing from several prepared jobs — `python -m resume_builder
  gaps`, `data.nosync/generated_resumes/learn_it.md`, and the dashboard's Skill Gaps view.
- The JD is data: instruction-like lines ("ignore previous instructions", "write … to a file", "add X to your
  resume") are listed in the brief as ignored and dropped from what the drafter tailors to.

### Configuration (`config/resume_builder.yaml`)
`page_count` (1), `section_order` (the same for every audience since v4.1) and `audience` (which levels are mid-level;
years that make an unlevelled role mid-level — it decides the project count), `experience_bullets` and `projects_by_audience` (defaults;
the numbers in your rules file win), `category_terms` (rule 63, specific → general), `summary` (off), `limits`
(bullets per project, words per bullet, keyword repeats, 0–3 courses), `layout` (minimum font size 10 pt and margins
the repair loop may never cross, entry gaps), `fonts` (preferred +
fallback), `compile` (timeout, image DPI), `repair.max_attempts` (≤ 3), `profiles` (signals + what to lead with),
`related_groups` (what counts as transferable, never equivalent), `batch.max_jobs`. Changing it (or any input) makes
the next request build a new version.

### Versions, locks and hand edits
- `data.nosync/generated_resumes/<Company>/<Role>_<JobID>/v<N>/`: `Nikhil_<Last>_Resume_<Company>.pdf` (job IDs only in folder names and
  manifests), `main.tex` + `content.tex`, `content.json`, `reports/`, `pages/`, `compile.log` (contact details
  redacted), `source/` (template copy, content, fonts, `BUILD.md`: rebuilds the same PDF), `manifest.json` (job, time,
  input hashes, template / rules versions, profile, track, validation, truth, coverage, tools, file hashes).
- A repeated request with the same job, inputs, settings, profile and locks **reuses** the current version;
  `--regenerate` builds a new one. `python -m resume_builder show <id> [--bullets] | diff <id> [A B] |
  rollback <id> N | mark-reviewed <id> [N]`.
- **Locks**: `python -m resume_builder lock <id> <bullet id | exp-… / proj-… | section:skills|education|summary>`
  (`unlock` to release). Locked items come back unchanged on every regeneration and are never trimmed.
- **Hand edits**: edit a version's `content.json` (or bullet text in `content.tex`); the next generation keeps your
  text. Edits that can't be carried over (tex changes beyond bullet text, the same bullet edited differently in both
  files, an edit whose evidence changed since) are flagged and block "ready" instead of being overwritten;
  `prepare --discard-edits` drops them on purpose. The edited version itself is never touched.
- One run per job at a time (job lock; a second run says another is in progress).
- Each build saves a version, also within one generation (a rebuild after tightening a bullet is `v<N+1>`). A
  generation allows 3 builds; a 4th is refused without building anything (`prepare --regenerate` starts a new one).
- Trying things against a copy of your data: `JOBSEARCH_DATA_DIR=/path/copy JOBSEARCH_DASHBOARD_DIR=/path/dash
  python -m resume_builder …` (the real database, resumes and dashboard stay untouched); add
  `JOBSEARCH_RESUME_INPUTS_DIR=/path/inputs-copy` to use a copy of `inputs/resume/` too (e.g. a tagged test copy).

- **HireScope reads the same v4 master** for scoring: only usable bullets and facts per role / project, "Yes"
  courses and the core / extended / self-reported skills — never Builder notes, Legacy bullets, planned or Excluded
  skills. Title rules include your Data Engineer and Cloud / AWS Developer tracks; graduation is May 2027.
- **Relocation line (rule 31, v4.2)**: for remote / out-of-state jobs the contact line reads "Boston, MA (Open to
  Relocation)" and drops the GitHub link (the projects already link GitHub); "+1" is dropped only if it still wraps.
- **Planned technology** (the master's "Planned" line, including Redis, PyTorch, Prometheus and Grafana, which are also
  self-reported) never appears in Technical Skills until built; an in-progress project's design bullet may name it
  (Redis in VibeSpace).

### Evidence interview
`python -m resume_builder interview <id>` lists the keyword questions (above) and up to 5 more (requirements your
inputs may not describe, strong matches with no stated scope). Claude asks them; an answer is saved only when you
confirm it (`--confirm`), as one block in `inputs/resume/evidence_additions.md`:
```
## 2026-10-14 — exp-accenture
- Question: Have you used Kafka? If yes, where?
- Answer: …
- Status: confirmed
- Role/project: Associate Software Developer — Accenture Technology, India
- Level: production            (tools only)
```
Tool answers go to `tool_checklist.md` instead (ticked, with where and level), never both. A block may name its
`- Keyword:`; with `- Answer: no` it records that you haven't used it (`decline --keyword K` writes one): never listed,
claimed or asked again, and not counted by HireScope — it also overrides a self-reported entry in the master. A skill
answer with `--entry skills --level coursework` (e.g. Go, Rust) is listed in Technical Skills only when a JD asks for
it, as "Go (coursework)", and never inside a bullet (rule 75). Blocks whose status isn't
`confirmed`, or that carry a test marker (`DEMO`, "lorem ipsum", "placeholder"), are ignored and reported by
`check` / `tags`. The older `## <id> — date — confirmed` form is still read. Your master resume is never edited.

### Troubleshooting
| Symptom | What to do |
|---|---|
| `Missing dependencies: tectonic …` | `brew install tectonic poppler`; `pip install -r requirements.txt` |
| `The font "Calibri" cannot be found` | `brew install --cask font-carlito` (the build falls back to it automatically) |
| First build slow / offline error | Tectonic downloads TeX packages once; run it online once, later builds are offline |
| `master resume problems: …` | the message names the line (headings need `### Title — Organization, Location (Mon YYYY – Mon YYYY)`) |
| `master resume problems` / a parse error | v4 master: the message names the section; check the `### Role` fields, tables and the `## Default one-page resume` headings (`**Org, Location — Title** (dates)`, `**Project** (GitHub) — context`) |
| `content problems: … rule NN` | the content breaks that rule of resume_rules.md v4.3 (order, bullet / project counts, a tool or project in the wrong place); the message says how |
| `conflict: master_resume.md names X, but tool_checklist.md leaves it unticked` | tick X in the checklist (with where / level) or take it out of the master resume |
| `content does not match the schema` | the drafter's JSON is invalid; the message lists each field |
| FAILED: `truth: …` | a claim isn't backed by the evidence it cites: fix the bullet, or add the fact via the evidence interview |
| FAILED: `page_count` / `bullet_length` | content too long after 3 attempts: shorten bullets or lower priorities, then `--regenerate` |
| FAILED: `manual_edit_conflicts` | reconcile the edited version's content.json, then `--regenerate` (or `--discard-edits`) |
| "a generation … is in progress" | finish it with `build`, or start over with `prepare … --force` |
| "the resume is saved, but updating HireScope failed" | `python -m resume_builder refresh` |
| Dashboard shows no resume | refresh the page; `python -m resume_builder refresh` rewrites `dashboard/resumes.js` |
| Preview image or PDF link doesn't open | the dashboard links `../data.nosync/generated_resumes/…` relative to `dashboard/`: keep `data.nosync/` in the repo folder |

## Running on a schedule

```sh
./scripts/schedule.sh install                 # full runs at schedule.full_times (08:00, 18:00) + quick checks every 2.5 h
./scripts/schedule.sh change-time 07:30,19:00 # new full-run times (quick-check interval: --quick-hours 3, 0 = off)
./scripts/schedule.sh status                  # what's installed and loaded
./scripts/schedule.sh uninstall
./scripts/schedule.sh install --dry-run       # print the plists and launchctl commands only
```
Plists are rendered from `scripts/launchd/com.nikhil.jobsearch{,.quick}.plist.template` into `~/Library/LaunchAgents`.
- **Asleep** at a scheduled time: launchd starts the missed full run once when the Mac wakes. **Powered off**:
  that run is skipped. **Offline**: the run logs "offline" and exits (code 75); the next scheduled run retries.
  Each request also retries with backoff, and a failed source never stops the run.
- **Quick checks** (`--quick`): My Companies + tier-1 companies and feeds, conditional requests (ETag/304), no link
  check, backup or digest — new postings from the companies you care most about surface within ~2.5 hours.
- **One run at a time**: `data.nosync/state/run.lock` (pid + start). A quick check that meets a running full run just skips; a lock
  whose process died, or older than 3 hours, is replaced.
- **macOS permissions**: none needed for the schedule — the repo is in `~/Projects`, which isn't a protected folder.
  (Kept in `~/Documents`, launchd jobs fail with "can't open input file", exit 127, unless `/bin/zsh` has Full Disk
  Access.) Statuses are imported from `~/Downloads` and `inputs/statuses/` (`statuses_import_dir`, newest wins):
  `~/Downloads` is protected, but macOS lets the venv's Python read it on this Mac (verified from a launchd job). If
  the Run Log ever says a folder couldn't be read, save the export to `inputs/statuses/` instead.
- **Notifications** are sent with `osascript`, so macOS lists them under **Script Editor**: allow them in System
  Settings → Notifications → Script Editor (Alerts or Banners). `notifications.enabled: false` turns them off.

### After every run
- **Run summary**: printed, logged, and saved to `data.nosync/logs/last_run_summary.txt` (coverage, new / updated / closed,
  per-track counts, new Strong matches, health alerts, reopened roles, backup).
- **Notifications** (`notifications.enabled`, `--no-notify`): new Strong matches with **early birds** (posted within
  `early_bird_hours`, 48), reopened roles you had shortlisted, broken-looking sources, the weekly digest.
- **Source health**: a source that returns 0 jobs, or > 70% fewer than its recent average, is flagged (banner,
  Coverage, notification) — a broken source never looks like "no new jobs".
- **Daily backup** (`backups.dir`, `keep: 14`): `data.nosync/backups/YYYY-MM-DD/` with a gzipped SQLite snapshot (embeddings
  cache dropped), imported status exports and saved application JDs. `python -m jobsearch.ops.backup list | now |
  restore YYYY-MM-DD` (restore keeps the current DB as `jobs.db.before-restore-…`). `--no-backup` skips it.
- **Weekly digest**: on the first run of `notifications.digest_weekday` (Sunday), or `--digest`:
  `data.nosync/digests/YYYY-MM-DD.html` — new Strong matches, applications sent, follow-ups due, skill gaps, outcome stats,
  hiring seasons coming up.
- **Hiring seasons**: dashboard view of when each company first posted co-op/intern and new-grad roles each year (from
  your DB history), the predicted next window, and a reminder ~3 weeks before it when nothing is posted yet.
- **Excel**: `--export-excel [FILE]` writes `data.nosync/exports/hirescope-<date>.xlsx` (jobs per track, applications,
  coverage; links clickable, text never turned into formulas).

## Data & storage

| Path | Contents |
|---|---|
| `data.nosync/db/jobs.db` | SQLite: `jobs`, `job_sources` (per-source presence), `applications`, `application_events`, `runs`, `source_status`, `embeddings` cache, `resume_versions` |
| `data.nosync/jd/<Company>/` | full JD text per posting (`_unassigned/`: files no job refers to any more) |
| `data.nosync/job_packages/<Company>/` | job packages of external jobs (rewritten each run); other jobs' packages download from the dashboard |
| `data.nosync/logs/jobsearch.log` | JSON-lines log, rotated at 2 MB × 5 |
| `data.nosync/applications/` | saved JD copies for your applications |
| `inputs/external_jobs/` | drop folder: JD files of jobs found outside HireScope (imported by the next run) |
| `data.nosync/external_jobs/<Company>/` | original PDF / DOCX files of imported external jobs; `_imported/` = drop-folder files after import |
| `data.nosync/imports/` | archived dashboard exports that were imported (`external/`: imported "Add external job" files) |
| `data.nosync/backups/` | daily backups (last 14) |
| `data.nosync/digests/`, `data.nosync/exports/` | weekly digest pages, Excel exports |
| `data.nosync/state/run.lock`, `data.nosync/logs/last_run_summary.txt` | run lock, last run's summary |
| `dashboard/data.js` | generated `window.META` / `window.JOBS` |
| `dashboard/sync.js` | generated from SQLite: your statuses, notes, overrides and applications (gitignored) |
| `data.nosync/generated_resumes/<Company>/<Role>_<JobID>/` | Resume Builder: `v1`, `v2`, … (PDF, .tex, content JSON, manifest, page images, source bundle), `reports/vN/`, `latest` → current version, `current.json`, `locks.json`, `history.jsonl`, `_work/` |
| `data.nosync/screenshots/` | dashboard screenshots from QA runs |

Full map of what lives where: [STRUCTURE.md](STRUCTURE.md).
| `dashboard/resumes.js` | generated: resume statuses and versions for the dashboard (gitignored) |

Dedupe: exact on (company, source job id); fuzzy cross-source on company + normalized title + location.
A JD whose text hash changes is marked **updated**. A job is **closed** after 2 consecutive *successful* fetches
that don't list it; a failed fetch never closes anything. A closed job that reappears counts as a repost.

## What leaves your machine

Only requests to public job sources (job-board APIs, careers pages that allow it, SimplifyJobs on GitHub, Hacker
News) — none of them carry your resume, preferences or statuses; the only identifying header is the User-Agent
below. Optional, only when you set them up: your IMAP login to your own mail provider (LinkedIn alert emails) and
your search queries plus API key to the aggregator. Scoring, embeddings and the dashboard run locally (no AI service,
no analytics). The Resume Builder's scripts are local too (Tectonic fetches TeX packages only, nothing is uploaded to
any resume-scoring service); its drafting and review happen in **your Claude Code session**, so the brief Claude reads
(your master resume's evidence and the JD) goes to Anthropic like any other Claude Code conversation. `inputs/`, `data/`, `.env` and the generated dashboard data are gitignored. Every request type:

## Outbound requests

| Request | When | Data sent |
|---|---|---|
| `GET boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true` (conditional) | per Greenhouse company, on its tier schedule | none |
| `GET api.lever.co/v0/postings/<company>?mode=json` (conditional) | per Lever company | none |
| `GET api.ashbyhq.com/posting-api/job-board/<board>` (conditional) | per Ashby company | none |
| `POST <tenant>.wdN.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs` + `GET …/job/<path>` | per Workday company (detail only for relevant new jobs) | search keywords ("software", "intern"…) and the site's US facet id |
| `GET api.smartrecruiters.com/v1/companies/<id>/postings[/<id>]` | per SmartRecruiters company | none |
| `GET api.rippling.com/platform/api/ats/v1/board/<slug>/jobs[/<uuid>]` | per Rippling company | none |
| `GET jobs.jobvite.com/<slug>/search?nl=1&p=N`, `…/job/<id>?nl=1` and `/robots.txt` | per Jobvite company | none |
| `GET <host>/api/pcsx/search?domain=…&query=…&location=United States`, `…/position_details` | per Eightfold company | search keywords |
| `GET apply.workable.com/api/v1/widget/accounts/<slug>?details=true` | per Workable company | none |
| `GET <portal>.icims.com/robots.txt`, `/sitemap.xml`, `/jobs/<id>/<slug>/job?in_iframe=1` | per iCIMS company (job pages only for relevant new/changed jobs) | none |
| `GET <careers site>/api/jobs?page=N&limit=100&country=United States` | per Jibe careers site; once during detection (`limit=1`) | none |
| `GET www.amazon.jobs/en/search.json?...` | Amazon | search keywords |
| `GET explore.jobs.netflix.net/careers/sitemap…` + job pages | Netflix (pages only when new/changed) | none |
| `GET raw.githubusercontent.com/SimplifyJobs/…/listings.json` (conditional) | SimplifyJobs feeds | none |
| `GET <careers page>` and `/robots.txt` | My Companies custom pages, Add job by URL | none |
| board-name probes (Greenhouse/Lever/Ashby/SmartRecruiters) | My Companies detection, only when not cached | the company name as a slug |
| `GET <apply URL>` | dead-link check, visible jobs, at most once per 24h each | none |
| `GET hacker-news.firebaseio.com/v0/user/whoishiring.json`, `…/item/<id>.json` | every run (new posts only) | none |
| IMAP over TLS to `IMAP_HOST` (read-only `SEARCH` + `FETCH BODY.PEEK[]`) | every run, only when `.env` is set (`--no-email` skips) | your IMAP login (to your own mail provider) |
| `GET jsearch.p.rapidapi.com/search` or `serpapi.com/search.json` | only when the aggregator is enabled | your search queries, "us", the recency window, your API key |
| Hugging Face model download (`sentence-transformers/all-MiniLM-L6-v2`) | first run only; later runs load from the local cache | none |
| Tectonic TeX bundle (`relay.fullyjustified.net`, redirected to its package host) | Resume Builder, first build (and any new package); cached in `~/Library/Caches/TectonicProject.Tectonic` | none (requests TeX package files; no resume content) |

All requests use per-host rate limits, timeouts, retries with backoff, and the User-Agent
`jobsearch-local/0.2`. No request carries resume content; the only personal data sent anywhere is your IMAP login, to your own mail provider.

External jobs added from a JD (`add_job.sh --file/--paste/--json/--pending`, the drop folder, the dashboard form) make
no requests at all: the file is read locally (a URL you give is stored, never fetched). With only a URL, Add job by
URL's requests above apply.

The dashboard makes no network calls (enforced by its Content-Security-Policy). Links you click (apply,
LinkedIn search) open in your browser as normal.

## Adding a source adapter

1. Create `src/jobsearch/adapters/<name>.py` with a class deriving `Adapter` (`adapters/base.py`):
   `name`, `capabilities = Capabilities(full_description=…, posted_date=…, pagination=…, official=…, conditional=…,
   multi_company=…, query_based=…)`, and `fetch(company, ctx) -> FetchResult` returning `RawJob`s (plain-text JD in
   `description`; `None` means "unchanged, keep the stored JD"). Use `self.http` (`HttpClient`: rate limits, retries,
   `get_conditional` for ETag/304, `allowed()` for robots.txt on HTML pages). Raise `SourceError` on failure — the run
   isolates it. Optional: `fetch_one(url)` (Add job by URL), `check_open()` (verify capped listings),
   `unavailable()` (skip with a note when not configured), `source_label()` (per-feed source names).
2. Register it in `adapters/registry.py`; set `canonical_key` from the posting URL (`adapters/canonical.py`) so the same
   posting merges across sources.
3. Add companies with `source: <name>` to `config/companies.yaml` (or let My Companies detection find them).
4. Test with DEMO fixtures and `tests/conftest.py → mock_client` (no network), and add any request it makes to
   **Outbound requests** below.

## Tests

```sh
~/.venvs/hirescope/bin/python -m pytest -q
~/.venvs/hirescope/bin/python -m pytest -q --cov=jobsearch --cov=resume_builder --cov-report=term   # line coverage
```
All test data is mock data labeled DEMO (`tests/fixtures/` and inline). Tests need no network and no embedding
model. If `node` is installed, the dashboard's tracker logic (`dashboard/core.js`) is checked against the Python
tracker on the same DEMO applications, and its view logic (`dashboard/view.js`: virtualization, JD sections, salary
range, saved views) is tested directly. Design-token contrast is checked against WCAG AA in both themes.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `No module named 'jobsearch'` | use `scripts/run.sh`, or re-run `scripts/setup.sh` (editable install into `~/.venvs/hirescope`) |
| Dashboard is empty / "No jobs yet" | run `./scripts/run.sh`; check that `dashboard/data.js` exists; reload |
| Statuses or applications not in SQLite | click **Export statuses** (it lands in `~/Downloads`); the next run imports the newest `jobsearch-statuses*.json` — see Run Log → "Statuses import". Or `python -m jobsearch.run --statuses FILE` |
| Lost browser data | nothing exported is lost: each run writes `dashboard/sync.js`, which the page merges on load. Older state: `python -m jobsearch.ops.backup list` / `restore DAY` |
| A company shows as failed | Coverage tab + `data.nosync/logs/jobsearch.log`; for Greenhouse check `https://boards-api.greenhouse.io/v1/boards/<board>/jobs`; for My Companies add a `careers_url` |
| "Manual check needed" | no readable job system was found, or the company is listed under `manual:` in `config/company_aliases.yaml` (the reason says which platform): open the link, use Add job by URL with `--jd-file`. If the company moves to a supported board, put that URL in `careers_url` and remove the `manual:` entry |
| Source-health banner | a source returned 0 or far fewer jobs than usual: usually a changed board token or careers page; fix it in `config/companies.yaml` / `my_companies.csv` |
| SimplifyJobs feed fails each autumn | the repos are renamed yearly (e.g. `Summer2028-Internships`): update the URL in `config/companies.yaml` |
| LinkedIn alerts: "not set up" | add `IMAP_USER` and `IMAP_APP_PASSWORD` to `.env` (Gmail app password). "IMAP … authentication failed": regenerate the app password. 0 jobs parsed: alerts may be in another folder (`IMAP_FOLDER`) |
| No macOS notifications | System Settings → Notifications → Script Editor (osascript) → allow; or `notifications.enabled` is false / `--no-notify` |
| launchd runs don't happen | `./scripts/schedule.sh status`; read `data.nosync/logs/launchd-full.log`. "can't open input file" / "Operation not permitted" → the repo is in a protected folder (`~/Documents`, `~/Desktop`): keep it in `~/Projects`, or give `/bin/zsh` Full Disk Access. After moving the repo: `./scripts/setup.sh`, then `./scripts/schedule.sh install` |
| Statuses not imported by scheduled runs | Run Log → "Statuses import": "can't read ~/Downloads" means macOS blocked the folder; save the export to `inputs/statuses/`, or run `./scripts/run.sh` from Terminal |
| "another run is in progress" | a run holds `data.nosync/state/run.lock`; it's replaced automatically when that process is gone or > 3 h old |
| Exit code 75 | offline: nothing was fetched; the next scheduled run retries |
| A run seems stuck | `kill -USR1 <pid>` prints every thread's stack to the run's stderr / log |
| Scores look off after editing preferences / resume | `python -m jobsearch.run --rescore` (no fetching) |
| Repo inside iCloud Drive | runtime data is always in `data.nosync/` (iCloud never syncs `*.nosync`; a live SQLite DB must not sync), or set `JOBSEARCH_DATA_DIR` |
| Old layout (`data/`, flat `inputs/`) | `~/.venvs/hirescope/bin/python scripts/migrate_layout.py --dry-run`, then without `--dry-run` (back up first) |
