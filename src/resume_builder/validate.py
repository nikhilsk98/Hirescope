"""Checks on the actual PDF: pages, compiler log, fonts, sizes, margins, clipping, overlaps, spacing, wrapping,
extracted text vs the intended content, contact details, and links.

Every check is {name, ok, severity, detail}. severity "error" blocks "ready"; "warn" is reported only.
"""
from __future__ import annotations

import logging
import re
import statistics
import subprocess
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

# pdfminer (inside pdfplumber) logs every PDF token at DEBUG, including mailto:/tel: links: keep it out of logs
logging.getLogger("pdfminer").setLevel(logging.WARNING)

CM = 28.3465   # PDF points per cm
LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
BULLET_CHARS = "•∙●◦▪"
# Hard rule (rules 50–51, v4.7): no project or entry on a resume carries a status label. Checked on the PDF's own text,
# so it holds whatever produced the line (master, template, a hand edit, a lock).
IN_PROGRESS = re.compile(r"\bin[\s\-‐‑–]*progress\b|\bon[\s\-‐‑]?going\b|\bwork[\s\-]+in[\s\-]+progress\b|\(\s*wip\s*\)|"
                         r"\bWIP\b|\bunder\s+development\b|\bcurrently\s+in\s+development\b", re.I)


def in_progress_labels(text: str) -> list[str]:
    """Every "In Progress"-style status label in resume text (case-insensitive; "WIP" only in capitals or brackets)."""
    out = []
    for m in IN_PROGRESS.finditer(unicodedata.normalize("NFKC", text or "")):
        if m.group(0).lower() == "wip" and m.group(0) != "WIP":
            continue
        out.append(m.group(0))
    return out


@dataclass
class Check:
    name: str
    ok: bool
    severity: str = "error"
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    pages: int = 0
    overflow_lines: int = 0
    font_main: str | None = None
    fonts: list[dict] = field(default_factory=list)
    bottom_whitespace: float | None = None
    long_bullets: list[str] = field(default_factory=list)
    runts: list[str] = field(default_factory=list)
    loose_lines: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    extracted_text: str = ""

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.severity == "error")

    def errors(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.severity == "error"]

    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.severity == "warn"]

    def add(self, name: str, ok: bool, detail: str = "", severity: str = "error") -> None:
        from .compile import redact
        self.checks.append(Check(name, bool(ok), severity, redact(detail)))   # reports never quote contact details

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("extracted_text")
        d["ok"] = self.ok
        return d


def _run(cmd: list[str], timeout: int = 60) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr.strip()[:300]}")
    return p.stdout


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", "".join(LIGATURES.get(ch, ch) for ch in s))
    s = s.replace("–", "-").replace("—", "-").replace("−", "-").replace("­", "")
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def key(s: str) -> str:
    """Comparison key: lowercase letters/digits only (immune to line breaks, hyphenation and spacing)."""
    return re.sub(r"[^a-z0-9%+]", "", norm(s).lower())


def pdffonts(pdf: Path) -> list[dict]:
    out = []
    for ln in _run(["pdffonts", str(pdf)]).splitlines()[2:]:
        parts = ln.split()
        if len(parts) >= 7:
            # name type... emb sub uni object ID: emb/sub/uni are the 3 yes/no columns before the object number
            yn = [p for p in parts if p in ("yes", "no")]
            out.append({"name": parts[0], "embedded": yn[0] == "yes" if yn else False,
                        "unicode": yn[2] == "yes" if len(yn) > 2 else False})
    return out


def _lines(page) -> list[dict]:
    """Text lines with `base`: the text baseline (bullet glyphs are taller than the text, so `top` is not a
    reliable line position)."""
    out = page.extract_text_lines(layout=False, strip=True, return_chars=True, x_tolerance=1.2)
    for ln in out:
        txt = [c for c in ln.get("chars", []) if c["text"] not in BULLET_CHARS and c["text"].strip()]
        bases = sorted(round(float(c["bottom"]) - float(c["size"]) * 0.2, 1) for c in txt) or [float(ln["bottom"])]
        ln["base"] = bases[len(bases) // 2]
    return out


def validate_pdf(pdf: Path, expected: list[tuple[str, str]], expected_links: list[dict], contact: dict,
                 settings, compile_info: dict | None = None, image_dir: Path | None = None) -> Report:
    import pdfplumber
    r = Report()
    lay = settings.layout or {}
    min_pt, margin_cm = float(lay.get("min_font_pt", 9)), float(lay.get("min_margin_cm", 1.3))
    max_lines = int((settings.limits or {}).get("bullet_max_lines", 2))
    ci = compile_info or {}

    # compiler log
    r.add("compile_errors", not ci.get("errors"), "; ".join(ci.get("errors") or [])[:500])
    r.add("missing_glyphs", not ci.get("missing_glyphs"), ", ".join(ci.get("missing_glyphs") or []))
    wide = [o for o in ci.get("overfull") or [] if float(re.search(r"\(([\d.]+)pt", o).group(1)) > 1.0]
    r.add("overfull_boxes", not wide, f"{len(wide)} line(s) stick out into the margin: " + " | ".join(w[:90] for w in wide[:3]))
    if ci.get("font_used"):
        pref, fb = (settings.fonts or {}).get("preferred"), (settings.fonts or {}).get("fallback")
        r.add("font_choice", ci["font_used"] in (pref, fb), f"main font: {ci['font_used']}"
              + (f" (fallback for {pref}, metric-compatible)" if ci["font_used"] == fb and fb != pref else ""), "warn")

    with pdfplumber.open(str(pdf)) as doc:
        r.pages = len(doc.pages)
        want = int(settings.page_count)
        if r.pages > want:
            r.overflow_lines = sum(len(_lines(p)) for p in doc.pages[want:])
        r.add("page_count", r.pages == want, f"{r.pages} page(s), want {want}"
              + (f"; {r.overflow_lines} line(s) overflow" if r.overflow_lines else ""))
        blanks = [i + 1 for i, p in enumerate(doc.pages) if len((p.extract_text() or "").strip()) < 20]
        r.add("blank_pages", not blanks, f"blank page(s): {blanks}" if blanks else "")

        sizes, fonts, edge, bad_margin = [], {}, [], []
        texts, links, all_lines = [], [], []
        for pno, page in enumerate(doc.pages, 1):
            W, H = float(page.width), float(page.height)
            m = margin_cm * CM
            for ch in page.chars:
                if not ch["text"].strip():
                    continue
                fn = re.sub(r"^[A-Z]{6}\+", "", ch.get("fontname") or "")
                if ch["text"] not in BULLET_CHARS and not fn.startswith("CMSY"):
                    sizes.append(round(float(ch["size"]), 2))
                    fonts[fn] = fonts.get(fn, 0) + 1
                if ch["x0"] < 0 or ch["x1"] > W or ch["top"] < 0 or ch["bottom"] > H:
                    edge.append(f"p{pno} '{ch['text']}'")
                # glyph boxes include the font's ascender space above the ink: allow it at the top edge (the ink-based
                # visual_margins check below is exact)
                elif (ch["x0"] < m - 0.5 or ch["x1"] > W - m + 0.5 or ch["top"] < m - 0.5 - 0.25 * float(ch["size"])
                      or ch["bottom"] > H - m + 0.5):
                    bad_margin.append(f"p{pno} '{ch['text']}' at x={ch['x0']:.0f}..{ch['x1']:.0f}")
            lines = _lines(page)
            all_lines.append((pno, W, H, lines))
            texts.append(page.extract_text(x_tolerance=1.2) or "")
            for h in page.hyperlinks or []:
                # crop (overlap), not within_bbox: a link box can be shorter than its glyph boxes
                words = page.crop((max(0, h["x0"] - 1), max(0, h["top"] - 1), min(W, h["x1"] + 1),
                                   min(H, h["bottom"] + 1))).extract_text() or ""
                links.append({"uri": h.get("uri"), "text": norm(words), "page": pno})
        labels = in_progress_labels("\n".join(texts))
        r.add("no_in_progress_label", not labels,
              f"the resume says {', '.join(repr(x) for x in dict.fromkeys(labels))}: no project or entry may carry an "
              "\"In Progress\" / \"Ongoing\" / \"WIP\" label (rules 50–51); in-progress work shows only through design "
              "verbs (\"Designing\", \"Architecting\")" if labels else "")
        r.add("min_font_size", not sizes or min(sizes) >= min_pt - 0.05,
              f"smallest text {min(sizes) if sizes else '-'}pt (minimum {min_pt}pt)")
        r.add("clipping", not edge, f"text outside the page: {', '.join(edge[:5])}")
        r.add("margins", not bad_margin, f"text inside the {margin_cm}cm margin: {', '.join(bad_margin[:5])}")
        r.font_main = max(fonts, key=fonts.get) if fonts else None

        # overlapping lines and spacing (page 1 .. n)
        overlaps, tight = [], []
        for pno, W, H, lines in all_lines:
            ls = sorted(lines, key=lambda ln: ln["base"])
            gaps = [b["base"] - a["base"] for a, b in zip(ls, ls[1:]) if b["base"] - a["base"] > 0.5]
            med = statistics.median(gaps) if gaps else 0
            for a, b in zip(ls, ls[1:]):
                same_row = abs(a["base"] - b["base"]) < 0.5
                if same_row:
                    if a["x1"] > b["x0"] + 1 and b["x1"] > a["x0"] + 1:
                        overlaps.append(f"p{pno}: '{a['text'][:30]}' / '{b['text'][:30]}'")
                    continue
                # glyph boxes of tightly set lines touch (the template uses \linespread{0.90}); text overlaps when
                # two baselines are much closer than the usual line pitch
                if med and b["base"] - a["base"] < 0.55 * med:
                    overlaps.append(f"p{pno}: '{a['text'][:30]}' overlaps '{b['text'][:30]}'")
                elif med and b["base"] - a["base"] < 0.9 * med:
                    tight.append(f"p{pno}: '{a['text'][:30]}'")
            if pno == 1 and ls:
                r.bottom_whitespace = round((H - ls[-1]["bottom"]) / H, 3)
        r.add("overlapping_text", not overlaps, "; ".join(overlaps[:4]))
        # word spacing: justified lines stretched far beyond the usual space (uneven, hard to read)
        loose = []
        first_head = next((key(t) for kind, t in expected if kind == "heading"), None)
        for pno, W, H, lines in all_lines:
            per_line = []
            body_from = next((ln["base"] for ln in sorted(lines, key=lambda x: x["base"])
                              if pno == 1 and first_head and key(ln["text"]) == first_head), None)
            for ln in lines:
                if body_from is not None and ln["base"] < body_from:
                    continue          # the template's header line is spaced on purpose
                ch = [c for c in ln.get("chars", []) if c["text"] not in BULLET_CHARS]
                gaps_ = [b["x0"] - a["x1"] for a, b in zip(ch, ch[1:]) if b["x0"] - a["x1"] > 1.0]
                if len(gaps_) >= 4:
                    per_line.append((statistics.median(gaps_), ln["text"]))
            if per_line:
                usual = statistics.median(g for g, _ in per_line)
                loose += [t for g, t in per_line if g > 1.6 * usual]
        r.loose_lines = loose
        r.add("loose_lines", not loose, f"stretched word spacing: {' | '.join(t[:50] for t in loose[:4])}", "warn")
        r.add("line_spacing", not tight, f"unusually tight line gaps: {'; '.join(tight[:4])}", "warn")
        mw = float(lay.get("max_bottom_whitespace", 0.3))
        if r.bottom_whitespace is not None:
            r.add("whitespace", r.bottom_whitespace <= mw,
                  f"{r.bottom_whitespace:.0%} of the page is empty at the bottom (warn above {mw:.0%})", "warn")

        # bullets: lines per bullet, one-word last lines, syllable hyphenation
        hyph = []
        for pno, W, H, lines in all_lines:
            ls = sorted(lines, key=lambda ln: (round(ln["top"]), ln["x0"]))
            cur, cur_lines = None, []

            def close():
                if cur is not None:
                    if len(cur_lines) > max_lines:
                        r.long_bullets.append(f"{len(cur_lines)} lines: {cur_lines[0][:60]}")
                    if len(cur_lines) > 1 and (len(cur_lines[-1].split()) == 1 and len(cur_lines[-1]) < 14):
                        r.runts.append(f"'{cur_lines[-1]}' alone on the last line of: {cur_lines[0][:50]}")
            bullet_x = None
            for ln in ls:
                t = ln["text"]
                if t[:1] in BULLET_CHARS:
                    close()
                    cur, cur_lines, bullet_x = t, [t[1:].strip()], ln["x0"]
                elif cur is not None and bullet_x is not None and ln["x0"] > bullet_x + 2:
                    cur_lines.append(t)
                else:
                    close()
                    cur, cur_lines = None, []
            close()
            for a, b in zip(ls, ls[1:]):
                if re.search(r"[a-z]-$", a["text"]) and re.match(r"^[a-z]", b["text"]):
                    hyph.append(f"{a['text'].split()[-1]}{b['text'].split()[0]}")
        r.add("bullet_length", not r.long_bullets, f"bullets over {max_lines} lines: " + " | ".join(r.long_bullets[:4]))
        r.add("wrapping", not r.runts, " | ".join(r.runts[:4]), "warn")
        expected_join = " ".join(t for _, t in expected)
        split_words = [h for h in hyph if h.replace("-", "") in expected_join.replace("-", "") and h not in expected_join]
        r.add("hyphenation", not split_words, f"words split across lines: {', '.join(split_words[:6])}", "warn")

    # fonts embedded
    try:
        r.fonts = pdffonts(pdf)
        not_emb = [f["name"] for f in r.fonts if not f["embedded"]]
        r.add("fonts_embedded", not not_emb, f"not embedded: {not_emb}" if not_emb else
              ", ".join(f["name"] for f in r.fonts))
    except (RuntimeError, OSError) as e:
        r.add("fonts_embedded", False, f"pdffonts: {e}")

    # extracted text vs intended content (pdftotext, as an ATS would read it)
    try:
        raw = _run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"])
    except (RuntimeError, OSError) as e:
        raw = ""
        r.add("text_extraction", False, f"pdftotext: {e}")
    r.extracted_text = raw
    garbled = re.findall(r"\(cid:\d+\)|�", raw)
    r.add("garbled_text", not garbled and bool(raw.strip()),
          f"{len(garbled)} unreadable glyph(s) in the extracted text" if garbled else ("no text" if not raw.strip() else ""))
    kx = key(raw)
    pos, missing, order_bad = 0, [], []
    for kind, t in expected:
        k = key(t)
        if not k:
            continue
        if kind == "date":
            if k not in kx:
                missing.append(t[:70])
            continue
        at = kx.find(k, pos)
        if at < 0:
            if kx.find(k) >= 0:
                order_bad.append(t[:60])
            else:
                missing.append(t[:70])
            continue
        pos = at + len(k)
    r.add("text_matches_content", not missing, f"missing or garbled in the PDF text: {' | '.join(missing[:5])}")
    r.add("reading_order", not order_bad, f"out of order: {' | '.join(order_bad[:4])}")
    exp_total = sum(len(key(t)) for _, t in expected)
    head_k = len(key(contact.get("text") or " ".join([contact.get("name") or ""] +
                                                      [l["text"] for l in contact.get("links") or []])))
    extra = len(kx) - exp_total - head_k
    r.add("unexpected_text", abs(extra) <= 0.02 * max(1, exp_total) + 20,
          f"{extra:+d} characters vs the content (header + every line)")
    n_bul_exp = sum(1 for kind, _ in expected if kind == "bullet")
    n_bul = sum(raw.count(c) for c in BULLET_CHARS)
    r.add("bullet_characters", n_bul == n_bul_exp, f"{n_bul} bullet characters, expected {n_bul_exp}")
    heads = [t for kind, t in expected if kind == "heading"]
    r.add("headings", all(key(h) in kx for h in heads), f"headings: {', '.join(heads)}")

    # contact details (from the template header) present and readable
    want_contact = [contact.get("name")] + [l["text"] for l in contact.get("links") or []]
    lost = [c for c in want_contact if c and key(c) not in kx]
    r.add("contact_details", not lost, f"{len(lost)} contact item(s) missing from the PDF text" if lost else
          f"{len([c for c in want_contact if c])} present")

    # links: every expected link is there, points where intended, sits on its own text; nothing unexpected
    got = {}
    for l in links:
        got.setdefault(l["uri"], []).append(l)
    exp_uris = {l["url"]: l for l in expected_links}
    miss_l = [u for u in exp_uris if u not in got]
    extra_l = [u for u in got if u not in exp_uris]
    wrong_text = [u for u, ls in got.items() if u in exp_uris and not any(
        key(exp_uris[u]["text"])[:12] in key(x["text"]) for x in ls)]
    detail = []
    if miss_l:
        detail.append(f"{len(miss_l)} link(s) missing")
    if extra_l:
        detail.append(f"unexpected link(s): {[_mask(u) for u in extra_l]}")
    if wrong_text:
        detail.append(f"{len(wrong_text)} link(s) not on their own text")
    r.add("links", not (miss_l or extra_l or wrong_text), "; ".join(detail) or f"{len(exp_uris)} link(s) verified")

    if image_dir is not None:
        r.images = render_pages(pdf, image_dir, int((settings.compile or {}).get("render_dpi", 110)))
        r.add("page_images", bool(r.images), f"{len(r.images)} page image(s)")
        ink = margin_ink(r.images, margin_cm, int((settings.compile or {}).get("render_dpi", 110)))
        r.add("visual_margins", not ink, "; ".join(ink[:3]))
    return r


def _mask(u: str) -> str:
    return re.sub(r"(mailto:|tel:).*", r"\1…", u or "")


def render_pages(pdf: Path, out_dir: Path, dpi: int = 110) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("page-*.png"):
        old.unlink()
    _run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(out_dir / "page")], timeout=120)
    return sorted(str(p) for p in out_dir.glob("page-*.png"))


def margin_ink(images: list[str], margin_cm: float, dpi: int) -> list[str]:
    """Dark pixels inside the left/right/bottom margin bands of the rendered pages (clipped or stray content)."""
    from PIL import Image
    out = []
    band = int(margin_cm * dpi / 2.54) - 2
    for path in images:
        im = Image.open(path).convert("L")
        w, h = im.size
        px = im.load()
        for name, box in (("left", (0, 0, band, h)), ("right", (w - band, 0, w, h)), ("bottom", (0, h - band, w, h))):
            x0, y0, x1, y1 = box
            dark = 0
            for y in range(y0, y1, 2):
                for x in range(x0, x1, 2):
                    if px[x, y] < 128:
                        dark += 1
            if dark > 3:
                out.append(f"{Path(path).name}: ink in the {name} margin")
    return out
