# HireScope build progress

Resume here: read this file and `git log --oneline` first, then continue from **In progress**.

## Current phase
**v1.0 released (2026-10-13).** All phases (1–7) and the status-audit gap list are done; see the final report at the
end, then "Known limitations" and "Needs Nikhil".
**Resume Builder v1.0 (2026-10-13, tag `resume-builder-v1.0`)**: all 5 phases + final integration done; see
"# Resume Builder" at the end of this file.

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
1. ~~`./scripts/schedule.sh install`~~ done; the repo now lives in `~/Projects/hirescope`, so no Full Disk Access is
   needed (2026-10-13).
2. Optional `.env`: `IMAP_USER`, `IMAP_APP_PASSWORD` (Gmail app password) for LinkedIn alerts; create alerts on LinkedIn.
3. Optional `inputs/Connections.csv` (LinkedIn data export) for the referral finder.
4. Optional `aggregator.enabled: true` + `JSEARCH_API_KEY` / `SERPAPI_API_KEY`.
5. Optional `sponsorship_history.enabled: true` + the USCIS H-1B Employer Data Hub CSV at `inputs/h1b_employer_data.csv`.
6. Use the dashboard and **Export statuses** regularly (learning, applications in SQLite, backups).
7. Confirm GitHub `main` → I delete the local `backup-before-rewrite` branch; say "delete old branches" for the remote
   `phase1-vertical-slice` / `phase2`.

## 2026-10-13 gap-closing work and v1.0 (final)
Every item from the status audit was closed, one commit per group, each snapshot tested on its own:
- **Watchlist**: 81 additions merged (35 already in the seed list, 7 moved to tier 1; Oracle a duplicate; 45 added,
  25 on verified boards). My Companies: 188.
- **Dashboard**: saved views; collapsible Full JD; focus trap in the panels (Esc closes, focus returns); day tabs;
  Seen badge; virtualized cards and table (no row cap); skeleton first paint; all charts inline SVG; salary min-max;
  Applied / Tracker view; `x` reason picker; one-click keyword highlight with evidence; Move on Unclear cards; Apply
  Today pinned; More like this per card; verified engineering-blog links; header log link and run summary; Type
  filter; track-switch transition; job + tab in the URL hash.
- **Runs / alerts**: early birds for Strong + Stretch; hiring-season notifications (once per window); exit code 1 only
  for real failures; ETag/304 for SmartRecruiters and Amazon; 304s never reopen closed jobs and keep counting misses.
- **Scoring**: `domains`, `years_experience` (YOE window + graded fit); "Member of Technical Staff" is not a staff
  level; Discovered-date label tested; SimplifyJobs early-career companies score as `company_tiers.feed_tier`.
- **Accuracy fixes found on real data**: summer-only internships on the co-op track (graduation / enrollment phrases,
  "Spring Boot", "6-month period", "Spring/Summer 2027" lists); "graduating by X or Y"; clearance from titles and
  structured headers (63 cleared roles were visible).
- **Sources**: Workable, iCIMS (sitemap + JSON-LD) and Jibe careers-site JSON feeds; 15 companies moved from manual to
  automatic; `--source X` includes My Companies whose source is known.
- **Cleanup**: hardcoded tuning values moved to preferences; dead code and the empty `experimental/` package removed;
  stray files deleted; README (daily use, changing preferences, what leaves your machine, permissions).

**Tests**: 397 passed, line coverage 91% (`pytest --cov=jobsearch`). Visual QA screenshots (light + dark, both tabs,
My Applications with DEMO applications, detail panel, Keywords, Full JD, Coverage, 760 px) in
`data.nosync/screenshots/`; design tokens pass WCAG AA in both themes (tested).

**Final full run** (`--all`, 2026-10-13): 467 sources, 414 OK, **0 failed**, 53 manual, 44 partial (page caps on big
employers); 106,661 listings; 7,008 visible (6,147 full-time / 855 co-op / 6 unclear). Profile checks on real data
all pass: no summer-only co-op roles, no graduation-window mismatches, New Grad boosted (16% of the top 200 vs 12%
overall), 0 roles needing 7+ years, 0 senior or 5-6-year roles labeled Strong, 0 non-US, 0 full-time
no-sponsorship / citizens-only / clearance roles, 3,515 roles in Boston / Atlanta / Seattle / SF Bay Area. A follow-up `--quick` run: 220 of 273 OK, 0 failed;
consecutive SmartRecruiters runs answered 304 for all 18 companies.

**Manual Checks view** (2026-10-13): the 53 manual companies with reason, careers link, "Mark checked" (date saved,
Undo), a "Check due" badge after `manual_checks.remind_days` (7), a due count in the sidebar + banner, Export statuses
format 4 → SQLite `manual_checks` (migration 7) → sync.js, and a "Manual checks due" section in the weekly digest.
Coverage now shows one row per company (its newest source), so companies that moved to an adapter or left the
watchlist no longer show stale "manual" / "failed" rows.

## Known limitations
- **53 companies are manual** (reason in `config/company_aliases.yaml`, link in My Companies): own careers sites
  whose robots.txt / terms forbid automated access or that are bot-blocked (Google, Meta, Apple, Tesla, Citadel,
  Uber, Walmart, IBM…), job systems without a public feed (Oracle Recruiting Cloud, SuccessFactors, Avature, Taleo,
  Paylocity, BambooHR, JazzHR, Comeet, Paycor, UKG, Njoyn, JobDiva, Kula, Dover, Pinpoint), iCIMS portals whose
  robots.txt disallows everything, and a few companies with no public board (OM1, ChaosSearch, Auriga).
- **Partial listings**: ~44 big employers exceed the per-keyword page caps (closures for them are verified per job).
- **Conditional requests**: Workday, Rippling, Eightfold and Jobvite send no validators (`no-store`), so they are
  re-listed each run (known jobs' details are not re-fetched for 7 days). Amazon's listing changes between most runs.
- **Eightfold**: only PCSX sites; older Eightfold sites answer "Not authorized" and stay manual.
- **LinkedIn alerts, aggregator, referral finder, H-1B history, feedback learning** are built and tested on DEMO
  data; they need your setup (below) to run on real data. LinkedIn listings are never opened; an official ATS posting
  of the same role takes over through dedupe.
- **Netflix** requirement parsing works for ~44% of postings (its pages carry little structure).

## Needs Nikhil
1. ~~Full Disk Access for `/bin/zsh`~~ not needed: the repo moved to `~/Projects/hirescope` (2026-10-13), the schedule
   was reinstalled and a launchd-started quick check ran end to end (219 of 273 OK; 1 failed: CACI's Workday answered
   HTTP 520). Statuses import from `~/Downloads` (readable by the venv's Python under launchd, verified) and
   `inputs/statuses/` (always readable) — use the latter if the Run Log reports a blocked folder.
2. **Notifications**: allow Script Editor in System Settings → Notifications.
3. Confirm `inputs/preferences.yaml → work_authorization` (needs_sponsorship: true, us_citizen: false).
4. Optional: LinkedIn alert emails (Gmail app password in `.env`), `inputs/Connections.csv`, the USCIS H-1B CSV,
   `backups.dir` (e.g. iCloud Drive).
5. Use the dashboard and **Export statuses** regularly (learning, applications in SQLite, backups).
6. Old branches: local `backup-before-rewrite`, remote `phase1-vertical-slice` / `phase2` (say "delete old branches").

---

# Resume Builder (`/resume-builder`)

Built from `RESUME_BUILDER_PROMPT.md`, all 5 phases back to back. Resume here: read this section and
`git log --oneline`, then continue from the first phase not marked done.

## Status
- [x] Phase 1 — core pipeline (2026-10-13)
- [x] Phase 2 — quality (drafter → reviewer, truth verification, reports, evidence interview) (2026-10-13)
- [x] Phase 3 — versioning (manifests, dedupe, diff, rollback, locks, manual edits) (2026-10-13)
- [x] Phase 4 — HireScope integration (button, statuses, bridge, dashboard, applications, batch) (2026-10-13)
- [x] Phase 5 — hardening (failure tests, docs, cleanup) (2026-10-13)
- [x] Final integration + end-to-end testing, tagged `resume-builder-v1.0` (2026-10-13)

## Phase 1 report (2026-10-13)
**Setup**: Tectonic 0.17.0 and poppler 26.10.0 (Homebrew). Calibri is not installed system-wide (only inside
Microsoft Word's app bundle, which XeTeX can't see), so the build copy of the template selects Calibri when it exists
and **Carlito** (installed with `brew install --cask font-carlito`; metric-compatible, same line breaks) otherwise;
the font used is reported in every validation report and manifest. Your template file is never modified.
`pdfplumber` added to `requirements.txt` (MIT; PDF text, glyph sizes, links).

**Built** (`src/resume_builder/`, `config/resume_builder.yaml`, `.claude/skills/resume-builder/SKILL.md`)
- `evidence.py`: evidence bank from `master_resume.md` (+ confirmed `evidence_additions.md`): entries (role,
  organization, location, dates exactly as written) and items with ids (`exp-accenture-b5`), technologies (HireScope
  keyword vocabulary), actions, outcomes, metrics only as written. Experience / projects / coursework / education /
  skills kept separate. Cached by input hashes. Malformed or contradictory input (no dates, end before start, the
  same role twice with different dates, bullets outside an entry, unknown addition targets) stops with the exact
  line. Template bullets missing from the master resume are reported.
- `jd_analysis.py`: starts from the job package v2 (`render_package` / `parse_package`); responsibilities,
  required / preferred / mentioned lines, ATS keywords, eligibility, profile auto-pick (backend / fullstack / cloud /
  ml), requirement → evidence mapping (direct / transferable / gap / needs_judgment; transferable only through
  `related_groups`, never equivalent), instruction-like JD lines listed and dropped.
- `content_schema.py` (pydantic, unknown fields rejected; titles/employers/dates are not in the JSON, the renderer
  takes them from the bank), `draft.py` (deterministic baseline: your bullets verbatim, ranked for the JD, track
  section order), `render.py` (template split into header + `\input{content.tex}`, full escaping, safe links, font
  fallback, no syllable hyphenation so an ATS never sees "produc-tion"), `compile.py` (Tectonic `--untrusted`,
  isolated build dir, timeout, log parsing, contact details redacted from logs), `validate.py` (page count, blank
  pages, compiler errors / overfull boxes / missing glyphs, fonts embedded, min font size, margins, clipping,
  overlapping lines, line spacing, stretched lines, bottom whitespace, bullets over 2 lines, one-word last lines,
  hyphenation, pdftotext vs content incl. reading order, headings, bullet characters, contact details, links on their
  own text, rendered page images + ink in the margins), bounded repair (max 3: repetition → lowest-priority bullets →
  summary / courses / extra projects / an irrelevant old role → entry spacing; never fonts or margins),
  `versions.py` (v<N> folders, source bundle with fonts, manifest, current pointer, job lock), CLI
  `python -m resume_builder check | prepare | build | show | schema`.

**Fixed along the way**
- HireScope: **3,288 visible jobs had no JD** since the repo moved to `~/Projects` (absolute `jd_path` values
  pointed at the old folder): read sites now fall back to the same file under `data/jd`, and each run repairs the
  stored paths (16,526 rows fixed; separate commit). Rescored: 7,025 visible.
- Master resume: the four Accenture bullets from the template (Solace, OSGi, Spring Boot, MySQL/Oracle) added at
  your request, in your wording; then rescored HireScope.
- Line pitch was measured from glyph tops (bullet symbols are taller than text) → measured from baselines; overlap
  test uses baseline distance (the template's `\linespread{0.90}` makes glyph boxes touch); right-aligned dates are
  checked for presence, not position; the header is excluded from the stretched-line check; GCP's "related" terms were
  everything (the vocabulary puts all skills in one category) → explicit `related_groups`.
- Your surname is kept out of the public repo: the PDF name comes from the `# First Last` heading of your master
  resume.

**Tests**: 443 passed (406 HireScope + 37 Resume Builder: bank parsing, metrics, rebuild on hash change, 6
malformed/contradictory inputs, additions, template drift, mapping categories incl. AWS≠GCP, profile override, job ID
validation incl. traversal, ambiguous IDs, baseline order per track, schema rejects raw LaTeX fields / empty evidence /
control chars, misattributed evidence, every LaTeX special char, unsafe links, template untouched + font fallback,
special chars + a long link compile and extract correctly, DEMO resume passes every check, missing and garbled text
detected, overflow trimmed in priority order through the real build loop, repair order, naming / path validation,
settings validation, prepare → build → v1, contact details redacted, stretched lines).

**Real data** (baseline content = your bullets verbatim; tailoring starts in Phase 2):
- Copart — Software Engineering Intern (co-op, job 32534, fullstack profile): READY, 1 page, Carlito, 26 checks pass;
  warnings: one-word last line ("CSS."), one stretched line.
- DoorDash — Software Engineer I (full-time, job 16070, backend profile, 12 requirements: 4 direct, 1 transferable,
  3 gap, 4 needs judgment): READY, 1 page; warnings: two one-word last lines, two stretched lines.
- Inspected: page images (layout matches the template), pdftotext output (reading order, headings, bullets), links
  (mailto / tel / LinkedIn on their own text), logs and reports (no email or phone outside the resume itself).

## Phase 2 report (2026-10-13)
**Built**
- `verify.py` — truth verification, separate from drafting, per bullet / summary / skill / talking point against the
  evidence it cites: numbers (exact; "15+" may become "15", never the reverse; no number words), technologies
  (HireScope vocabulary + unknown tech-looking tokens such as OSGi or gRPC; umbrella terms like CI/CD backed by a
  concrete one), ownership (led / managed / spearheaded / architected …), scale (large-scale / millions /
  enterprise …), outcomes (reduced / improved … only when the evidence states one). The drafter's truth pass
  (`truth_review.json`) can only add findings. Any finding blocks "ready" (`truth.md`, `truth.json`).
- `review.py` — rule findings from `resume_rules.md` (weak openers, filler, first person, length, repeated opening
  verb, near-duplicates, tense, punctuation mix, keyword repetition, product-name capitalization) and the reviewer
  packet (checklist + findings + truth check of the draft). `build` refuses content that skipped the reviewer pass;
  the review runs once per generation.
- `reports.py` — coverage (definition and "internal coverage measure, not an ATS score" in every report; missing
  resume keywords kept apart from missing qualifications; requirements with category and whether they're shown),
  change report (selection, before → after for every bullet, what was left out, reviewer changes, remaining gaps),
  interview defense notes (2–3 talking points per bullet, from the evidence).
- `interview.py` + `interview` / `add-evidence --confirm` — up to 5 questions (missing-evidence requirements first,
  then strong matches with no stated scope/outcome); an answer is saved, dated, to `inputs/evidence_additions.md` only
  with explicit confirmation; headings in answers are neutralized; the master resume is never touched.
- JD analysis: requirement bullets without a known technology are kept when a JD has no requirement headings (Copart
  lost "web standards / cross-browser" and "browser rendering"); 2–3-word footer bullets ("Right to Work") dropped.
- SKILL.md: prepare → draft → review → truth pass → build → interview; references (bullet guidelines, US norms,
  reviewer checklist).

**Fixed along the way**: CI/CD flagged as two unknown acronyms (vocabulary spans are now excluded from the
unknown-token check); "e-commerce" / "authentication" flagged for capitalization (only product names now); dates
inside answers parsed as metrics ("2026-01-01" → "01"); coverage said the degree requirement wasn't shown (education
and skills evidence now count).

**Tests**: 466 passed (60 Resume Builder; 23 new: 10 kinds of unsupported claims / invented metrics caught, 5 faithful
rewrites pass, whole-content verification incl. skills, talking points and the LLM truth pass, rule findings, keyword
repetition, review once + build requires review, unsupported claim → failed version while the last good one stays
current, coverage / change / defense reports, evidence interview with DEMO answers, prompt injection in a JD).

**Real data** (Claude as drafter, then as a separate reviewer and truth pass):
- Copart — Software Engineering Intern (co-op, 32534): v1 READY, v2 READY after tightening two bullets (2 of 3
  attempts); 0 unsupported claims (39 checked); coverage 100% (keywords are all "mentioned" in this JD); 2
  missing-evidence requirements (cross-browser, browser rendering). Warning left: one one-word last line ("50%").
- DoorDash — Software Engineer I (full-time, 16070): v1 READY, 0 unsupported (40 checked), coverage 68%; missing
  resume keyword "code reviews" (the bullet says "reviewing code"); missing qualifications: data structures, Kotlin.
- Inspected: page images, extracted text, truth / coverage / changes / defense / review reports.

## Phase 3 report (2026-10-13)
**Built** (`versions.py`, `pipeline.prepare/build`, CLI)
- Manifest per version: job, generation time, input hashes (master resume, additions, template, rules, config),
  template / rules version, profile, track, settings hash, JD package hash, validation, truth and coverage results,
  attempts, repairs, font, tool versions, file hashes, locks, hand edits carried, **dedupe key**.
- Dedupe: `prepare` returns **REUSED** with the current version when job + JD package + inputs + settings + profile +
  locks are unchanged and the version passed and wasn't edited by hand; `--regenerate` builds a new version anyway.
- Versions kept (`v1`, `v2`, … written as `.vN.tmp` then renamed); `current.json` only ever points at a passing
  version; `history.jsonl` (built / rollback / lock / reviewed); `rollback <job> N`; `diff <job> [A B]` (bullet
  changes incl. rewordings, section order, skills, content.tex lines; notes hand-edited versions);
  `mark-reviewed` (only a ready version).
- Locks: `lock <job> <bullet id | entry id | section:skills|education|summary>` from the current version
  (`show --bullets` lists ids); applied to the baseline, restored at build if the drafter changed or dropped them,
  never trimmed by the repair loop; locks are part of the dedupe key.
- Hand edits: each version keeps an untouched `.generated/` copy; edits to `content.json` (changed / deleted / added
  bullets) and to bullet text in `content.tex` are detected and carried into the next generation (origin "manual",
  restored at build, kept from trimming). Conflicts are flagged and block "ready" instead of overwriting: tex edits
  beyond bullet text, the same bullet edited differently in json and tex, an edited bullet whose evidence changed in
  the master resume since. `--discard-edits` is an explicit choice; the edited version itself is never modified.
- Concurrency: a job lock (pid + time; stale locks replaced) around prepare / build / lock / rollback, and a
  "generation in progress" guard (2 h, `--force` to start over).

**Fixed along the way**: the diff missed rewordings of the same bullet (ids are stable across rewrites); "restored"
notes for locked/hand-edited bullets fired when only priority/origin differed.

**Tests**: 477 passed (71 Resume Builder; 11 new: manifest fields, duplicate request reuses, `--regenerate` → v2 with v1
kept, input change → no reuse, diff + rollback + history, failed versions can't be current or reviewed, locked
bullet / entry / section survive a drafter that ignores them, locked bullets never trimmed, hand edits in
content.json and in content.tex preserved, unmappable tex edits and json-vs-tex disagreement and changed evidence
flagged, `--discard-edits`, a second process holding the lock blocks a run, stale lock replaced, generation-in-progress
guard, unique atomic version folders + PDF hash in the manifest).

**Real data**
- DoorDash (16070): locked the "Java-based back-end services … 70%" bullet; hand-edited v1's Spring Boot bullet to
  "performing debugging and code reviews" (the JD's term, the evidence's own wording); `prepare --regenerate` carried
  1 edit + 1 lock → v2 READY with both unchanged; same request again → **REUSED v2**; diff v1 → v2 shows the line;
  rollback v1 → v2 works. (v1 predates dedupe keys, so the first request after this phase regenerated, as expected.)
- Copart (32534): regenerated → v3 READY (the one-word last line is gone); same request again → **REUSED v3**; diff
  v2 → v3 shows the reworded bullet.

## Phase 4 report (2026-10-13)
**Built**
- SQLite migration 8: `resume_status` (per job: status, current / latest version, message), `resume_builds` (every
  version: status, PDF / tex / manifest paths, manifest hash, summary), `applications.resume`.
- `hirescope_bridge.py`: statuses queued → generating (prepare) → validating (build) → ready / failed (the message
  says which version is still current) → reviewed; versions mirrored from the manifests; `dashboard/resumes.js`
  (gitignored) with relative `file://` links (`../data/resumes/…`) after every command and every HireScope run;
  `refresh` re-syncs everything. A failed HireScope update never loses the resume: the CLI says it's saved and how
  to retry (exit 1).
- Dashboard: **Generate Resume (copy command)** copies `claude "/resume-builder <id>"` (shortcut `g`; the old job
  package download is now the secondary "Job package" button), "queued" until the run reports; resume chips on cards
  and the panel; Resume section in Overview (status, version, message / failure reasons, Preview PDF, Download PDF,
  Download .tex, Validation report, All reports, checks summary, coverage labeled "internal coverage measure, not an
  ATS score", page-1 preview image, **Mark reviewed**, Regenerate (copy command)); a **Resumes** view; a ready resume
  moves a job that isn't further along to HireScope's "package exported" (once per version); CSP allows `file:`
  images only (still no network).
- Applications: **Mark Applied** stores the exact current resume (job, version, PDF, manifest hash) on the
  application; Export statuses **format 5** adds `resume_reviews` and the application's resume; the import stores
  both (a later export without a resume never erases it); applications without one are linked to the version current
  on the date applied; the drawer shows "Resume used".
- `batch shortlisted`: counts shortlisted jobs, says which already have a current resume, prints a usage warning and
  exits 3 until `--yes`; then queues them (dashboard "queued", `data/resumes/batch_queue.json`); cap
  `batch.max_jobs` (15).

**Fixed along the way**: `refresh` for a job with no status row left it "generating" (a placeholder looked newer than
the build); copying the command hid **Mark reviewed** for the existing ready version; a test's `monkeypatch.undo()`
also removed the DEMO sandbox (it ran one refresh against the real data, harmless: it synced the real resumes), now
scoped.

**Tests**: 485 passed (79 Resume Builder; 8 new: status flow + resumes.js links resolve, failed build keeps the last
good version and says so, compile failure keeps it, HireScope update failure reported + `refresh` recovers, format-5
import (review applied, application resume stored and never erased, back to sync.js), date-based linking, batch
count / warning / confirmation / cap / queue, status validation + refresh after a lost status table).

**Real data**: `refresh` → DoorDash v2 and Copart v3 "ready" in SQLite and `resumes.js`. Headless Chrome (throwaway
profile, your browser data untouched): both panels show the Resume section with working preview and links (dark +
light screenshots in `data.nosync/screenshots/rb-panel-*.png`); copy command → "queued"; Mark reviewed; Mark Applied
recorded v2 + manifest hash; Export statuses (format 5) imported into a **scratch copy** of the data: review applied,
application linked to v2 (your real v2 stays "ready" until you mark it). `batch shortlisted` on real data: 0
shortlisted; on the copy with 3 shortlisted: count, 1 reusable, usage warning, exit 3 without `--yes`.

## Phase 5 report (2026-10-13)
**Built / changed**
- Failure handling: missing tools named with their install command; missing / empty template, rules or master resume
  named; a broken template copy stops the build with the reason, records "failed" and keeps the last good version;
  compile timeout and missing fonts reported; corrupt manifests / `current.json` never crash or count as valid;
  malformed content JSON rejected field by field; lock targets and job IDs validated.
- Status bookkeeping: the CLI now sets the final status explicitly after every build; a status recorded after the
  newest version (a failure that saved nothing, a run in progress) wins over the manifests; in-progress statuses
  older than 2 h count as abandoned.
- Privacy: **pdfminer logged every PDF token at DEBUG, including the mailto: / tel: links** (it would have put your
  email and phone in HireScope's log on a verbose run): silenced below WARNING; a test scans the CLI output, logs,
  compile log, reports and manifest for DEMO contact details.
- Docs: README **Resume Builder** section (setup incl. the Calibri → Carlito fallback, use, what gets checked,
  configuration, versions / locks / hand edits, evidence interview, troubleshooting), Tectonic's bundle download
  added to Outbound requests, an accurate privacy note (drafting happens in your Claude Code session, so the brief
  goes to Anthropic like any conversation), job-package / `g` key / Mark Applied wording, data table. SKILL.md: check
  step, `--regenerate` pass-through, REUSED handling, what to report at the end.
- Cleanup: unused imports / variables, a hard-coded font name in the validation report, an unused DEMO-only
  additions header form, a vacuous test assertion.

**Tests**: 498 passed (92 Resume Builder; 13 new incl. a walk through every CLI command). Line coverage
`resume_builder` 93% (CLI 94%), HireScope + Resume Builder 90%.

**Real data**: `check` OK on your inputs (61 evidence items, 7 entries, Carlito); `refresh` OK; no contact details in
any log, report, manifest or `resumes.js` (only in the PDF, its .tex sources and the extracted-text report).

## Final integration report (2026-10-13)
**Full test suite**: 505 passed (406 HireScope + 99 Resume Builder), ~40 s, no network. Line coverage: HireScope +
Resume Builder 92%; `resume_builder` modules 88–100% (versions 88%, pipeline / validate / verify 91%, CLI 94%).

**Full HireScope run** (`./scripts/run.sh`, scheduled-style): exit 0; resume statuses survived the run (each run now
re-syncs them into `dashboard/resumes.js`).

**End to end from the dashboard** (headless Chrome, throwaway profile): Generate Resume copied
`claude "/resume-builder 34880"`, `… 46867`, `… 35047`; each ran through the skill (check → prepare → draft → review →
truth pass → build):
| Job | Track | Result | Truth | Coverage* | Notes |
|---|---|---|---|---|---|
| HPE — Systems Software Engineer Intern (34880) | co-op | v3 READY, 1 page, `Nikhil_<Last>_Resume_Hewlett_Packard_Enterprise.pdf` | 0 / 41 unsupported | 70.5% | 3 builds (whitespace, then two one-word last lines); a 4th build refused (limit) |
| Centric — Software Engineer 1 (46867) | full-time | v2 READY → v4 (regenerated) | 0 / 42 | — | v1 31% empty → v2; v3/v4 regeneration test |
| Bose — Data Engineer Co-Op (35047) | co-op, clear gaps | v2 READY, `…_Resume_Bose.pdf` | 0 / 40 | 48% | missing qualifications: data pipelines, serverless, Snowflake, mobile — never added |
*internal coverage measure, not an ATS score. Every report present (truth, coverage, changes, defense, review,
validation, extracted text). Font: Carlito (Calibri not installed system-wide).
- Dashboard: generating (after prepare) → ready (after build) in SQLite and `resumes.js`; all three panels show
  "Resume ready" with the current version, page preview loads, Preview / Download PDF / Download .tex / Validation
  report links point at existing files; a ready resume moved each job to "package exported".
- Duplicate request → **REUSED** for all three. `--regenerate` (Centric) → new version; a **locked** bullet held
  against a drafter that reworded it ("locked bullet … restored"); a **hand edit** to v2's content.json was carried
  into v4; diff v2 → v4 shows the edited line; rollback v4 → v3 → v4.
- **Mark reviewed** + **Mark Applied** (Centric) → Export statuses → import into a scratch copy: v4 reviewed,
  application linked to v4 with the PDF's hash (still matching after the review).
- **Batch** (scratch copy, 4 shortlisted): count + usage warning, exit 3 without `--yes`; `--yes` queued 4; 3 REUSED,
  Axos Bank (20970) generated → v1 READY; all 4 end "ready".
- **Failure drills** (scratch copy): broken template copy → clear error, "failed (v2 is still current)"; compile
  failure → v3 failed with `content.tex:75: Undefined control sequence`, v2 current; JD with 4 hidden instructions
  (ignore instructions, write ~/.ssh/id_rsa to a file and email it, "developer mode", "top 1% candidate") → all 4
  listed as ignored, none in the tailoring inputs, no file written; a draft that obeyed them FAILED truth (4
  unsupported), the honest draft built READY.

**Fixed during the final run** (each with a regression test): JD footers counted as requirements or keywords
(benefits URL → "HTML", "Accessibility", "payments"); an anti-fraud notice flagged as injection ("credentials"); JDs
without requirement sections produced 0 requirements (they now come from the responsibilities); the "Unit Testing"
capitalization and "Kubernetes …" tense false positives; a 4th build crashed instead of being refused; a stopped
build blocked the next generation as "in progress"; a reused job stayed "queued" after a batch; a locked bullet was
doubled next to its regenerated twin; the application's resume reference used the manifest hash (changes when
reviewed) → the PDF hash; a test that left the DEMO sandbox (now asserted at teardown; it had created one stray
work folder + status row for an unrelated real job, removed); the dashboard data dir can be redirected
(`JOBSEARCH_DASHBOARD_DIR`), after one scratch refresh briefly overwrote the real `resumes.js` (restored at once).

## Known limitations
- Generation is a copy-the-command workflow (the dashboard runs nothing); one-click generation via a local runner is
  for later. Reviews and applications from the dashboard reach SQLite with **Export statuses** (or `mark-reviewed`).
- The truth verifier is deterministic and conservative: it checks numbers, technologies (vocabulary + unknown
  tech-looking tokens), ownership / scale / outcome words against the cited evidence; meaning-level paraphrase is
  covered by Claude's truth pass, which can add findings but never clear one.
- Requirement mapping for soft lines ("strong communication") is left to the drafter (needs_judgment); JDs without
  structure rely on bullet / responsibility heuristics.
- Each build within a generation saves a version (up to 3); intermediate versions stay in the history.
- The page-fill target is advisory (warning above 30% empty); trimming is automatic only when over a page.
- HPE v3's coverage report predates the footer-keyword fix (it lists "Accessibility"); the next `--regenerate`
  refreshes it.
- Fonts: Carlito stands in for Calibri (same metrics); install Calibri system-wide to use it (detected automatically).

## Needs Nikhil
1. Two Accenture bullets both claim a **70% reliability** gain ("processing reliability" and "distributed storage
   reliability"). If they are the same result, say which wording to keep; the drafter will not use both together.
2. **Evidence interview** (answer any you can; I save only what you confirm):
   - Copart: Have you worked on web standards / cross-browser compatibility (which role or project, what you did)?
   - Copart: Have you worked on browser rendering performance (not API latency)?
   - Copart / all: the Full-Stack E-Commerce project — any scale or outcome (users, orders, load, team size)?
   - DoorDash: data structures beyond the Algorithms course (coursework, projects, interviews)?
   - DoorDash: AI-powered tools in your development work (which, how you evaluate their output)?
3. **DoorDash eligibility**: the JD asks for "no more than two years of full-time professional experience". Freelance
   Feb 2022–Aug 2025 plus 11 months at Accenture may exceed that, depending on how freelance work counts. Your call
   before applying (the resume itself is unchanged by this).
4. **HPE eligibility**: the posting says "currently pursuing a Bachelor's degree" (its job-family text also lists
   Master's); you're pursuing an M.S. Your call before applying.
5. **More interview questions** (Centric / HPE / Axos): data structures and object-oriented design beyond coursework;
   AI-assisted development tools you use; SQL stored procedures or SSIS (Axos, scratch-copy test only); browser
   rendering / cross-browser work (Copart).
6. **Review the 5 resumes** (dashboard → Resumes view → Preview PDF → Mark reviewed → Export statuses): DoorDash v2,
   Copart v3, HPE v3, Centric v4, Bose v2. Nothing was marked reviewed in your real data; that's yours to do.
7. Optional: install Calibri system-wide if you prefer it over Carlito.


---

# Folder reorganization (2026-10-13)
**Backup first**: `~/hirescope-backup-20261013-1536/` (data.nosync.tgz 413 MB + inputs.tgz, both verified).

**Moved, not regenerated** (`scripts/migrate_layout.py`, idempotent, `--dry-run`): `data` symlink removed (code points at
`data.nosync/`); `jobs.db` → `db/`; 46,076 JD files → `jd/<Company>/` (336 no job refers to → `jd/_unassigned/`);
`resumes/<Co>_<id>/` → `generated_resumes/<Company>/<Role>_<JobID>/` with `reports/vN/`, `latest` → current version,
`_work/`; evidence cache → `generated_resumes/_cache/`; `packages/` → `job_packages/`; run lock → `state/`; inputs →
`inputs/resume/`, `inputs/companies/` (+ `linkedin/`, `statuses/`, `h1b/`), preferences paths updated; the build
prompts → `docs/prompts/`; stray `.coverage`, `.DS_Store`, `.Rhistory` files removed. SQLite: 45,785 `jd_path` rows,
resume statuses / builds (paths rebuilt from the manifests) updated. See STRUCTURE.md.

**Code**: every location in `paths.py` (data paths are derived from `DATA` when read, so redirecting it — tests,
`JOBSEARCH_DATA_DIR` — redirects everything); JD files written into company folders; stored paths found again in
company folders; resume folders found by job ID (title changes don't matter); SQLite waits up to 30 s for another
writer; `setup.sh` no longer makes the symlink; `.gitignore` covers the input subfolders (only `*.example.*` tracked).

**Found and fixed while testing**: a test still held the run lock at the old location, so `jobsearch.run` started a real
run (before the derived-paths fix it was pointed at the real database: run #64 at 19:41 fetched your real sources for a
few minutes and was stopped — 38 new jobs, JD files rewritten into the new folders; integrity check OK, nothing
synthetic written); an autouse fixture now drops paths frozen by monkeypatch; `verify` printed the old work folder.

**Checks**: 510 tests passed (5 new layout tests, incl. the migration on a DEMO copy of the old layout). Dry run
(`--dry-run --company Copart`): database unchanged. Real quick run: 219 of 273 OK, 1 failed (Stryker's Workday: HTTP
520, unrelated). New resume generated in the new layout (Axos Bank → `Axos-Bank/Software-Development-Intern_20970/v1`,
`reports/v1/`, `latest → v1`; READY). Dashboard (headless Chrome): all 6 resumes show "Resume ready", previews load,
Preview / Download PDF / Download .tex / Validation report / All reports links resolve, every apply link present; Log
link → `data.nosync/logs/jobsearch.log`. Schedule reinstalled (new log paths) and a launchd quick check started.

## Reliability fixes (2026-10-13 evening)
- The 18:00 scheduled full run was lost to a momentary network check failure → runs now wait about a minute for the
  network (5 probes, 15 s apart) before giving up.
- HTTP 520-524 (Cloudflare in front of Workday; Stryker, CACI) are retried with backoff like other 5xx.
- Page caps on full runs: Workday 50 → 120 pages (2,400 results per search), Eightfold 20 → 70 pages (700).
- Runs that never finished (run #64, from a test) are closed by the next run (finish time, no summary).
- Missed full run re-run through launchd: exit 0; 273 sources, **0 failed**, 53 manual, 3 partial (down from ~44),
  78,045 listings, 493 new. With the new caps, Qualcomm (+206 never-seen jobs), Microsoft (+158) and RTX (+29) are
  complete: **0 partial sources** left. Tests: 513 passed.
- Still manual by design: 53 companies whose sites forbid or block automated access (robots.txt / terms / bot
  checks) or have no public job feed — the Manual Checks view lists them with their careers links.

## External jobs + applied tracking (2026-10-14)
**Extended, not duplicated**: Add job by URL (`add_url.sh`) is unchanged and is what `add_job.sh --url` (URL only) and
the dashboard form (no JD) fall back to; the Add application form stays for jobs you don't add to HireScope.

- **Import paths**: `./scripts/add_job.sh` (`--file` .txt/.md/.pdf/.docx, `--paste`, `--text`, `--json`, `--pending`),
  the dashboard's **+ Add external job** form (saves `hirescope-external-job-*.json`; imported by the next run or
  `--pending`), and the drop folder `inputs/external_jobs/` (header or file name gives company / role / URL / track).
  Jobs get source `external`, full scoring, a job package, the External badge and an **External Jobs** view, and are
  always shown (rules that would hide them become a note). PDFs via pdfplumber (bullets drawn as shapes and wrapped
  lines are rebuilt), DOCX via the standard library.
- **Duplicates**: same URL / ATS posting, same JD text, or same company + role with a similar JD → not created; the
  existing job is named and linked (`--force` / "add it anyway"). External imports never fuzzy-merge into another job.
- **Applied tracking**: Mark Applied records source HireScope / External; every job marked applied / interviewing /
  offer gets an application (applied date, company, role, source, track, resume version, saved JD, link) even when
  set elsewhere; `apps mark-applied JOB_ID`; **Applied Companies** tab (one row per company: count, latest status,
  links) and `apps companies`.

**Real test** (on a copy of the real data — `JOBSEARCH_DATA_DIR` / `JOBSEARCH_DASHBOARD_DIR` in the scratchpad — so
the real My Applications got no test applications): two real postings at companies HireScope doesn't track.
Squarespace "Software Engineer, Frontend" (pasted JD) → job 141813, full-time, mid (3+ YOE), 66 Stretch, ATS 55%;
Mercury "Software Engineer - Product" (browser-printed PDF) → job 141814, entry (1+ YOE), 65 Stretch, ATS 51%. A
re-paste of the Squarespace JD under another title was refused as a duplicate (exit 3). `/resume-builder` on both
(prepare → draft → review → verify → build): **READY v1**, one page, every claim supported (one stretched-line
warning each). Dashboard in headless Chrome: External view + badges, Generate Resume, Mark Applied (source External,
resume v1 + PDF hash, 5,530-char saved JD, link), Export statuses → `apps import`; Mercury marked applied with
`apps mark-applied`; **Applied Companies** shows both; the form flagged Mercury as a duplicate and saved a third real
job (Squarespace Security Engineer, Dublin), imported with `--json` (shown despite the location/title rules, with
the note). No console errors.

**Found and fixed during the real test**: the PDF's list structure was lost (no requirements found) → paragraph /
bullet reconstruction; your chosen track was stored as a feed hint ("listed in a new-grad feed") → it's an override
and detection stays from the JD; a "different job" decision could still fuzzy-merge into the existing posting;
the drop folder pointed at the real `inputs/` in tests → it follows the repo root; `apps mark-applied` recorded no
PDF name; a `run.main` test leaked `--no-email` into later tests. Tests: 526 passed (13 new).


**~/Downloads under launchd (tested 2026-10-14)**: a temporary agent with the same chain as the schedule (launchd →
`/bin/zsh` script → venv Python) could list, read and move files in `~/Downloads`; `/bin/ls` in the same job got
"Operation not permitted". macOS grants this per program (here Homebrew's `python3.12` 3.12.15), so the scheduled
import works today, but a Python upgrade can lose it. The external-job import now reports a blocked `~/Downloads`
(it used to skip it silently) and both messages say what to run (`./scripts/add_job.sh --pending`,
`./scripts/run.sh --rescore`). The earlier "needs Full Disk Access" wording in the README was wrong and is corrected.

**`./scripts/import_pending.sh`** (fallback after a dashboard session): imports the newest Export statuses file and
every "Add external job" file (+ the drop folder) from the folders a run reads, then rewrites the dashboard once; no
fetching, a few seconds; takes the run lock (exit 3 while a run is in progress). The dashboard form, the Export
statuses message and the blocked-~/Downloads warnings now point to it. Tested: unit test (import, nothing-waiting
re-run leaves the dashboard alone, lock); a sandbox run (empty scratch database, copy of your preferences) with files
made by the dashboard itself in headless Chrome; a no-op run on the real setup (nothing waiting; dashboard not
rewritten). Tests: 528 passed.

## Resume rules v3 + tool checklist (2026-10-14)
- **Rule 1 status tags**: `[confirmed]` / `[needs confirmation]` / `[demo]` at the end of a line (a heading tag covers
  what's below; in skills lists a tag after an item covers that item, after the category the line). Only confirmed
  items enter the evidence bank; ids stay stable around excluded ones; content citing an unconfirmed item is refused
  with the reason; `python -m resume_builder tags` lists what needs a tag. Your master resume has **61 untagged
  items**, so real builds stop with that message until you tag it (the builder never edits it).
- **Rules 15–17**: section order from track + HireScope level (student vs experienced); only Projects / Experience may
  swap. **22–24 / 36–38**: bullet totals, per-role caps and rule 24's per-employer allocations read from your rules file
  (names stay out of the public config); 2 projects × 2 bullets, a 3rd only for co-op when it fills a JD gap, 1 for
  mid-level — enforced by the build. **53**: overflow trimming in the rule's order; fonts never below 10 pt.
- **61 / 63**: keyword questions in the brief (have you used X / which of these tools), broader true wording, unticked or
  declined → known gaps, specific → general only (`category_terms`); answers saved only with `--confirm` (tools →
  tool_checklist.md, others → evidence_additions.md in the new block format); learn-it list (`gaps`, `learn_it.md`,
  dashboard Skill Gaps). Ticked tools: Skills freely, bullets only where "Where" names the role.
- **62**: project work only under a role when the project is tagged `[client work: …]`. **3**: the same % in two bullets
  is flagged in check / brief / review, never changed. Tools the master names but the checklist leaves unticked
  (Oracle, OSGi today) are flagged and kept out of Skills.
- evidence_additions.md: created with the format header, no entries (none existed, so no test / DEMO entries).

**Real test** (sandbox: a tagged copy of your inputs with one bullet `[needs confirmation]` and a planted `[demo]` line,
the two real jobs and JDs in a scratch database; your real inputs and data untouched): Axos Bank Software Development
Intern (co-op, job 20970) and Anduril Production Software Engineer (full-time mid-level, 3+ years, job 139201). Both
**READY v1**, one page, every claim supported. PDFs: Axos Education → Technical Skills → Experience → Projects, Anduril
Technical Skills → Experience → Projects → Education; 10 experience bullets each (rule 24 backend allocation Accenture
4 / Freelance 3 / Rescho 2, Agathsya 1), 1 project × 2 bullets (the only project); no Solace (needs confirmation), no
planted demo line, no Oracle / OSGi, no C / C++ (Anduril's open keyword questions, not answered for you). The rule-3
check flagged 25% in three bullets and, on Anduril, two of them together (kept unchanged).
Found and fixed while testing: degree fields asked as "have you used"; a trailing skills-list tag covered the whole
line; the bank cache ignored code changes (now part of its key); OSGi conflicts missed (not in the keyword
vocabulary); the additions header's format example parsed as an entry. Tests: 540 passed (12 new).

## Master v4 + resume rules v4.1 (2026-10-15)
- **Master v4 parser** (`master_v4.py`): Education (`**Degree:**` / `**Graduation:**` / `**GPA:**`, coursework table —
  "Yes" rows only), per-role fields, Metrics tables with statuses, Bullet banks (groups A–F, core / extended, variants),
  "In my own words", Legacy bullets, Excluded roles / projects / skills, the Default one-page snapshot, Skills
  inventory tiers (core / extended / self-reported max 4 / planned / concepts), Certifications, Contact, Target roles.
  Italic "Builder:" lines are instructions (listed in the brief), never content. Statuses from the master's labels:
  verified / estimated usable; unverified / pending / planned / Legacy / Excluded never — a bullet stating an
  unverified metric's number is dropped too. The `[confirmed]` tag requirement is gone (older format: untagged = usable).
- **Rules v4.1 checks** (`rules_v4.py`): precedence (notes set selection, never relax truth), the snapshot as the start
  (no JD → reproduced unchanged), one section order for every level, rule 8 metric kinds, 11–14, 29, 31, 37–39
  (review), 45, 50, 53 tiers (the master's Excluded list beats the tool checklist; checklist ticks without master
  evidence are self-reported), 78, the A3/C2 and B3/Material UI overlaps, the master's per-role pick ranges.
  Rendering uses the template's own macros (`v4build.py`); f-ligatures are broken in generated text so "fi" extracts as
  letters (Tectonic writes the single glyph "ﬁ" otherwise), dashes are TeX ligature input. The layout specification is a
  set of validation checks (T1 + lmodern, 0.5 in, 10.5/12 pt, hyphenation off, 12.5 pt headings, 1.2 em math bullets,
  hidelinks, 3 pt gaps, Letter, Latin Modern only, no Type 3, "•" bullets, "–" dates, 10.5 pt body, 20 pt name, contact
  on one line). Overflow follows rule 67, ending with `\entrygap` 3 → 2 pt and leading 12.0 → 11.8 pt.
- **Engine**: only Tectonic is installed (no pdfLaTeX); with lmodern it reproduces the template on one page — the
  default build's extracted text is identical, line for line, to the template compiled as written.
- **HireScope**: graduation date May 2027; Data Engineer and Cloud / AWS Developer are core titles; HireScope's own
  resume parser now reads the v4 master (it had been reading Builder notes, field labels and planned skills as resume
  content); rescored: 7,134 visible (6,260 full-time, 868 co-op); 280 visible Data Engineer / Cloud-AWS Developer jobs.

**Real runs** (your real inputs and data):
- Default resume (no JD): **READY** `_default/v1`, identical to the template compiled as written; every check passed.
- Co-op: HPE Cloud Developer Intern (job 34877, out of state): **READY v5** — freelance D2 + D4 swapped in (cloud /
  ML), scikit-learn in Skills, AZ-900 (cloud role), relocation line, both projects, 4 / 3 / 2 bullets. One warning:
  the relocation contact line wraps even without "+1" (open decision, see README).
- Full-time: Amazon SDE Early Career (job 19540, Cambridge MA): **READY v2**, first attempt, no repairs — D2 in for C1
  (backend-distributed), NoSQL in Skills, default contact line, coursework kept.

**Found and fixed during the real runs**: the PDF would have been named after "# Master Resume — …" (now the Contact
name); `C++` and `C` shared an id; the ML profile (not one of your tracks) picked for a cloud role; keyword questions
about "OE", "OR" and "code review"; snapshot bullets were "weak" by position, so overflow trimming cut the approved
GitHub Actions bullet; repairs could cut a role below the master's minimum and nothing re-checked the rules afterwards
(HPE v2 was built that way and is superseded; failed / superseded versions stay in the folder, never current); the
TeX-comment portfolio link and the `\bfseries` name in the header; link boxes shorter than their glyphs.
Tests: 557 passed (DEMO v4 master + template fixtures; the default-resume regression compares extracted text).

**Decisions applied (2026-10-15, rules v4.2)**: relocation jobs drop the GitHub link from the contact line (rule 31;
"+1" only if it still wraps); confirmed answers stay in `evidence_additions.md`, never in `master_resume.md` (rules 75,
77 reworded in your rules file); planned technology — including Redis, PyTorch, Prometheus and Grafana, which the
master also lists as self-reported — never in Technical Skills, while VibeSpace's design bullet may name Redis (rule
50); the master's changelog line about the relocation line now says confirmed (the only edit to master_resume.md).
HPE Cloud Developer Intern rebuilt: **READY v6**, first attempt, no repairs, contact on one line (relocation, "+1"
kept, GitHub link only on the project lines), coursework kept. C#, Go, Rust (Amazon): still unanswered.
Tests: 558 passed.

**Answers recorded (2026-10-15)**: C# — no; PyTorch — no (also unticked in tool_checklist.md; stays planned for
VibeSpace, never in Skills); Go and Rust — coursework at Northeastern (Skills only when a JD asks, as "Go (coursework)",
never in bullets); Prometheus / Grafana stay planned. All kept in `evidence_additions.md` (new `- Keyword:` field;
"Answer: no" = a recorded decline, which beats a self-reported entry in the master and is read by HireScope's matcher
too). Fixed while rebuilding: the truth check compared "Go (coursework)" with the label attached ("Go" isn't in the
keyword vocabulary). Amazon SDE Early Career rebuilt: **READY v4**, first attempt — Go / Rust (coursework) in
Languages, no C#. Old answers archived in `inputs/resume/archive/before-decisions/`. Tests: 559 passed.

## ⌘K palette: every job (2026-10-15)
The palette searched only the jobs the page loads (and skipped filtered ones). Each run now also writes
`dashboard/search.js` — all 142,511 jobs (open, Filtered Out, filtered but not listed, beyond the top N per track,
closed; 9.6 MB, companies and filter reasons stored once), loaded the first time the palette opens. Search and ranking
live in `view.js` (tested in Node): jobs in the lists first, then Filtered Out, beyond the top N, unlisted filtered,
closed; the current track first within each. Results show track and view; opening one switches tab and view (clearing
filters that hide it) and opens the panel; jobs without details open an info card. Real test (headless Chrome, the
real dashboard): a co-op job from the Full-time tab → Co-op tab, All Jobs, panel, card focused; a listed filtered job →
Filtered Out; an unlisted filtered, a beyond-top-N and a closed job → info card with status / reason; a search that hid
the job → filters cleared. No console errors. Tests: 561 passed.

## check wording (v4) + one-off non-JSON responses (2026-10-15)
- `python -m resume_builder check` with a v4 master: entries, bank bullets and the snapshot; skills by tier (core,
  extended, self-reported, coursework, planned) and declines; how tool_checklist.md ticks resolved (already in the
  master's inventory / self-reported / planned / excluded); and what is **correctly left out**, by reason (Legacy
  bullets, excluded by your decision, planned courses, pending or unverified items) — no more "not confirmed (rule 1)"
  or "0 ticked tools". `tags` explains that v4 needs no tags and lists the same exclusions.
- JSON API calls retry a non-JSON 200 body with backoff (the Workday "Expecting value" failures at Xcel Energy and Palo
  Alto Networks). Still failing: the failure is marked transient; a quick check logs a warning and exits 0 unless the
  same source also failed on its previous check. Full runs unchanged. Tests: 564 passed.
