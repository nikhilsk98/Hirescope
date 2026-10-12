"""Canonical posting keys from URLs, so the same job found via different sources dedupes exactly.

    greenhouse:<job id>                  (Greenhouse ids are global)
    lever:<posting uuid>
    ashby:<posting uuid>
    workday:<tenant>:<job path slug>     (e.g. software-engineer_jr2015623)
    smartrecruiters:<posting id>
    amazon:<job id>
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def canonical_key(url: str | None) -> str | None:
    if not url:
        return None
    try:
        u = urlparse(url.strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    path = u.path
    qs = parse_qs(u.query)
    if "gh_jid" in qs:  # company-hosted Greenhouse embed
        return f"greenhouse:{qs['gh_jid'][0]}"
    if "greenhouse.io" in host:
        if m := re.search(r"/jobs/(\d+)", path):
            return f"greenhouse:{m.group(1)}"
        if "token" in qs:
            return f"greenhouse:{qs['token'][0]}"
    if host.endswith("lever.co") and (m := re.search(_UUID, path)):
        return f"lever:{m.group(0)}"
    if host.endswith("ashbyhq.com") and (m := re.search(_UUID, path)):
        return f"ashby:{m.group(0)}"
    if host.endswith("myworkdayjobs.com") or host.endswith("myworkdaysite.com"):
        tenant = host.split(".")[0]
        if m := re.search(r"/job/(?:[^/]+/)*([^/?#]+)", path):
            # "..._R260021450-1" is the same requisition re-posted (another location): same job
            slug = re.sub(r"(_[a-z]{0,4}-?\d{4,})-\d{1,2}$", r"\1", m.group(1).lower())
            return f"workday:{tenant}:{slug}"
    if "smartrecruiters.com" in host and (m := re.search(r"/(\d{6,})", path)):
        return f"smartrecruiters:{m.group(1)}"
    if host.endswith("amazon.jobs") and (m := re.search(r"/jobs/(\d+)", path)):
        return f"amazon:{m.group(1)}"
    return None
