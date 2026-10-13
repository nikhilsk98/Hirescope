"""Dashboard view logic (dashboard/view.js, run in Node) and dashboard data additions: virtualization math,
collapsible JD sections, salary range, saved views, skip-reason keys, day tabs, Seen badge, engineering blogs,
design-token contrast (WCAG AA). (All DEMO data.)"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jobsearch.output.dashboard import _badges, eng_blogs

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "dashboard" / "view.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="node not installed")


def _node(script: str):
    out = subprocess.run([NODE, "-e", f"const V = require({json.dumps(str(VIEW))});\n{script}"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_virtual_range_and_measured_heights():
    js = _node("""
const H = new V.Heights(1000, 100);
const a = V.visibleRange(H, 0, 800, 0);          // rows 0..8 visible
const b = V.visibleRange(H, 5000, 800, 2);       // row 50 at the top, 2 rows of overscan
H.set(0, 300);                                   // a measured row shifts every later offset
const c = [H.offsetOf(1), H.offsetOf(10), H.total(), H.indexAt(299), H.indexAt(300)];
const empty = V.visibleRange(new V.Heights(0, 100), 0, 800);
const end = V.visibleRange(H, 1e9, 800, 3);
console.log(JSON.stringify({a, b, c, empty, end}));""")
    assert js["a"] == [0, 9]
    assert js["b"] == [48, 61]
    assert js["c"] == [300, 1200, 100200, 0, 1]
    assert js["empty"] == [0, 0]
    assert js["end"][1] == 1000 and js["end"][0] >= 990


@needs_node
def test_jd_sections_are_collapsible_groups():
    jd = ("Intro paragraph about the team.\n\nAbout the role\nYou will build things.\n- Ship code\n- Review code\n\n"
          "Minimum qualifications\n- BS in CS.\n- 2+ years of Python.\n\nNice to have\nKubernetes experience is a plus.")
    js = _node(f"console.log(JSON.stringify(V.jdSections({json.dumps(jd)})));")
    assert [s["heading"] for s in js] == [None, "About the role", "Minimum qualifications", "Nice to have"]
    assert [s["requirements"] for s in js] == [False, False, True, False]
    assert js[1]["blocks"] == [{"type": "p", "text": "You will build things."}, {"type": "ul", "items": ["Ship code", "Review code"]}]
    assert js[2]["blocks"][0]["items"] == ["BS in CS.", "2+ years of Python."]


@needs_node
def test_salary_range_filter():
    js = _node("""
const s = (min, max, cur) => ({min, max, currency: cur || "USD"});
console.log(JSON.stringify([
  V.salaryMatch(s(120000, 160000), 100000, 0, false),     // overlaps an open-ended range
  V.salaryMatch(s(120000, 160000), 170000, 0, false),     // top of range below the minimum
  V.salaryMatch(s(120000, 160000), 0, 110000, false),     // bottom of range above the maximum
  V.salaryMatch(s(120000, 160000), 150000, 200000, false),
  V.salaryMatch(null, 100000, 0, false),                  // unstated passes unless "stated only"
  V.salaryMatch(null, 0, 0, true),
  V.salaryMatch(s(90000, 110000, "CAD"), 0, 0, true),     // non-USD counts as unstated
  V.salaryLabel(100000, 200000), V.salaryLabel(100000, 0), V.salaryLabel(0, 150000), V.salaryLabel(0, 0)]));""")
    assert js == [True, False, False, True, True, False, False, "$100k–$200k", "≥ $100k", "≤ $150k", ""]


@needs_node
def test_saved_views_round_trip():
    js = _node("""
let list = [];
const st = {view: "all", sort: "newest", q: "rust", filters: {label: ["Strong"]}, salary: 120000, salaryMax: 0, salaryKnown: true, modes: {all: "table"}};
list = V.saveView(list, "  Strong Rust  ", st);
list = V.saveView(list, "strong rust", Object.assign({}, st, {q: "go"}));   // same name (any case) replaces
list = V.saveView(list, "Alpha", {view: "last24", sort: "overall", filters: {}});
const target = {view: "mine", sort: "ats", q: "x", filters: {company: ["A"]}, salary: 1, modes: {mine: "cards"}};
V.applyView(target, list[1]);
let bad = null; try { V.saveView(list, "  ", st); } catch (e) { bad = e.message; }
console.log(JSON.stringify({names: list.map((v) => v.name), target, bad, after: V.deleteView(list, "Alpha").map((v) => v.name)}));""")
    assert js["names"] == ["Alpha", "strong rust"]
    t = js["target"]
    assert t["q"] == "go" and t["filters"] == {"label": ["Strong"]} and t["salary"] == 120000 and t["salaryKnown"] is True
    assert t["modes"] == {"mine": "cards"}           # per-view card/table modes aren't part of a saved view
    assert js["bad"] == "name required" and js["after"] == ["strong rust"]


@needs_node
def test_skip_reason_keys_and_day_tabs():
    js = _node("""
const R = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"];
console.log(JSON.stringify({
  keys: ["1", "9", "0", "x", "Enter"].map((k) => V.reasonForKey(k, R)),
  idx: [0, 8, 9, 10].map(V.keyForIndex),
  short: V.reasonForKey("5", ["a", "b"]),
  tabs: V.dayTabs(["2026-10-12", "2026-10-11", "2026-10-10", "2026-10-09", "2026-10-08", "2026-10-07"], 4),
}));""")
    assert js["keys"] == ["a", "i", "j", None, None]
    assert js["idx"] == ["1", "9", "0", ""]
    assert js["short"] is None
    assert js["tabs"] == {"recent": ["2026-10-12", "2026-10-11", "2026-10-10", "2026-10-09"], "older": ["2026-10-08", "2026-10-07"]}


def _job(**kw):
    base = {"job_status": "seen", "first_seen": "2026-10-10T14:00:00+00:00", "company": "DemoCo", "signals": {}}
    return {**base, **kw}


def test_seen_badge_next_to_new():
    tz = "America/New_York"
    assert "new" in _badges(_job(first_seen="2026-10-12T14:00:00+00:00"), "2026-10-12", "2026-10-12", tz, set())
    old = _badges(_job(), "2026-10-12", "2026-10-12", tz, set())
    assert "seen" in old and "new" not in old
    assert "seen" not in _badges(_job(job_status="updated"), "2026-10-12", "2026-10-12", tz, set())


def test_eng_blogs_match_job_company_names():
    blogs = {"Stripe": "https://stripe.dev/blog/topic/engineering", "Expedia Group": "https://medium.com/expedia-group-tech",
             "Bad": "javascript:alert(1)"}
    got = eng_blogs({"Stripe, Inc.", "expedia group", "Unknown Co", "Bad"}, blogs)
    assert got == {"Stripe, Inc.": "https://stripe.dev/blog/topic/engineering", "expedia group": "https://medium.com/expedia-group-tech"}


def test_eng_blogs_config_is_valid():
    import yaml
    data = yaml.safe_load((ROOT / "config" / "eng_blogs.yaml").read_text(encoding="utf-8"))
    assert len(data) >= 50 and all(re.match(r"^https://[^\s]+$", u) for u in data.values())


# --- WCAG AA contrast of the design tokens (both themes) --------------------------------------------
def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", block))


def _lum(hexc: str) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# text color token -> background tokens it is drawn on
PAIRS = {
    "text": ["bg", "surface", "surface-2"], "text-2": ["bg", "surface", "surface-2"], "text-3": ["bg", "surface", "surface-2"],
    "accent": ["bg", "surface"], "accent-fg": ["accent"],
    "strong": ["strong-bg", "surface"], "stretch": ["stretch-bg", "surface"], "weak": ["weak-bg", "surface"],
    "flag": ["flag-bg", "surface"], "coop": ["coop-bg", "surface"], "ft": ["ft-bg", "surface"], "unclear": ["unclear-bg", "surface"],
    "kw-match": ["kw-match-bg", "surface-2"], "kw-syn": ["kw-syn-bg", "surface-2"], "kw-miss": ["kw-miss-bg", "surface-2"],
    "info": ["info-bg"], "warn": ["warn-bg"], "err": ["err-bg"],
}


def theme_tokens() -> dict[str, dict[str, str]]:
    css = (ROOT / "dashboard" / "styles.css").read_text(encoding="utf-8")
    light = _tokens(re.search(r":root \{(.*?)\n\}", css, re.S).group(1))
    dark = {**light, **_tokens(re.search(r':root\[data-theme="dark"\] \{(.*?)\n\}', css, re.S).group(1))}
    return {"light": light, "dark": dark}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_design_tokens_meet_wcag_aa(theme):
    t = theme_tokens()[theme]
    low = [(fg, bg, round(contrast(t[fg], t[bg]), 2)) for fg, bgs in PAIRS.items() for bg in bgs if contrast(t[fg], t[bg]) < 4.5]
    assert not low, f"{theme}: pairs below 4.5:1: {low}"
