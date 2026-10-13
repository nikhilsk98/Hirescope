# HireScope build progress

Resume here: read this file and `git log --oneline` first, then continue from **In progress**.

## Current phase
**v1.0 released (2026-10-13).** All phases (1–7) and the status-audit gap list are done; see the final report at the
end, then "Known limitations" and "Needs Nikhil".

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
- [ ] Phase 4 — HireScope integration (button, statuses, bridge, dashboard, applications, batch)
- [ ] Phase 5 — hardening (failure tests, docs, cleanup) + final E2E, tag `resume-builder-v1.0`

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

