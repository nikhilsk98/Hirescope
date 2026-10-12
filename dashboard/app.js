/* HireScope dashboard (vanilla JS, no build, works on file://).
   Data: window.META + window.JOBS (summaries) from data.js; per-job details load on demand from details/d_N.js.
   All job text is rendered with textContent / DOM nodes: JD content is data, never markup. */
(function () {
  "use strict";

  const JOBS = Array.isArray(window.JOBS) ? window.JOBS : [];
  const META = window.META || {};
  const BY_ID = new Map(JOBS.map((j) => [j.id, j]));
  const K = { statuses: "jobsearch.statuses.v1", overrides: "jobsearch.trackOverrides.v1", theme: "jobsearch.theme",
              ui: "jobsearch.ui.v2", exported: "jobsearch.lastExport", tip: "jobsearch.tipDismissed" };
  const STATUSES = ["", "interested", "shortlisted", "package exported", "applied", "interviewing", "offer", "rejected", "skipped"];
  const SKIP_REASONS = ["not interested in role", "location", "too senior", "too junior", "missing skills", "company",
                        "compensation", "visa/sponsorship", "start date", "duplicate", "other"];
  const TRACKS = { coop_intern: "Co-op / Intern", full_time: "Full-time", unclear: "Unclear" };
  const PAGE = 60;

  // ---------- tiny DOM helpers ----------
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, props, ...kids) => {
    const n = document.createElement(tag);
    if (props) for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "style") n.style.cssText = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of kids.flat(Infinity)) if (c != null && c !== false) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return n;
  };
  const svg = (tag, attrs, ...kids) => {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    n.append(...kids); return n;
  };
  const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const fmtTime = (iso) => {
    try {
      const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: META.timezone || undefined, month: "short", day: "numeric",
        hour: "numeric", minute: "2-digit" }).formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
      return `${p.month} ${p.day}, ${p.hour}:${p.minute} ${p.dayPeriod || ""}`.trim();
    } catch (e) { return iso; }
  };
  const fmtDate = (d) => { try { return new Date(d + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }); } catch (e) { return d; } };
  const daysUntil = (d) => Math.round((new Date(d + "T12:00:00") - new Date((META.today || new Date().toISOString().slice(0, 10)) + "T12:00:00")) / 864e5);

  // ---------- state ----------
  let statuses = store.get(K.statuses, {});
  const overrides = store.get(K.overrides, {});
  const ui = Object.assign({ track: "full_time", both: false, mode: "cards", compact: false, perTrack: {} }, store.get(K.ui, {}));
  const tstate = () => (ui.perTrack[ui.track] = ui.perTrack[ui.track] || { view: null, sort: "overall", filters: {}, q: "" });
  const saveUI = () => store.set(K.ui, ui);
  const keyOf = (j) => `${j.company}::${j.source}::${j.job_id}`;
  const statusOf = (j) => (statuses[keyOf(j)] || {}).status || "";
  const trackOf = (j) => overrides[keyOf(j)] || j.track;

  function setStatus(j, patch) {
    const k = keyOf(j);
    const before = statuses[k] ? { ...statuses[k] } : null;
    statuses[k] = Object.assign({ company: j.company, title: j.title, job_id: j.job_id, source: j.source }, statuses[k] || {}, patch,
      { updated_at: new Date().toISOString() });
    store.set(K.statuses, statuses);
    renderBanners();
    return () => { if (before) statuses[k] = before; else delete statuses[k]; store.set(K.statuses, statuses); renderBanners(); renderAll(true); if (pJob) renderActions(); };
  }

  // ---------- theme ----------
  const applyTheme = (t) => (t ? document.documentElement.setAttribute("data-theme", t) : document.documentElement.removeAttribute("data-theme"));
  applyTheme(store.get(K.theme, null));
  $("#theme").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark"; store.set(K.theme, next); applyTheme(next);
  });

  // ---------- toasts, clipboard, download ----------
  function toast(msg, undo) {
    const t = el("div", { class: "toast" }, el("span", { text: msg }));
    if (undo) t.append(el("button", { type: "button", onclick: () => { undo(); t.remove(); toast("Undone"); } }, "Undo"));
    $("#toasts").append(t); setTimeout(() => t.remove(), undo ? 6000 : 2400);
  }
  function download(name, text, type) {
    const url = URL.createObjectURL(new Blob([text], { type: type || "text/plain;charset=utf-8" }));
    const a = el("a", { href: url, download: name }); document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fall back */ }
    const ta = el("textarea", { style: "position:fixed;opacity:0" }); ta.value = text; document.body.append(ta); ta.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch (e) { ok = false; } ta.remove(); return ok;
  }

  // ---------- details (lazy chunks) ----------
  const DETAILS = new Map();
  const loading = new Map();
  window.__details = (obj) => { for (const [id, d] of Object.entries(obj)) DETAILS.set(Number(id), d); };
  function loadDetail(j) {
    if (DETAILS.has(j.id)) return Promise.resolve(DETAILS.get(j.id));
    if (j.chunk == null) return Promise.resolve(null);
    if (!loading.has(j.chunk)) {
      loading.set(j.chunk, new Promise((res) => {
        const s = el("script", { src: `details/d_${j.chunk}.js` });
        s.onload = () => res(); s.onerror = () => res(); document.body.append(s);
      }));
    }
    return loading.get(j.chunk).then(() => DETAILS.get(j.id) || null);
  }

  // ---------- derived ----------
  const freshH = (j) => (j.fresh_h != null ? j.fresh_h : null);
  const isFresh24 = (j) => freshH(j) != null && freshH(j) <= 24;
  const inTrack = (j) => ui.both || trackOf(j) === ui.track || trackOf(j) === "unclear";
  const visibleJobs = () => JOBS.filter((j) => !j.filtered && inTrack(j));
  const DONE = ["applied", "skipped", "rejected", "interviewing", "offer"];
  const applyQueue = () => (META.apply_today ? (ui.both ? [...(META.apply_today.coop_intern || []), ...(META.apply_today.full_time || [])] : META.apply_today[ui.track] || []) : [])
    .map((id) => BY_ID.get(id)).filter((j) => j && !DONE.includes(statusOf(j))).slice(0, ui.both ? 10 : 5);

  const VIEWS = [
    { id: "last24", label: "Last 24h", ic: "⚡", group: "Discover", jobs: () => visibleJobs().filter((j) => isFresh24(j)) },
    { id: "apply", label: "Apply Today", ic: "★", jobs: () => applyQueue() },
    { id: "all", label: "All Jobs", ic: "▤", jobs: () => visibleJobs() },
    { id: "mine", label: "My Companies", ic: "♥", jobs: () => visibleJobs().filter((j) => j.my_company) },
    { id: "shortlisted", label: "Shortlisted", ic: "✓", group: "Mine", jobs: () => JOBS.filter((j) => inTrack(j) && statusOf(j) === "shortlisted") },
    { id: "closing", label: "Closing Soon", ic: "⏳", jobs: () => visibleJobs().filter((j) => j.deadline && daysUntil(j.deadline) >= 0 && daysUntil(j.deadline) <= 7) },
    { id: "skillgaps", label: "Skill Gaps", ic: "◔", group: "Insights", special: true, count: () => ((META.skill_gaps || {})[`${ui.track}:30`] || { skills: [] }).skills.length },
    { id: "missed", label: "Missed Matches", ic: "◎", jobs: () => (META.missed || []).map((id) => BY_ID.get(id)).filter((j) => j && inTrack(j)) },
    { id: "filtered", label: "Filtered Out", ic: "⊘", jobs: () => JOBS.filter((j) => j.filtered && inTrack(j)) },
    { id: "coverage", label: "Coverage", ic: "◍", group: "System", special: true, count: () => (META.coverage || []).length },
  ];
  const viewById = (id) => VIEWS.find((v) => v.id === id) || VIEWS[2];
  const defaultView = () => (viewById("last24").jobs().length ? "last24" : "all");

  // ---------- filters ----------
  function counts(xs) { const m = new Map(); for (const x of xs) if (x != null && x !== "") m.set(x, (m.get(x) || 0) + 1); return [...m.entries()].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0]))); }
  function stateOf(loc) {
    const s = loc || "Unknown";
    if (/remote/i.test(s)) return "Remote (US)";
    const m = /,\s*([A-Z]{2})\b/.exec(s);
    return m ? m[1] : s.split(/[·,]/)[0].trim().slice(0, 30);
  }
  const BADGE = {
    new: ["New", "good"], fresh: ["Fresh", "good"], updated: ["Updated", "warn"], reopened: ["Reopened", "warn"],
    closing: ["Closing soon", "flag"], ghost: ["Ghost risk", "warn"], newgrad: ["New Grad", "info"], eligibility: ["Check eligibility", "warn"],
    broken: ["Broken link", "flag"], nojd: ["No JD", ""], meaning: ["Meaning match", "info"], mine: ["My Company", "info"],
  };
  const RED = { sponsorship: "No sponsorship", citizenship: "US citizenship required", clearance: "Clearance required" };
  function flagNames(j) {
    const out = Object.entries(RED).filter(([k, v]) => (j.flags || {})[k] === v).map(([, v]) => v);
    for (const b of j.badges || []) if (["ghost", "eligibility", "broken", "closing", "nojd", "newgrad", "meaning"].includes(b)) out.push(BADGE[b][0]);
    return out;
  }
  const FILTERS = [
    { id: "company", label: "Company", values: (js) => counts(js.map((j) => j.company)), test: (j, v) => v.includes(j.company), search: true },
    { id: "tier", label: "Tier", values: (js) => counts(js.map((j) => (j.tier ? `Tier ${j.tier}` : "Untiered"))), test: (j, v) => v.includes(j.tier ? `Tier ${j.tier}` : "Untiered") },
    { id: "label", label: "Label", values: (js) => counts(js.map((j) => j.label || "—")), test: (j, v) => v.includes(j.label || "—") },
    { id: "level", label: "Level", values: (js) => counts(js.map((j) => j.level)), test: (j, v) => v.includes(j.level) },
    { id: "mode", label: "Work mode", values: (js) => counts(js.map((j) => j.work_mode)), test: (j, v) => v.includes(j.work_mode) },
    { id: "location", label: "Location", values: (js) => counts(js.map((j) => stateOf(j.location))), test: (j, v) => v.includes(stateOf(j.location)), search: true },
    { id: "status", label: "My status", values: (js) => counts(js.map((j) => statusOf(j) || "(none)")), test: (j, v) => v.includes(statusOf(j) || "(none)") },
    { id: "flags", label: "Flags & badges", values: (js) => counts(js.flatMap(flagNames)), test: (j, v) => flagNames(j).some((f) => v.includes(f)) },
    { id: "posted", label: "Posted within", single: true, values: () => [["24h"], ["3d"], ["7d"], ["30d"]],
      test: (j, v) => { const h = freshH(j); const lim = { "24h": 24, "3d": 72, "7d": 168, "30d": 720 }[v[0]]; return h != null && h <= lim; } },
    { id: "score", label: "Min score", single: true, values: () => [["40"], ["50"], ["60"], ["70"], ["80"]], test: (j, v) => (j.score || 0) >= Number(v[0]) },
  ];
  function applyFilters(list) {
    const st = tstate();
    const q = (st.q || "").trim().toLowerCase();
    return list.filter((j) => {
      for (const f of FILTERS) { const v = st.filters[f.id]; if (v && v.length && !f.test(j, v)) return false; }
      if (ui.day && j.day !== ui.day) return false;
      if (q) {
        const hay = `${j.title} ${j.company} ${j.location} ${(j.terms || []).join(" ")} ${(j.ats && j.ats.top || []).join(" ")} ${(j.reasons || []).join(" ")}`.toLowerCase();
        if (!q.split(/\s+/).every((w) => hay.includes(w))) return false;
      }
      return true;
    });
  }

  // ---------- sorting (secondary sort is always newest posted) ----------
  const postedKey = (j) => (j.posted ? Date.parse(j.posted) : j.discovered ? Date.parse(j.first_seen) : -Infinity);
  const SORTS = {
    newest: (a, b) => postedKey(b) - postedKey(a),
    match: (a, b) => (b.qual ?? -1) - (a.qual ?? -1),
    overall: (a, b) => (b.score ?? -1) - (a.score ?? -1),
    ats: (a, b) => ((b.ats && b.ats.c) ?? -1) - ((a.ats && a.ats.c) ?? -1),
    deadline: (a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999"),
    company: (a, b) => a.company.localeCompare(b.company),
  };
  function sortJobs(list, view) {
    if (view === "apply") return list;  // queue order is the recommendation
    const s = SORTS[tstate().sort] || SORTS.overall;
    return [...list].sort((a, b) => s(a, b) || postedKey(b) - postedKey(a) || a.id - b.id);
  }

  // ---------- header, banners, tiles ----------
  function renderHeader() {
    const s = META.summary || {};
    if (META.last_run) {
      const failed = (s.sources_failed || 0) + (s.sources_manual || 0);
      $("#run-dot").className = "dot " + (failed ? "red" : s.sources_partial ? "amber" : "green");
      $("#run-text").textContent = `Last run ${fmtTime(META.last_run)} · ${s.sources_ok ?? 0} of ${s.sources_checked ?? 0} checked`;
      $("#run-dot").parentElement.title = `${s.sources_ok} of ${s.sources_checked} companies/sources checked successfully this run; ` +
        `${s.sources_failed || 0} failed, ${s.sources_manual || 0} manual, ${s.sources_partial || 0} partial, ${s.sources_skipped || 0} not due. ` +
        `Jobs found ${s.jobs_found}, new ${s.new}, updated ${s.updated}, closed ${s.closed}.`;
    }
    const dp = $("#day-picker");
    dp.textContent = "";
    dp.append(el("option", { value: "", text: "All days" }));
    for (const d of META.days || []) dp.append(el("option", { value: d, text: d, selected: ui.day === d }));
    dp.addEventListener("change", () => { ui.day = dp.value || null; saveUI(); renderAll(); });
    const L = META.learned || {};
    const box = $("#learned"); box.textContent = "";
    box.append(el("h4", { text: "Learned adjustments" }), L.adjustments && L.adjustments.length
      ? el("ul", { class: "list-plain" }, L.adjustments.map((a) => el("li", { text: a }))) : el("div", { text: L.status || "None yet." }));
  }
  function unsynced() {
    const last = store.get(K.exported, null);
    return Object.values(statuses).some((s) => !last || (s.updated_at || "") > last) || (Object.keys(overrides).length > 0 && !last);
  }
  function renderBanners() {
    const box = $("#banners"); box.textContent = "";
    const s = META.summary || {};
    const fails = s.failures || [], manual = s.manual || [], partial = s.partial || [];
    if (fails.length || manual.length) {
      const d = el("details", null, el("summary", { text: `Last run had problems: ${fails.length} failed, ${manual.length} need a manual check, ${partial.length} partial listings` }),
        el("ul", null, fails.map((f) => el("li", { text: `${f.company} (${f.source}) failed — ${f.error}` })),
          manual.map((m) => el("li", null, `${m.company}: manual check needed `, safeUrl(m.url) ? el("a", { href: m.url, target: "_blank", rel: "noopener noreferrer", text: m.url }) : ""))));
      box.append(el("div", { class: "banner err" }, d));
    } else if (partial.length) {
      box.append(el("div", { class: "banner warn" }, `${partial.length} sources returned partial listings (page caps); closures for those are verified per job. See Coverage.`));
    }
    if (META.partial_run) box.append(el("div", { class: "banner warn", text: "The last run checked only one company/source; other companies were not re-checked." }));
    if (/example/i.test(META.resume_path || "")) box.append(el("div", { class: "banner warn", text: `Scored against ${META.resume_path} (DEMO resume).` }));
    if (unsynced()) box.append(el("div", { class: "banner info" }, el("span", { text: "Unsynced changes — your statuses live only in this browser until you export them." }),
      el("button", { class: "btn small x", type: "button", onclick: exportStatuses }, "Export statuses")));
    if (!store.get(K.tip, false)) {
      box.append(el("div", { class: "banner info" },
        el("div", null, el("b", { text: "Tip: " }), "Co-op / Internships and Full-time are separate workspaces (keys 1 / 2). Open a job (Enter), shortlist (s), skip (x), next (j / k), shortcuts (?). ",
          "Statuses save here instantly; Export statuses hands them to the pipeline."),
        el("button", { class: "btn small ghost x", type: "button", "aria-label": "Dismiss tip", onclick: () => { store.set(K.tip, true); renderBanners(); } }, "✕")));
    }
  }
  function renderTiles() {
    const box = $("#tiles"); box.textContent = "";
    const vis = visibleJobs();
    const latest = (META.days || [])[0];
    const tiles = [
      ["New today", vis.filter((j) => j.day === latest).length, () => { ui.day = latest || null; saveUI(); setView("all"); }],
      ["Strong matches", vis.filter((j) => j.label === "Strong").length, () => { tstate().filters = { label: ["Strong"] }; setView("all"); }],
      ["Apply today", applyQueue().length, () => setView("apply")],
      ["Follow-ups due", "—", null, "Follow-up reminders arrive with My Applications (Phase 4)"],
      ["Closing soon", viewById("closing").jobs().length, () => setView("closing")],
    ];
    for (const [l, n, fn, title] of tiles) {
      box.append(el("button", { class: "tile" + (fn ? "" : " disabled"), type: "button", onclick: fn, title, "aria-label": `${l}: ${n}` },
        el("span", { class: "n", text: typeof n === "number" ? n.toLocaleString() : n }), el("span", { class: "l", text: l })));
    }
  }
  function renderCounts() {
    for (const b of document.querySelectorAll(".track-tab")) {
      const t = b.dataset.track;
      b.setAttribute("aria-selected", String(t === ui.track));
      // true totals from the run (the dashboard holds the top N per track)
      const bt = ((META.summary || {}).by_track || {});
      const total = bt[t] ? bt[t].visible + ((bt.unclear || {}).visible || 0) : null;
      const shown = JOBS.filter((j) => !j.filtered && (trackOf(j) === t || trackOf(j) === "unclear")).length;
      b.querySelector(".count").textContent = (total != null ? total : shown).toLocaleString();
      b.title = total != null && total > shown ? `${total.toLocaleString()} matching jobs; the ${shown.toLocaleString()} best-scored are on this dashboard` : "";
    }
    $("#both").checked = ui.both;
    const nav = $("#views"); nav.textContent = "";
    const cur = tstate().view || defaultView();
    let group = null;
    for (const v of VIEWS) {
      if (v.group && v.group !== group) { group = v.group; nav.append(el("div", { class: "group", text: group })); }
      const n = v.special ? v.count() : v.jobs().length;
      nav.append(el("button", { class: "view-btn", type: "button", "aria-current": String(v.id === cur), onclick: () => setView(v.id) },
        el("span", { class: "ic", "aria-hidden": "true", text: v.ic }), v.label, el("span", { class: "n", text: n.toLocaleString() })));
    }
  }

  // ---------- filter chips ----------
  let openPop = null;
  const closePop = () => { if (openPop) { openPop.remove(); openPop = null; } };
  document.addEventListener("click", (e) => { if (openPop && !openPop.contains(e.target) && !e.target.closest(".filter-chip")) closePop(); });
  function renderFilters(base) {
    const box = $("#filters"); box.textContent = "";
    const st = tstate();
    for (const f of FILTERS) {
      const sel = st.filters[f.id] || [];
      const wrap = el("div", { class: "filter-wrap" });
      wrap.append(el("button", { class: "filter-chip" + (sel.length ? " active" : ""), type: "button", "aria-haspopup": "true", "aria-expanded": "false",
        onclick: (e) => { e.stopPropagation(); const was = openPop && openPop.dataset.f === f.id; closePop(); if (!was) openPopover(wrap, f, base); } },
        sel.length ? `${f.label}: ${sel.length > 2 ? sel.length + " selected" : sel.join(", ")}` : `${f.label} ▾`));
      box.append(wrap);
    }
    if (FILTERS.some((f) => (st.filters[f.id] || []).length) || st.q || ui.day) {
      box.append(el("button", { class: "btn small ghost", type: "button", onclick: () => { st.filters = {}; st.q = ""; $("#q").value = ""; ui.day = null; $("#day-picker").value = ""; saveUI(); renderAll(); } }, "Clear all"));
    }
  }
  function openPopover(wrap, f, base) {
    const st = tstate();
    const sel = new Set(st.filters[f.id] || []);
    const pop = el("div", { class: "popover", role: "dialog", "aria-label": f.label }); pop.dataset.f = f.id;
    const values = f.values(base);
    const list = el("div");
    const draw = (q) => {
      list.textContent = "";
      for (const [v, n] of values.filter(([x]) => !q || String(x).toLowerCase().includes(q)).slice(0, 300)) {
        const cb = el("input", { type: f.single ? "radio" : "checkbox", name: `f-${f.id}`, checked: sel.has(String(v)) });
        cb.addEventListener("change", () => {
          if (f.single) { sel.clear(); if (cb.checked) sel.add(String(v)); } else if (cb.checked) sel.add(String(v)); else sel.delete(String(v));
          st.filters[f.id] = [...sel]; saveUI(); renderCounts(); renderList(true); reopen();
        });
        list.append(el("label", null, cb, String(v), n != null ? el("span", { class: "n", text: n }) : null));
      }
    };
    const reopen = () => { const w = [...document.querySelectorAll(".filter-wrap")][FILTERS.indexOf(f)]; if (w && !w.contains(pop)) { w.append(pop); openPop = pop; } };
    if (f.search) { const s = el("input", { type: "search", placeholder: `Find ${f.label.toLowerCase()}…`, "aria-label": `Find ${f.label}` }); s.addEventListener("input", () => draw(s.value.toLowerCase())); pop.append(s); setTimeout(() => s.focus(), 0); }
    draw("");
    pop.append(list, el("div", { class: "row" }, el("button", { class: "btn small ghost", type: "button", onclick: () => { delete st.filters[f.id]; saveUI(); closePop(); renderAll(); } }, "Clear")));
    wrap.append(pop); openPop = pop;
  }

  // ---------- cards / table ----------
  const avatarColor = (s) => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0; return `hsl(${h % 360} 42% 42%)`; };
  function ring(score, label, size = 52) {
    const r = size / 2 - 5, c = 2 * Math.PI * r, v = Math.max(0, Math.min(100, score || 0));
    return el("div", { class: "ring", style: `width:${size}px;height:${size}px`, title: `${Math.round(v)} / 100 — ${label || ""}. ${META.disclaimer || ""}` },
      svg("svg", { width: size, height: size, viewBox: `0 0 ${size} ${size}`, "aria-hidden": "true" },
        svg("circle", { cx: size / 2, cy: size / 2, r, fill: "none", "stroke-width": 5, class: "track" }),
        svg("circle", { cx: size / 2, cy: size / 2, r, fill: "none", "stroke-width": 5, class: label || "Weak", "stroke-linecap": "round", "stroke-dasharray": `${(c * v) / 100} ${c}` })),
      el("span", { class: "v", text: score == null ? "—" : Math.round(score) }));
  }
  function atsMeter(j) {
    const a = j.ats; if (!a) return el("span", { class: "ats muted", text: "ATS —" });
    const tot = a.m + a.s + a.x || 1;
    const tip = a.top && a.top.length ? `Top missing required keywords: ${a.top.join(", ")}` : "No missing required keywords";
    return el("span", { class: "ats", title: `${tip}\n${META.ats_note || ""}` },
      el("span", { class: "segbar", "aria-hidden": "true" }, el("span", { class: "m", style: `width:${(a.m / tot) * 100}%` }),
        el("span", { class: "s", style: `width:${(a.s / tot) * 100}%` }), el("span", { class: "x", style: `width:${(a.x / tot) * 100}%` })),
      `ATS ${Math.round(a.c ?? 0)}%`, a.rt ? el("span", { class: "muted", text: ` · Required ${a.rh}/${a.rt}` }) : null);
  }
  function badgesOf(j) {
    const t = trackOf(j);
    const out = [el("span", { class: `chip ${t}`, title: t === "unclear" ? "Unclear track: shown in both tabs — open it to move it" : "", text: t === "unclear" ? "? Unclear" : TRACKS[t] })];
    if (j.label) out.push(el("span", { class: `chip ${j.label}`, text: j.label }));
    for (const b of j.badges || []) if (BADGE[b]) out.push(el("span", { class: `chip ${BADGE[b][1]}`, text: BADGE[b][0] }));
    for (const [, v] of Object.entries(RED).filter(([k, v]) => (j.flags || {})[k] === v)) out.push(el("span", { class: "chip flag", text: v }));
    const st = statusOf(j); if (st) out.push(el("span", { class: "chip", text: `● ${st}` }));
    return out;
  }
  const postedText = (j) => (j.posted ? `Posted ${fmtDate(j.posted)}` : j.discovered ? `Discovered ${fmtTime(j.first_seen)} (posting date unknown)` : "Posting date unknown");
  function card(j) {
    const c = el("article", { class: `card ${trackOf(j)}`, tabindex: "0", "data-id": j.id, "aria-label": `${j.title} at ${j.company}, score ${j.score == null ? "none" : Math.round(j.score)}`,
      onclick: () => openPanel(j.id), onkeydown: (e) => { if (e.key === "Enter") { e.stopPropagation(); openPanel(j.id); } } },
      el("div", { class: "avatar", style: `background:${avatarColor(j.company)}`, "aria-hidden": "true", text: (j.company.match(/[A-Za-z0-9]/) || ["?"])[0].toUpperCase() }),
      el("div", { class: "card-body" },
        el("div", { class: "card-title", text: j.title }),
        el("div", { class: "card-sub", text: `${j.company}${j.tier ? ` · tier ${j.tier}` : ""} · ${j.location} · ${j.work_mode}` }),
        el("div", { class: "card-badges" }, badgesOf(j)),
        el("div", { class: "card-meta" }, el("span", { text: postedText(j) }),
          j.level !== "unknown" ? el("span", { text: j.level }) : null,
          (j.terms || []).length ? el("span", { text: j.terms.join(", ") }) : null,
          j.deadline ? el("span", { text: `Apply by ${fmtDate(j.deadline)}` }) : null,
          j.salary ? el("span", { text: `${j.salary.currency || ""} ${j.salary.min.toLocaleString()}–${j.salary.max.toLocaleString()}` }) : null,
          j.filtered ? el("span", { class: "err", text: (j.reasons || []).join("; ") }) : atsMeter(j))),
      el("div", { class: "card-side" }, j.filtered ? el("span", { class: "chip", text: "Filtered" }) : ring(j.score, j.label)));
    if (selectedId === j.id) c.classList.add("selected");
    return c;
  }
  const COLS = [
    ["Score", (j) => (j.score == null ? "—" : Math.round(j.score)), "overall", "num"],
    ["Role", (j) => el("div", null, el("div", { class: "title", text: j.title }), el("div", { class: "muted", text: j.company })), "company", "role"],
    ["Track", (j) => el("span", { class: `chip ${trackOf(j)}`, text: TRACKS[trackOf(j)] }), null],
    ["Label", (j) => (j.label ? el("span", { class: `chip ${j.label}`, text: j.label }) : "—"), null],
    ["Level", (j) => j.level, null],
    ["Location", (j) => el("span", { title: j.location, text: j.location }), null, "loc"],
    ["ATS", (j) => atsMeter(j), "ats", "nowrap"],
    ["Match", (j) => (j.qual == null ? "—" : Math.round(j.qual)), "match", "num"],
    ["Posted", (j) => (j.posted ? j.posted : j.discovered ? "found " + j.first_seen.slice(0, 10) : "unknown"), "newest", "nowrap"],
    ["Deadline", (j) => j.deadline || "", "deadline", "nowrap"],
    ["Status", (j) => statusOf(j) || "", null],
  ];
  function table(list) {
    const st = tstate();
    const head = el("tr", null, COLS.map(([h, , sk]) => el("th", { scope: "col", "aria-sort": sk && st.sort === sk ? "descending" : null, title: sk ? "Sort" : null,
      onclick: () => { if (sk) { st.sort = sk; saveUI(); renderList(); } } }, h, sk && st.sort === sk ? " ↓" : "")));
    const body = el("tbody");
    for (const j of list) body.append(el("tr", { class: selectedId === j.id ? "selected" : "", "data-id": j.id, onclick: () => openPanel(j.id) },
      COLS.map(([, f, , cls]) => el("td", { class: cls || "" }, f(j)))));
    return el("div", { class: "table-wrap" }, el("table", { class: "grid" }, el("thead", null, head), body));
  }

  let current = []; let shown = 0; let selectedId = null; let observer = null;
  function setView(id) { tstate().view = id; saveUI(); closePop(); renderAll(); }
  function renderList(keepScroll) {
    const st = tstate();
    const v = viewById(st.view || defaultView());
    $("#view-title").textContent = v.label;
    $("#sort").value = st.sort;
    $("#list").classList.toggle("compact", ui.compact);
    $("#mode-toggle").textContent = ui.mode === "cards" ? "Table" : "Cards";
    $("#density").setAttribute("aria-pressed", String(ui.compact));
    const box = $("#list"); box.textContent = "";
    if (observer) observer.disconnect();
    if (v.id === "coverage" || v.id === "skillgaps") {
      $("#filters").hidden = true; $("#count").textContent = "";
      (v.id === "coverage" ? renderCoverage : renderSkillGaps)(box); return;
    }
    $("#filters").hidden = false;
    const base = v.jobs();
    renderFilters(base);
    current = sortJobs(applyFilters(base), v.id);
    $("#count").textContent = `${current.length.toLocaleString()} of ${base.length.toLocaleString()}`;
    if (v.id === "filtered") box.append(el("div", { class: "banner info", style: "margin:0 0 8px" },
      `${(META.filtered_count || 0).toLocaleString()} jobs filtered; ${(META.filtered_listed || 0).toLocaleString()} listed here (missed-match candidates and software-relevant first). Top reasons: ` +
      Object.entries(META.filtered_reasons || {}).slice(0, 6).map(([k, n]) => `${k} (${n.toLocaleString()})`).join(" · ")));
    if (v.id === "missed") box.append(el("div", { class: "banner info", style: "margin:0 0 8px" },
      "Weekly review: low-scored or title-filtered jobs from tier 1 and 2 companies seen in the last 7 days. Skim for false negatives and shortlist the good ones."));
    if (v.id === "apply") box.append(el("div", { class: "banner info", style: "margin:0 0 8px" },
      "Your top actionable jobs right now: Strong or Stretch, freshest first, deadline-aware, not applied or skipped."));
    if (!current.length) {
      const last = META.last_run ? fmtTime(META.last_run) : "never";
      const msg = v.id === "last24" ? `No new jobs in the last 24 hours — last run ${last}.` : v.id === "apply" ? "Nothing queued — you're caught up." :
        v.id === "shortlisted" ? "Nothing shortlisted yet — press s on any job." : "No jobs match these filters.";
      box.append(el("div", { class: "empty" }, el("b", { text: msg }), el("div", { style: "margin-top:8px" },
        el("button", { class: "btn small", type: "button", onclick: () => { st.filters = {}; st.q = ""; $("#q").value = ""; ui.day = null; saveUI(); setView("all"); } }, "Show all jobs"))));
      return;
    }
    if (ui.mode === "table") {
      box.append(table(current.slice(0, 600)));
      if (current.length > 600) box.append(el("p", { class: "muted", text: `Showing the first 600 of ${current.length.toLocaleString()} — narrow the filters to see the rest.` }));
      return;
    }
    shown = 0;
    const more = el("div", { class: "skeleton", "aria-hidden": "true" });
    const page = () => {
      const frag = document.createDocumentFragment();
      for (const j of current.slice(shown, shown + PAGE)) frag.append(card(j));
      shown += PAGE; more.before(frag);
      if (shown >= current.length) { more.remove(); if (observer) observer.disconnect(); }
    };
    box.append(more); page();
    if (shown < current.length) {
      observer = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) page(); }, { rootMargin: "900px" });
      observer.observe(more);
    }
    if (!keepScroll) window.scrollTo({ top: 0 });
  }

  // ---------- skill gaps / coverage ----------
  function renderSkillGaps(box) {
    ui.gapDays = ui.gapDays || 30;
    const g = (META.skill_gaps || {})[`${ui.track}:${ui.gapDays}`] || { jobs: 0, skills: [] };
    const seg = el("div", { class: "seg", role: "group", "aria-label": "Time window" },
      [7, 30].map((d) => el("button", { type: "button", "aria-pressed": String(ui.gapDays === d), onclick: () => { ui.gapDays = d; saveUI(); renderList(); } }, `${d} days`)));
    box.append(el("div", { class: "toolbar" }, seg, el("span", { class: "muted", text: `Across ${g.jobs.toLocaleString()} Strong / Stretch ${TRACKS[ui.track]} jobs: skills those JDs ask for that your resume doesn't show. Gaps only — never add a skill you don't have.` })));
    if (!g.skills.length) { box.append(el("div", { class: "empty", text: "No skill gaps in this window." })); return; }
    const max = g.skills[0].jobs;
    const wrap = el("div", { class: "side-card", style: "padding:12px 16px" });
    for (const s of g.skills) {
      wrap.append(el("div", { class: "gap-row" }, el("b", { text: s.skill }),
        el("div", { class: "hbar" }, el("span", { style: `width:${(s.jobs / max) * 100}%;background:var(--kw-miss)` })),
        el("span", { class: "muted", text: `${s.jobs} jobs` }),
        el("div", { class: "ex" }, "e.g. ", s.examples.map((x, i) => [i ? " · " : "", el("a", { href: "#", onclick: (e) => { e.preventDefault(); openPanel(x.id); }, text: `${x.company} — ${x.title}` })]))));
    }
    box.append(wrap);
  }
  function renderCoverage(box) {
    const rows = META.coverage || [];
    const c = { ok: 0, failed: 0, manual: 0, skipped: 0 }; for (const r of rows) c[r.status] = (c[r.status] || 0) + 1;
    const s = META.summary || {};
    box.append(el("p", { style: "margin-top:0" }, el("b", { text: `${s.sources_ok ?? c.ok} of ${s.sources_checked ?? rows.length} companies/sources checked successfully in the last run.` }),
      ` ${c.failed} failed · ${c.manual} manual check · ${c.skipped} not due (checked on their tier schedule).`));
    const order = { failed: 0, manual: 1, ok: 2, skipped: 3 };
    const label = { ok: ["OK", "good"], failed: ["Failed", "flag"], manual: ["Manual check", "warn"], skipped: ["Not due", ""] };
    const tb = el("tbody");
    for (const r of [...rows].sort((a, b) => order[a.status] - order[b.status] || a.company.localeCompare(b.company))) {
      const [lt, lc] = label[r.status] || [r.status, ""];
      tb.append(el("tr", null,
        el("td", null, el("b", { text: r.company }), r.my_company ? el("span", { class: "chip info", style: "margin-left:6px", text: "My Company" }) : null, r.tier ? el("div", { class: "muted", text: `tier ${r.tier}` }) : null),
        el("td", null, el("span", { class: `chip ${lc}`, text: lt })), el("td", { text: r.source }),
        el("td", { text: r.detected_via || (r.origin === "seed" ? "seed list" : r.origin || "") }),
        el("td", { class: "nowrap", text: r.last_checked ? fmtTime(r.last_checked) : "—" }), el("td", { class: "num", text: (r.open_roles ?? "—").toLocaleString() }),
        el("td", null, r.error ? el("div", { class: "err", text: r.error }) : null, r.partial ? el("div", { class: "muted", text: `Partial listing: ${r.partial}` }) : null,
          r.notes ? el("div", { class: "muted", text: r.notes }) : null,
          safeUrl(r.url) ? el("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", text: r.status === "manual" ? "Check manually" : "link" }) : null)));
    }
    box.append(el("div", { class: "table-wrap" }, el("table", { class: "grid cov-table" },
      el("thead", null, el("tr", null, ["Company", "Status", "Source", "Detected via", "Last checked", "Open roles", "Notes"].map((h) => el("th", { text: h })))), tb)));
  }

  // ---------- detail panel ----------
  const TABS = ["Overview", "Requirements", "Keywords", "Score", "Full JD", "Similar"];
  let pTab = "Overview"; let pJob = null; let pDetail = null; let jdHighlight = null; let kwFilter = null; let lastFocus = null;
  function openPanel(id) {
    const j = BY_ID.get(id); if (!j) return;
    if (!pJob) lastFocus = document.activeElement;
    selectedId = id; pJob = j; pDetail = null; jdHighlight = null; kwFilter = null;
    for (const n of document.querySelectorAll(".card.selected, tr.selected")) n.classList.remove("selected");
    const n = document.querySelector(`[data-id="${id}"]`); if (n) n.classList.add("selected");
    $("#panel").classList.add("open"); $("#panel").setAttribute("aria-hidden", "false"); $("#scrim").classList.add("open");
    $("#p-title").textContent = j.title;
    $("#p-sub").textContent = `${j.company}${j.tier ? ` · tier ${j.tier}` : ""} · ${j.location} · ${j.work_mode} · ${postedText(j)}`;
    const pb = $("#p-badges"); pb.textContent = ""; pb.append(...badgesOf(j));
    const pr = $("#p-ring"); pr.textContent = ""; if (!j.filtered) pr.append(ring(j.score, j.label, 60));
    renderActions();
    $("#p-tabs").textContent = "";
    renderPanelBody(el("div", { class: "skeleton" }));
    loadDetail(j).then((d) => { if (pJob === j) { pDetail = d; renderPanel(); } });
    setTimeout(() => $("#panel").focus({ preventScroll: true }), 0);
  }
  function closePanel() {
    $("#panel").classList.remove("open"); $("#panel").setAttribute("aria-hidden", "true"); $("#scrim").classList.remove("open"); pJob = null;
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }
  $("#p-close").addEventListener("click", closePanel); $("#scrim").addEventListener("click", closePanel);
  function renderPanelBody(node) { const b = $("#p-body"); b.textContent = ""; b.append(node); b.scrollTop = 0; }
  function renderPanel() {
    const tabs = $("#p-tabs"); tabs.textContent = "";
    const avail = pJob.filtered || !pDetail ? ["Overview"] : TABS;
    if (!avail.includes(pTab)) pTab = "Overview";
    for (const t of avail) tabs.append(el("button", { class: "panel-tab", role: "tab", "aria-selected": String(t === pTab), type: "button", onclick: () => { pTab = t; renderPanel(); } }, t));
    const fn = { Overview: overviewTab, Requirements: reqTab, Keywords: keywordsTab, Score: scoreTab, "Full JD": jdTab, Similar: similarTab }[pTab];
    renderPanelBody(fn(pJob, pDetail || {}));
  }
  const facts = (pairs) => el("dl", { class: "facts" }, pairs.filter(Boolean).map(([k, v]) => [el("dt", { text: k }), el("dd", null, v)]));
  const quote = (t) => (t ? el("div", { class: "quote", text: `“${t}”` }) : null);
  function overviewTab(j, d) {
    const out = el("div");
    if (j.filtered) {
      out.append(el("div", { class: "section" }, el("h3", { text: "Why this job is hidden" }), el("ul", { class: "list-plain" }, (j.reasons || []).map((r) => el("li", { text: r }))),
        el("p", { class: "muted", text: "Hard filters live in inputs/preferences.yaml (rules: hard | soft) and config/title_rules.yaml. If this is a false negative, note it — feedback learning (Phase 4) will use your statuses." })));
      return out;
    }
    const sig = d.signals || {}; const bd = d.breakdown || {};
    if (d.explanation) out.append(el("div", { class: "section" }, el("p", { style: "margin:0", text: d.explanation })));
    const st = (s) => (!s || s.status === "not stated" ? "Not stated" : s.status);
    out.append(el("div", { class: "section" }, el("h3", { text: "At a glance" }), facts([
      ["Track", el("span", null, el("span", { class: `chip ${trackOf(j)}`, text: TRACKS[trackOf(j)] }), " ", el("span", { class: "muted", text: d.track_evidence || "" }), trackControls(j))],
      ["Level", `${j.level}${d.level_evidence ? " — " + d.level_evidence : ""}`],
      ["Years required", d.yoe == null ? "Not stated" : el("span", null, `${d.yoe}+${d.yoe_pref != null ? ` (preferred ${d.yoe_pref}+)` : ""}`, quote(d.yoe_evidence))],
      ["Terms", (j.terms || []).join(", ") || "Not stated"],
      ["Start date", el("span", null, st(sig.start), quote(sig.start && sig.start.evidence))],
      ["Graduation", el("span", null, st(sig.grad), quote(sig.grad && sig.grad.evidence))],
      sig.check_eligibility ? ["Check eligibility", el("span", null, el("span", { class: "chip warn", text: "Check eligibility" }), quote(sig.check_eligibility))] : null,
      sig.meaning_match ? ["Meaning match", `Title didn't match the keyword rules; ${Math.round(sig.meaning_match.similarity * 100)}% similar to “${sig.meaning_match.profile}”.`] : null,
      ["Deadline", j.deadline ? el("span", null, `${fmtDate(j.deadline)} (${daysUntil(j.deadline)} days)`, quote(d.deadline_evidence)) : "Not stated"],
      ["Salary", j.salary ? `${j.salary.currency || ""} ${j.salary.min.toLocaleString()}–${j.salary.max.toLocaleString()}` : "Not stated"],
      ["Degree", d.degree || "Not stated"],
      ["Apply link", d.link_status || "not checked"],
      ["Source", `${j.source} · job ${j.job_id} · first seen ${fmtTime(j.first_seen)}`],
    ])));
    out.append(el("div", { class: "section" }, el("h3", { text: "Flags (only when explicitly stated)" }), facts(["sponsorship", "citizenship", "clearance"].map((k) =>
      [k[0].toUpperCase() + k.slice(1), el("span", null, (j.flags || {})[k] || "Not stated", quote((d.flag_evidence || {})[k]))]))));
    const h1 = (META.h1b || {})[j.company];
    if (h1) out.append(el("div", { class: "section" }, el("h3", { text: "Sponsorship history (company, not this job)" }),
      el("p", { style: "margin:0;font-size:12px" }, `USCIS H-1B approvals matched to “${h1.employer}”: `, Object.entries(h1.by_year).map(([y, n]) => `${y}: ${n.toLocaleString()}`).join(" · ")),
      el("p", { class: "muted", style: "margin:4px 0 0;font-size:12px", text: "From the USCIS Employer Data Hub file you provided. Company history only — it says nothing about this job's eligibility." })));
    const list = (title, xs) => (xs && xs.length ? el("div", { class: "section" }, el("h3", { text: title }), el("ul", { class: "list-plain" }, xs.map((g) => el("li", { text: g })))) : null);
    out.append(...[list("Gaps", d.gaps), list("Ghost-job signals", bd.ghost), list("Soft-rule issues (score penalties)", j.soft)].filter(Boolean));
    out.append(el("p", { class: "muted", text: META.disclaimer || "" }));
    return out;
  }
  function trackControls(j) {
    if (j.track_auto !== "unclear" && !overrides[keyOf(j)]) return null;
    const set = (t) => { overrides[keyOf(j)] = t; store.set(K.overrides, overrides); toast(`Moved to ${TRACKS[t]}`, () => { delete overrides[keyOf(j)]; store.set(K.overrides, overrides); renderAll(true); openPanel(j.id); }); renderBanners(); renderAll(true); openPanel(j.id); };
    return el("span", { style: "display:inline-flex;gap:6px;margin-left:6px" },
      el("button", { class: "btn small", type: "button", onclick: () => set("coop_intern") }, "Move to Co-op"),
      el("button", { class: "btn small", type: "button", onclick: () => set("full_time") }, "Move to Full-time"),
      overrides[keyOf(j)] ? el("button", { class: "btn small ghost", type: "button", onclick: () => { delete overrides[keyOf(j)]; store.set(K.overrides, overrides); renderAll(true); openPanel(j.id); } }, "Reset") : null);
  }
  function reqTab(j, d) {
    const out = el("div");
    const icon = { "✓": ["ok", "All recognizable keywords in this line are on your resume"], "✗": ["no", "At least one keyword is missing from your resume"], "?": ["unk", "No recognizable keyword — can't tell"] };
    const sec = (title, rows) => el("div", { class: "section" }, el("h3", { text: title }), rows && rows.length ? rows.map(([line, s, kws]) =>
      el("div", { class: "req" }, el("span", { class: `st ${icon[s][0]}`, title: icon[s][1], "aria-label": icon[s][1], text: s }),
        el("div", null, line, kws.length ? el("div", { class: "kws" }, kws.map((k) => el("span", { class: "chip", text: k }))) : null))) : el("p", { class: "muted", text: "None stated (or the JD has no recognizable requirements section)." }));
    out.append(el("p", { class: "muted", style: "margin-top:0", text: "✓ matched · ✗ gap · ? unknown — judged from the recognizable keywords in each line." }));
    out.append(sec("Minimum requirements", (d.reqs || {}).min), sec("Preferred requirements", (d.reqs || {}).pref));
    return out;
  }
  function keywordsTab(j, d) {
    const kw = d.keywords || {}; const ks = kw.keywords || [];
    const out = el("div");
    if (!ks.length) { out.append(el("div", { class: "empty", text: "No keywords extracted (no JD text available for this listing)." })); return out; }
    const n = { matched: 0, synonym: 0, missing: 0 }; for (const k of ks) n[k.status]++;
    const size = 96, r = 40, c = 2 * Math.PI * r, v = kw.coverage || 0;
    const ringEl = el("div", { class: "kw-ring", title: META.ats_note || "" }, svg("svg", { width: size, height: size, viewBox: `0 0 ${size} ${size}`, "aria-hidden": "true" },
      svg("circle", { cx: 48, cy: 48, r, fill: "none", "stroke-width": 9, stroke: "var(--surface-2)" }),
      svg("circle", { cx: 48, cy: 48, r, fill: "none", "stroke-width": 9, stroke: "var(--kw-match)", "stroke-linecap": "round", "stroke-dasharray": `${(c * v) / 100} ${c}` })),
      el("div", { class: "v" }, el("span", null, `${Math.round(v)}%`, el("small", { text: "coverage" }))));
    const counter = (stt, cls, label) => el("button", { class: `kw-counter ${cls}`, type: "button", "aria-pressed": String(kwFilter === stt), title: "Show only these",
      onclick: () => { kwFilter = kwFilter === stt ? null : stt; renderPanel(); } }, el("b", { text: n[stt] }), el("span", { text: label }));
    out.append(el("div", { class: "kw-head" }, ringEl, el("div", null,
      el("div", { class: "kw-counters" }, counter("matched", "m", "✓ Matched"), counter("synonym", "s", "≈ Synonym"), counter("missing", "x", "✗ Missing")),
      el("div", { class: "muted", style: "margin-top:8px;font-size:12px", text: `Required ${kw.required_hit}/${kw.required_total} · required terms and frequent terms weigh more` }))));
    const icon = { matched: "✓", synonym: "≈", missing: "✗" };
    const chips = (lst) => {
      const box = el("div", { class: "kw-chips" });
      for (const k of lst.filter((x) => !kwFilter || x.status === kwFilter)) {
        let detail = null;
        const chip = el("button", { class: `kw ${k.status}`, type: "button", "aria-expanded": "false",
          title: `${k.status} · ${k.section} · weight ${k.weight}${k.count > 1 ? ` · mentioned ${k.count}×` : ""}`,
          onclick: () => {
            const open = chip.getAttribute("aria-expanded") === "true";
            chip.setAttribute("aria-expanded", String(!open));
            if (detail) { detail.remove(); detail = null; }
            if (!open) {
              detail = el("div", { class: "kw-detail" },
                k.status === "synonym" ? el("div", null, "Your resume says ", el("b", { text: k.resume_term }), " → JD says ", el("b", { text: k.term }), el("span", { class: "muted", text: " (use the JD's wording where it's true)" })) : null,
                k.evidence ? el("div", null, `${k.evidence_section}: `, el("q", { text: k.evidence })) : el("div", { text: k.status === "missing" ? "Not on your resume — a gap. Only add it if you actually have the experience." : "" }),
                el("button", { class: "btn small", type: "button", style: "margin-top:6px", onclick: () => { jdHighlight = k.term; pTab = "Full JD"; renderPanel(); } }, "Highlight in Full JD"));
              chip.after(detail);
            }
          } }, el("span", { "aria-hidden": "true", text: icon[k.status] }), el("span", { class: "sr-only", text: `${k.status}: ` }), k.term);
        box.append(chip);
      }
      return box;
    };
    for (const [sec, title] of [["required", "Required"], ["preferred", "Preferred"], ["mentioned", "Also mentioned"]]) {
      const lst = ks.filter((k) => k.section === sec);
      if (lst.length) out.append(el("div", { class: "section" }, el("h3", { text: `${title} (${lst.length})` }), chips(lst)));
    }
    const plain = (fn) => ks.filter(fn).map((k) => k.term).join(", ");
    const copyBtn = (label, text, done) => el("button", { class: "btn small", type: "button", onclick: async () => toast((await copyText(text())) ? done : "Clipboard unavailable") }, label);
    out.append(el("div", { class: "toolbar" },
      copyBtn("Copy all", () => plain(() => true), "Copied all keywords"),
      copyBtn("Copy missing only", () => plain((k) => k.status === "missing"), "Copied missing keywords"),
      copyBtn("Copy JD exact terms", () => ks.filter((k) => k.status !== "missing").map((k) => (k.status === "synonym" ? `${k.term} (resume: ${k.resume_term})` : k.term)).join(", "), "Copied JD exact terms")));
    out.append(el("div", { class: "legend" }, el("span", { text: "✓ green = on your resume as written" }), el("span", { text: "≈ blue = on your resume under another name" }),
      el("span", { text: "✗ red = not on your resume (a gap)" })), el("p", { class: "muted", text: META.ats_note || "" }));
    return out;
  }
  function scoreTab(j, d) {
    const bd = d.breakdown || {}; const out = el("div");
    const block = (title, b) => {
      if (!b) return null;
      const g = el("div", { class: "bars" }, el("span", { class: "k head", text: title }), el("div", { class: "hbar" }, el("span", { style: `width:${b.score || 0}%` })), el("span", { class: "v", text: b.score == null ? "?" : Math.round(b.score) }));
      for (const [k, p] of Object.entries(b.parts || {})) {
        g.append(el("span", { class: "k", text: k.replace(/_/g, " ") }), el("div", { class: "hbar" }, el("span", { style: `width:${p.score || 0}%;opacity:${p.score == null ? 0 : 1}` })),
          el("span", { class: "v", text: p.score == null ? "?" : Math.round(p.score) }), el("span", { class: "detail", text: `${p.detail} · weight ${p.weight}` }));
      }
      return el("div", { class: "section" }, g);
    };
    out.append(el("div", { class: "section" }, el("h3", { text: `Overall ${Math.round(bd.overall ?? j.score ?? 0)} — ${j.label}` }),
      el("p", { class: "muted", style: "margin:0;font-size:12px", text: `Qualification × ${(bd.qualification || {}).weight} + preference × ${(bd.preference || {}).weight}, plus capped bonuses (freshness, New Grad, terms, graduation), minus penalties.` })));
    out.append(...[block("Qualification fit", bd.qualification), block("Preference fit", bd.preference)].filter(Boolean));
    const list = (title, items, sign) => (items && items.length ? el("div", { class: "section" }, el("h3", { text: title }), el("ul", { class: "list-plain" }, items.map((x) => el("li", { text: `${sign}${x.points}  ${x.reason}` })))) : null);
    out.append(...[list("Bonuses (capped)", bd.bonuses, "+"), list("Penalties", bd.penalties, "−")].filter(Boolean));
    if ((bd.stretch_reasons || []).length) out.append(el("div", { class: "section" }, el("h3", { text: "Label capped at Stretch" }), el("ul", { class: "list-plain" }, bd.stretch_reasons.map((x) => el("li", { text: x })))));
    if ((bd.uncertain || []).length) out.append(el("p", { class: "muted", text: `Uncertain (no data, left out of the average): ${bd.uncertain.join(", ")}` }));
    const ev = bd.evidence || [];
    if (ev.length) out.append(el("div", { class: "section" }, el("h3", { text: "Evidence from your resume" }), ev.map((e) =>
      el("div", { class: "evidence" }, el("div", { class: "jd", text: `JD: ${e.jd}` }), el("div", null, `${e.section}: `, el("q", { text: e.resume }), el("span", { class: "muted", text: `  (similarity ${e.similarity})` }))))));
    out.append(el("p", { class: "muted", text: META.disclaimer || "" }));
    return out;
  }
  function jdTab(j, d) {
    const text = d.jd || "";
    const out = el("div", { class: "jd-text" });
    if (!text) { out.append(el("div", { class: "empty", text: "No job description text for this listing (aggregator). Open the posting to read it." })); return out; }
    const req = ((d.keywords || {}).keywords || []).filter((k) => k.section === "required").map((k) => k.term);
    const hl = jdHighlight;
    const parts = [hl, ...req].filter(Boolean).sort((a, b) => b.length - a.length);
    const rx = parts.length ? new RegExp(`(${parts.map(esc).join("|")})`, "gi") : null;
    const inline = (s) => (rx ? s.split(rx).map((piece, i) => (i % 2 ? el("mark", { class: hl && piece.toLowerCase() === hl.toLowerCase() ? "" : "kwm", text: piece }) : piece)) : [s]);
    let ul = null; let firstReq = null;
    const reqHead = /^(minimum|basic|required|requirements|qualifications|what you('|’)ll need|what we('|’)re looking for|you have|what you bring|to succeed|what we (require|value|need))/i;
    for (const raw of text.split("\n")) {
      const line = raw.trim().replace(/^#+\s*/, "");
      if (!line) { ul = null; continue; }
      if (line.startsWith("- ")) { if (!ul) { ul = el("ul"); out.append(ul); } ul.append(el("li", null, inline(line.slice(2)))); continue; }
      ul = null;
      if (line.length < 90 && !/[.!?]$/.test(line)) { const h = el("h4", null, inline(line)); if (!firstReq && reqHead.test(line)) firstReq = h; out.append(h); }
      else out.append(el("p", null, inline(line)));
    }
    const wrap = el("div", null, el("div", { class: "toolbar" },
      firstReq ? el("button", { class: "btn small", type: "button", onclick: () => firstReq.scrollIntoView({ behavior: "smooth", block: "start" }) }, "Jump to requirements") : null,
      hl ? el("span", { class: "chip warn", text: `Highlighting “${hl}”` }) : null,
      hl ? el("button", { class: "btn small ghost", type: "button", onclick: () => { jdHighlight = null; renderPanel(); } }, "Clear highlight") : null,
      el("span", { class: "muted", text: "Required keywords are tinted." })), out);
    if (hl) setTimeout(() => { const m = out.querySelector("mark:not(.kwm)"); if (m) m.scrollIntoView({ block: "center" }); }, 60);
    return wrap;
  }
  function similarTab(j) {
    const out = el("div");
    const sims = (j.similar || []).map((id) => BY_ID.get(id)).filter(Boolean);
    if (!sims.length) { out.append(el("div", { class: "empty", text: "No similar open roles found." })); return out; }
    out.append(el("p", { class: "muted", style: "margin-top:0", text: "More like this — the nearest open roles in the same track (local embedding similarity)." }));
    out.append(el("div", { class: "list" }, sims.map(card)));
    return out;
  }
  function quickLinks(j) {
    const name = encodeURIComponent(j.company);
    const slug = j.company.toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    const cov = (META.coverage || []).find((r) => r.company === j.company);
    const links = [["Levels.fyi", `https://www.levels.fyi/companies/${slug}/salaries`], ["Glassdoor", `https://www.glassdoor.com/Search/results.htm?keyword=${name}`],
      ["Eng blog", `https://www.google.com/search?q=${name}+engineering+blog`]];
    if (cov && safeUrl(cov.careers_url || "")) links.push(["Careers", cov.careers_url]);
    return links.map(([t, u]) => el("a", { class: "btn small ghost", href: u, target: "_blank", rel: "noopener noreferrer", title: `${t} for ${j.company}` }, t));
  }
  function renderActions() {
    const j = pJob; if (!j) return; const box = $("#p-actions"); box.textContent = "";
    if (!j.filtered) box.append(el("button", { class: "btn primary", type: "button", onclick: () => generate(j), title: "Download the job package (.md) and copy it (g)" }, "Generate Resume"));
    const apply = safeUrl(j.apply_url);
    if (apply) box.append(el("a", { class: "btn", href: apply, target: "_blank", rel: "noopener noreferrer", title: "Open application (o)" }, "Open Application"));
    box.append(el("button", { class: "btn", type: "button", "aria-pressed": String(statusOf(j) === "shortlisted"), onclick: () => shortlist(j), title: "Shortlist (s)" }, statusOf(j) === "shortlisted" ? "Shortlisted ✓" : "Shortlist"));
    const reason = el("select", { "aria-label": "Skip with reason" }, el("option", { value: "", text: "Skip…" }), SKIP_REASONS.map((r) => el("option", { value: r, text: r })));
    reason.addEventListener("change", () => { if (reason.value) skip(j, reason.value); });
    const st = el("select", { "aria-label": "Status" }, STATUSES.map((s) => el("option", { value: s, text: s || "Status…", selected: s === statusOf(j) })));
    st.addEventListener("change", () => { const undo = setStatus(j, { status: st.value }); toast(st.value ? `Status: ${st.value}` : "Status cleared", undo); renderAll(true); renderActions(); });
    box.append(reason, st, el("span", { class: "spacer" }));
    const li = safeUrl(j.linkedin_url); if (li) box.append(el("a", { class: "btn small ghost", href: li, target: "_blank", rel: "noopener noreferrer" }, "LinkedIn search"));
    box.append(...quickLinks(j));
  }
  async function generate(j) {
    const d = await loadDetail(j);
    if (!d) { toast("Details not available for this job"); return; }
    download(d.package_filename, d.package, "text/markdown;charset=utf-8");
    const ok = await copyText(d.package);
    const undo = setStatus(j, { status: "package exported" });
    toast(ok ? "Package downloaded and copied" : "Package downloaded (clipboard unavailable)", undo);
    renderAll(true); if (pJob) renderActions();
  }
  function shortlist(j) {
    const on = statusOf(j) === "shortlisted";
    const undo = setStatus(j, { status: on ? "" : "shortlisted" });
    toast(on ? "Removed from shortlist" : `Shortlisted: ${j.title}`, undo); renderAll(true); if (pJob) renderActions();
  }
  function skip(j, reason) {
    const undo = setStatus(j, { status: "skipped", skip_reason: reason || "other" });
    toast(`Skipped (${reason || "other"})`, undo); renderAll(true); if (pJob) renderActions();
  }
  function exportStatuses() {
    const payload = { exported_at: new Date().toISOString(), format: 2, statuses, track_overrides: overrides };
    download("jobsearch-statuses.json", JSON.stringify(payload, null, 2), "application/json");
    store.set(K.exported, payload.exported_at); renderBanners(); toast("Exported jobsearch-statuses.json");
  }
  $("#export-statuses").addEventListener("click", exportStatuses);

  // ---------- add job ----------
  $("#add-job").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = $("#add-url").value.trim();
    if (!/^https?:\/\//i.test(url)) { toast("Enter a full http(s) URL"); return; }
    const cmd = `cd ${JSON.stringify(META.repo_root || ".")} && ./scripts/add_url.sh ${JSON.stringify(url)}`;
    toast((await copyText(cmd)) ? "Command copied — paste it in Terminal" : cmd);
  });

  // ---------- track tabs, toolbar ----------
  for (const b of document.querySelectorAll(".track-tab")) b.addEventListener("click", () => switchTrack(b.dataset.track));
  function switchTrack(t) {
    ui.track = t; saveUI();
    history.replaceState(null, "", t === "coop_intern" ? "#coop" : "#fulltime");
    $("#q").value = tstate().q || ""; closePop(); renderAll();
  }
  $("#both").addEventListener("change", () => { ui.both = $("#both").checked; saveUI(); renderAll(); });
  $("#sort").addEventListener("change", () => { tstate().sort = $("#sort").value; saveUI(); renderList(); });
  $("#mode-toggle").addEventListener("click", () => { ui.mode = ui.mode === "cards" ? "table" : "cards"; saveUI(); renderList(); });
  $("#density").addEventListener("click", () => { ui.compact = !ui.compact; saveUI(); renderList(true); });
  let qTimer = null;
  $("#q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(() => { tstate().q = $("#q").value; saveUI(); renderCounts(); renderList(); }, 120); });
  $("#help-btn").addEventListener("click", () => $("#help").showModal());

  // ---------- keyboard ----------
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select"].includes(tag) && e.key !== "Escape") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const idx = current.findIndex((j) => j.id === selectedId);
    const move = (dlt) => {
      const n = current[Math.max(0, Math.min(current.length - 1, (idx < 0 ? -1 : idx) + dlt))]; if (!n) return;
      selectedId = n.id;
      for (const x of document.querySelectorAll(".card.selected, tr.selected")) x.classList.remove("selected");
      const node = document.querySelector(`#list [data-id="${n.id}"]`);
      if (node) { node.classList.add("selected"); node.scrollIntoView({ block: "nearest" }); }
      if (pJob) openPanel(n.id);
    };
    const sel = BY_ID.get(selectedId);
    switch (e.key) {
      case "j": move(1); break;
      case "k": move(-1); break;
      case "Enter": if (sel && tag !== "button" && tag !== "a") openPanel(sel.id); else return; break;
      case "Escape": if ($("#help").open) $("#help").close(); else if (pJob) closePanel(); else closePop(); break;
      case "s": if (sel) shortlist(sel); break;
      case "x": if (sel) skip(sel, "other"); break;
      case "o": if (sel && safeUrl(sel.apply_url)) window.open(sel.apply_url, "_blank", "noopener"); break;
      case "g": if (sel && !sel.filtered) generate(sel); break;
      case "/": e.preventDefault(); $("#q").focus(); break;
      case "?": $("#help").showModal(); break;
      case "1": switchTrack("coop_intern"); break;
      case "2": switchTrack("full_time"); break;
      default: return;
    }
  });

  // ---------- boot ----------
  function renderAll(keepScroll) { renderCounts(); renderTiles(); renderList(keepScroll); }
  if (location.hash === "#coop") ui.track = "coop_intern"; else if (location.hash === "#fulltime") ui.track = "full_time";
  if (!["coop_intern", "full_time"].includes(ui.track)) ui.track = "full_time";
  $("#q").value = tstate().q || "";
  renderHeader(); renderBanners();
  if (!JOBS.length) {
    renderCounts();
    $("#list").append(el("div", { class: "empty" }, el("b", { text: "No jobs yet." }), el("div", { text: "Run ./scripts/run.sh, then reload this page." })));
  } else {
    renderAll();
  }
  history.replaceState(null, "", ui.track === "coop_intern" ? "#coop" : "#fulltime");
})();
