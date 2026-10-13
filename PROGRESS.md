# HireScope build progress

Resume here: read this file and `git log --oneline` first, then continue from **In progress**.

## Current phase
All phases (1–7) complete; the 2026-10-13 gap-closing pass (status-audit items) is done — see the last report.

## Completed
- Phase 1 — vertical slice (Greenhouse → SQLite → scoring → dashboard → job package)
- Phase 2 — sources (Lever, Ashby, Workday, SmartRecruiters, Amazon, Netflix, SimplifyJobs), 300-company verified
  seed list, tiers, levels/locations, My Companies watchlist + ATS detection, add-by-URL, coverage, dead links
- Phase 3 — two-track rules, ATS keywords, freshness, meaning pre-filter, skill gaps, Apply Today, dashboard rebuild
- Phase 4 — statuses import/merge, feedback learning, My Applications, follow-ups, outcome stats, referral finder,
  duplicate guard, reopened alerts, dashboard polish (report below)
- Phase 5 — Hacker News Who's Hiring, LinkedIn alert emails (IMAP), optional aggregator, big-tech investigation
- Phase 6 — launchd schedule + quick checks, lock, offline handling, notifications, early birds, run summary,
  Excel export, source health alerts, hiring seasons, weekly digest, daily backups + restore
- Phase 7 — README: quick start, data-flow table, adding adapters, troubleshooting table

## Phase 4 report (2026-10-12)
**Built**
- `tracker/statuses.py`: imports the newest `jobsearch-statuses*.json` from `~/Downloads` each run (`--statuses FILE`,
  `--no-import`); per item SQLite wins unless the export is newer; entries capped at the export's own time; timelines
  unioned; idempotent; archived to `data/imports/`. SQLite → `dashboard/sync.js` (statuses, notes, overrides,
  applications, job state), merged by the page on load (newer wins).
- Migration 6: `applications`, `application_events`, `jobs.track_override_at`, `jobs.user_notes`. Saved JD copies in
  `data/applications/` (from the export, else copied from the stored JD).
- `tracker/feedback.py`: bounded, reason-specific learning (company / skills / domain / location / level; ±3 per
  group, ±6 per job, weights ±25%, min evidence 2); shown in the sidebar and each job's Score tab; suggestions for
  filtered jobs you shortlist (never un-hides).
- `tracker/applications.py` + `dashboard/core.js` (same rules, parity-tested via Node): follow-ups 7–10 days, ghosted
  suggestions at 30 days, duplicate guard (same posting / near-duplicate title / same company in 30 days), per-track
  stats, outcome breakdowns, formula-safe CSV. `python -m jobsearch.apps list|followups|stats|csv|import`.
- `tracker/referrals.py`: LinkedIn Connections.csv → "You know N people here" (brand match; prefix = possible only).
- Reopened shortlisted roles: banner + badge + log warning.
- Dashboard: My Applications (board with drag + `[`/`]`, table + CSV, stats with inline SVG, drawer with stage bar,
  timeline, fields, saved JD; Add application with live duplicate warnings), Follow-ups Due, Stats, Run Log, Mark
  Applied (`a`), Notes & History tab, Cmd+K palette, bulk select (shortlist / skip / CSV), salary slider, URL-hash
  filters, per-view cards/table, updated onboarding tip; two light tokens raised to WCAG AA (all pairs ≥ 4.5:1).
- Fixes found during QA: Workday `_R123-1` reposts now share a canonical key and stored duplicates are merged
  (295 merged, checked: same requisitions); KLA was listed twice in the seed list (`KLA` + `KLA Corporation`) → one
  entry on the "Search" site; company name no longer counted as a "skill" in learning; sync.js no longer lists
  every closed job (1.8 MB → 0.2 KB).

**Tests**: 284 passed (24 new: import/merge, newer-wins, idempotence, bad input, file pick, JD copy, sync payload,
reopen, feedback bounds/reasons/weights/suggestions/score effect, referrals, follow-up/ghosted, duplicate guard,
stats, CSV, core.js parity + mark-applied/stage/timeline/merge, canonical repost suffix, duplicate merge).

**Real run** (`--all`, 2026-10-12): 424 sources, 356 OK, 21 failed + 47 manual (same custom careers pages as
before), 43 partial; 96,393 listings, 893 new, 461 updated, 464 closed; 6,343 visible (5,454 full-time, 884
co-op, 5 unclear), 1,197 Strong. No statuses export in ~/Downloads yet (expected).

**Checks**: 0 visible non-US / 7+ YOE / staff+ / senior-as-Strong / summer-only co-op / grad mismatch; 0 visible
full-time no-sponsorship, citizenship or clearance; 46 co-ops flagged "check CPT/OPT"; 3,145 visible in preferred
metros; 693 New Grad roles. 15 random jobs spot-checked (track, level, links, sponsorship "Not stated" unless stated,
no duplicate keys; the remaining same-title pairs are distinct requisitions, e.g. Capital One / Amazon / Booz Allen).
5 applications built with core.js → export → merge into a copy of the real DB → sync: stages, timelines, links,
follow-ups, JD copies, duplicate guard and re-import all correct. Screenshots reviewed (light + dark, both tabs, board,
table, stats, drawer, follow-ups, palette, add dialog, bulk bar, run log, 760 px).

## Phase 5 report (2026-10-12)
**Built**
- `adapters/hn_hiring.py`: official HN API; newest "Ask HN: Who is hiring?" thread; one job per post (header
  `Company | Role | Location | …`), non-software posts skipped and cached per thread, only new posts fetched, posts
  stored as seen by id; apply link = careers/ATS link (canonical key → merges with official postings).
- `adapters/linkedin_alerts.py`: your own LinkedIn job-alert emails via read-only IMAP (`BODY.PEEK`, `readonly`),
  plain-text and HTML layouts, sender allow-list, earliest alert date wins, never opens LinkedIn.
- `adapters/aggregator.py`: optional JSearch / SerpAPI (off by default); query-based (misses kept 30 days); keys in
  `.env`, masked in logs (`safe_url`).
- `secrets.py` (.env loader, redaction); `Adapter.unavailable()` → unconfigured sources are **skipped with a note**
  (Coverage: "Not set up / off"), never failures; `--no-email`; `--source X` now runs only that adapter.
- `docs/experimental_big_tech.md`: Google (robots disallows job pages), Apple (site terms forbid robots), Meta (notice
  forbids automated collection) → no adapters; add_url never fetches LinkedIn / Apple / Meta pages (paste the JD).
- README: sources table, LinkedIn alert setup, aggregator, outbound requests; `.env.example`.

**Tests**: 297 passed (13 new: alert parsing text/HTML, sender check, dedupe, credentials/redaction, skipped-not-failed,
HN parsing, only-new fetching + skip cache, HN end-to-end incl. age-out, aggregator gating, JSearch + SerpAPI parsing,
key masking, real seed list loads, add_url no-fetch hosts).

**Real runs**: `--source hn_hiring` (live): October 2026 thread, 304 posts → 139 software posts stored, 52 visible
after your rules (2 Strong). Full `--all`: 424 sources, 356 OK, 22 failed + 46 manual (same custom careers pages +
OM1's Greenhouse board now 404), 42 partial; HN re-checked with 0 new fetches; LinkedIn alerts "not set up";
6,393 visible (5,501 full-time / 887 co-op / 5 unclear), 1,207 Strong. Profile checks all OK (no non-US, 7+ YOE,
staff+, senior-Strong, summer-only co-op, grad mismatch; no visible full-time no-sponsorship / citizenship / clearance).
Screenshots: HN job detail (light), Coverage with the skipped source (dark).

## Phase 6 report (2026-10-12)
**Built** (`src/jobsearch/ops/`)
- `schedule.py` + `scripts/schedule.sh install | uninstall | status | change-time` + templates
  `scripts/launchd/com.nikhil.jobsearch{,.quick}.plist.template`: full runs 08:00 / 18:00 (`schedule.full_times`),
  quick checks every 2.5 h (`schedule.quick_hours`); rendered plists validated with plistlib.
- `--quick`: My Companies + tier-1 sources only, no link check / backup / digest.
- `lock.py`: `data/run.lock` (pid + start), stale when the process is gone or > 3 h; quick checks yield (exit 0),
  a second full run exits 3. Offline → exit 75, the next scheduled run retries.
- `notify.py` (osascript, escaped), `alerts.py`: new Strong matches with early birds (posted < 48 h), reopened
  shortlisted roles, source-health alerts; run summary printed + `data/logs/last_run_summary.txt`.
- `health.py`: 0 jobs (usual ≥ 3) or > 70% drop (usual ≥ 10) vs the last 5 successful runs → banner, log, notification.
- `excel.py` (`--export-excel [FILE]`): per-track jobs, applications, coverage; hyperlinks; formula-safe text.
- `backup.py`: daily gzipped SQLite snapshot (embeddings cache dropped; 553 MB → 42 MB) + imports + application JDs,
  keep 14, `python -m jobsearch.ops.backup list | now | restore DAY` (keeps the current DB first).
- `digest.py`: weekly HTML digest on the first Sunday run (or `--digest`).
- `seasons.py` + dashboard **Hiring Seasons** view (inline SVG month strips, reminders ~3 weeks ahead).
- Fixes found in QA: "Toronto, CA" was read as California (now Canada); "LMTS/PMTS/SMTS/AMTS" level ladder
  (an LMTS role was visible as Strong); "2027 Summer Intern" term order (45 summer-only internships now filtered).

**Tests**: 313 passed (16 new: lock incl. stale pid/age/garbage, notification escaping + switch, alert sending +
summary text, health thresholds, quick plan, Excel, backup/restore/prune/once-a-day, digest escaping + weekday, seasons,
schedule render/install/uninstall with a fake launchctl, offline + locked exit codes, online probe, plus 3 extraction
regressions).

**Real runs**: `--all --export-excel --digest`: 357 of 424 OK (21 failed + 46 manual custom pages, 42 partial), backup
42 MB, digest + xlsx written, 5 new Strong matches (5 early birds), notification sent, lock released. `--quick`:
223 sources (My Companies + tier 1), 156 OK, 47 new, ~15 min. After the fixes: 6,352 visible (5,508 full-time / 839
co-op / 5 unclear), 1,206 Strong; all profile checks OK; 0 visible LMTS/PMTS; 15 random jobs spot-checked.
Screenshots: digest (dark), Hiring Seasons (light). launchd itself was **not installed** (Needs Nikhil).

## Final report (2026-10-12)
**What's built**: discovery from 300 verified seed companies (Greenhouse, Lever, Ashby, Workday, SmartRecruiters),
Amazon, Netflix, SimplifyJobs (internships incl. off-season + new grad), Hacker News Who's Hiring, LinkedIn alert
emails (read-only IMAP, optional), an optional aggregator, your 144-company My Companies watchlist with ATS
auto-detection, and Add job by URL → dedupe (source id, canonical ATS key, fuzzy) → two-track rules (Spring 2027
co-op/intern, full-time from May 2027, entry–mid 0–5 YOE, USA, sponsorship/citizenship rules from explicit text only)
→ qualification + preference scoring with freshness, New Grad and preferred-metro boosts, ghost signals and
bounded feedback learning → dashboard (two track workspaces, ATS keywords, Apply Today, skill gaps, missed matches,
coverage, My Applications board/table/stats, follow-ups, hiring seasons, run log, ⌘K, bulk actions) → job packages.
Unattended: launchd full runs + quick checks, lock, offline handling, notifications, health alerts, digest, backups,
Excel.

**Tests**: 313 passed (`~/.venvs/hirescope/bin/python -m pytest -q`, ~1.5 s, no network, DEMO data).

**Final real run** (scheduled-style, tiers respected): 223 sources due, 155 OK, 22 failed + 46 manual (custom careers
pages without job data), 202 not due; 36 new, 101 closed; 6,350 visible (5,505 full-time / 840 co-op / 5 unclear),
1,204 Strong; all profile checks OK.

**Limitations**
- *(Superseded 2026-10-13: Jobvite, Eightfold, Rippling, Workable, iCIMS and Jibe careers-site feeds are supported now;
  see the gap-closing report for what is still manual.)*
- Page caps leave ~42 large sources partial (closures verified per job). Netflix requirement parsing ~44%.
- SimplifyJobs JD enrichment is capped at 200 per feed per run; aggregator/LinkedIn listings may have no JD.
- Feedback learning has nothing to learn until you label jobs and export statuses.
- Hiring seasons start with ~1 year of evidence (mostly posting dates); predictions are labeled as guesses.
- The LinkedIn alert parser is built from the documented layout and DEMO emails; check its first real run.

**Setup checklist (Needs Nikhil)**
1. ~~`./scripts/schedule.sh install`~~ done 2026-10-12 (both jobs loaded); Full Disk Access for /bin/zsh only if
   `data/logs/launchd-*.log` shows "Operation not permitted".
2. Optional `.env`: `IMAP_USER`, `IMAP_APP_PASSWORD` (Gmail app password) for LinkedIn alerts; create alerts on LinkedIn.
3. Optional `inputs/Connections.csv` (LinkedIn data export) for the referral finder.
4. Optional `aggregator.enabled: true` + `JSEARCH_API_KEY` / `SERPAPI_API_KEY`.
5. Optional `sponsorship_history.enabled: true` + the USCIS H-1B Employer Data Hub CSV at `inputs/h1b_employer_data.csv`.
6. Use the dashboard and **Export statuses** regularly (learning, applications in SQLite, backups).
7. Confirm GitHub `main` → I delete the local `backup-before-rewrite` branch; say "delete old branches" for the remote
   `phase1-vertical-slice` / `phase2`.

## 2026-10-13 gap-closing work (one commit per group)
- **Watchlist merge**: `inputs/watchlist_additions.csv` (81 companies) merged into My Companies — 35 already covered
  by the seed list (7 moved to tier 1: Cohere, Vercel, Jane Street, Jump Trading, Capital One, Expedia Group, Waymo),
  1 duplicate (Oracle), 45 added: 25 on verified boards (Greenhouse / Ashby / Lever / Workday / Eightfold), 20 marked
  manual with the reason (Apple, Tesla, Citadel, Uber… own sites; iCIMS / Avature / Oracle / Workable). Eightfold now
  checks robots.txt. Real run: 205 of 273 OK, 0 failed, 68 manual, 9 partial; 6,147 new jobs.
