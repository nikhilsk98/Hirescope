"""Hacker News "Ask HN: Who is hiring?" (monthly), via HN's official read-only API (hacker-news.firebaseio.com).

The newest thread is found among the `whoishiring` account's submissions. Each top-level comment is one employer
post, conventionally `Company | Role(s) | Location | Remote/Onsite | ... | URL` followed by a description.
One RawJob per post: title = the role(s) named in the header (posts with no software role are skipped), location
from the header, apply link = the first link to a careers page / ATS (else the HN comment), JD = the post text.
Level, YOE, location and work-authorization rules then apply as for any job (entry–mid only). Comments are
fetched once; later runs only fetch new ones. Last month's posts age out when a new thread starts.
"""
from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from datetime import datetime, timezone

from ..config import Company
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError
from .canonical import canonical_key

API = "https://hacker-news.firebaseio.com/v0"
THREAD_TITLE = re.compile(r"^Ask HN: Who is hiring\?", re.I)


@lru_cache(maxsize=1)
def _role_re() -> re.Pattern:
    """A header segment naming a software role: config/title_rules.yaml -> posting_role_words."""
    from ..config import load_title_rules
    words = load_title_rules().posting_role_words
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.I) if words else re.compile(r"(?!)")
NOT_ROLE = re.compile(r"\b(remote|onsite|on-site|hybrid|full[- ]?time|part[- ]?time|contract|visa|salary|equity|"
                      r"\$|€|£|https?://|www\.)", re.I)
LOCATION = re.compile(r"\b(remote|onsite|on-site|hybrid|usa?|united states|new york|nyc|san francisco|sf|bay area|"
                      r"seattle|boston|atlanta|austin|chicago|los angeles|denver|[A-Z][a-z]+,\s*[A-Z]{2})\b")
APPLY_HINT = re.compile(r"(greenhouse\.io|lever\.co|ashbyhq\.com|myworkdayjobs|smartrecruiters|workable|"
                        r"/careers|/jobs|careers\.|jobs\.|/apply|wellfound|ycombinator\.com/companies)", re.I)
MAX_NEW_PER_RUN = 900


def comment_text(raw_html: str) -> tuple[str, list[str]]:
    """HN comment HTML -> (plain text with paragraphs as lines, links in order)."""
    links = [html.unescape(h) for h in re.findall(r'href="([^"]+)"', raw_html or "")]
    t = re.sub(r"(?i)<p>", "\n", raw_html or "")
    t = re.sub(r"(?i)<a\b[^>]*href=\"([^\"]+)\"[^>]*>.*?</a>", lambda m: html.unescape(m.group(1)), t)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    return "\n".join(ln.strip() for ln in t.splitlines() if ln.strip()), links


def parse_post(item: dict) -> dict | None:
    """One top-level comment -> {company, title, location, remote, apply_url, text} or None (not a software role)."""
    if not item or item.get("deleted") or item.get("dead") or not item.get("text"):
        return None
    text, links = comment_text(item["text"])
    header = text.split("\n", 1)[0]
    segs = [s.strip() for s in re.split(r"\s+\|\s+|\s*\|\s*", header) if s.strip()]
    if len(segs) < 2:
        return None
    company = re.sub(r"\s*\((?:YC|yc)[^)]*\)\s*$", "", segs[0]).strip()
    role = _role_re()
    if not company or len(company) > 80 or role.search(company) and len(segs) < 3:
        return None
    roles, locs = [], []
    for s in segs[1:]:
        if re.match(r"https?://", s):
            continue
        if role.search(s) and not (NOT_ROLE.search(s) and not re.search(r"engineer|developer|intern", s, re.I)):
            roles.append(s)
        elif LOCATION.search(s) or re.search(r"remote|onsite|hybrid", s, re.I):
            locs.append(s)
    if not roles:
        return None
    title = ", ".join(dict.fromkeys(roles))[:160]
    remote = any(re.search(r"\bremote\b", s, re.I) for s in locs)
    loc = " · ".join(s for s in locs if not re.fullmatch(r"(?i)(remote|onsite|on-site|hybrid)( \(.*\))?", s.strip()))
    if remote:
        loc = " · ".join(x for x in (loc, "Remote" if not re.search(r"(?i)\b(us|usa|united states)\b", " ".join(locs))
                                     else "Remote (US)") if x)
    apply = next((u for u in links if "ycombinator.com/item" not in u and APPLY_HINT.search(u)), None) or \
        next((u for u in links if "news.ycombinator.com" not in u), None)
    return {"company": company, "title": title, "location": loc or None, "remote": remote, "apply_url": apply,
            "text": text, "time": item.get("time")}


class HNHiringAdapter(Adapter):
    name = "hn_hiring"
    capabilities = Capabilities(full_description=True, posted_date=True, pagination=False, official=False,
                                multi_company=True)

    def latest_thread(self) -> dict:
        user = self.http.get_json(f"{API}/user/whoishiring.json")
        for sid in (user or {}).get("submitted", [])[:12]:
            item = self.http.get_json(f"{API}/item/{sid}.json")
            if item and THREAD_TITLE.match(item.get("title") or ""):
                return item
        raise SourceError("no 'Ask HN: Who is hiring?' thread among the latest whoishiring submissions")

    def fetch(self, company: Company, ctx: FetchContext) -> FetchResult:
        thread = self.latest_thread()
        kids = thread.get("kids") or []
        jobs: list[RawJob] = []
        seen: set[str] = set()
        skipped = 0
        cache_key = f"hn_hiring:skipped:{thread.get('id')}"   # non-software posts: never fetched twice
        not_sw = set(json.loads((ctx.pages.get(cache_key) or {}).get("payload") or "[]"))
        new = [k for k in kids if str(k) not in ctx.known and k not in not_sw]
        capped = len(new) > MAX_NEW_PER_RUN
        fetch_ids = set(new[:MAX_NEW_PER_RUN])
        for kid in kids:
            jid = str(kid)
            if jid in ctx.known:
                seen.add(jid)        # already stored: touched by the pipeline (posts are immutable enough)
                continue
            if kid not in fetch_ids:
                continue
            post = parse_post(self.http.get_json(f"{API}/item/{kid}.json"))
            if post is None:
                skipped += 1
                not_sw.add(kid)
                continue
            seen.add(jid)
            hn = f"https://news.ycombinator.com/item?id={kid}"
            jobs.append(RawJob(
                company=post["company"], job_id=jid, title=post["title"], source=self.name, source_url=hn,
                apply_url=post["apply_url"] or hn, location=post["location"], description=post["text"],
                posted_date=datetime.fromtimestamp(post["time"] or thread.get("time", 0), timezone.utc),
                canonical_key=canonical_key(post["apply_url"]),
                extra={"hn_thread": thread.get("title"), "hn_item": hn}))
        notes = [f"{thread.get('title')}: {len(kids)} posts, {len(fetch_ids)} fetched this run, {len(not_sw)} not "
                 f"software roles"]
        return FetchResult(jobs=jobs, seen_ids=seen, notes=notes,
                           pages={cache_key: {"lastmod": None, "payload": json.dumps(sorted(not_sw))}},
                           partial=f"{len(new) - MAX_NEW_PER_RUN} new posts left for the next run" if capped else None)
