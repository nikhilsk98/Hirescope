# HireScope — local job search

A local, single-user job-search tool for macOS: discovery from employer job boards and feeds → dedupe → rules +
scoring for two tracks (Spring 2027 co-op/intern and full-time from May 2027) → a static HTML dashboard with
My Applications → per-job "job packages" for a future resume builder. The core pipeline makes **zero LLM calls**,
never logs in, never scrapes LinkedIn, never applies or messages anyone, and sends no resume data anywhere.

> Status: Phases 1–7 complete. 300 verified seed companies + Amazon, Netflix, SimplifyJobs, Hacker News Who's
> Hiring, LinkedIn alert emails (optional) and an optional aggregator; your My Companies watchlist; two-track rules;
> ATS keywords; feedback learning; My Applications; launchd schedule with quick checks, notifications, digest, backups.

## Quick start

1. `./scripts/setup.sh` — venv in `~/.venvs/hirescope`, dependencies, editable install.
2. Copy and edit your inputs: `inputs/preferences.yaml`, `inputs/master_resume.md` (and optionally
   `inputs/my_companies.csv`, `inputs/Connections.csv`, `.env` for LinkedIn alert emails).
3. `./scripts/run.sh --all` — first full run (~25 min; later runs fetch only what changed).
4. Open `dashboard/index.html`. Shortlist / skip with a reason / **Mark Applied**; **Export statuses** now and then.
5. `./scripts/schedule.sh install` — full runs 08:00 + 18:00 and quick checks every 2.5 h.

## How it works (data flow)

| Stage | Reads | Writes |
|---|---|---|
| Inputs | `inputs/preferences.yaml`, `inputs/master_resume.md`, `inputs/my_companies.csv`, `config/*.yaml`, `.env` | — |
| Import your side | newest `~/Downloads/jobsearch-statuses*.json` (dashboard export) | SQLite `jobs.user_*`, `applications`, `application_events`; `data/imports/` |
| Plan | tier schedules, My Companies, `--quick` / `--source` / `--company` | which sources run now |
| Fetch (parallel, polite) | employer ATS APIs, feeds, HN API, IMAP (optional) | `data/jd/*.txt`, HTTP validators (ETag) |
| Extract | JD text | title/level/track/terms/location/YOE/degree/flags/deadline/keywords per job |
| Dedupe + lifecycle | same source id → canonical ATS key → fuzzy company+title+location | new / updated / seen / closed (2 misses) |
| Rules + score | preferences, resume, learned adjustments | filtered (with reasons) or scored: qualification + preference + bonuses − penalties |
| Publish | SQLite | `dashboard/data.js` + `details/`, `dashboard/sync.js` (your statuses & applications) |
| After the run | — | notifications, `data/logs/last_run_summary.txt`, daily backup, weekly digest, optional .xlsx |

## Setup

```sh
./scripts/setup.sh                       # venv at ~/.venvs/hirescope, dependencies, editable install
cp inputs/preferences.example.yaml inputs/preferences.yaml      # then edit
cp inputs/master_resume.example.md inputs/master_resume.md      # then replace with YOUR resume
```

`inputs/` is gitignored except the `*.example.*` files. `master_resume.md` is the only source of facts about you.
Keep its `## Experience / ## Projects / ## Coursework / ## Education / ## Skills` headings, `### ` entry headings
and `- ` bullets.

> **Why is the venv outside the repo?** This repo lives in `~/Documents`, which iCloud Drive syncs. Inside a
> synced folder, macOS flags new `.pth` files as hidden and Python 3.12 skips hidden `.pth` files, so an editable
> install silently stops working (and every installed file gets uploaded). The venv defaults to
> `~/.venvs/hirescope`; set `JOBSEARCH_VENV` to put it elsewhere.

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

Then double-click `dashboard/index.html` (opens via `file://`, no server). The exit code is `0` when every
source succeeded, `1` when any failed (the run still completes and the dashboard flags the missing coverage),
`2` for missing or invalid inputs, `3` when another run holds the lock, and `75` when offline.

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
| SmartRecruiters | public Posting API (`country=us`) | JD fetched only for relevant jobs |
| Rippling | public board API behind `ats.rippling.com/<slug>/jobs` | whole board in one call; JD fetched only for relevant jobs |
| Jobvite | the employer's hosted `jobs.jobvite.com/<slug>` pages (its JSON API needs an employer key) | listing pages + `JobPosting` JSON-LD on job pages; robots.txt respected |
| Eightfold | the PCSX JSON behind `<host>/careers?domain=<domain>` (e.g. Microsoft) | ≤300 US jobs: listed in full; bigger sites: role keyword searches, capped at 200 results each |
| Amazon | `amazon.jobs/en/search.json` (best effort) | software-development category + intern/co-op/new-grad searches |
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
`inputs/my_companies.csv` (gitignored; see `inputs/my_companies.example.csv`):
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
- Sidebar views: Last 24h (default when it has jobs), Apply Today, All Jobs, My Companies, **My Applications**,
  Shortlisted, **Follow-ups Due**, Closing Soon, Skill Gaps, **Stats**, Missed Matches (weekly false-negative review),
  Filtered Out (with reasons), Coverage, **Run Log**.
- Cards (score ring, label, badges, ATS meter) or a sortable table, remembered per view; Comfortable / Compact density.
- Filter chips (company, tier, label, level, work mode, location, status, flags, posted within, min score, **salary
  slider**); sort by Newest posted, Best JD match, Overall, ATS coverage, Deadline, Company (secondary: newest posted).
  Filters, search and sort live in the URL hash (`#fulltime/all?label=Strong&sort=newest`), so a view is bookmarkable.
- Detail panel: Overview (incl. "You know N people here"), Requirements (✓ / ✗ / ? per line), Keywords, Score
  breakdown (incl. learned adjustments), Full JD (keywords highlighted, Jump to requirements), Similar jobs, Notes &
  History; actions Generate Resume, Open Application, Shortlist, **Mark Applied**, Skip with reason, status, LinkedIn
  search, Levels.fyi / Glassdoor / eng-blog links.
- **Bulk select** (checkbox on hover, or `Space`): shortlist, skip with a reason, or export CSV for many jobs at once.
- **Command palette** `⌘K`: jump to any job, company, application, view or action.
- Keys: `j`/`k`, `Enter`, `s` shortlist, `x` skip, `a` mark applied, `o` open, `g` package, `/` search, `1`/`2` tabs,
  `Space` select, `⌘K` palette, `[` / `]` move a board card, `?` help.

### My Applications (`#applications`)
- **Mark Applied** (or `a`) moves a job here with today's date (editable), its apply link, the job package you
  generated, and a **saved local copy of the JD** (kept after the posting disappears). **Add application** covers jobs
  you applied to elsewhere (company, role, track, link, date, source, optional pasted JD).
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
- CLI (SQLite copy, as of the last import): `python -m jobsearch.apps list | followups | stats | csv [FILE] | import FILE`.

### Statuses, sync and feedback learning
- Statuses, notes, track overrides and applications save in the browser instantly. **Export statuses** downloads
  `jobsearch-statuses.json` (format 3). Each run imports the newest `jobsearch-statuses*.json` in
  `statuses_import_dir` (default `~/Downloads`; `--statuses FILE` for a specific one, `--no-import` to skip) and merges
  it into SQLite: per item, **SQLite wins unless the export's entry is newer**; application timelines are unioned.
  Imported files are archived in `data/imports/`.
- Each run writes `dashboard/sync.js` from SQLite, and the page merges it on load (newer wins), so clearing browser
  data or opening the dashboard in another browser loses nothing that was exported.
- **Feedback learning** (`feedback` in preferences): shortlist / package / applied / interviewing / offer are
  positive; a skip teaches only what its reason is about (company → company, location → location, too senior/junior
  → level, missing skills → those skills, not interested in role → skills and domain). Compensation, visa,
  start-date, duplicate and "other" skips teach nothing; rejections are ignored. Each company / skill / domain /
  location / level needs `min_evidence` labeled jobs, moves the score by at most `max_per_feature` per group and
  `max_total` per job, and component weights move at most ±25%. Everything learned is listed under **Learned
  adjustments** and in each job's Score tab. Learning never un-hides a filtered job; it suggests reviewing the rule.
- **Referral finder**: put LinkedIn's official connections export at `inputs/Connections.csv` (LinkedIn → Settings →
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
Hub CSV you downloaded at `inputs/h1b_employer_data.csv`; matched companies show past approvals as company history,
never as job-level eligibility.

## Where the settings live

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
3. **Preference fit**: location, company tier, freshness, employment type, salary.
4. **Overall** = weighted qualification + preference − penalties (YOE above your max, soft-rule misses, explicit
   no-sponsorship/citizenship/clearance when relevant to your `work_authorization`). Components with no data
   are left out of the average and listed as *uncertain*. Labels: Strong ≥ `thresholds.strong`, Stretch ≥
   `thresholds.stretch`, else Weak.

The score ranks postings against your resume and preferences. **It is not an interview probability.**

Semantic similarity scale: cosine 0.15 → 0 and 0.60 → 100, linear and clipped; the score is the mean of the
top 8 JD lines' best resume match. This is a heuristic.

Flags (sponsorship, citizenship, clearance) are set only when the JD states them explicitly, and the dashboard
quotes the sentence. Otherwise they read "Not stated".

## Job package format (v2, stable contract for the future resume builder)

The **Generate Resume** button downloads `Company_Role_JobID.md`, copies it to the clipboard, and sets the job's
status to "package exported". Format:

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
- **One run at a time**: `data/run.lock` (pid + start). A quick check that meets a running full run just skips; a lock
  whose process died, or older than 3 hours, is replaced.
- **macOS permissions**: the repo lives in `~/Documents` and statuses are imported from `~/Downloads`, both protected
  folders. If `data/logs/launchd-full.log` shows "Operation not permitted", give `/bin/zsh` Full Disk Access
  (System Settings → Privacy & Security → Full Disk Access), or move the repo out of Documents.

### After every run
- **Run summary**: printed, logged, and saved to `data/logs/last_run_summary.txt` (coverage, new / updated / closed,
  per-track counts, new Strong matches, health alerts, reopened roles, backup).
- **Notifications** (`notifications.enabled`, `--no-notify`): new Strong matches with **early birds** (posted within
  `early_bird_hours`, 48), reopened roles you had shortlisted, broken-looking sources, the weekly digest.
- **Source health**: a source that returns 0 jobs, or > 70% fewer than its recent average, is flagged (banner,
  Coverage, notification) — a broken source never looks like "no new jobs".
- **Daily backup** (`backups.dir`, `keep: 14`): `data/backups/YYYY-MM-DD/` with a gzipped SQLite snapshot (embeddings
  cache dropped), imported status exports and saved application JDs. `python -m jobsearch.ops.backup list | now |
  restore YYYY-MM-DD` (restore keeps the current DB as `jobs.db.before-restore-…`). `--no-backup` skips it.
- **Weekly digest**: on the first run of `notifications.digest_weekday` (Sunday), or `--digest`:
  `data/digests/YYYY-MM-DD.html` — new Strong matches, applications sent, follow-ups due, skill gaps, outcome stats,
  hiring seasons coming up.
- **Hiring seasons**: dashboard view of when each company first posted co-op/intern and new-grad roles each year (from
  your DB history), the predicted next window, and a reminder ~3 weeks before it when nothing is posted yet.
- **Excel**: `--export-excel [FILE]` writes `data/exports/hirescope-<date>.xlsx` (jobs per track, applications,
  coverage; links clickable, text never turned into formulas).

## Data & storage

| Path | Contents |
|---|---|
| `data/jobs.db` | SQLite: `jobs`, `job_sources` (per-source presence), `applications`, `application_events`, `runs`, `source_status`, `embeddings` cache, `resume_versions` |
| `data/jd/` | full JD text per posting |
| `data/logs/jobsearch.log` | JSON-lines log, rotated at 2 MB × 5 |
| `data/applications/` | saved JD copies for your applications |
| `data/imports/` | archived dashboard exports that were imported |
| `data/backups/` | daily backups (last 14) |
| `data/digests/`, `data/exports/` | weekly digest pages, Excel exports |
| `data/run.lock`, `data/logs/last_run_summary.txt` | run lock, last run's summary |
| `dashboard/data.js` | generated `window.META` / `window.JOBS` |
| `dashboard/sync.js` | generated from SQLite: your statuses, notes, overrides and applications (gitignored) |

Dedupe: exact on (company, source job id); fuzzy cross-source on company + normalized title + location.
A JD whose text hash changes is marked **updated**. A job is **closed** after 2 consecutive *successful* fetches
that don't list it; a failed fetch never closes anything. A closed job that reappears counts as a repost.

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

All requests use per-host rate limits, timeouts, retries with backoff, and the User-Agent
`jobsearch-local/0.2`. No request carries resume content; the only personal data sent anywhere is your IMAP login, to your own mail provider.

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
```
All test data is mock data labeled DEMO (`tests/fixtures/` and inline). Tests need no network and no embedding
model. If `node` is installed, the dashboard's tracker logic (`dashboard/core.js`) is checked against the Python
tracker on the same DEMO applications.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `No module named 'jobsearch'` | use `scripts/run.sh`, or re-run `scripts/setup.sh` (editable install into `~/.venvs/hirescope`) |
| Dashboard is empty / "No jobs yet" | run `./scripts/run.sh`; check that `dashboard/data.js` exists; reload |
| Statuses or applications not in SQLite | click **Export statuses** (it lands in `~/Downloads`); the next run imports the newest `jobsearch-statuses*.json` — see Run Log → "Statuses import". Or `python -m jobsearch.run --statuses FILE` |
| Lost browser data | nothing exported is lost: each run writes `dashboard/sync.js`, which the page merges on load. Older state: `python -m jobsearch.ops.backup list` / `restore DAY` |
| A company shows as failed | Coverage tab + `data/logs/jobsearch.log`; for Greenhouse check `https://boards-api.greenhouse.io/v1/boards/<board>/jobs`; for My Companies add a `careers_url` |
| "Manual check needed" | no readable job system was found, or the company is listed under `manual:` in `config/company_aliases.yaml` (the reason says which platform): open the link, use Add job by URL with `--jd-file`. If the company moves to a supported board, put that URL in `careers_url` and remove the `manual:` entry |
| Source-health banner | a source returned 0 or far fewer jobs than usual: usually a changed board token or careers page; fix it in `config/companies.yaml` / `my_companies.csv` |
| SimplifyJobs feed fails each autumn | the repos are renamed yearly (e.g. `Summer2028-Internships`): update the URL in `config/companies.yaml` |
| LinkedIn alerts: "not set up" | add `IMAP_USER` and `IMAP_APP_PASSWORD` to `.env` (Gmail app password). "IMAP … authentication failed": regenerate the app password. 0 jobs parsed: alerts may be in another folder (`IMAP_FOLDER`) |
| No macOS notifications | System Settings → Notifications → Script Editor (osascript) → allow; or `notifications.enabled` is false / `--no-notify` |
| launchd runs don't happen | `./scripts/schedule.sh status`; read `data/logs/launchd-full.log`. "Operation not permitted" → Full Disk Access for `/bin/zsh` (the repo is in ~/Documents) |
| "another run is in progress" | a run holds `data/run.lock`; it's replaced automatically when that process is gone or > 3 h old |
| Exit code 75 | offline: nothing was fetched; the next scheduled run retries |
| A run seems stuck | `kill -USR1 <pid>` prints every thread's stack to the run's stderr / log |
| Scores look off after editing preferences / resume | `python -m jobsearch.run --rescore` (no fetching) |
| Repo inside iCloud Drive | `setup.sh` makes `data/` a symlink to `data.nosync/` (never synced; a live SQLite DB must not sync), or set `JOBSEARCH_DATA_DIR` |
