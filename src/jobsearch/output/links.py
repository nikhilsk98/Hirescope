"""Dead-link check for apply URLs: rate-limited, cached 24h, marks links broken instead of hiding jobs.

broken  = 404 / 410, or the ATS redirected to its "job not found" page (Greenhouse ?error=true)
ok      = 2xx/3xx landing on the posting
unknown = blocked or flaky (401/403/429/5xx/timeouts) - never treated as broken
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

from ..adapters.base import HttpClient
from ..storage import db

log = logging.getLogger(__name__)
CACHE_HOURS = 24


def classify(status: int | None, final_url: str | None) -> tuple[bool | None, str]:
    if status is None:
        return None, "no response"
    if status in (404, 410):
        return False, f"HTTP {status}"
    if final_url and "greenhouse.io" in final_url and "error=true" in final_url:
        return False, "Greenhouse says the job is no longer available"
    if 200 <= status < 400:
        return True, f"HTTP {status}"
    return None, f"HTTP {status} (not treated as broken)"


def check_url(client: HttpClient, url: str) -> tuple[int | None, str | None]:
    client._wait_for_host(httpx.URL(url).host)
    try:
        with client.client.stream("GET", url, headers={"Accept": "text/html,*/*"}, timeout=20) as r:
            return r.status_code, str(r.url)
    except httpx.HTTPError:
        return None, None


def check_links(conn, client: HttpClient, jobs: list[dict], limit: int = 300, workers: int = 8) -> dict:
    now = datetime.now(timezone.utc)
    todo, results = [], {"checked": 0, "broken": 0, "cached": 0}
    for j in sorted((j for j in jobs if not j.get("filtered") and j.get("apply_url")),
                    key=lambda j: -(j.get("score") or 0))[:limit]:
        url = j["apply_url"]
        cached = db.get_link_check(conn, url)
        if cached and now - datetime.fromisoformat(cached["checked_at"]) < timedelta(hours=CACHE_HOURS):
            ok = None if cached["ok"] is None else bool(cached["ok"])
            db.save_scores(conn, j["id"], link_status={True: "ok", False: "broken", None: "unknown"}[ok],
                           link_checked_at=cached["checked_at"])
            results["cached"] += 1
            continue
        todo.append(j)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for j, (status, final) in zip(todo, pool.map(lambda j: check_url(client, j["apply_url"]), todo)):
            ok, note = classify(status, final)
            db.set_link_check(conn, j["apply_url"], status, ok, final, note)
            db.save_scores(conn, j["id"], link_status={True: "ok", False: "broken", None: "unknown"}[ok],
                           link_checked_at=db.utcnow())
            results["checked"] += 1
            results["broken"] += ok is False
    conn.commit()
    return results
