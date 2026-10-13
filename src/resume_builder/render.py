"""Content JSON → LaTeX, inside a copy of your template (inputs/resume/resume_template.tex is never modified).

The template is split into its preamble + header (kept byte for byte, except the main-font line, which gets a
metric-compatible fallback when the preferred font is not installed) and its body, which is replaced by
\\input{content.tex}. content.tex is generated here with the template's own body markup (\\section, \\textbf{…}
\\hfill \\textbf{dates}, itemize, the template's \\vspace gaps).

Every piece of generated text goes through escape(): LaTeX special characters can never become commands. Links are
limited to http(s) and mailto and escaped for \\href.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .config import InputError, Settings
from .content_schema import Content
from .evidence import Bank

_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
        "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
_ESC_RE = re.compile(r"[\\&%$#_{}~^]")
_UNICODE = {"\u00a0": " ", "\u200b": "", "\ufeff": "", "\u2028": " ", "\u2029": " "}

SECTION_TITLES = {"summary": "Summary", "skills": "Technical Skills", "education": "Education",
                  "experience": "Experience", "projects": "Projects"}


def escape(text: str) -> str:
    t = "".join(_UNICODE.get(ch, ch) for ch in str(text))
    t = _ESC_RE.sub(lambda m: _ESC[m.group(0)], t)
    # straight double quotes would become ” in TeX fonts; keep them as typed
    t = t.replace('"', r"\textquotedbl{}") if '"' in t else t
    return t


def safe_url(url: str) -> str | None:
    u = (url or "").strip()
    p = urlparse(u)
    if p.scheme in ("http", "https") and p.netloc and not re.search(r"[\s\"<>\\{}]", u):
        return u
    if p.scheme == "mailto" and re.fullmatch(r"mailto:[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", u):
        return u
    return None


def href(url: str, label: str) -> str:
    u = safe_url(url)
    if not u:
        return escape(label)
    u_esc = u.replace("\\", "").replace("%", r"\%").replace("#", r"\#").replace("~", r"\~{}").replace("{", "").replace("}", "")
    disp = re.sub(r"^https?://(www\.)?", "", label) if label == url else label
    # long links: break-friendly display (\nolinkurl breaks at / . -), the target stays exact
    inner = rf"\nolinkurl{{{_nolink(disp)}}}" if len(disp) > 45 else escape(disp)
    return rf"\href{{{u_esc}}}{{{inner}}}"


def _nolink(s: str) -> str:
    return re.sub(r"[\\{}%#~^]", "", s)


@dataclass
class TemplateParts:
    preamble_header: str    # \documentclass … \begin{document} … header block (kept as written)
    body: str               # the template's own body (replaced)
    font_used_line: str | None
    header_text: str


def split_template(tex: str, settings: Settings) -> TemplateParts:
    if "\\documentclass" not in tex or "\\begin{document}" not in tex or "\\end{document}" not in tex:
        raise InputError("resume_template.tex must contain \\documentclass, \\begin{document} and \\end{document}")
    if re.search(r"\\(write18|immediate\\write|openout|ShellEscape|directlua|input\|)", tex):
        raise InputError("resume_template.tex uses shell-escape / file-writing commands; remove them")
    pre, rest = tex.split("\\begin{document}", 1)
    m = re.search(r"\\end\{center\}", rest)
    if not m:
        raise InputError("resume_template.tex: no header block (\\begin{center} … \\end{center}) after "
                         "\\begin{document}; the builder keeps that header as written")
    header = rest[:m.end()]
    body = rest[m.end():].rsplit("\\end{document}", 1)[0]
    pre, font_line = patch_font(pre, settings)
    return TemplateParts(pre + "\\begin{document}" + header, body, font_line, header)


def patch_font(preamble: str, settings: Settings) -> tuple[str, str | None]:
    pref = (settings.fonts or {}).get("preferred")
    fb = (settings.fonts or {}).get("fallback")
    m = re.search(r"\\setmainfont(\[[^\]]*\])?\{([^}]+)\}", preamble)
    if not m or not fb or m.group(2) != pref:
        return preamble, None
    opts = m.group(1) or ""
    repl = (rf"\IfFontExistsTF{{{pref}}}{{\setmainfont{opts}{{{pref}}}\typeout{{RB-FONT: {pref}}}}}"
            rf"{{\setmainfont{opts}{{{fb}}}\typeout{{RB-FONT: {fb}}}}}")
    return preamble[:m.start()] + repl + preamble[m.end():], m.group(0)


def contact_from_header(header: str) -> dict:
    links = re.findall(r"\\href\{([^}]*)\}\{([^}]*)\}", header)
    name = re.search(r"\\textbf\{([^}]*)\}", header)
    plain = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", header)
    plain = re.sub(r"\\[a-zA-Z]+\*?|[{}\\]|\\,", " ", plain.replace("\\,", " "))
    return {"name": name.group(1).strip() if name else None, "text": re.sub(r"\s+", " ", plain).strip(),
            "links": [{"url": u.replace("\\%", "%").replace("\\#", "#"), "text": t} for u, t in links]}


def _bullets(items: list[str]) -> list[str]:
    return ["\\begin{itemize}", *[f"\\item {b}" for b in items], "\\end{itemize}"]


def render_content(content: Content, bank: Bank, settings: Settings, gaps: dict | None = None) -> str:
    """content.tex: the body, in the template's markup. gaps: entry/education gap in pt (repair loop may shrink)."""
    g = {"entry": settings.layout.get("entry_gap_pt", 6), "education": settings.layout.get("education_gap_pt", 4),
         **(gaps or {})}
    out = ["% Generated by the Resume Builder from content.json. Edits here are detected and preserved.",
           f"\\newcommand{{\\rbEntryGap}}{{\\vspace{{{g['entry']}pt}}}}",
           f"\\newcommand{{\\rbEduGap}}{{\\vspace{{{g['education']}pt}}}}",
           "% no syllable hyphenation: an ATS reads 'produc-tion' as two words (breaks at real hyphens stay)",
           "\\hyphenpenalty=10000 \\exhyphenpenalty=50 \\emergencystretch=1.5em", ""]
    for sec in content.section_order:
        if sec == "summary" and content.summary:
            out += ["% ---------- SUMMARY ----------", "\\section{Summary}", escape(content.summary.text), ""]
        elif sec == "skills":
            out += ["% ---------- TECHNICAL SKILLS ----------", "\\section{Technical Skills}"]
            out += _bullets([f"\\textbf{{{escape(c.category)}:}} " + ", ".join(escape(i.name) for i in c.items)
                             for c in content.skills])
            out.append("")
        elif sec == "education":
            out += ["% ---------- EDUCATION ----------", "\\section{Education}"]
            for n, ed in enumerate(content.education):
                e = bank.entries[ed.entry_id]
                inst = ", ".join(x for x in (e.organization, e.location) if x)
                lines = [f"\\textbf{{{escape(inst)}}} \\hfill \\textbf{{{escape(e.dates or '')}}}"]
                deg = escape(e.role or "")
                if ed.course_ids:
                    deg += " — Courses: " + ", ".join(escape(bank.items[c].text) for c in ed.course_ids)
                lines.append(deg)
                lines += [escape(bank.items[d].text) for d in ed.detail_ids]
                if n:
                    out += ["", "\\rbEduGap", ""]
                out.append("\\\\\n".join(lines))
            out.append("")
        elif sec == "experience" and content.experience:
            out += ["% ---------- EXPERIENCE ----------", "\\section{Experience}", ""]
            for n, ex in enumerate(content.experience):
                e = bank.entries[ex.entry_id]
                if n:
                    out += ["", "\\rbEntryGap", ""]
                out.append(f"\\textbf{{{escape(e.heading)}}} \\hfill \\textbf{{{escape(e.dates or '')}}}")
                out += _bullets([escape(b.text) for b in ex.bullets])
            out.append("")
        elif sec == "projects" and content.projects:
            out += ["% ---------- PROJECTS ----------", "\\section{Projects}"]
            for n, pr in enumerate(content.projects):
                e = bank.entries[pr.entry_id]
                tech = pr.tech if pr.tech else e.tech
                title = escape(e.role or e.heading) + (" | " + ", ".join(escape(t) for t in tech) if tech else "")
                if e.link:
                    title += " | " + href(e.link, e.link)
                if n:
                    out += ["", "\\rbEntryGap", ""]
                out.append(f"\\textbf{{{title}}}")
                out += _bullets([escape(b.text) for b in pr.bullets])
            out.append("")
    return "\n".join(out) + "\n"


def main_tex(parts: TemplateParts) -> str:
    return (parts.preamble_header.rstrip() + "\n\n% ---------- BODY (generated: content.tex) ----------\n"
            "\\input{content.tex}\n\n\\end{document}\n")


def plain_lines(content: Content, bank: Bank) -> list[tuple[str, str]]:
    """What the PDF should say, in reading order: (kind, text) with kind heading | entry | bullet | line | date.
    Dates are right-aligned (\\hfill), so text extraction may place them elsewhere: checked for presence only."""
    out: list[tuple[str, str]] = []
    for sec in content.section_order:
        if sec == "summary" and content.summary:
            out += [("heading", "Summary"), ("line", content.summary.text)]
        elif sec == "skills":
            out.append(("heading", "Technical Skills"))
            out += [("bullet", f"{c.category}: " + ", ".join(i.name for i in c.items)) for c in content.skills]
        elif sec == "education":
            out.append(("heading", "Education"))
            for ed in content.education:
                e = bank.entries[ed.entry_id]
                out.append(("entry", ", ".join(x for x in (e.organization, e.location) if x)))
                out.append(("date", e.dates or ""))
                out.append(("line", (e.role or "") + (" — Courses: " + ", ".join(bank.items[c].text for c in ed.course_ids)
                                                      if ed.course_ids else "")))
                out += [("line", bank.items[d].text) for d in ed.detail_ids]
        elif sec == "experience" and content.experience:
            out.append(("heading", "Experience"))
            for ex in content.experience:
                e = bank.entries[ex.entry_id]
                out += [("entry", e.heading), ("date", e.dates or "")]
                out += [("bullet", b.text) for b in ex.bullets]
        elif sec == "projects" and content.projects:
            out.append(("heading", "Projects"))
            for pr in content.projects:
                e = bank.entries[pr.entry_id]
                tech = pr.tech if pr.tech else e.tech
                out.append(("entry", (e.role or e.heading) + (" | " + ", ".join(tech) if tech else "")
                            + (f" | {re.sub(r'^https?://(www[.])?', '', e.link)}" if e.link else "")))
                out += [("bullet", b.text) for b in pr.bullets]
    return out
