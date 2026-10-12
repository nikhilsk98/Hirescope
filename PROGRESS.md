# HireScope build progress

Resume here: read this file and `git log --oneline` first, then continue from **In progress**.

## Current phase
Pre-Phase 4 housekeeping: remove Claude co-author lines from history.

## Completed
- Phase 1 — vertical slice (Greenhouse → SQLite → scoring → dashboard → job package)
- Phase 2 — sources (Lever, Ashby, Workday, SmartRecruiters, Amazon, Netflix, SimplifyJobs), 300-company verified
  seed list, tiers, levels/locations, My Companies watchlist + ATS detection, add-by-URL, coverage, dead links
- Phase 3 — two-track rules, ATS keywords, freshness, meaning pre-filter, skill gaps, Apply Today, dashboard rebuild
- Setup — work authorization rules (needs sponsorship, not a US citizen), watchlist brand resolution (144 companies),
  preferred metros (Boston, Atlanta, Seattle, SF Bay Area), venv moved to ~/.venvs/hirescope

## In progress
- [x] Disable Claude attribution in `.claude/settings.json` (`attribution.commit` / `.pr` = "", `sessionUrl` = false)
- [x] Ground rule added to BUILD_PROMPT.md
- [ ] Backup branch `backup-before-rewrite`, rewrite main's commit messages, force-push, verify

## Next
Phases 4 → 7 per BUILD_PROMPT.md.

## Open issues
- 21 watchlist companies have custom careers pages without readable job data; 47 need a careers_url (Manual check).
- 45 sources return partial listings (page caps); closures for those are verified per job.

## Needs Nikhil
(none yet)
