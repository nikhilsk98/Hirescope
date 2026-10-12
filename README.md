# Local Job Search

A local, single-user job-search pipeline for macOS: discovery → dedupe → scoring → static HTML dashboard →
per-job "job package" export. The core pipeline makes **zero LLM calls** and sends no personal data anywhere.

> Status: **Phase 1 (vertical slice)**. Greenhouse adapter, SQLite, hard filters, skill + semantic scoring,
> dashboard with day tabs, job-package export. Later phases add more sources, the watchlist, feedback, scheduling.

## Setup

```sh
./scripts/setup.sh                       # venv (.venv), dependencies, PYTHONPATH hook
cp inputs/preferences.example.yaml inputs/preferences.yaml      # then edit
cp inputs/master_resume.example.md inputs/master_resume.md      # then replace with YOUR resume
```

`inputs/` is gitignored except the `*.example.*` files. `master_resume.md` is the only source of facts about you.
Keep its `## Experience / ## Projects / ## Coursework / ## Education / ## Skills` headings, `### ` entry headings
and `- ` bullets.

> **Why no `pip install -e .`?** On this Mac, new `.pth` files in the venv get the `hidden` file flag, and
> Python 3.12 skips hidden `.pth` files, so the editable install silently stops working. `setup.sh` instead adds
> `src/` to `PYTHONPATH` in `.venv/bin/activate`, and `scripts/run.sh` sets it itself.

## Usage

```sh
./scripts/run.sh                         # full run, writes dashboard/data.js
source .venv/bin/activate && python -m jobsearch.run --verbose     # same thing
python -m jobsearch.run --company "Figma" --verbose               # one company
python -m jobsearch.run --source greenhouse --dry-run             # fetch + score, write nothing
python -m jobsearch.run --resume path.md --prefs path.yaml        # alternate inputs
```

Then double-click `dashboard/index.html` (opens via `file://`, no server). The exit code is `0` when every
source succeeded, `1` when any failed (the run still completes and the dashboard flags the missing coverage),
and `2` for missing or invalid inputs.

### Adding a company
Edit `config/companies.yaml` only:
```yaml
- {name: Stripe, source: greenhouse, board: stripe, tier: 2}
```
`board` is the token in `https://boards-api.greenhouse.io/v1/boards/<board>/jobs`. Tiers in
`preferences.yaml → company_tiers` override the `tier` here.

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

## Job package format (v1, stable contract for the future resume builder)

The **Generate Resume** button downloads `Company_Role_JobID.md`, copies it to the clipboard, and sets the job's
status to "package exported". Format:

```markdown
---
package_format: 1
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
## My matching evidence
## Gaps
## Flags
```

The front-matter keys and section names/order are fixed (`output/packages.py`: `FRONT_MATTER_KEYS`,
`SECTIONS`). JD lines that look like Markdown headings are escaped (`\## ...`) so they can't be mistaken for
sections. `parse_package()` is the reference parser.

## Data & storage

| Path | Contents |
|---|---|
| `data/jobs.db` | SQLite: `jobs`, `job_sources` (per-source presence), `runs`, `source_status`, `embeddings` cache, `resume_versions` |
| `data/jd/` | full JD text per posting |
| `data/logs/jobsearch.log` | JSON-lines log, rotated at 2 MB × 5 |
| `dashboard/data.js` | generated `window.META` / `window.JOBS` |

Dedupe: exact on (company, source job id); fuzzy cross-source on company + normalized title + location.
A JD whose text hash changes is marked **updated**. A job is **closed** after 2 consecutive *successful* fetches
that don't list it; a failed fetch never closes anything. A closed job that reappears counts as a repost.

## Outbound requests

| Request | When | Data sent |
|---|---|---|
| `GET boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true` | each run, per Greenhouse company | none (public API; User-Agent identifies the tool) |
| Hugging Face model download (`sentence-transformers/all-MiniLM-L6-v2`) | first run only; later runs load from the local cache | none |

The dashboard makes no network calls (enforced by its Content-Security-Policy). Links you click (apply,
LinkedIn search) open in your browser as normal.

## Tests

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q
```
All test data is mock data labeled DEMO (`tests/fixtures/`). Tests need no network and no embedding model.

## Troubleshooting

- `No module named 'jobsearch'`: use `scripts/run.sh`, or `source .venv/bin/activate` (which sets `PYTHONPATH`).
- A company shows as failed: check `data/logs/jobsearch.log`; verify the board token at
  `https://boards-api.greenhouse.io/v1/boards/<board>/jobs`.
