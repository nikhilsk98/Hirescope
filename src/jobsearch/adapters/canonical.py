"""Canonical posting keys from URLs, so the same job found via different sources dedupes exactly.

    greenhouse:<job id>                  (Greenhouse ids are global)
    lever:<posting uuid>
    ashby:<posting uuid>
    workday:<tenant>:<job path slug>     (e.g. software-engineer_jr2015623)
    smartrecruiters:<posting id>
    amazon:<job id>
    rippling:<posting uuid>
    workable:<shortcode>
    jibe:<host>:<job id>                 (careers.amd.com/careers-home/jobs/91180 = careers.amd.com/jobs/91180?icims=1)
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

# Eightfold sites in config/companies.yaml: host -> the domain= value their job ids belong to
EIGHTFOLD_DOMAINS = {"apply.careers.microsoft.com": "microsoft.com", "careers.qualcomm.com": "qualcomm.com"}
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def workday_key(tenant: str, slug: str) -> str:
    """"..._R260021450-1" is the same requisition re-posted (another location): same job, same key (the workday
    adapter and links to the posting from elsewhere must agree)."""
    return f"workday:{tenant.lower()}:{re.sub(r'(_[a-z]{0,4}-?\d{4,})-\d{1,2}$', r'\1', slug.lower())}"


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
        if host.endswith("myworkdaysite.com") and (t := re.match(r"^/(?:[a-z]{2}-[A-Z]{2}/)?recruiting/([^/]+)/", path)):
            tenant = t.group(1)                # wd1.myworkdaysite.com/recruiting/{tenant}/{site}/job/…
        if m := re.search(r"/job/(?:[^/]+/)*([^/?#]+)", path):
            return workday_key(tenant, m.group(1))
    if "smartrecruiters.com" in host and (m := re.search(r"/(\d{6,})", path)):
        return f"smartrecruiters:{m.group(1)}"
    if host.endswith("amazon.jobs") and (m := re.search(r"/jobs/(\d+)", path)):
        return f"amazon:{m.group(1)}"
    if host == "ats.rippling.com" and (m := re.search(_UUID, path)):
        return f"rippling:{m.group(0)}"
    if host.endswith("workable.com") and (m := re.search(r"/j/([0-9A-F]{6,12})(?:/|$)", path, re.I)):
        return f"workable:{m.group(1).upper()}"
    if host.endswith("oraclecloud.com") and (m := re.search(r"/sites/[A-Za-z0-9_]+/(?:job|requisitions/preview)/(\d+)", path)):
        return f"oracle_cloud:{host.split('.')[0]}:{m.group(1)}"
    if host.endswith(".bamboohr.com") and (m := re.search(r"/careers/(\d+)", path)):
        return f"bamboohr:{host.split('.')[0]}:{m.group(1)}"
    if host.endswith(".applytojob.com") and (m := re.search(r"/apply/([A-Za-z0-9]{6,})", path)):
        return f"jazzhr:{m.group(1)}"
    if host == "recruiting.paylocity.com" and (m := re.search(r"/jobs/details/(\d+)", path, re.I)):
        return f"paylocity:{m.group(1)}"
    if "/JobDetail/" in path and (m := re.search(r"/JobDetail/(?:[^/]+/)?(\d+)/?$", path)):
        return f"avature:{host}:{m.group(1)}"
    # Eightfold career sites (the eightfold adapter's key needs the site's domain= value)
    if (m := re.match(r"^/careers/job/(\d+)", path)) and (dom := (qs.get("domain") or [None])[0] or EIGHTFOLD_DOMAINS.get(host)):
        return f"eightfold:{dom}:{m.group(1)}"
    # Jibe career sites (the jibe adapter's key): the site's own job page, or the same page linked with ?icims=1
    if (m := re.match(r"^(?:/[a-z]{2}(?:-[a-z]{2})?)?/careers-home/jobs/(\d+)", path)) or \
            ("icims" in qs and (m := re.match(r"^/jobs/(\d+)", path))):
        return f"jibe:{host.removeprefix('www.')}:{m.group(1)}"
    return None


def normalized(key: str | None) -> str | None:
    """A stored key in today's form (older Workday keys kept the "-1" repost suffix)."""
    if key and key.startswith("workday:") and key.count(":") >= 2:
        _, tenant, slug = key.split(":", 2)
        return workday_key(tenant, slug)
    return key
