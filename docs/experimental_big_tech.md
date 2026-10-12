# Experimental: Google, Apple, Meta careers sites (investigation, 2026-10-12)

Goal: could HireScope read these three careers sites directly, the way it reads Greenhouse / Workday boards?
Rule we hold to: only official public APIs, feeds or pages that `robots.txt` **and** the site's terms allow.
No logins, no CAPTCHAs, no private/undocumented APIs scraped against the terms.

## Findings (checked live, with HireScope's User-Agent, a handful of requests)

| Company | What exists | robots.txt | Terms | Verdict |
|---|---|---|---|---|
| **Google** (`google.com/about/careers/applications/jobs/results`) | HTML results and job pages; no public job API or careers sitemap (`/about/careers/applications/sitemap.xml` → 404) | `Disallow: /about/careers/applications/jobs/results` (covers the results **and** every job page under it, plus `?page=`) | — | **Not allowed.** No adapter. |
| **Apple** (`jobs.apple.com`) | A jobs sitemap (`/sitemap/sitemap-jobs-en-us.xml` → 200) and an internal search API used by the page | none (`/robots.txt` redirects to "page not found") | Apple Website Terms of Use: *"You may not use any 'deep-link', 'page-scrape', 'robot', 'spider' or other automatic device … to access, acquire, copy or monitor any portion of the Site"* | **Not allowed** (terms). No adapter. |
| **Meta** (`metacareers.com`) | `Sitemap: …/jobsearch/sitemap.xml` (job-detail URLs, lastmod) | allows `User-agent: *` except a few paths, **but** its header says: *"Collection of data on Facebook through automated means is prohibited unless you have express written permission"* | same notice | **Not allowed** without written permission. No adapter. |

## How these companies are covered instead

- **SimplifyJobs feeds** (already on every run) list their intern and new-grad roles with the official apply link.
  On 2026-10-12 the DB held Apple 74, Google 23, Meta 15 open listings from those feeds (11 / 2 / 3 visible after
  your rules — most are summer-only internships, which the co-op track filters).
- **LinkedIn job-alert emails** (Phase 5): create alerts for "Software Engineer" at Google / Apple / Meta; the alert
  emails in your own inbox are parsed (read-only IMAP). Nothing is fetched from LinkedIn.
- **Add job by URL**: paste a Google / Apple / Meta posting you found; HireScope can't read the page (robots/terms),
  so it offers manual entry with a pasted JD, then scores and tracks it like any other job.
- **Hacker News Who's Hiring** occasionally includes teams at these companies.

## If this changes
Re-check `robots.txt` and the terms before building anything. An official public jobs API (Google Cloud Talent
Solution is a product for employers, not a public index of Google's own jobs) or an RSS/JSON feed allowed by the
terms would be added as a normal adapter with conditional requests and the usual rate limits.
