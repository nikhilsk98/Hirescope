"""Master v4 + resume_template.tex: the baseline (the master's Default one-page snapshot), rendering with the
template's own macros (\\entry, \\project, \\skillrow, \\entrygap), the expected text, and the layout-spec check.

Without a job description the baseline *is* the snapshot (rule 3), and its PDF reproduces the template's own body
(tests/resume_builder/test_master_v4.py compares the extracted text). With a job description the baseline is still
the snapshot; the brief lists the bullets the master allows swapping in, and the drafter swaps by the master's notes.

Text for TeX: LaTeX specials escaped; typographic dashes / quotes turned into TeX ligature input (an en dash typed as
U+2013 would land in the wrong slot of a T1 font); f-ligatures (fi, fl, ff, ffi, ffl) broken with {} so the PDF text
reads "fi", not the single glyph "ﬁ" an ATS keyword search would miss.
"""
from __future__ import annotations

import re
from pathlib import Path

from .content_schema import (Bullet, Content, EducationEntry, ExperienceEntry, Notes, ProjectEntry, RequirementMap,
                             SkillCategory, SkillItem)
from .evidence import Bank, canonical
from .render import safe_url
from . import rules_v4

ROW_FOR = {  # Skills inventory category → default row (rule 52)
    "language": "Languages", "languages": "Languages",
    "backend": "Backend & Testing", "testing": "Backend & Testing", "architecture": "Backend & Testing",
    "practice": "Backend & Testing", "practices": "Backend & Testing", "testing / automation": "Backend & Testing",
    "testing / tools": "Backend & Testing", "testing & monitoring": "Backend & Testing", "ml": "Backend & Testing",
    "ai & ml": "Backend & Testing", "payments": "Backend & Testing", "full-stack": "Frontend",
    "frontend": "Frontend", "database": "Databases & Messaging", "databases": "Databases & Messaging",
    "messaging": "Databases & Messaging", "real-time": "Databases & Messaging",
    "cloud": "Cloud & DevOps", "iac": "Cloud & DevOps", "containers": "Cloud & DevOps", "ci/cd": "Cloud & DevOps",
    "tools": "Cloud & DevOps", "cloud & devops": "Cloud & DevOps",
}
_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
        "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex(text: str) -> str:
    t = re.sub(r"[\\&%$#_{}~^]", lambda m: _ESC[m.group(0)], str(text))
    t = t.replace("\u2014", "---").replace("\u2013", "--").replace("\u2019", "'").replace("\u2018", "`")
    t = t.replace("\u201c", "``").replace("\u201d", "''").replace("\u00a0", " ")
    t = t.replace('"', "''")
    t = re.sub(r"f(?=[fil])", "f{}", t)          # no f-ligatures: the PDF text must read "fi", not "ﬁ"
    return t


def bar(text: str) -> str:
    """'a | b | c' → the template's separator ' \\,\\textbar\\, '."""
    return r" \,\textbar\, ".join(tex(x.strip()) for x in text.split("|"))


def relevance(item, analysis: dict | None) -> float:
    if not analysis:
        return 0.0
    w = {}
    for k in analysis.get("keywords", []):
        w[k["canonical"]] = max(w.get(k["canonical"], 0), float(k.get("weight") or 1))
    for r in analysis.get("requirements", []):
        for t in r.get("terms", []):
            w.setdefault(t, 1.0)
    # rule 79: the JD's responsibilities count as well as its technologies (skills themselves rank by technology)
    resp = rules_v4.responsibility_score(item.text, analysis) if item.kind in ("bullet", "snapshot") else 0.0
    return round(sum(w.get(t, 0) for t in set(item.technologies)) + (0.3 if item.metrics else 0) + resp, 3)


def baseline(analysis: dict | None, bank: Bank, settings) -> Content:
    X = bank.v4
    snap = X["snapshot"]
    notes = Notes(stage="baseline")
    # the snapshot is approved content, never "weak": priorities 1–2, so overflow trimming (rule 67) reaches it only
    # after duplicates, weak additions, coursework and extras
    exp = [ExperienceEntry(entry_id=r["entry_id"], priority=n + 1, bullets=[
        Bullet(text=bank.items[i].text, evidence_ids=[i], origin="verbatim", priority=1 if k == 0 else 2)
        for k, i in enumerate(r["bullets"])]) for n, r in enumerate(snap["experience"])]
    projs = [ProjectEntry(entry_id=p["entry_id"], priority=2 + n, bullets=[
        Bullet(text=bank.items[i].text, evidence_ids=[i], origin="verbatim", priority=2)
        for k, i in enumerate(p["bullets"])]) for n, p in enumerate(snap["projects"])]
    dflt, _mx = rules_v4.audience_projects(bank, settings, analysis)
    if len(projs) > dflt:
        score = {p.entry_id: sum(relevance(bank.items[b.evidence_ids[0]], analysis) for b in p.bullets) for p in projs}
        keep = sorted(projs, key=lambda p: -score[p.entry_id])[:dflt]
        projs = [p for p in projs if p in keep]
        notes.selection.append(f"Projects: {dflt} for this level (rules 46–48): kept {', '.join(p.entry_id for p in projs)}.")
    # skills: the default rows; JD-relevant items first; extended / self-reported only when the JD asks (rule 53)
    sk = X["skills"]
    rows = []
    for label, ids in bank.skill_categories:
        its = [bank.items[i] for i in ids]
        if analysis:
            its.sort(key=lambda i: -relevance(i, analysis))
        rows.append([label, [i.id for i in its]])
    if analysis:
        added, self_n = [], 0
        have = {canonical(bank.items[i].text) for _l, ids in rows for i in ids}
        for name, tier in sk["tier"].items():
            if tier not in ("extended", "self-reported", "coursework") or canonical(name) in have:
                continue
            if canonical(name) in {canonical(x) for x in sk.get("planned_also", [])}:
                continue                     # planned for an in-progress project: never in Skills until built
            if not rules_v4.mentions(analysis, name):
                continue
            if tier == "self-reported":
                if self_n >= int(sk.get("self_max", 4)):
                    continue
                self_n += 1
            iid = next((i.id for i in bank.items.values() if i.kind == "skill" and i.text == name), None)
            if not iid:
                continue
            if tier == "coursework":                 # rule 75: listed at its level, in the first (Languages) row
                rows[0][1].append(("coursework", iid))
                added.append(f"{name} (coursework)")
                have.add(canonical(name))
                continue
            row = ROW_FOR.get((sk.get("category", {}).get(name) or "").casefold(), rows[-1][0])
            target = next((r for r in rows if r[0] == row), rows[-1])
            target[1].append(iid)
            added.append(f"{name} ({tier})")
            have.add(canonical(name))
        if added:
            notes.selection.append("Skills added for this JD (rule 53): " + ", ".join(added) + ".")
    def item(x):
        if isinstance(x, tuple):                     # a coursework-level skill: "Go (coursework)"
            return SkillItem(name=f"{bank.items[x[1]].text} (coursework)", evidence_ids=[x[1]])
        return SkillItem(name=bank.items[x].text, evidence_ids=[x])
    skills = [SkillCategory(category=l, items=[item(i) for i in ids]) for l, ids in rows]
    certs = [x for x in X["certs"] if x["rule"] == "always" or (x["rule"] == "cloud_or_azure" and analysis and (
        rules_v4.is_cloud(analysis, analysis["profile"]["picked"]) or rules_v4.mentions(analysis, "Azure")))]
    if certs:
        # "Microsoft Certified: Azure Fundamentals (AZ-900)" shows as rule 29 writes it ("Microsoft Azure Fundamentals")
        skills.append(SkillCategory(category="Certifications", items=[SkillItem(
            name=x["name"].replace("Certified: ", "") if "Certified: " in x["name"] else x["name"],
            evidence_ids=[x["id"]]) for x in certs]))
    # education: the MS with the master's default courses (rule 59), then the bachelor's
    edu = []
    dc = (X["education"].get("_default_courses") or {}).get("names", [])
    for eid, e in X["education"].items():
        if eid.startswith("_"):
            continue
        cids = sorted((c for c in e.get("courses", []) if bank.items[c].text in dc),
                      key=lambda c: dc.index(bank.items[c].text)) if not edu else []    # the note's order
        edu.append(EducationEntry(entry_id=eid, course_ids=cids, detail_ids=[]))
    reqs = [RequirementMap(text=r["text"][:600], kind=r["kind"], category=r["category"],
                           evidence_ids=r["evidence_ids"][:12], note=r.get("note", "")[:400])
            for r in (analysis or {}).get("requirements", [])[:60]]
    notes.selection.insert(0, "Started from the master's Default one-page resume (rule 3)"
                           + (": no job description, so it is reproduced unchanged." if not analysis else
                              "; swap bullets for the JD by the master's notes (see the brief)."))
    loc = "relocation" if rules_v4.wants_relocation(analysis) else "default"
    if loc == "relocation":
        notes.selection.append("Contact line: relocation (remote or out-of-state job; rule 31).")
    return Content(job_pk=(analysis or {}).get("job", {}).get("pk", 0), profile=(analysis or {}).get(
        "profile", {}).get("picked", "default") or "default", track=(analysis or {}).get("track", "full_time")
        if (analysis or {}).get("track") in ("coop_intern", "full_time", "unclear") else "full_time",
        section_order=list(rules_v4.ORDER), skills=skills, education=edu, experience=exp, projects=projs,
        requirements=reqs, notes=notes, contact_location=loc)


def _gap(gaps: dict, key: str = "entry") -> str:
    return r"\entrygap"


def render_content(c: Content, bank: Bank, settings, gaps: dict | None = None) -> str:
    X = bank.v4
    g = gaps or {}
    out = ["% Generated by the Resume Builder from content.json (master v4, the template's own macros).",
           "% Edits here are detected and preserved."]
    if g.get("entry") is not None and float(g["entry"]) != float(g.get("template_entry", g["entry"])):
        out.append(rf"\renewcommand{{\entrygap}}{{\vspace{{{g['entry']}pt}}}}")
    if g.get("leading") and float(g["leading"]) != 12.0:
        out.append(rf"\fontsize{{10.5}}{{{g['leading']}}}\selectfont")
    out.append("")
    for sec in c.section_order:
        if sec == "education":
            out.append(r"\section{Education}")
            for n, ed in enumerate(c.education):
                e = X["education"][ed.entry_id]
                deg = tex(e["degree"]) + (rf" \,\textbar\, GPA: {tex(e['gpa'])}" if e.get("gpa") and n == 0 else "")
                if n:
                    out += ["", r"\entrygap"]
                line = rf"\entry{{{tex(e['org'])}}}{{{tex(e['location'])}}}{{{deg}}}{{{tex(e['dates'])}}}"
                if ed.course_ids:
                    out.append(line + r"\\")
                    out.append(r"\textbf{Relevant Coursework:} " + ", ".join(tex(bank.items[x].text) for x in ed.course_ids))
                else:
                    out.append(line)
            out.append("")
        elif sec == "skills":
            out.append(r"\section{Technical Skills}")
            for row in c.skills:
                out.append(rf"\skillrow{{{tex(row.category)}}}{{" + ", ".join(tex(s.name) for s in row.items) + "}")
            out.append("")
        elif sec == "experience" and c.experience:
            out.append(r"\section{Experience}")
            for n, ex in enumerate(c.experience):
                r = X["roles"][ex.entry_id]
                if n:
                    out += ["", r"\entrygap"]
                title = ex.title or r.get("display_title") or r["titles"][0]
                e = bank.entries[ex.entry_id]
                out.append(rf"\entry{{{tex(r.get('display_org') or e.organization)}}}{{"
                           rf"{tex(r.get('display_location') or e.location)}}}{{{bar(title)}}}{{{tex(e.dates)}}}")
                out += [r"\begin{itemize}", *[rf"  \item {tex(b.text)}" for b in ex.bullets], r"\end{itemize}"]
            out.append("")
        elif sec == "projects" and c.projects:
            out.append(r"\section{Projects}")
            for n, pr in enumerate(c.projects):
                p = X["projects"][pr.entry_id]
                if n:
                    out += ["", r"\entrygap"]
                link = rf"\href{{{p['link']}}}{{GitHub}}" if p.get("link") and safe_url(p["link"]) else ""
                out.append(rf"\project{{{tex(p.get('display') or p['name'])}}}{{{link}}}{{{bar(p.get('display_context') or p['context'])}}}")
                out += [r"\begin{itemize}", *[rf"  \item {tex(b.text)}" for b in pr.bullets], r"\end{itemize}"]
            out.append("")
    return "\n".join(out) + "\n"


def plain_lines(c: Content, bank: Bank) -> list[tuple[str, str]]:
    X = bank.v4
    out: list[tuple[str, str]] = []
    for sec in c.section_order:
        if sec == "education":
            out.append(("heading", "Education"))
            for n, ed in enumerate(c.education):
                e = X["education"][ed.entry_id]
                out += [("entry", f"{e['org']}, {e['location']}"), ("date", e["dates"]),
                        ("line", e["degree"] + (f" | GPA: {e['gpa']}" if e.get("gpa") and n == 0 else ""))]
                if ed.course_ids:
                    out.append(("line", "Relevant Coursework: " + ", ".join(bank.items[x].text for x in ed.course_ids)))
        elif sec == "skills":
            out.append(("heading", "Technical Skills"))
            out += [("line", f"{row.category}: " + ", ".join(s.name for s in row.items)) for row in c.skills]
        elif sec == "experience" and c.experience:
            out.append(("heading", "Experience"))
            for ex in c.experience:
                r = X["roles"][ex.entry_id]
                e = bank.entries[ex.entry_id]
                out += [("entry", f"{r.get('display_org') or e.organization}, {r.get('display_location') or e.location}"),
                        ("date", e.dates), ("line", ex.title or r.get("display_title") or r["titles"][0])]
                out += [("bullet", b.text) for b in ex.bullets]
        elif sec == "projects" and c.projects:
            out.append(("heading", "Projects"))
            for pr in c.projects:
                p = X["projects"][pr.entry_id]
                out.append(("entry", p.get("display") or p["name"]))
                if p.get("link"):
                    out.append(("date", "GitHub"))          # right-aligned like a date: checked for presence
                out.append(("line", p.get("display_context") or p["context"]))
                out += [("bullet", b.text) for b in pr.bullets]
    return out


def expected_links(c: Content, bank: Bank, contact_links: list[dict]) -> list[dict]:
    links = list(contact_links)
    for pr in c.projects:
        p = bank.v4["projects"][pr.entry_id]
        if p.get("link") and safe_url(p["link"]):
            links.append({"url": p["link"], "text": "GitHub"})
    return links


def patch_header(header: str, c: Content, bank: Bank, drop_plus: bool = False) -> str:
    """Rule 31: the relocation line for remote / out-of-state jobs; "+1" dropped when the contact line would wrap."""
    ct = bank.v4.get("contact", {})
    h = header
    if c.contact_location == "relocation" and ct.get("relocation") and ct.get("location"):
        h = h.replace(ct["location"], tex(ct["relocation"]), 1)
        # rule 31 (v4.2): relocation jobs drop the GitHub link from the contact line (the projects link GitHub)
        gh = re.escape(ct.get("github") or "github.com")
        h = re.sub(rf"\s*\\,\\textbar\\,\s*\\href\{{https?://(?:www\.)?{gh}/?\}}\{{[^}}]*\}}", "", h, count=1)
    if drop_plus:
        h = h.replace("+1 (", "(", 1)
    return h


def template_values(tex_text: str) -> dict:
    m = re.search(r"\\newcommand\{\\entrygap\}\{\\vspace\{([\d.]+)pt\}\}", tex_text)
    lead = re.search(r"\\fontsize\{10\.5\}\{([\d.]+)\}", tex_text)
    return {"entry": float(m.group(1)) if m else 3.0, "leading": float(lead.group(1)) if lead else 12.0}


SPEC = [  # the layout specification in resume_rules.md, as checks on the generated main.tex
    ("engine_font", r"\\usepackage\[T1\]\{fontenc\}", "T1 font encoding"),
    ("engine_font_lmodern", r"\\usepackage\{lmodern\}", "Latin Modern (lmodern)"),
    ("margins_0_5in", r"\\usepackage\[(?=[^\]]*top=0\.5in)(?=[^\]]*bottom=0\.5in)(?=[^\]]*left=0\.5in)"
                      r"(?=[^\]]*right=0\.5in)[^\]]*\]\{geometry\}", "0.5 in margins on all sides"),
    ("body_10_5", r"\\fontsize\{10\.5\}\{12(?:\.0)?\}", "10.5 pt body on 12.0 pt leading"),
    ("hyphenation_off", r"\\usepackage\[none\]\{hyphenat\}", "hyphenation off"),
    ("section_12_5", r"\\titleformat\{\\section\}\{\\fontsize\{12\.5\}", "12.5 pt bold section headings with a rule"),
    ("bullets", r"leftmargin=1\.2em,\s*label=\$\\bullet\$", "math bullet, 1.2 em indent"),
    ("links_hidden", r"\\usepackage\[hidelinks\]\{hyperref\}", "clickable links in the text colour"),
    ("entrygap_3pt", r"\\newcommand\{\\entrygap\}\{\\vspace\{3pt\}\}", "3 pt between entries"),
    ("letter", r"letterpaper", "US Letter"),
]


def layout_checks(main_tex: str, pdf: Path, report, n_bullets: int) -> None:
    """Layout specification + font / text checks (rules 16–23, 69) added to the validation report."""
    import pdfplumber
    from .validate import _run
    for name, rx, what in SPEC:
        report.add(f"layout_{name}", bool(re.search(rx, main_tex)), what if re.search(rx, main_tex)
                   else f"template / build differs from the layout specification: {what}")
    fonts_out = _run(["pdffonts", str(pdf)])
    t3 = [ln.split()[0] for ln in fonts_out.splitlines()[2:] if "Type 3" in ln]
    report.add("no_type3_fonts", not t3, f"Type 3 fonts: {t3}" if t3 else "no Type 3 fonts")
    names = [ln.split()[0] for ln in fonts_out.splitlines()[2:] if ln.strip()]
    odd = [n for n in names if not re.search(r"LMRoman|LMMathSymbols|LMSans|LMMono", n)]
    report.add("latin_modern_fonts", not odd, f"non-Latin-Modern fonts: {odd}" if odd else ", ".join(
        re.sub(r"^[A-Z]{6}\+", "", n) for n in names))
    raw = _run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"])
    lig = re.findall(r"[\ufb00-\ufb06]", raw)
    report.add("ligatures_extract_as_letters", not lig, f"{len(lig)} ligature glyph(s) (ﬁ/ﬂ) in the PDF text"
               if lig else "fi / fl extract as letters")
    report.add("bullets_extract_as_dot", raw.count("•") == n_bullets,
               f"{raw.count('•')} '•' in the PDF text, expected {n_bullets}")
    report.add("en_dashes", "–" in raw and "\u0013" not in raw, "date ranges extract with '–'")
    with pdfplumber.open(str(pdf)) as doc:
        page = doc.pages[0]
        report.add("page_letter", (round(float(page.width)), round(float(page.height))) == (612, 792),
                   f"{float(page.width):.0f}×{float(page.height):.0f} pt")
        sizes = [round(float(ch["size"]), 1) for ch in page.chars if ch["text"].strip() and ch["text"] != "•"]
        from collections import Counter
        body = Counter(sizes).most_common(1)[0][0] if sizes else 0
        report.add("body_size", 10.4 <= body <= 11.05, f"body text {body} pt (10.5–11 pt)")
        report.add("name_size", any(abs(s - 20) < 0.3 for s in sizes), "name 20 pt")
        lines = page.extract_text_lines()
        head = next((i for i, ln in enumerate(lines) if ln["text"].strip() == "Education"), None)
        contact_lines = lines[1:head] if head else []
        # rules 21 + 31: one line; with the relocation line and "+1" already dropped there is no further step in the
        # rules, so a wrap is reported (a warning, for Nikhil to decide) instead of refusing the resume
        stuck = "(Open to Relocation)" in main_tex and "+1 (" not in main_tex.split("\\begin{document}", 1)[-1]
        report.add("contact_one_line", len(contact_lines) <= 1,
                   f"contact details on {len(contact_lines)} line(s)" + (
                       " — the relocation line wraps even without '+1'; the rules give no further step: decide in "
                       "resume_rules.md rule 31 (e.g. drop the GitHub link from the contact line for these jobs)"
                       if stuck and len(contact_lines) > 1 else ""), "warn" if stuck else "error")


def brief_lines(analysis: dict, bank: Bank, settings) -> list[str]:
    """The v4 part of the drafter's brief."""
    X = bank.v4
    L = ["## Master v4: start from the Default one-page resume (rule 3)",
         "baseline.json is the master's snapshot, unchanged. Swap bullets for this JD only as the master's Builder notes "
         "allow (below); cite the bank bullet's id when you swap one in. Section order is fixed (Education → "
         "Technical Skills → Experience → Projects, rules 24–25). No summary (rule 28).", "",
         "## Builder notes from master_resume.md (rule 2: they override the general rules for style and selection, "
         "never a truthfulness rule)"]
    for sec, ns in X.get("notes", {}).items():
        for x in ns:
            L.append(f"- [{sec}] {x}")
    for eid, r in X.get("roles", {}).items():
        for x in r.get("notes", []):
            L.append(f"- [{eid}] {x}")
    L += ["", "## Swap candidates (bank bullets not on the snapshot, most JD-relevant first)"]
    snap = {i for r in X["snapshot"]["experience"] + X["snapshot"]["projects"] for i in r["bullets"]}
    from .rules_v4 import bank_id, metric_kinds
    on_page = {bank_id(bank, i) for i in snap}
    for r in X["snapshot"]["experience"] + X["snapshot"]["projects"]:
        eid = r["entry_id"]
        cands = [i for i in bank.items.values() if i.kind == "bullet" and i.entry_id == eid and i.id not in on_page]
        cands.sort(key=lambda i: -relevance(i, analysis))
        L.append(f"### {eid} (snapshot: {', '.join(r['bullets'])})")
        for i in cands[:6]:
            tag = (" [extended: only if the JD needs it]" if i.level == "extended" else "") + (
                f" [metric: {', '.join(metric_kinds(i.text))}]" if metric_kinds(i.text) else "") + (
                f" [areas: {', '.join(rules_v4.areas_of(i.text))}]" if rules_v4.areas_of(i.text) else "")
            L.append(f"- {i.id} (relevance {relevance(i, analysis)}){tag}: {i.text}")
    if X.get("overlaps"):
        L += ["", "## Overlapping bullets (at most one of each group per resume)",
              *[f"- {' / '.join(g)}" for g in X["overlaps"]]]
    if analysis:
        L += ["", "## Selection quality (rules 79–84)",
              "- Rank by the JD's responsibilities as well as its technologies (rule 79; relevance above includes both).",
              "- Show a required skill in a bullet rather than only in Skills when a bullet demonstrates it (rule 80).",
              "- Distinct evidence across areas; this JD asks for: " + (", ".join(rules_v4.jd_areas(analysis)) or "—")
              + ". Don't spend several bullets on the same thing (rule 81).",
              "- TDD is not automated testing: never claim or map TDD from test-writing bullets (rule 82).",
              "- Every Skills / certification item cites its master entry; the JD only selects (rule 83).",
              "- Introduce a client / sector before a bullet refers back to it (\"the distributor\") (rule 84).",
              "- Overflow: JD-irrelevant skills go before coursework or a project (rule 67)."]
    sk = X["skills"]
    asked = [(n, t) for n, t in sk["tier"].items() if t in ("extended", "self-reported") and rules_v4.mentions(analysis, n)]
    L += ["", "## Skills (rule 53)", "- Default rows from the master; JD-relevant first. Core always; extended / "
          f"self-reported only when the JD mentions them (self-reported at most {sk.get('self_max', 4)}, never in a "
          "bullet); planned never; Excluded never: " + ", ".join(sk.get("excluded", [])) + "."]
    if asked:
        L.append("- This JD mentions: " + ", ".join(f"{n} ({t})" for n, t in asked))
    certs = [x["name"] + (" (always)" if x["rule"] == "always" else " (cloud roles / Azure in the JD only)")
             for x in X.get("certs", [])]
    L.append("- Certifications row (last, rule 29): " + "; ".join(certs))
    dflt, mx = rules_v4.audience_projects(bank, settings, analysis)
    L += [f"- Projects: {dflt}" + (f" (a 3rd only if it fills a real JD gap; up to {mx})" if mx > dflt else "")
          + " (rules 46–48).",
          f"- Contact line: {'relocation — ' + X['contact'].get('relocation', '') if rules_v4.wants_relocation(analysis) else 'default — ' + X['contact'].get('location', '')} (rule 31).",
          "- One metric of each kind per resume (rule 8): latency/p95, throughput, deployment/pipeline duration, "
          "query time.", ""]
    return L
