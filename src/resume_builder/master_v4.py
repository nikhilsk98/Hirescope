"""master_resume.md v4 format → evidence bank (+ everything the v4 rules need).

The v4 master is written for people: a Default one-page snapshot, per-role fields ("- **Title:** …"), Metrics tables
with a Status column, Bullet banks (core / extended), "In my own words" quotes, Legacy bullets, Excluded items, a
Skills inventory with tiers, Certifications, and italic "Builder:" notes. This module reads it into the same
Entry / Item bank the rest of the Resume Builder uses, plus `v4` extras on the bank:

  snapshot      the Default one-page resume (roles, projects, bullets) — the starting point for every resume
  notes         every "Builder:" instruction, by section (instructions, never resume content)
  roles         per experience entry: titles (default + alternates), pick range, skill-only / barred terms
  projects      per project: display name, context line, link, in-progress, status table
  education     degree lines, graduation, GPA (or none), courses (only "Yes" rows), default courses
  skills        default rows, tiers (core / extended / self-reported / planned / concept), excluded, max self-reported
  certs         certifications with their rule (always / cloud roles or a JD term)
  contact       name, location, relocation line, phone, email, LinkedIn, GitHub
  overlaps      bullet groups of which a resume may use at most one ("A3 and C2 overlap")
  tracks        the target-role track map

Usable (rule 4): bank bullets and the snapshot; metrics with status verified / estimated (a bullet whose number comes
from an unverified / pending / planned metric is left out); never Legacy bullets or anything Excluded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import InputError
from .evidence import Entry, Item, _facts, _parse_dates, canonical, terms_in

USABLE = ("verified", "estimated")
UNUSABLE = ("unverified", "pending", "planned")
_BUILDER_ITALIC = re.compile(r"_\s*(?:\(?\s*)?Builder\b[^_]*_")
_BUILDER_INLINE = re.compile(r"\bBuilder\s*[:,][^\n]*$")
_VARIANT = re.compile(r"^(?:[A-Z][\w/ -]*?variant(?: of [^:]+)?|Combined variant):\s*", re.I)
_TRAIL_STATUS = re.compile(r"\s*[\[(_]\s*(unverified|pending|planned|estimated|verified)\s*[\])_]\s*$", re.I)


def is_v4(text: str) -> bool:
    return bool(re.search(r"^##\s+Default one-page resume", text, re.M)) and "#### Bullet bank" in text


def split_builder(line: str) -> tuple[str, list[str]]:
    """(content, [builder instructions]). Italic "_Builder: …_" segments and a trailing "Builder: …" sentence are
    instructions to the builder, never resume content."""
    notes = [re.sub(r"^_+|_+$", "", m.group(0)).strip() for m in _BUILDER_ITALIC.finditer(line)]
    rest = _BUILDER_ITALIC.sub("", line)
    if m := _BUILDER_INLINE.search(rest):
        notes.append(m.group(0).strip())
        rest = rest[:m.start()]
    return rest.rstrip(" -"), notes


def _md(s: str) -> str:
    """Plain text of a markdown fragment (bold / italic / code markers removed)."""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<![\w])_(.+?)_(?![\w])", r"\1", s)
    return s.replace("`", "").strip()


def _slug(s: str) -> str:
    s = s.casefold().replace("++", "pp").replace("+", "plus").replace("#", "sharp")   # C++ ≠ C, C# ≠ C
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _table(lines: list[str]) -> list[dict]:
    rows = [ln for ln in lines if ln.strip().startswith("|")]
    if len(rows) < 2:
        return []
    head = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    out = []
    for r in rows[2:]:
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        out.append({h.casefold(): (cells[i] if i < len(cells) else "") for i, h in enumerate(head)})
    return out


def split_skills(s: str) -> list[str]:
    """'Node.js, Express.js' → two; 'AWS (EC2, RDS, …)' stays one; 'C++ (C++17)' → 'C++'."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    clean = []
    for x in out:
        x = _md(x).strip().strip('"“”')
        x = re.sub(r"^Generic\s+", "", x).strip('"“” ')
        m = re.match(r"^(.+?)\s*\((.+)\)$", x)
        if m and not m.group(1).upper().startswith("AWS"):
            x = m.group(1)          # C++ (C++17), scikit-learn (TF-IDF, …): the name; AWS (EC2, …) keeps its services
        if x:
            clean.append(x)
    return clean


@dataclass
class V4:
    snapshot: dict = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)
    roles: dict[str, dict] = field(default_factory=dict)
    projects: dict[str, dict] = field(default_factory=dict)
    education: dict[str, dict] = field(default_factory=dict)
    skills: dict = field(default_factory=dict)
    certs: list[dict] = field(default_factory=list)
    contact: dict = field(default_factory=dict)
    overlaps: list[list[str]] = field(default_factory=list)
    tracks: str = ""
    excluded_roles: list[str] = field(default_factory=list)
    excluded_projects: list[str] = field(default_factory=list)
    unusable_metrics: list[str] = field(default_factory=list)
    targets: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return dict(self.__dict__)


def _sections(text: str) -> list[tuple[str, list[tuple[int, str]]]]:
    out, cur = [], ("_top", [])
    for n, ln in enumerate(text.splitlines(), 1):
        if m := re.match(r"^##\s+(.+?)\s*$", ln):
            out.append(cur)
            cur = (m.group(1), [])
        else:
            cur[1].append((n, ln))
    out.append(cur)
    return out


def _subsections(lines: list[tuple[int, str]]) -> list[tuple[str, list[tuple[int, str]]]]:
    out, cur = [], ("", [])
    for n, ln in lines:
        if m := re.match(r"^###\s+(.+?)\s*$", ln):
            out.append(cur)
            cur = (m.group(1), [])
        else:
            cur[1].append((n, ln))
    out.append(cur)
    return [x for x in out if x[0] or x[1]]


def _fields(lines: list[tuple[int, str]]) -> dict[str, str]:
    out = {}
    for _n, ln in lines:
        if m := re.match(r"^\s*-\s+\*\*(.+?):\*\*\s*(.*)$", ln):
            out[m.group(1).strip().casefold()] = m.group(2).strip()
        if ln.startswith("#### "):
            break
    return out


def parse(text: str, source: str = "master_resume.md"):
    """→ (entries, items, skill_categories, warnings, excluded, v4)."""
    v4 = V4()
    entries: dict[str, Entry] = {}
    items: dict[str, Item] = {}
    excluded: list[dict] = []
    warnings: list[str] = []
    cats: list[tuple[str, list[str]]] = []

    def note(sec: str, s: str) -> None:
        if s:
            v4.notes.setdefault(sec, []).append(s)

    def add(iid, section, text_, entry=None, kind="bullet", line=None, **kw) -> Item:
        a, o, m = _facts(text_)
        it = Item(iid, section, text_, entry.id if entry else None, entry.organization if entry else None,
                  entry.role if entry else None, entry.dates if entry else None, terms_in(text_), a, o, m,
                  status="confirmed", line=line, **kw)
        it.kind = kind
        items[iid] = it
        return it

    secs = _sections(text)
    by_name = {name.casefold(): lines for name, lines in secs}

    # ---- builder rules + notes everywhere ----
    for name, lines in secs:
        for _n, ln in lines:
            _c, ns = split_builder(ln)
            for x in ns:
                note(name, x)
    rules = by_name.get("rules for the builder") or []
    for _n, ln in rules:
        if ln.strip().startswith("- "):
            note("Rules for the builder", _md(ln.strip()[2:]))

    # ---- contact ----
    cf = _fields(by_name.get("contact") or [])
    alt = cf.get("location (alternate, for remote/out-of-state roles)", "")
    v4.contact = {"name": _md(cf.get("name", "")), "location": _md(cf.get("location", "")),
                  "relocation": _md(alt.split(". ")[0]) if alt else "", "phone": _md(cf.get("phone", "")),
                  "email": _md(cf.get("email", "")), "linkedin": _md(cf.get("linkedin", "")),
                  "github": re.sub(r"\s*\(.*\)$", "", _md(cf.get("github", "")))}

    # ---- target roles → the profiles a resume may be tailored for ----
    v4.tracks = " ".join(n for n in (v4.notes.get("Target roles") or []))
    targets = [_md(re.sub(r"^\d+\.\s*", "", ln.strip())) for _n, ln in (by_name.get("target roles") or [])
               if re.match(r"^\s*\d+\.\s", ln)]
    prof = []
    for t in targets:
        low = t.casefold()
        prof.append("fullstack" if "full" in low else "cloud" if "cloud" in low or "aws" in low else
                    "data" if "data" in low else "ml" if "machine learning" in low or re.search(r"\bml\b", low) else
                    "backend")                     # Backend, and SWE (which the master leans on backend evidence for)
    v4.targets = {"roles": targets, "profiles": list(dict.fromkeys(prof))}

    # ---- education ----
    for head, lines in _subsections(by_name.get("education") or []):
        if not head:
            continue
        org, _, loc = head.partition(" — ")
        f = _fields(lines)
        grad = re.sub(r"\s*\(.*\)$", "", _md(f.get("graduation", "")))
        gpa = _md(f.get("gpa", ""))
        gpa = None if not gpa or gpa.casefold().startswith("do not") else gpa
        dates, start, end = _parse_dates(grad) if grad else ("", None, None)
        eid = f"edu-{_slug(org).split('-')[0]}"
        e = Entry(eid, "education", head, _md(f.get("degree", "")), org.strip(), loc.strip(), dates, start, end,
                  status="confirmed")
        entries[eid] = e
        add(f"{eid}-h", "education", f"{e.role} — {org.strip()}", e, kind="context",
            line=lines[0][0] if lines else None)
        if gpa:
            add(f"{eid}-gpa", "education", f"GPA: {gpa}", e, kind="context")
        v4.education[eid] = {"degree": e.role, "org": e.organization, "location": e.location, "dates": dates,
                             "gpa": gpa}
        rows = _table([ln for _n, ln in lines])
        courses = []
        for r in rows:
            name = re.sub(r"\s*\(.*\)$", "", _md(r.get("course", "")))
            use = r.get("use on resume?", "").casefold()
            if not name:
                continue
            if use.startswith("yes"):
                cid = f"course-{_slug(name)[:40]}"
                add(cid, "coursework", name, None, kind="course", level=_md(r.get("status", "")).casefold())
                courses.append(cid)
            else:
                excluded.append({"id": f"course-{_slug(name)[:40]}", "line": None, "text": name,
                                 "status": _md(r.get("status", "")) or "not for resume", "source": source,
                                 "section": "coursework"})
        if courses:
            v4.education[eid]["courses"] = courses
        for ns in (v4.notes.get("Education") or []):
            if m := re.search(r"shows\s+\d+\s+courses?\s*\(([^)]+)\)", ns):
                names = [x.strip() for x in m.group(1).split(",")]
                v4.education.setdefault("_default_courses", {})["names"] = names

    # ---- experience ----
    for head, lines in _subsections(by_name.get("experience") or []):
        if not head:
            continue
        f = _fields(lines)
        m = re.match(r"^(.+?)\s+\((.+)\)$", head)
        if " — " in head:
            org, _, role = head.partition(" — ")
        elif m:
            role, org = m.group(1), m.group(2)
        else:
            org, role = head, f.get("title", head)
        org, role = org.strip(), _md(f.get("title") or f.get("title (default)") or role)
        dates, start, end = _parse_dates(_md(f.get("dates", "")))
        eid = f"exp-{_slug(org).split('-')[0]}"
        e = Entry(eid, "experience", head, role, org, _md(f.get("location", "")), dates, start, end,
                  status="confirmed")
        entries[eid] = e
        add(f"{eid}-h", "experience", head, e, kind="context")
        alts = [re.sub(r"\s+for full-stack roles\.?$", "", _md(x)).strip() for x in
                re.split(r";\s*", f.get("title (alternates)", "").split(". Never")[0]) if x.strip()]
        rinfo = {"titles": [role, *[a for a in alts if a]], "client": _md(f.get("client", "")).split(" (")[0],
                 "barred": {}, "skill_only": [], "pick": None, "groups": {}, "core": [], "extended": [],
                 "location_field": _md(f.get("location", ""))}
        for key in ("context", "clients by sector", "ownership (component level)", "team efforts (use \"contributed "
                    "to\" / \"worked with the team on\")", "project", "core stack (default, interview-safe)",
                    "tech used"):
            if f.get(key):
                add(f"{eid}-ctx-{_slug(key)[:20]}", "experience", _md(split_builder(f[key])[0]), e, kind="context")
        tech = f.get("tech used", "")
        for tm in re.finditer(r"([A-Za-z.+#][\w.+# /-]*?)\s*_\(([^)]*)\)_", tech):
            name, ann = tm.group(1).strip(" ,"), tm.group(2).casefold()
            if "skill only" in ann:
                rinfo["skill_only"].append(name)
            if "stays out of" in ann:
                rinfo["barred"][name] = eid
        # metrics, bullet bank, legacy
        mode, group, gi, n_in_group = None, "", 0, {}
        metric_lines = []
        for n, ln in lines:
            s = ln.strip()
            if s.startswith("#### "):
                h = s[5:].casefold()
                mode = ("metrics" if h.startswith("metrics") else "legacy" if h.startswith("legacy") else
                        "extended" if "extended" in h else "bank" if h.startswith("bullet bank") else None)
                group = "core" if mode == "bank" and "core" in h else ""
                continue
            if mode == "metrics":
                metric_lines.append(ln)
                continue
            if mode in ("bank", "extended"):
                if gm := re.match(r"^\*\*(?:([A-F])\.\s+(.+?)|([A-Z][a-z]+(?: [a-z]+)?))\*\*\s*$", s):
                    group = gm.group(1).lower() if gm.group(1) else _slug(gm.group(3)).split("-")[0]
                    rinfo["groups"][group] = _md(gm.group(2) or gm.group(3))
                    continue
                if s.startswith("_In my own words:_"):
                    add(f"{eid}-{group or 'x'}-own", "experience", _md(s.replace("_In my own words:_", "")), e,
                        kind="own_words", line=n, category=group)
                    continue
                if s.startswith("- "):
                    body, ns = split_builder(s[2:])
                    for x in ns:
                        note("Experience", x)
                    variant = bool(_VARIANT.match(body))
                    body = _VARIANT.sub("", body).strip()
                    st = _TRAIL_STATUS.search(body)
                    status = st.group(1).casefold() if st else None
                    body = _TRAIL_STATUS.sub("", body).strip()
                    g = group or ("ext" if mode == "extended" else "core")
                    if mode == "extended":
                        g = "ext"
                    n_in_group[g] = n_in_group.get(g, 0) + 1
                    iid = f"{eid}-{g}{n_in_group[g]}"
                    if status in UNUSABLE:
                        excluded.append({"id": iid, "line": n, "text": body, "status": status, "source": source,
                                         "section": "experience"})
                        continue
                    it = add(iid, "experience", body, e, kind="bullet", line=n, category=g)
                    it.level = "extended" if mode == "extended" else "core"
                    it.variant = variant
                    (rinfo["extended"] if mode == "extended" else rinfo["core"]).append(iid)
                continue
            if mode == "legacy" and s.startswith("- "):
                excluded.append({"id": f"{eid}-legacy", "line": n, "text": _md(s[2:]), "status": "legacy",
                                 "source": source, "section": "experience"})
        bad_numbers = set()
        for r in _table(metric_lines):
            status = _md(r.get("status", "")).casefold()
            mtext = f"{_md(r.get('metric', ''))}: {_md(r.get('before → after', r.get('value', '')))}"
            mid = f"{eid}-m-{_slug(r.get('metric', ''))[:24]}"
            if status in USABLE:
                add(mid, "experience", mtext, e, kind="metric")
            else:
                from .evidence import metrics_in
                bad = [m.lstrip("+−-") for m in metrics_in(mtext.split(":", 1)[-1].replace("−", "-"))]
                bad_numbers |= {b for b in bad if b}
                v4.unusable_metrics += bad
                excluded.append({"id": mid, "line": None, "text": mtext, "status": status or "no status",
                                 "source": source, "section": "experience"})
        # a bullet stating a figure from an unverified / pending / planned metric is not usable either (rule 4)
        for iid in [i for i, it in items.items() if it.entry_id == eid and it.kind == "bullet"]:
            hit = [m for m in items[iid].metrics if m.lstrip("+") in bad_numbers]
            if hit:
                it = items.pop(iid)
                for lst in (rinfo["core"], rinfo["extended"]):
                    if iid in lst:
                        lst.remove(iid)
                excluded.append({"id": iid, "line": it.line, "text": it.text, "status": "uses an unverified metric "
                                 f"({', '.join(hit)})", "source": source, "section": "experience"})
        for x in (v4.notes.get("Experience") or []):
            pass
        v4.roles[eid] = rinfo
    # role notes: pick ranges and overlaps (the note under each role, in file order)
    exp_lines = by_name.get("experience") or []
    cur_role = None
    for n, ln in exp_lines:
        if m := re.match(r"^###\s+(.+?)\s*$", ln):
            hh = m.group(1)
            cur_role = next((k for k, e in entries.items() if e.section == "experience" and e.heading == hh), None)
            continue
        _c, ns = split_builder(ln)
        for x in ns:
            if not cur_role:
                continue
            if pm := re.search(r"pick\s+(\d+)\s*(?:[–-]\s*(\d+))?\s+bullets? from this role", x):
                v4.roles[cur_role]["pick"] = [int(pm.group(1)), int(pm.group(2) or pm.group(1))]
            v4.roles[cur_role].setdefault("notes", []).append(x)
            for sent in re.split(r"(?<=\.)\s+", x):
                if "overlap" in sent.casefold() and "at most one" in sent.casefold() or (
                        "overlap" in sent.casefold() and "use at most one" in x.casefold()):
                    ids = [f"{cur_role}-{t[0].lower()}{t[1:]}" for t in re.findall(r"\b([A-F]\d)\b", sent)]
                    for tn in re.findall(r"the ([A-Z][\w.]+(?: [A-Z][\w.]+)*) (?:dashboard |)bullet", sent):
                        ids += [i for i in v4.roles[cur_role]["core"] if tn in items[i].text and i not in ids]
                    ids = [i for i in ids if i in items]
                    if len(ids) > 1 and ids not in v4.overlaps:
                        v4.overlaps.append(ids)
    for ln in [ln for _n, ln in exp_lines] + [ln for name, ls in secs for _n, ln in ls if name == "Experience"]:
        if m := re.search(r"Excluded by [^:]*decision:\s*([^(.]+)", ln):
            if m.group(1).strip() not in v4.excluded_roles:
                v4.excluded_roles.append(m.group(1).strip())

    # ---- projects ----
    for head, lines in _subsections(by_name.get("projects") or []):
        if not head:
            continue
        if head.casefold() == "excluded":
            for _n, ln in lines:
                if ln.strip().startswith("- "):
                    v4.excluded_projects.append(_md(ln.strip()[2:]).split(":")[0].split(" (")[0])
            continue
        name = head.split(":")[0].strip()
        eid = f"proj-{_slug(name).split('-')[0]}"
        f = _fields(lines)
        ctx = _md(f.get("context", ""))
        in_progress = bool(re.search(r"in[- ]progress", ctx, re.I))
        e = Entry(eid, "project", head, name, None, None, None, None, None,
                  [t.strip() for t in split_skills(_md(f.get("tech") or f.get("planned tech") or ""))],
                  ("https://" + _md(f["repo"]).split(" ")[0]) if f.get("repo") else None, status="confirmed")
        entries[eid] = e
        add(f"{eid}-h", "project", head, e, kind="context")
        if ctx:
            add(f"{eid}-ctx", "project", ctx, e, kind="context")
        if f.get("what it is"):
            add(f"{eid}-what", "project", _md(split_builder(f["what it is"])[0]), e, kind="context")
        comp = {}
        mode, k = None, 0
        for n, ln in lines:
            s = ln.strip()
            if s.startswith("#### "):
                h = s[5:].casefold()
                mode = "status" if "status" in h else "metrics" if "metric" in h else "bank" if "bullet" in h else None
                continue
            if mode == "status" and s.startswith("|") and not s.startswith("|---"):
                cells = [c.strip() for c in s.strip("|").split("|")]
                if len(cells) >= 2 and cells[0].casefold() != "component":
                    comp[cells[0]] = cells[1].casefold()
            elif mode == "metrics" and s.startswith("|") and not s.startswith("|---"):
                cells = [c.strip() for c in s.strip("|").split("|")]
                if len(cells) >= 4 and cells[0].casefold() != "metric":
                    if cells[-1].casefold() in USABLE:
                        add(f"{eid}-m-{_slug(cells[0])[:24]}", "project", f"{_md(cells[0])}: {_md(cells[1])}", e,
                            kind="metric")
            elif mode == "bank" and s.startswith("- "):
                body, ns = split_builder(s[2:])
                body = _VARIANT.sub("", body).strip()
                k += 1
                it = add(f"{eid}-b{k}", "project", body, e, kind="bullet", line=n)
                it.level = "core"
        v4.projects[eid] = {"name": name, "context": ctx, "in_progress": in_progress, "components": comp,
                            "link": e.link, "notes": [x for _n, ln in lines for x in split_builder(ln)[1]]}

    # ---- snapshot (Default one-page resume) ----
    snap_lines = next((ls for name, ls in secs if name.casefold().startswith("default one-page resume")), [])
    snap = {"experience": [], "projects": []}
    cur = None
    for n, ln in snap_lines:
        s = ln.strip()
        if s == "---":
            break
        if m := re.match(r"^\*\*(.+?)\*\*\s*\(([^)]*\d{4}[^)]*)\)\s*$", s):
            left, _, title = m.group(1).partition(" — ")
            org, _, loc = left.partition(", ")
            eid = next((k for k, e in entries.items() if e.section == "experience"
                        and e.organization.casefold() == org.strip().casefold()), None)
            if not eid:
                raise InputError(f"{source}:{n}: default resume role '{org}' has no matching ### role under Experience")
            cur = {"entry_id": eid, "org": org.strip(), "location": loc.strip(), "title": title.strip(),
                   "dates": _parse_dates(m.group(2))[0], "bullets": []}
            snap["experience"].append(cur)
            continue
        if m := re.match(r"^\*\*(.+?)\*\*\s*\(GitHub\)\s*[—-]\s*(.+)$", s):
            name = m.group(1).strip()
            key = re.split(r"\s*[–:-]\s*", name)[0].casefold()
            eid = next((k for k, p in v4.projects.items() if p["name"].casefold().startswith(key)), None)
            if not eid:
                raise InputError(f"{source}:{n}: default resume project '{name}' has no matching ### project")
            cur = {"entry_id": eid, "name": name, "context": m.group(2).strip(), "bullets": []}
            snap["projects"].append(cur)
            v4.projects[eid]["display"] = name
            v4.projects[eid]["display_context"] = cur["context"]
            continue
        if s.startswith("- ") and cur is not None:
            sec = "experience" if cur in snap["experience"] else "project"
            iid = f"snap-{cur['entry_id']}-{len(cur['bullets']) + 1}"
            add(iid, sec, s[2:].strip(), entries[cur["entry_id"]], kind="snapshot", line=n)
            cur["bullets"].append(iid)
    v4.snapshot = snap
    for r in snap["experience"]:
        v4.roles[r["entry_id"]].update(display_org=r["org"], display_location=r["location"], display_title=r["title"])

    # ---- skills inventory ----
    sk = {"rows": [], "tier": {}, "category": {}, "excluded": [], "planned": [], "concepts": [], "self_max": 4}
    inv = by_name.get("skills inventory") or []
    mode = None
    tbl: list[str] = []
    for _n, ln in inv + [(0, "#### END")]:
        s = ln.strip()
        if s.startswith("#### "):
            if mode == "inventory":
                for r in _table(tbl):
                    tier = _md(r.get("tier", "")).casefold().split(",")[0].strip()
                    for name in split_skills(r.get("skill", "")):
                        sk["tier"][name] = tier
                        sk["category"][name] = _md(r.get("category", ""))
                        if "skill only" in r.get("evidence", "").casefold():
                            for v in v4.roles.values():
                                v["skill_only"] = list(dict.fromkeys([*v["skill_only"], name]))
            elif mode == "self":
                for r in _table(tbl):
                    for name in split_skills(r.get("skills", "")):
                        sk["tier"].setdefault(name, "self-reported")
                        sk["category"].setdefault(name, _md(r.get("category", "")))
            mode = ("rows" if "resume default" in s.casefold() else "inventory" if s.casefold() == "#### inventory"
                    else "self" if "self-reported" in s.casefold() else "concepts" if "concepts" in s.casefold()
                    else "planned" if "planned" in s.casefold() else "excluded" if "excluded" in s.casefold() else None)
            if mode == "self" and (m := re.search(r"max\s+(\d+)", s)):
                sk["self_max"] = int(m.group(1))
            tbl = []
            continue
        if mode in ("inventory", "self"):
            tbl.append(s)
        elif mode == "rows" and (m := re.match(r"^-\s+\*\*(.+?):\*\*\s*(.+)$", s)):
            sk["rows"].append({"label": m.group(1), "items": split_skills(m.group(2)) if m.group(1) != "Certifications"
                               else [x.strip() for x in m.group(2).split(";")]})
        elif mode == "concepts" and s.startswith("- "):
            sk["concepts"] = [x.strip() for x in s[2:].split(",")]
        elif mode == "planned" and s.startswith("- "):
            first, *rest = s[2:].split(".")
            also = set(split_skills(" ".join(rest).replace("are also self-reported above", "").replace(" and ", ", ")))
            sk["planned"] = [x for x in split_skills(re.sub(r"\(.*?\)", "", first)) if x not in also]
        elif mode == "excluded" and s.startswith("- "):
            sk["excluded"] = split_skills(s[2:])
    for p in sk["planned"]:
        sk["tier"].setdefault(p, "planned")
    v4.skills = sk
    for name, tier in sk["tier"].items():
        if tier in ("core", "extended", "self-reported"):
            add(f"skill-{_slug(name)[:40]}", "skill", name, None, kind="skill", category=tier)
    for c in sk["concepts"]:
        add(f"concept-{_slug(c)[:40]}", "skill", c, None, kind="skill", category="concept")
    for row in sk["rows"]:
        if row["label"].casefold() == "certifications":
            continue
        ids = []
        for name in row["items"]:
            iid = f"skill-{_slug(name)[:40]}"
            if iid not in items:
                warnings.append(f"{source}: default skills row item '{name}' is not in the Skills inventory")
                add(iid, "skill", name, None, kind="skill", category="core")
            ids.append(iid)
        cats.append((row["label"], ids))

    # ---- certifications ----
    cert_lines = by_name.get("certifications") or []
    cnotes = " ".join(v4.notes.get("Certifications") or [])
    for _n, ln in cert_lines:
        if ln.strip().startswith("- "):
            name = _md(ln.strip()[2:])
            short = name.split(":")[-1].strip().split(" (")[0]
            rule = "always"
            low = cnotes.casefold()
            if "azure" in name.casefold() and "only for cloud roles" in low:
                rule = "cloud_or_azure"
            cid = f"cert-{_slug(name)[:40]}"
            add(cid, "skill", name, None, kind="cert", category="cert")
            v4.certs.append({"id": cid, "name": name, "rule": rule, "short": short})
    return entries, items, cats, warnings, excluded, v4
