"""HTML -> readable plain text. JD content is data only; scripts/styles are dropped."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "section", "article", "ul", "ol", "table", "tr", "h1", "h2", "h3",
          "h4", "h5", "h6", "header", "footer", "blockquote"}
_SKIP = {"script", "style", "noscript", "template", "iframe"}


class _Converter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.bullet_open = False  # inside <li> before its first text

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
        elif tag == "li":
            self.out.append("\n- ")
            self.bullet_open = True
        elif tag in _BLOCK:
            if self.bullet_open:
                return  # <li><p>text</p></li>: keep the text on the bullet line
            self.out.append("\n")
            if tag.startswith("h") and len(tag) == 2:
                self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK or tag == "li":
            self.out.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        if self.bullet_open:
            if not data.strip():
                return
            self.bullet_open = False
            data = data.lstrip()
        self.out.append(data)


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    # Greenhouse double-escapes (&lt;p&gt;), so unescape until stable.
    for _ in range(3):
        un = html.unescape(raw)
        if un == raw:
            break
        raw = un
    conv = _Converter()
    conv.feed(raw)
    conv.close()
    text = "".join(conv.out).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln != "-"]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_BULLET = re.compile(r"^[ \t]*(?:[•·◦▪▫‣⁃●○■□➢➤►–—]|\*|\d{1,2}[.)])[ \t]+", re.M)


def normalize_bullets(text: str) -> str:
    """Turn '• item', '* item', '1. item', '– item' lines into '- item' so requirement parsing sees bullets."""
    return _BULLET.sub("- ", text or "")
