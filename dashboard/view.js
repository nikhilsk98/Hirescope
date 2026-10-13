/* HireScope dashboard view logic with no DOM: list virtualization math, JD sections, salary range,
   saved views, skip-reason keys, day tabs. Used by app.js (window.HSView) and by tests/test_dashboard.py (Node). */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.HSView = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------- virtualization: per-item heights (estimated until measured) + prefix sums ----------
  class Heights {
    constructor(n, estimate) { this.est = estimate; this.h = new Float64Array(n).fill(estimate); this.pre = null; }
    get length() { return this.h.length; }
    set(i, px) { if (i >= 0 && i < this.h.length && px > 0 && Math.abs(this.h[i] - px) > 0.5) { this.h[i] = px; this.pre = null; } }
    _build() {
      if (this.pre) return;
      this.pre = new Float64Array(this.h.length + 1);
      for (let i = 0; i < this.h.length; i++) this.pre[i + 1] = this.pre[i] + this.h[i];
    }
    offsetOf(i) { this._build(); return this.pre[Math.max(0, Math.min(this.h.length, i))]; }
    total() { return this.offsetOf(this.h.length); }
    indexAt(y) {   // first item whose bottom edge is below y
      this._build();
      let lo = 0, hi = this.h.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (this.pre[mid + 1] <= y) lo = mid + 1; else hi = mid; }
      return Math.min(lo, Math.max(0, this.h.length - 1));
    }
  }
  /** [start, end) of items intersecting [top, top + height], plus `overscan` items either side. */
  function visibleRange(heights, top, height, overscan) {
    const n = heights.length; if (!n) return [0, 0];
    const o = overscan == null ? 6 : overscan;
    const s = heights.indexAt(Math.max(0, top));
    let e = heights.indexAt(Math.max(0, top + height)) + 1;
    return [Math.max(0, s - o), Math.min(n, e + o)];
  }

  // ---------- Full JD: lines -> blocks -> collapsible sections ----------
  const REQ_HEAD = /^(minimum|basic|required|requirements|qualifications|what you('|’)ll need|what we('|’)re looking for|you have|what you bring|to succeed|what we (require|value|need))/i;
  const isHeading = (line) => line.length < 90 && !/[.!?]$/.test(line) && !line.startsWith("- ");
  function jdBlocks(text) {
    const out = []; let ul = null;
    for (const raw of String(text || "").split("\n")) {
      const line = raw.trim().replace(/^#+\s*/, "");
      if (!line) { ul = null; continue; }
      if (line.startsWith("- ")) { if (!ul) { ul = { type: "ul", items: [] }; out.push(ul); } ul.items.push(line.slice(2)); continue; }
      ul = null;
      out.push(isHeading(line) ? { type: "h", text: line } : { type: "p", text: line });
    }
    return out;
  }
  /** [{heading: string|null, requirements: bool, blocks: [...]}]: content before the first heading has heading null. */
  function jdSections(text) {
    const secs = []; let cur = null;
    for (const b of jdBlocks(text)) {
      if (b.type === "h") { cur = { heading: b.text, requirements: REQ_HEAD.test(b.text), blocks: [] }; secs.push(cur); continue; }
      if (!cur) { cur = { heading: null, requirements: false, blocks: [] }; secs.push(cur); }
      cur.blocks.push(b);
    }
    return secs;
  }

  // ---------- salary range filter (USD, top/bottom of the posted range) ----------
  /** min/max 0 = open-ended. A job matches when its posted range overlaps [min, max]. Unstated salary passes
      unless `knownOnly`. Non-USD salaries count as unstated. */
  function salaryMatch(salary, min, max, knownOnly) {
    const lo = Number(min) || 0, hi = Number(max) || 0;
    const usd = salary && salary.max && (!salary.currency || salary.currency === "USD");
    if (!usd) return !knownOnly;
    if (lo && salary.max < lo) return false;
    if (hi && (salary.min || salary.max) > hi) return false;
    return true;
  }
  const money = (n) => `$${Math.round(n / 1000)}k`;
  function salaryLabel(min, max) {
    if (min && max) return `${money(min)}–${money(max)}`;
    if (min) return `≥ ${money(min)}`;
    if (max) return `≤ ${money(max)}`;
    return "";
  }

  // ---------- saved views (per track): filters + sort + search + salary + view ----------
  const VIEW_KEYS = ["view", "sort", "q", "filters", "salary", "salaryMax", "salaryKnown"];
  function snapshot(state) {
    const s = {};
    for (const k of VIEW_KEYS) if (state[k] != null) s[k] = JSON.parse(JSON.stringify(state[k]));
    s.filters = s.filters || {};
    return s;
  }
  function saveView(list, name, state) {
    const n = String(name || "").trim().slice(0, 60);
    if (!n) throw new Error("name required");
    const rest = (list || []).filter((v) => v.name.toLowerCase() !== n.toLowerCase());
    return [...rest, { name: n, state: snapshot(state) }].sort((a, b) => a.name.localeCompare(b.name));
  }
  function applyView(state, saved) {
    for (const k of VIEW_KEYS) delete state[k];
    Object.assign(state, JSON.parse(JSON.stringify(saved.state)));
    state.filters = state.filters || {};
    return state;
  }
  const deleteView = (list, name) => (list || []).filter((v) => v.name !== name);

  // ---------- skip-reason picker keys: 1..9 then 0 ----------
  function reasonForKey(key, reasons) {
    const i = key === "0" ? 9 : /^[1-9]$/.test(key) ? Number(key) - 1 : -1;
    return i >= 0 && i < reasons.length ? reasons[i] : null;
  }
  const keyForIndex = (i) => (i < 9 ? String(i + 1) : i === 9 ? "0" : "");

  // ---------- day tabs: the most recent days as tabs, older ones in a dropdown ----------
  function dayTabs(days, n) {
    const d = days || []; const k = n == null ? 4 : n;
    return { recent: d.slice(0, k), older: d.slice(k) };
  }

  return { Heights, visibleRange, jdBlocks, jdSections, salaryMatch, salaryLabel, snapshot, saveView, applyView,
           deleteView, reasonForKey, keyForIndex, dayTabs, REQ_HEAD };
});
