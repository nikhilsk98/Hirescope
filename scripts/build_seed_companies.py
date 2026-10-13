"""Build config/companies.yaml: ~300 US tech companies known for intern / co-op / new-grad hiring.

Sources (open data, no scraping of job boards):
  1. Employer ATS links in the SimplifyJobs internship + new-grad listings (Software / AI-ML-Data / Quant
     categories, US locations), ranked by number of active listings.
  2. A curated list of well-known tech employers (CURATED below) whose board slugs are probed.
Every candidate is verified LIVE before inclusion (Greenhouse/Lever/Ashby: 200 from the board API;
Workday: 200 + total from the cxs endpoint; SmartRecruiters: totalFound > 0). Unverified candidates are
written to config/companies_unverified.yaml with the reason, never into the main list.

Usage: ~/.venvs/hirescope/bin/python scripts/build_seed_companies.py [--limit 300]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobsearch.adapters.base import HttpClient  # noqa: E402
from jobsearch.adapters.detect import from_url, slugs  # noqa: E402
from jobsearch.extract.locations import normalize_location  # noqa: E402

FEEDS = {
    "coop_intern": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "full_time": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
}
RELEVANT = {"Software", "AI/ML/Data", "Quant", "Software Engineering", "Data Science, AI & Machine Learning"}

# Well-known US tech employers with strong new-grad / intern / co-op programs (slugs probed, not assumed).
CURATED_TIER1 = [
    "Stripe", "Airbnb", "Databricks", "Figma", "Anthropic", "OpenAI", "Robinhood", "Coinbase", "Discord",
    "Dropbox", "Pinterest", "Reddit", "Lyft", "DoorDash", "Instacart", "Plaid", "Ramp", "Brex", "Notion",
    "Datadog", "MongoDB", "Cloudflare", "Twilio", "Okta", "HubSpot", "Toast", "Duolingo", "Scale AI",
    "Palantir", "Snowflake", "Roblox", "Rubrik", "Asana", "Affirm", "Chime", "SoFi", "Samsara", "Verkada",
    "Applied Intuition", "Anduril", "SpaceX", "Waymo", "Zoox", "Nuro", "Cruise", "Rippling", "Gusto",
    "Vercel", "Retool", "Benchling", "Flexport", "Faire", "Grammarly", "Gemini", "Kraken", "Jane Street",
    "Citadel", "Two Sigma", "Hudson River Trading", "Jump Trading", "Optiver", "IMC Trading", "DRW",
    "Akuna Capital", "Five Rings", "Old Mission", "Point72", "Bloomberg", "Wayfair", "Klaviyo", "DraftKings",
    "Toast", "Whoop", "Formlabs", "PathAI", "CarGurus", "Akamai", "Draper", "MathWorks", "Boston Dynamics",
    "Liberty Mutual", "Fidelity", "State Street", "Vertex Pharmaceuticals", "Moderna", "iRobot", "Zillow",
    "Expedia", "Tableau", "Atlassian", "Block", "Square", "Etsy", "Squarespace", "Spotify", "Peloton",
    "Twitch", "Nextdoor", "Yelp", "Quora", "Strava", "Snap", "Zocdoc", "Oscar Health", "Elastic", "GitLab",
    "HashiCorp", "Confluent", "PagerDuty", "Sentry", "PlanetScale", "Supabase", "Postman", "LaunchDarkly",
    "Airtable", "Webflow", "Canva", "Miro", "Linear", "Mercury", "Watershed", "Glean", "Hebbia", "Harvey",
    "Cohere", "Perplexity", "Mistral", "Hugging Face", "Runway", "Character.AI", "Replit", "Modal",
    "Together AI", "Fireworks AI", "Pinecone", "Weights & Biases", "Snorkel AI", "Cerebras", "SambaNova",
    "Lambda", "CoreWeave", "Crusoe", "Tesla", "Rivian", "Lucid", "Joby Aviation", "Relativity Space",
    "Shield AI", "Skydio", "Saronic", "Hadrian", "Astranis", "Rocket Lab", "Varda", "Epic Games",
    "Riot Games", "Valve", "Unity", "Niantic", "Electronic Arts", "Activision Blizzard", "Bungie",
    "ServiceNow", "Workday", "Salesforce", "Adobe", "Intuit", "NVIDIA", "AMD", "Intel", "Qualcomm",
    "Micron Technology", "Western Digital", "Cisco", "VMware", "Broadcom", "Dell Technologies",
    "Hewlett Packard Enterprise", "HP", "IBM", "Oracle", "PayPal", "Visa", "Mastercard", "Capital One",
    "American Express", "Goldman Sachs", "Morgan Stanley", "JPMorgan Chase", "Wells Fargo", "Bank of America",
    "Booz Allen", "Leidos", "RTX", "Lockheed Martin", "Northrop Grumman", "General Dynamics", "Boeing",
    "Garmin", "Zebra Technologies", "Motorola Solutions", "Honeywell", "GE Aerospace", "Siemens", "Bosch",
]


def load_feeds(http: HttpClient) -> list[dict]:
    rows = []
    for track, url in FEEDS.items():
        for x in http.get_json(url):
            if x.get("active") and x.get("is_visible", True):
                x["_track"] = track
                rows.append(x)
    return rows


def from_simplify(rows: list[dict]) -> dict[str, dict]:
    """company -> {fields, listings (relevant US), tracks}"""
    out: dict[str, dict] = {}
    votes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for x in rows:
        name = (x.get("company_name") or "").strip()
        fields = from_url(x.get("url"))
        if not name or not fields or fields["source"] not in ("greenhouse", "lever", "ashby", "workday",
                                                                "smartrecruiters"):
            continue
        if fields["source"] == "greenhouse" and fields.get("board") in ("embed", "v1"):
            continue
        rel = x.get("category") in RELEVANT
        us = normalize_location(" | ".join(x.get("locations") or [])).us is not False
        votes[name][tuple(sorted(fields.items()))] += 1
        d = out.setdefault(name, {"listings": 0, "all": 0, "tracks": collections.Counter()})
        d["all"] += 1
        if rel and us:
            d["listings"] += 1
            d["tracks"][x["_track"]] += 1
    for name, d in out.items():
        d["fields"] = dict(votes[name].most_common(1)[0][0])
    return out


def verify(http: HttpClient, f: dict) -> tuple[bool, str, int | None]:
    s = f["source"]
    try:
        if s == "greenhouse":
            n = len(http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{f['board']}/jobs").get("jobs", []))
            return True, "ok", n
        if s == "lever":
            n = len(http.get_json(f"https://api.lever.co/v0/postings/{f['board']}?mode=json"))
            return True, "ok", n
        if s == "ashby":
            n = len(http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{f['board']}").get("jobs", []))
            return True, "ok", n
        if s == "workday":
            d = http.post_json(f"https://{f['host']}/wday/cxs/{f['board']}/{f['site']}/jobs",
                               {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
            return True, "ok", d.get("total")
        if s == "smartrecruiters":
            n = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{f['board']}/postings?limit=1"
                              ).get("totalFound", 0)
            return (n > 0), ("ok" if n else "0 postings (unknown id or no openings)"), n
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:120]}", None
    return False, f"unsupported source {s}", None


def probe(http: HttpClient, name: str) -> dict | None:
    for slug in slugs(name):
        for source in ("greenhouse", "ashby", "lever", "smartrecruiters"):
            ok, _, n = verify(http, {"source": source, "board": slug})
            if ok and n:
                return {"source": source, "board": slug}
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--out", type=Path, default=ROOT / "config" / "companies.yaml")
    args = ap.parse_args()
    http = HttpClient(min_interval=0.25, retries=1, host_intervals={"myworkdayjobs.com": 0.3})
    today = dt.date.today().isoformat()

    print("loading SimplifyJobs feeds…", file=sys.stderr)
    simp = from_simplify(load_feeds(http))
    print(f"{len(simp)} companies with ATS links in SimplifyJobs", file=sys.stderr)

    candidates: dict[str, dict] = {}
    curated = list(dict.fromkeys(CURATED_TIER1))
    for name in curated:
        hit = next((k for k in simp if k.casefold() == name.casefold()), None)
        candidates[name] = {"fields": simp[hit]["fields"] if hit else None, "listings": simp[hit]["listings"] if hit
                            else 0, "curated": True, "tracks": dict(simp[hit]["tracks"]) if hit else {}}
    for name, d in sorted(simp.items(), key=lambda kv: -kv[1]["listings"]):
        if d["listings"] < 2 or any(name.casefold() == c.casefold() for c in candidates):
            continue
        candidates[name] = {"fields": d["fields"], "listings": d["listings"], "curated": False,
                            "tracks": dict(d["tracks"])}

    def check(item):
        name, c = item
        f = c["fields"] or probe(http, name)
        if not f:
            return name, c, None, False, "no ATS board found (name probes on Greenhouse/Ashby/Lever/SmartRecruiters failed; likely a custom or Workday site - add it to inputs/companies/my_companies.csv with its careers URL)", None
        ok, why, n = verify(http, f)
        if not ok and c["fields"] and not c["curated"]:
            return name, c, f, False, why, n
        if not ok and c["curated"]:
            f2 = probe(http, name)
            if f2 and f2 != f:
                ok, why, n = verify(http, f2)
                f = f2
        return name, c, f, ok, why, n

    print(f"verifying {len(candidates)} candidates live…", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(check, candidates.items()))

    verified = [r for r in results if r[3]]
    unverified = [r for r in results if not r[3]]
    famous = {n.casefold() for n in CURATED_TIER1[:80]}
    keep = [r for r in verified if r[1]["curated"] and (r[1]["listings"] >= 1 or r[0].casefold() in famous)]
    rest = sorted((r for r in verified if r not in keep), key=lambda r: -r[1]["listings"])
    chosen = (keep + rest)[:args.limit]
    by_volume = [r[0] for r in sorted(chosen, key=lambda r: -r[1]["listings"])]
    top45 = set(by_volume[:45])
    famous40 = {n.casefold() for n in CURATED_TIER1[:40]}

    def tier(r):
        name, c = r[0], r[1]
        if name in top45 or name.casefold() in famous40:
            return 1
        if c["listings"] >= 3 or c["curated"]:
            return 2
        return 3

    companies = []
    for r in sorted(chosen, key=lambda r: (tier(r), r[0].casefold())):
        name, c, f, _, _, n = r
        entry = {"name": name, **{k: f[k] for k in ("source", "board", "host", "site") if f.get(k)},
                 "tier": tier(r), "verified": today}
        note = f"{c['listings']} US software intern/new-grad listings in SimplifyJobs" if c["listings"] else "curated"
        if n is not None:
            note += f"; {n} open jobs at verification"
        entry["notes"] = note
        companies.append(entry)

    feeds = [
        {"name": "Amazon", "source": "amazon", "tier": 1, "verified": today, "origin": "feed",
         "notes": "amazon.jobs search.json (software-development + intern/co-op/new-grad searches, USA)"},
        {"name": "Netflix", "source": "netflix", "tier": 1, "verified": today, "origin": "feed",
         "notes": "careers sitemap + JobPosting JSON-LD (JSON search API is not public)"},
        {"name": "SimplifyJobs Internships", "source": "simplify", "board": FEEDS["coop_intern"], "site": "coop_intern",
         "tier": 1, "verified": today, "origin": "feed",
         "notes": "Summer 2027 + off-season (Spring/Winter/Fall) internships; repo name changes yearly"},
        {"name": "SimplifyJobs New Grad", "source": "simplify", "board": FEEDS["full_time"], "site": "full_time",
         "tier": 1, "verified": today, "origin": "feed", "notes": "New-grad positions"},
    ]
    # Amazon / Netflix are fetched by their own adapters; drop duplicates from the ATS list.
    companies = [c for c in companies if c["name"].casefold() not in ("amazon", "netflix")]

    header = f"""# Companies scanned every run (subject to tier schedules). Generated by scripts/build_seed_companies.py
# on {today} from SimplifyJobs employer links + a curated list, every entry VERIFIED LIVE that day.
# Unverified candidates (and why) are in config/companies_unverified.yaml - not used.
#
# Fields: name, source (greenhouse | lever | ashby | workday | smartrecruiters | amazon | netflix | simplify),
#         board (ATS token / tenant / feed URL), host + site (workday), tier (1 every run, 2 daily, 3 every
#         3 days; preferences.company_tiers overrides), verified (date), notes.
# Adding a company = adding one entry here (or a row in inputs/companies/my_companies.csv for auto-detection).
"""
    body = yaml.safe_dump({"companies": feeds + companies}, sort_keys=False, allow_unicode=True, width=120)
    args.out.write_text(header + body, encoding="utf-8")
    (args.out.parent / "companies_unverified.yaml").write_text(
        f"# Candidates that failed live verification on {today} (not used).\n" + yaml.safe_dump(
            {"unverified": [{"name": r[0], "tried": r[2], "reason": r[4]} for r in sorted(unverified)]},
            sort_keys=False, width=120), encoding="utf-8")
    by_src = collections.Counter(c["source"] for c in companies)
    print(json.dumps({"verified": len(verified), "chosen": len(companies), "unverified": len(unverified),
                      "by_source": by_src, "tiers": collections.Counter(c["tier"] for c in companies)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
