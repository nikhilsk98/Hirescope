"""LinkedIn job-alert emails, read from your own mailbox over IMAP (read-only). Never fetches LinkedIn pages.

Setup (.env): IMAP_HOST (default imap.gmail.com), IMAP_USER, IMAP_APP_PASSWORD (a Gmail app password, not your
account password), optional IMAP_FOLDER (default INBOX). Messages from LinkedIn's job-alert senders received in
the last `company.board` days (default 21) are parsed; nothing is marked read, moved or deleted.

Each alert lists jobs as title / company / location blocks with a link to linkedin.com/jobs/view/<id>. Both the
plain-text and the HTML part are understood. Listings carry no JD (we don't open LinkedIn), so they score on title
and preferences, labeled "No JD"; when an employer's own ATS lists the same role (company + title + location), the
official record takes over. A job that stops appearing in recent alerts ages out like any other missing listing.
"""
from __future__ import annotations

import email
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import parsedate_to_datetime

from .. import secrets
from ..config import Company
from .base import Adapter, Capabilities, FetchContext, FetchResult, RawJob, SourceError

log = logging.getLogger(__name__)

SENDERS = ("jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com", "jobs-listings@linkedin.com")
JOB_LINK = re.compile(r"https?://(?:[a-z]+\.)?linkedin\.com/(?:comm/)?jobs/view/(\d{6,})[^\s\"'<>)]*", re.I)
NOISE = re.compile(r"^(view job|apply|easy apply|actively recruiting|promoted|be an early applicant|\d+ (?:company )?"
                   r"(?:alumni|connections?|school alumni)|new|see all jobs|unsubscribe|manage alerts?|your job alert.*|"
                   r"this company is actively hiring|\d+ applicants?|.*ago)$", re.I)
DEFAULT_DAYS = 21


def _parts(msg: Message) -> tuple[str, str]:
    text, htm = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AssertionError):
            continue
        if ctype == "text/plain" and not text:
            text = body
        elif ctype == "text/html" and not htm:
            htm = body
    return text, htm


def _html_lines(htm: str) -> list[str]:
    """HTML -> text lines, with each job link kept on its own line as 'LINK <url>' right after its anchor text."""
    htm = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", htm)
    htm = re.sub(r"(?is)<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                 lambda m: f"\n{m.group(2)}\nLINK {html.unescape(m.group(1))}\n", htm)
    htm = re.sub(r"(?i)<br\s*/?>|</(p|div|td|tr|li|h\d|table)>", "\n", htm)
    txt = html.unescape(re.sub(r"<[^>]+>", " ", htm))
    return [" ".join(x.split()) for x in txt.splitlines() if x.strip()]


def _clean(line: str) -> str:
    return " ".join(line.replace(" ", " ").split()).strip(" ·•|-–")


def parse_alert(msg: Message) -> list[dict]:
    """One alert email -> [{job_id, title, company, location, url}]. Unknown layouts yield fewer jobs, never junk:
    a job needs a LinkedIn job id, a title and a company."""
    text, htm = _parts(msg)
    jobs: dict[str, dict] = {}
    if htm:
        lines = _html_lines(htm)
        for i, ln in enumerate(lines):
            if not ln.startswith("LINK "):
                continue
            m = JOB_LINK.match(ln[5:])
            if not m or m.group(1) in jobs:
                continue
            # anchor text (title, sometimes an image alt) is just above; company and location follow
            title = _clean(lines[i - 1]) if i else ""
            after = []
            for x in lines[i + 1:i + 8]:
                if x.startswith("LINK "):
                    if after:
                        break
                    continue
                x = _clean(x)
                if x and not NOISE.match(x) and x != title:
                    after.append(x)
                if len(after) == 2:
                    break
            if title and not NOISE.match(title) and after:
                jobs[m.group(1)] = {"job_id": m.group(1), "title": title, "company": after[0],
                                    "location": after[1] if len(after) > 1 else None}
    if text and not jobs:
        block: list[str] = []
        for raw in text.splitlines():
            ln = _clean(raw)
            m = JOB_LINK.search(raw)
            if m:
                body = [x for x in block if not NOISE.match(x)]
                if len(body) >= 2 and m.group(1) not in jobs:
                    jobs[m.group(1)] = {"job_id": m.group(1), "title": body[-3] if len(body) >= 3 else body[-2],
                                        "company": body[-2] if len(body) >= 3 else body[-1],
                                        "location": body[-1] if len(body) >= 3 else None}
                block = []
            elif ln and not set(ln) <= set("-=_ "):
                block.append(ln)
    for j in jobs.values():
        j["url"] = f"https://www.linkedin.com/jobs/view/{j['job_id']}/"
    return list(jobs.values())


def is_alert(msg: Message) -> bool:
    sender = (msg.get("From") or "").lower()
    return any(s in sender for s in SENDERS)


class LinkedInAlertsAdapter(Adapter):
    """company.board = how many days of alerts to read (default 21)."""
    name = "linkedin_alerts"
    capabilities = Capabilities(full_description=False, posted_date=True, pagination=False, official=False,
                                multi_company=True)
    disabled = False   # set by `--no-email`

    @classmethod
    def unavailable(cls, company: Company) -> str | None:
        if cls.disabled:
            return "email sources skipped (--no-email)"
        missing = [k for k in ("IMAP_USER", "IMAP_APP_PASSWORD") if not secrets.get(k)]
        return f"not set up: add {', '.join(missing)} to .env (see README → LinkedIn alerts)" if missing else None

    def fetch(self, company: Company, ctx: FetchContext, mailbox=None) -> FetchResult:
        days = int(company.board or DEFAULT_DAYS)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        messages = mailbox if mailbox is not None else self._read_mailbox(since)
        jobs: dict[str, RawJob] = {}
        n_alerts = 0
        for raw in messages:
            msg = raw if isinstance(raw, Message) else email.message_from_bytes(raw)
            if not is_alert(msg):
                continue
            n_alerts += 1
            try:
                received = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
            except (TypeError, ValueError):
                received = None
            for j in parse_alert(msg):
                prev = jobs.get(j["job_id"])
                if prev and prev.posted_date and received and prev.posted_date <= received:
                    continue   # keep the earliest alert's date
                jobs[j["job_id"]] = RawJob(
                    company=j["company"], job_id=j["job_id"], title=j["title"], source=self.name,
                    source_url="email:linkedin-job-alert", apply_url=j["url"], location=j["location"],
                    description=None if j["job_id"] in ctx.known else "", posted_date=received,
                    extra={"via": "LinkedIn job alert email", "alert_date": received.isoformat() if received else None})
        return FetchResult(jobs=list(jobs.values()), notes=[f"{n_alerts} alert emails from the last {days} days, "
                                                             f"{len(jobs)} jobs"])

    def _read_mailbox(self, since) -> list[bytes]:
        from imapclient import IMAPClient   # imported lazily: only needed when email is set up
        host = secrets.get("IMAP_HOST", "imap.gmail.com")
        user, pw = secrets.get("IMAP_USER"), secrets.get("IMAP_APP_PASSWORD")
        folder = secrets.get("IMAP_FOLDER", "INBOX")
        try:
            with IMAPClient(host, ssl=True, timeout=30) as box:
                box.login(user, pw)
                box.select_folder(folder, readonly=True)          # read-only: nothing is marked read
                ids: set[int] = set()
                for s in SENDERS:
                    ids |= set(box.search(["FROM", s, "SINCE", since]))
                if not ids:
                    return []
                got = box.fetch(sorted(ids)[-300:], ["BODY.PEEK[]"])
                return [d[b"BODY[]"] for d in got.values() if b"BODY[]" in d]
        except Exception as e:  # noqa: BLE001 - never leak credentials into logs
            raise SourceError(f"IMAP {host}: {secrets.redact(str(e), 'IMAP_APP_PASSWORD', 'IMAP_USER')}") from None
