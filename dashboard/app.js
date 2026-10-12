/* HireScope dashboard (vanilla JS, no build, works on file://).
   Data: window.META + window.JOBS (summaries) from data.js; per-job details load on demand from details/d_N.js.
   All job text is rendered with textContent / DOM nodes: JD content is data, never markup. */
(function () {
  "use strict";

  const JOBS = Array.isArray(window.JOBS) ? window.JOBS : [];
  const META = window.META || {};
  const BY_ID = new Map(JOBS.map((j) => [j.id, j]));
  const K = { statuses: "jobsearch.statuses.v1", overrides: "jobsearch.trackOverrides.v1", theme: "jobsearch.theme", apps: "jobsearch.applications.v1",
              ui: "jobsearch.ui.v2", exported: "jobsearch.lastExport", tip: "jobsearch.tipDismissed.v2" };
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
  // statuses: {jobKey: {status, skip_reason, notes, history, updated_at, ...}}; overrides: {jobKey: {track, updated_at}};
  // apps: {id: application} (see core.js). All three save to localStorage instantly and go out with Export statuses.
  const C = window.HSCore;
  let statuses = store.get(K.statuses, {});
  const overrides = {};
  for (const [k, v] of Object.entries(store.get(K.overrides, {}))) overrides[k] = typeof v === "string" ? { track: v, updated_at: "1970-01-01T00:00:00Z" } : v;
  let apps = store.get(K.apps, {});
  const ui = Object.assign({ track: "full_time", both: false, mode: "cards", compact: false, perTrack: {}, appTab: "board" }, store.get(K.ui, {}));
  const tstate = () => (ui.perTrack[ui.track] = ui.perTrack[ui.track] || { view: null, sort: "overall", filters: {}, q: "", modes: {} });
  const saveUI = () => store.set(K.ui, ui);
  const keyOf = (j) => `${j.company}::${j.source}::${j.job_id}`;
  const statusOf = (j) => (statuses[keyOf(j)] || {}).status || "";
  const trackOf = (j) => ((overrides[keyOf(j)] || {}).track) || j.track;
  const saveOverrides = () => store.set(K.overrides, overrides);
  const saveApps = () => store.set(K.apps, apps);
  const liveApps = () => Object.values(apps).filter((a) => !a.deleted);
  const appForJob = (j) => liveApps().find((a) => a.job_key === keyOf(j)) || null;
  const today = () => META.today || new Date().toISOString().slice(0, 10);
  const appsCfg = Object.assign({ follow_up_days: [7, 10], ghosted_after_days: 30, same_company_window_days: 30, near_duplicate_ratio: 85 }, META.applications_cfg || {});
  const UI_CFG = Object.assign({ apply_today_size: 5, closing_soon_days: 7 }, META.ui_cfg || {});   // preferences apply_today

  // SQLite's copy (sync.js, written by each run) fills in anything this browser doesn't have; newer always wins
  (function mergeSync() {
    const S = window.SYNC; if (!S) return;
    let changed = false;
    for (const [k, v] of Object.entries(S.statuses || {})) if (!statuses[k] || String(v.updated_at) > String(statuses[k].updated_at || "")) { statuses[k] = Object.assign({}, statuses[k] || {}, v); changed = true; }
    for (const [k, v] of Object.entries(S.track_overrides || {})) if (!overrides[k] || String(v.updated_at) > String(overrides[k].updated_at || "")) { overrides[k] = v; changed = true; }
    for (const a of S.applications || []) { const m = C.mergeApp(apps[a.id], a); if (JSON.stringify(m) !== JSON.stringify(apps[a.id])) { apps[a.id] = m; changed = true; } }
    if (changed) { store.set(K.statuses, statuses); saveOverrides(); saveApps(); }
  })();

  const STAGE_STATUS = { applied: "applied", oa: "applied", interviewing: "interviewing", offer: "offer", rejected: "rejected", ghosted: "applied", withdrawn: "applied" };
  function setStatus(j, patch) {
    const k = keyOf(j);
    const before = statuses[k] ? JSON.parse(JSON.stringify(statuses[k])) : null;
    const prev = statuses[k] || {};
    const at = new Date().toISOString();
    const history = [...(prev.history || [])];
    if ("status" in patch && patch.status !== prev.status) history.push({ status: patch.status || "(cleared)", at, reason: patch.skip_reason || null });
    statuses[k] = Object.assign({ company: j.company, title: j.title, job_id: j.job_id, source: j.source }, prev, patch,
      { history: history.slice(-30), updated_at: at });
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
    .map((id) => BY_ID.get(id)).filter((j) => j && !DONE.includes(statusOf(j))).slice(0, (ui.both ? 2 : 1) * UI_CFG.apply_today_size);

  const VIEWS = [
    { id: "last24", label: "Last 24h", ic: "⚡", group: "Discover", jobs: () => visibleJobs().filter((j) => isFresh24(j)) },
    { id: "apply", label: "Apply Today", ic: "★", jobs: () => applyQueue() },
    { id: "all", label: "All Jobs", ic: "▤", jobs: () => visibleJobs() },
    { id: "mine", label: "My Companies", ic: "♥", jobs: () => visibleJobs().filter((j) => j.my_company) },
    { id: "applications", label: "My Applications", ic: "▦", group: "Mine", special: true, count: () => appsInTrack().length },
    { id: "shortlisted", label: "Shortlisted", ic: "✓", jobs: () => JOBS.filter((j) => inTrack(j) && statusOf(j) === "shortlisted") },
    { id: "followups", label: "Follow-ups Due", ic: "↻", special: true, count: () => new Set([...followupsDue(), ...ghostsDue()].map((a) => a.id)).size },
    { id: "closing", label: "Closing Soon", ic: "⏳", jobs: () => visibleJobs().filter((j) => j.deadline && daysUntil(j.deadline) >= 0 && daysUntil(j.deadline) <= UI_CFG.closing_soon_days) },
    { id: "skillgaps", label: "Skill Gaps", ic: "◔", group: "Insights", special: true, count: () => ((META.skill_gaps || {})[`${ui.track}:30`] || { skills: [] }).skills.length },
    { id: "stats", label: "Stats", ic: "▥", special: true, count: () => appsInTrack().length },
    { id: "seasons", label: "Hiring Seasons", ic: "◷", special: true, count: () => seasonsInTrack().filter((x) => x.remind).length },
    { id: "missed", label: "Missed Matches", ic: "◎", jobs: () => (META.missed || []).map((id) => BY_ID.get(id)).filter((j) => j && inTrack(j)) },
    { id: "filtered", label: "Filtered Out", ic: "⊘", jobs: () => JOBS.filter((j) => j.filtered && inTrack(j)) },
    { id: "coverage", label: "Coverage", ic: "◍", group: "System", special: true, count: () => (META.coverage || []).length },
    { id: "runlog", label: "Run Log", ic: "≡", special: true, count: () => (META.runs || []).length },
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
  const REF = META.referrals || {};
  const RED = { sponsorship: "No sponsorship", citizenship: "US citizenship required", clearance: "Clearance required" };
  function flagNames(j) {
    const out = Object.entries(RED).filter(([k, v]) => (j.flags || {})[k] === v).map(([, v]) => v);
    for (const b of j.badges || []) if (["ghost", "eligibility", "broken", "closing", "nojd", "newgrad", "meaning", "reopened"].includes(b)) out.push(BADGE[b][0]);
    if ((REF[j.company] || {}).count) out.push("Referral");
    if (appForJob(j)) out.push("Applied (in My Applications)");
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
      if (st.salary > 0) {
        if (j.salary && (!j.salary.currency || j.salary.currency === "USD")) { if (j.salary.max < st.salary) return false; }
        else if (st.salaryKnown) return false;
      }
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
    box.append(...[el("h4", { text: "Learned adjustments" }), el("div", { text: L.status || "None yet." }),
      L.adjustments && L.adjustments.length ? el("ul", { class: "list-plain", style: "margin-top:6px" }, L.adjustments.map((a) => el("li", { text: a }))) : null,
      Object.keys(L.ignored || {}).length ? el("div", { style: "margin-top:6px", text: `Skips that teach nothing (not about the job itself): ${Object.entries(L.ignored).map(([k, n]) => `${k} ×${n}`).join(", ")}` }) : null,
      ...(L.suggestions || []).map((x) => el("div", { class: "banner warn", style: "margin-top:6px", text: x }))].filter(Boolean));
  }
  // unsynced = changed in this browser after the last export and after SQLite's copy (sync.js) of the same item
  const SYNC_AT = (() => {
    const S = window.SYNC || {}; const m = { s: {}, o: {}, a: {} };
    for (const [k, v] of Object.entries(S.statuses || {})) m.s[k] = v.updated_at || "";
    for (const [k, v] of Object.entries(S.track_overrides || {})) m.o[k] = v.updated_at || "";
    for (const a of S.applications || []) m.a[a.id] = a.updated_at || "";
    return m;
  })();
  function unsynced() {
    const last = store.get(K.exported, null) || "";
    const newer = (map) => ([k, x]) => { const t = String(x.updated_at || ""); return t > last && t > (map[k] || ""); };
    return Object.entries(statuses).some(newer(SYNC_AT.s)) || Object.entries(overrides).some(newer(SYNC_AT.o)) || Object.entries(apps).some(newer(SYNC_AT.a));
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
    if (META.partial_run) box.append(el("div", { class: "banner warn", text: (META.summary || {}).scope === "quick"
      ? "Last run was a quick check (My Companies and tier-1 sources); everything else was refreshed by the last full run."
      : "The last run checked only one company/source; other companies were not re-checked." }));
    const ha = META.health_alerts || [];
    if (ha.length) box.append(el("div", { class: "banner err" }, el("details", null, el("summary", { text: `Source health: ${ha.length} source${ha.length > 1 ? "s" : ""} returned far fewer jobs than usual (likely broken, not "no new jobs")` }),
      el("ul", null, ha.map((a) => el("li", { text: a.message }))))));
    if (/example/i.test(META.resume_path || "")) box.append(el("div", { class: "banner warn", text: `Scored against ${META.resume_path} (DEMO resume).` }));
    if (unsynced()) box.append(el("div", { class: "banner info" }, el("span", { text: "Unsynced changes — your statuses live only in this browser until you export them." }),
      el("button", { class: "btn small x", type: "button", onclick: exportStatuses }, "Export statuses")));
    const reo = JOBS.filter((j) => (j.badges || []).includes("reopened") && ["shortlisted", "interested", "package exported"].includes(statusOf(j)));
    if (reo.length) box.append(el("div", { class: "banner warn" }, el("div", null, el("b", { text: `Reopened: ${reo.length} job${reo.length > 1 ? "s" : ""} you shortlisted came back. ` }),
      reo.slice(0, 4).map((j, i) => [i ? " · " : "", el("a", { href: "#", onclick: (e) => { e.preventDefault(); openPanel(j.id); }, text: `${j.company} — ${j.title}` })]))));
    if (!store.get(K.tip, false)) {
      box.append(el("div", { class: "banner info" },
        el("div", null, el("b", { text: "Welcome. " }), "Co-op / Internships and Full-time are separate workspaces (keys 1 / 2). Open a job (Enter), shortlist (s), skip (x), mark applied (a), next (j / k), ⌘K to jump anywhere, ? for all shortcuts. ",
          "Applications live in My Applications. Everything saves in this browser instantly; Export statuses hands it to the pipeline, which keeps it in SQLite and learns from your shortlists and skips."),
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
      ["Follow-ups due", followupsDue().length, () => setView("followups"), `Applications still at "Applied" ${appsCfg.follow_up_days[0]}+ days after applying or your last follow-up`],
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
    box.append(salaryChip(base));
    if (FILTERS.some((f) => (st.filters[f.id] || []).length) || st.q || ui.day || st.salary) {
      box.append(el("button", { class: "btn small ghost", type: "button", onclick: () => { st.filters = {}; st.q = ""; st.salary = 0; st.salaryKnown = false; $("#q").value = ""; ui.day = null; $("#day-picker").value = ""; saveUI(); renderAll(); } }, "Clear all"));
    }
  }
  const money = (n) => `$${Math.round(n / 1000)}k`;
  function salaryChip(base) {
    const st = tstate();
    const wrap = el("div", { class: "filter-wrap" });
    const known = base.filter((j) => j.salary && (!j.salary.currency || j.salary.currency === "USD"));
    const btn = el("button", { class: "filter-chip" + (st.salary ? " active" : ""), type: "button", "aria-haspopup": "true",
      onclick: (e) => {
        e.stopPropagation(); const was = openPop && openPop.dataset.f === "salary"; closePop(); if (was) return;
        const pop = el("div", { class: "popover salary-pop", role: "dialog", "aria-label": "Salary" }); pop.dataset.f = "salary";
        const out = el("output", { text: st.salary ? `at least ${money(st.salary)}` : "any" });
        const range = el("input", { type: "range", min: 0, max: 300000, step: 10000, value: st.salary || 0, "aria-label": "Minimum salary (top of range)" });
        const chk = el("input", { type: "checkbox", checked: !!st.salaryKnown });
        const apply = () => { st.salary = Number(range.value); st.salaryKnown = chk.checked; saveUI(); renderCounts(); renderList(true); btn.classList.toggle("active", !!st.salary);
          const w = [...document.querySelectorAll("#filters .filter-wrap")][FILTERS.length]; if (w && !w.contains(pop)) { w.append(pop); openPop = pop; } };
        range.addEventListener("input", () => { out.textContent = Number(range.value) ? `at least ${money(Number(range.value))}` : "any"; });
        range.addEventListener("change", apply); chk.addEventListener("change", apply);
        pop.append(el("div", { class: "muted", style: "font-size:12px;margin-bottom:6px", text: `Top of the posted range (USD). ${known.length.toLocaleString()} of ${base.length.toLocaleString()} jobs state a salary.` }),
          range, el("div", { style: "font-size:13px;margin:4px 0 8px" }, "Salary ", out),
          el("label", null, chk, "Hide jobs that don't state a salary"));
        wrap.append(pop); openPop = pop;
      } }, st.salary ? `Salary ≥ ${money(st.salary)}` : "Salary ▾");
    wrap.append(btn);
    return wrap;
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
    const ref = REF[j.company]; if (ref && ref.count) out.push(el("span", { class: "chip info", title: `You know ${ref.count} ${ref.count > 1 ? "people" : "person"} here (LinkedIn connections export)`, text: `Referral · ${ref.count}` }));
    const st = statusOf(j); if (st) out.push(el("span", { class: "chip", text: `● ${st}` }));
    return out;
  }
  const postedText = (j) => (j.posted ? `Posted ${fmtDate(j.posted)}` : j.discovered ? `Discovered ${fmtTime(j.first_seen)} (posting date unknown)` : "Posting date unknown");
  function card(j) {
    const c = el("article", { class: `card ${trackOf(j)}`, tabindex: "0", "data-id": j.id, "aria-label": `${j.title} at ${j.company}, score ${j.score == null ? "none" : Math.round(j.score)}`,
      onclick: () => openPanel(j.id), onkeydown: (e) => { if (e.key === "Enter") { e.stopPropagation(); openPanel(j.id); } } },
      el("div", { class: "avatar-wrap" },
        el("div", { class: "avatar", style: `background:${avatarColor(j.company)}`, "aria-hidden": "true", text: (j.company.match(/[A-Za-z0-9]/) || ["?"])[0].toUpperCase() }),
        selBox(j)),
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
    if (picked.has(j.id)) c.classList.add("picked");
    return c;
  }
  // bulk selection
  const picked = new Set();
  function selBox(j) {
    const cb = el("input", { type: "checkbox", class: "pick", "aria-label": `Select ${j.title} at ${j.company}`, checked: picked.has(j.id),
      onclick: (e) => e.stopPropagation(), onkeydown: (e) => e.stopPropagation() });
    cb.addEventListener("change", () => togglePick(j.id, cb.checked));
    return cb;
  }
  function togglePick(id, on) {
    if (on ?? !picked.has(id)) picked.add(id); else picked.delete(id);
    for (const n of document.querySelectorAll(`#list [data-id="${id}"]`)) { n.classList.toggle("picked", picked.has(id)); const cb = n.querySelector("input.pick"); if (cb) cb.checked = picked.has(id); }
    document.body.classList.toggle("picking", picked.size > 0);
    renderBulkbar();
  }
  function renderBulkbar() {
    const bar = $("#bulkbar"); bar.textContent = ""; bar.hidden = picked.size === 0;
    if (!picked.size) return;
    const jobs = [...picked].map((id) => BY_ID.get(id)).filter(Boolean);
    const reason = el("select", { "aria-label": "Skip selected with reason" }, el("option", { value: "", text: "Skip selected…" }), SKIP_REASONS.map((r) => el("option", { value: r, text: r })));
    reason.addEventListener("change", () => { if (reason.value) bulk(jobs, { status: "skipped", skip_reason: reason.value }, `Skipped ${jobs.length} (${reason.value})`); });
    bar.append(el("b", { text: `${jobs.length} selected` }),
      el("button", { class: "btn small", type: "button", onclick: () => bulk(jobs, { status: "shortlisted" }, `Shortlisted ${jobs.length}`) }, "Shortlist"), reason,
      el("button", { class: "btn small", type: "button", onclick: () => exportJobsCsv(jobs) }, "Export CSV"),
      el("button", { class: "btn small ghost", type: "button", onclick: () => { current.forEach((j) => picked.add(j.id)); renderList(true); renderBulkbar(); } }, `Select all ${current.length.toLocaleString()}`),
      el("span", { class: "spacer" }),
      el("button", { class: "btn small ghost", type: "button", onclick: clearPicks }, "Clear"));
  }
  function clearPicks() { picked.clear(); document.body.classList.remove("picking"); renderList(true); renderBulkbar(); }
  function bulk(jobs, patch, msg) {
    const undos = jobs.map((j) => setStatus(j, patch));
    toast(msg, () => undos.reverse().forEach((u) => u()));
    picked.clear(); document.body.classList.remove("picking"); renderAll(true); renderBulkbar();
  }
  const csvCell = (v) => { let x = v == null ? "" : String(v); if (/^[=+\-@]/.test(x)) x = "'" + x; return /[",\r\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x; };
  function exportJobsCsv(jobs) {
    const cols = ["company", "title", "track", "label", "score", "ats", "level", "location", "work_mode", "posted", "deadline", "status", "apply_url"];
    const rows = [cols.join(",")].concat(jobs.map((j) => [j.company, j.title, TRACKS[trackOf(j)], j.label || "", j.score == null ? "" : Math.round(j.score),
      j.ats ? Math.round(j.ats.c) : "", j.level, j.location, j.work_mode, j.posted || "", j.deadline || "", statusOf(j), j.apply_url || ""].map(csvCell).join(",")));
    download(`hirescope-jobs-${today()}.csv`, rows.join("\r\n") + "\r\n", "text/csv;charset=utf-8");
    toast(`Exported ${jobs.length} jobs`);
  }
  const COLS = [
    ["", (j) => selBox(j), null, "pickcol"],
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
    const head = el("tr", null, COLS.map(([h, , sk]) => el("th", { scope: "col", "aria-sort": sk && st.sort === sk ? "descending" : null, title: sk ? "Sort" : null, "aria-label": h || "Select",
      onclick: () => { if (sk) { st.sort = sk; saveUI(); renderList(); } } }, h, sk && st.sort === sk ? " ↓" : "")));
    const body = el("tbody");
    for (const j of list) body.append(el("tr", { class: (selectedId === j.id ? "selected" : "") + (picked.has(j.id) ? " picked" : ""), "data-id": j.id, onclick: () => openPanel(j.id) },
      COLS.map(([, f, , cls]) => el("td", { class: cls || "" }, f(j)))));
    return el("div", { class: "table-wrap" }, el("table", { class: "grid" }, el("thead", null, head), body));
  }

  let current = []; let shown = 0; let selectedId = null; let observer = null;
  function setView(id) { tstate().view = id; saveUI(); closePop(); renderAll(); }
  const modeOf = (vid) => (tstate().modes || {})[vid] || ui.mode || "cards";
  const SPECIAL = { coverage: () => renderCoverage, skillgaps: () => renderSkillGaps, applications: () => renderApplications,
    followups: () => renderFollowups, stats: () => renderStatsView, runlog: () => renderRunLog, seasons: () => renderSeasons };
  function renderList(keepScroll) {
    const st = tstate();
    const v = viewById(st.view || defaultView());
    $("#view-title").textContent = v.label;
    $("#sort").value = st.sort;
    $("#list").classList.toggle("compact", ui.compact);
    $("#mode-toggle").textContent = modeOf(v.id) === "cards" ? "Table" : "Cards";
    $("#density").setAttribute("aria-pressed", String(ui.compact));
    const box = $("#list"); box.textContent = "";
    if (observer) observer.disconnect();
    writeHash();
    const special = SPECIAL[v.id];
    for (const n of ["#sort", "#mode-toggle", "#density"]) $(n).hidden = !!special;
    if (special) {
      $("#filters").hidden = true; $("#count").textContent = "";
      special()(box); if (!keepScroll) window.scrollTo({ top: 0 }); return;
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
    if (modeOf(v.id) === "table") {
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
      const [lt, lc] = r.status === "skipped" && r.error ? ["Not set up / off", "warn"] : label[r.status] || [r.status, ""];
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

  // ---------- My Applications ----------
  const KEY_MAP = new Map(JOBS.map((j) => [keyOf(j), j]));
  const appsInTrack = () => liveApps().filter((a) => ui.both || a.track === ui.track);
  const fuOf = (a) => C.followUp(a, today(), appsCfg.follow_up_days);
  const followupsDue = () => appsInTrack().filter((a) => { const f = fuOf(a); return f && f.state !== "upcoming"; });
  const ghostsDue = () => appsInTrack().filter((a) => C.ghostSuggestion(a, today(), appsCfg.ghosted_after_days));
  const daysSince = (d) => { const x = C.day(d); return x == null ? null : C.day(today()) - x; };
  const STAGE_LABEL = C.STAGE_LABELS;
  const OPEN_STAGES = ["applied", "oa", "interviewing", "offer"];
  const CLOSED_STAGES = ["rejected", "ghosted", "withdrawn"];
  function postingState(a) {
    if (!a.job_key) return null;
    if (KEY_MAP.has(a.job_key)) return "open";
    const s = ((window.SYNC || {}).job_state || {})[a.job_key];
    return s ? (s[0] === "closed" ? "closed" : "open") : null;
  }
  function syncJobStatus(a) {
    if (!a.job_key) return;
    const want = a.deleted ? null : STAGE_STATUS[a.stage];
    const j = KEY_MAP.get(a.job_key);
    if (j) { if (want && statusOf(j) !== want) setStatus(j, { status: want }); return; }
    const cur = statuses[a.job_key];
    if (want && cur && cur.status !== want) { statuses[a.job_key] = Object.assign({}, cur, { status: want, updated_at: new Date().toISOString() }); store.set(K.statuses, statuses); }
  }
  function saveApp(next, msg) {
    const before = apps[next.id] ? JSON.parse(JSON.stringify(apps[next.id])) : null;
    const beforeStatus = next.job_key && statuses[next.job_key] ? JSON.parse(JSON.stringify(statuses[next.job_key])) : null;
    apps[next.id] = next; saveApps(); syncJobStatus(next);
    renderBanners(); renderAll(true); if (dApp && dApp === next.id) renderDrawer();
    if (msg) toast(msg, () => {
      if (before) apps[next.id] = before; else delete apps[next.id];
      if (next.job_key) { if (beforeStatus) statuses[next.job_key] = beforeStatus; else delete statuses[next.job_key]; store.set(K.statuses, statuses); }
      saveApps(); renderBanners(); renderAll(true); if (dApp) renderDrawer();
    });
  }
  function moveTo(a, to) {
    if (!a || a.stage === to) return;
    saveApp(C.moveStage(a, to), `${a.company}: ${STAGE_LABEL[a.stage]} → ${STAGE_LABEL[to]}`);
  }
  const STAGE_ORDER = [...OPEN_STAGES, ...CLOSED_STAGES];
  function shiftStage(a, dir) {
    const i = STAGE_ORDER.indexOf(a.stage); const to = STAGE_ORDER[Math.max(0, Math.min(STAGE_ORDER.length - 1, i + dir))];
    if (to === a.stage) return;
    moveTo(a, to);
    setTimeout(() => { const n = document.querySelector(`[data-app="${a.id}"]`); if (n) n.focus(); }, 0);
  }

  function confirmDialog(title, lines, yes) {
    return new Promise((resolve) => {
      const d = $("#confirm"); $("#confirm-title").textContent = title;
      const body = $("#confirm-body"); body.textContent = ""; body.append(el("ul", { class: "list-plain" }, lines.map((l) => el("li", { text: l }))));
      $("#confirm-yes").textContent = yes || "Continue";
      const done = (v) => { d.close(); $("#confirm-yes").onclick = null; $("#confirm-no").onclick = null; resolve(v); };
      $("#confirm-yes").onclick = () => done(true); $("#confirm-no").onclick = () => done(false);
      d.oncancel = () => resolve(false);
      d.showModal(); $("#confirm-no").focus();
    });
  }

  async function markApplied(j) {
    const existing = appForJob(j);
    if (existing) { openDrawer(existing.id); toast("Already in My Applications"); return; }
    const warns = C.duplicateWarnings(j.company, j.title, keyOf(j), liveApps(), today(), appsCfg.same_company_window_days, null, appsCfg.near_duplicate_ratio);
    if (warns.length && !(await confirmDialog("Possible duplicate application", warns.map((w) => w.message), "Mark applied anyway"))) return;
    const d = await loadDetail(j);
    const st = statuses[keyOf(j)] || {};
    const t = trackOf(j) === "unclear" ? ui.track : trackOf(j);
    const a = C.newApp({ job_key: keyOf(j), company: j.company, title: j.title, track: t, link: safeUrl(j.apply_url) || "", source: "HireScope",
      package: st.package || "", jd: (d && d.jd) || "", job: { source: j.source, tier: j.tier, label: j.label, score: j.score == null ? null : Math.round(j.score), level: j.level, job_id: j.job_id } });
    const undoStatus = setStatus(j, { status: "applied" });
    apps[a.id] = a; saveApps();
    toast(`Marked applied: ${j.company} (date editable in My Applications)`, () => { delete apps[a.id]; saveApps(); undoStatus(); });
    renderAll(true); if (pJob) renderActions();
  }

  // ----- page -----
  function renderApplications(box) {
    const list = appsInTrack();
    const seg = el("div", { class: "seg", role: "tablist", "aria-label": "My Applications view" },
      [["board", "Board"], ["table", "Table"], ["stats", "Stats"]].map(([id, label]) => el("button", { type: "button", role: "tab", "aria-selected": String(ui.appTab === id), "aria-pressed": String(ui.appTab === id),
        onclick: () => { ui.appTab = id; saveUI(); renderList(true); } }, label)));
    box.append(el("div", { class: "toolbar" }, seg,
      el("span", { class: "muted", text: `${list.length} application${list.length === 1 ? "" : "s"} · ${ui.both ? "both tracks" : TRACKS[ui.track]}` }),
      el("span", { class: "spacer" }),
      el("button", { class: "btn small", type: "button", onclick: () => openAddApp() }, "+ Add application"),
      el("button", { class: "btn small", type: "button", onclick: () => { download(`my-applications-${today()}.csv`, C.toCsv(list, today(), appsCfg.follow_up_days), "text/csv;charset=utf-8"); toast(`Exported ${list.length} applications`); } }, "Export CSV")));
    if (!list.length && ui.appTab !== "stats") {
      box.append(el("div", { class: "empty" }, el("b", { text: `No ${ui.both ? "" : TRACKS[ui.track] + " "}applications yet.` }),
        el("div", { style: "margin-top:6px", text: "Press a (or Mark Applied) on any job, or add one you applied to elsewhere." }),
        el("div", { style: "margin-top:10px" }, el("button", { class: "btn small primary", type: "button", onclick: () => openAddApp() }, "+ Add application"))));
      return;
    }
    const hs = ui.appTab !== "stats" ? helperStrip(list) : null; if (hs) box.append(hs);
    ({ board: appBoard, table: appTable, stats: appStats })[ui.appTab](box, list);
  }
  function helperStrip(list) {
    const fu = list.filter((a) => { const f = fuOf(a); return f && f.state !== "upcoming"; });
    const gh = list.filter((a) => C.ghostSuggestion(a, today(), appsCfg.ghosted_after_days));
    const t0 = C.day(today());
    const up = list.filter((a) => a.next_step_date && C.day(a.next_step_date) >= t0 && C.day(a.next_step_date) - t0 <= 14 && OPEN_STAGES.includes(a.stage))
      .sort((x, y) => x.next_step_date.localeCompare(y.next_step_date));
    if (!fu.length && !gh.length && !up.length) return null;
    const item = (a, text) => el("li", null, el("a", { href: "#", onclick: (e) => { e.preventDefault(); openDrawer(a.id); }, text: `${a.company} — ${a.title}` }), el("span", { class: "muted", text: ` · ${text}` }));
    return el("div", { class: "helpers" },
      up.length ? el("div", { class: "side-card" }, el("h4", { text: `Upcoming steps (${up.length})` }), el("ul", { class: "list-plain" }, up.slice(0, 6).map((a) => item(a, `${a.next_step || "Next step"} · ${fmtDate(a.next_step_date)} (${C.day(a.next_step_date) - t0 === 0 ? "today" : `in ${C.day(a.next_step_date) - t0}d`})`)))) : null,
      fu.length ? el("div", { class: "side-card" }, el("h4", { text: `Follow-ups due (${fu.length})` }), el("ul", { class: "list-plain" }, fu.slice(0, 6).map((a) => item(a, `${fuOf(a).state} · ${fuOf(a).days} days since ${a.followed_up ? "your follow-up" : "applying"}`)))) : null,
      gh.length ? el("div", { class: "side-card" }, el("h4", { text: `Ghosted? (${gh.length})` }), el("ul", { class: "list-plain" }, gh.slice(0, 6).map((a) => item(a, `no update in ${C.day(today()) - C.lastActivity(a)} days — you decide`)))) : null);
  }
  function appChips(a, compact) {
    const out = [el("span", { class: `chip ${a.track}`, text: TRACKS[a.track] || a.track })];
    const ds = daysSince(a.applied_date);
    if (ds != null && !compact) out.push(el("span", { class: "chip", title: `Applied ${a.applied_date}`, text: ds === 0 ? "today" : `${ds}d ago` }));
    const f = fuOf(a);
    if (f && f.state !== "upcoming") out.push(el("span", { class: `chip ${f.state === "overdue" ? "flag" : "warn"}`, title: `Follow-up window ${appsCfg.follow_up_days.join("–")} days`, text: f.state === "overdue" ? "↻ Follow-up overdue" : "↻ Follow-up due" }));
    if (C.ghostSuggestion(a, today(), appsCfg.ghosted_after_days)) out.push(el("span", { class: "chip warn", text: "Ghosted?" }));
    if (a.referral || (REF[a.company] || {}).count) out.push(el("span", { class: "chip info", title: a.referral ? `Referral: ${a.referral}` : "You have connections here", text: a.referral ? "Referral" : `Know ${(REF[a.company] || {}).count}` }));
    if (postingState(a) === "closed") out.push(el("span", { class: "chip flag", title: "The posting disappeared from its source; your saved JD copy is kept", text: "Posting closed" }));
    return out;
  }
  function appCard(a) {
    const c = el("div", { class: `app-card ${a.track}`, tabindex: "0", draggable: "true", role: "button", "data-app": a.id,
      "aria-label": `${a.title} at ${a.company}, ${STAGE_LABEL[a.stage]}. Enter opens details; [ and ] move between stages.`,
      onclick: () => openDrawer(a.id),
      onkeydown: (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); openDrawer(a.id); }
        else if (e.key === "[" || e.key === "]") { e.preventDefault(); e.stopPropagation(); shiftStage(a, e.key === "]" ? 1 : -1); }
      },
      ondragstart: (e) => { e.dataTransfer.setData("text/plain", a.id); e.dataTransfer.effectAllowed = "move"; c.classList.add("dragging"); },
      ondragend: () => c.classList.remove("dragging") },
      el("div", { class: "app-co", text: a.company }), el("div", { class: "app-role", text: a.title }),
      el("div", { class: "card-badges" }, appChips(a)),
      a.next_step || a.next_step_date ? el("div", { class: "app-next" }, el("b", { text: "Next: " }), a.next_step || "step", a.next_step_date ? ` · ${fmtDate(a.next_step_date)}` : "") : null);
    return c;
  }
  function appBoard(box, list) {
    const board = el("div", { class: "board", role: "list", "aria-label": "Applications by stage" });
    for (const stg of STAGE_ORDER) {
      const items = list.filter((a) => a.stage === stg).sort((x, y) => String(y.updated_at).localeCompare(String(x.updated_at)));
      const col = el("section", { class: `col ${CLOSED_STAGES.includes(stg) ? "closed" : ""}`, role: "listitem", "aria-label": `${STAGE_LABEL[stg]}: ${items.length}`,
        ondragover: (e) => { e.preventDefault(); col.classList.add("over"); }, ondragleave: () => col.classList.remove("over"),
        ondrop: (e) => { e.preventDefault(); col.classList.remove("over"); moveTo(apps[e.dataTransfer.getData("text/plain")], stg); } },
        el("h3", null, STAGE_LABEL[stg], el("span", { class: "n", text: items.length })),
        items.length ? items.map(appCard) : el("div", { class: "col-empty", text: "Drop here" }));
      board.append(col);
    }
    box.append(board, el("p", { class: "muted", style: "font-size:12px", text: "Drag cards between stages, or focus a card and press [ / ]. Every move is logged on the card's timeline." }));
  }
  const APP_COLS = [
    ["Company", (a) => a.company, "company"], ["Role", (a) => a.title, "title"], ["Track", (a) => el("span", { class: `chip ${a.track}`, text: TRACKS[a.track] }), "track"],
    ["Stage", (a) => STAGE_LABEL[a.stage], "stage"], ["Applied", (a) => a.applied_date || "", "applied_date"], ["Days", (a) => daysSince(a.applied_date) ?? "", "days"],
    ["Next step", (a) => [a.next_step, a.next_step_date ? ` (${a.next_step_date})` : ""].join(""), "next_step_date"],
    ["Follow-up", (a) => { const f = fuOf(a); return f ? `${f.state} · ${f.due}` : ""; }, "follow"], ["Source", (a) => a.source || "", "source"],
    ["Flags", (a) => el("span", { class: "card-badges", style: "margin:0" }, appChips(a, true).slice(1)), null],
  ];
  function appTable(box, list) {
    const q = el("input", { type: "search", placeholder: "Search applications…", "aria-label": "Search applications", value: ui.appQ || "" });
    const stage = el("select", { "aria-label": "Stage filter" }, el("option", { value: "", text: "All stages" }), STAGE_ORDER.map((s2) => el("option", { value: s2, text: STAGE_LABEL[s2], selected: ui.appStage === s2 })));
    const wrap = el("div");
    const draw = () => {
      wrap.textContent = "";
      const words = (ui.appQ || "").toLowerCase().split(/\s+/).filter(Boolean);
      let rows = list.filter((a) => (!ui.appStage || a.stage === ui.appStage) && words.every((w) => `${a.company} ${a.title} ${a.notes} ${a.contact} ${a.referral} ${a.source}`.toLowerCase().includes(w)));
      const sk = ui.appSort || "applied_date"; const dir = ui.appDir || -1;
      const val = (a) => (sk === "days" ? daysSince(a.applied_date) ?? -1 : sk === "stage" ? STAGE_ORDER.indexOf(a.stage) : sk === "follow" ? (fuOf(a) || {}).due || "9999" : String(a[sk] || ""));
      rows = rows.sort((x, y) => { const p = val(x), r = val(y); return (p < r ? -1 : p > r ? 1 : 0) * dir; });
      const head = el("tr", null, APP_COLS.map(([h, , k]) => el("th", { scope: "col", "aria-sort": k && sk === k ? (dir > 0 ? "ascending" : "descending") : null,
        onclick: () => { if (!k) return; if (ui.appSort === k) ui.appDir = -(ui.appDir || -1); else { ui.appSort = k; ui.appDir = -1; } saveUI(); draw(); } }, h, k && sk === k ? (dir > 0 ? " ↑" : " ↓") : "")));
      const body = el("tbody");
      for (const a of rows) body.append(el("tr", { tabindex: "0", onclick: () => openDrawer(a.id), onkeydown: (e) => { if (e.key === "Enter") openDrawer(a.id); } }, APP_COLS.map(([, f, k]) => el("td", { class: ["applied_date", "days", "follow"].includes(k) ? "nowrap" : k === "title" ? "role" : "" }, f(a)))));
      wrap.append(el("div", { class: "table-wrap" }, el("table", { class: "grid" }, el("thead", null, head), body)), el("p", { class: "muted", style: "font-size:12px", text: `${rows.length} of ${list.length}` }));
    };
    q.addEventListener("input", () => { ui.appQ = q.value; saveUI(); draw(); });
    stage.addEventListener("change", () => { ui.appStage = stage.value; saveUI(); draw(); });
    box.append(el("div", { class: "toolbar" }, q, stage), wrap); draw();
  }

  // ----- stats (inline SVG) -----
  function weekChart(stats) {
    const W = 560, H = 150, pad = 22, n = stats.per_week.length, max = Math.max(1, ...stats.per_week), bw = (W - pad * 2) / n;
    const g = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": `Applications per week, last ${n} weeks: ${stats.per_week.join(", ")}` });
    g.append(svg("line", { x1: pad, x2: W - pad, y1: H - pad, y2: H - pad, class: "axis" }));
    stats.per_week.forEach((v, i) => {
      const h = ((H - pad * 2) * v) / max, x = pad + i * bw + 3;
      g.append(svg("rect", { x, y: H - pad - h, width: bw - 6, height: Math.max(h, v ? 2 : 0), rx: 3, class: "bar" }, svg("title", {}, `Week of ${C.iso(C.day(stats.week_start) + 7 * i)}: ${v}`)));
      if (v) g.append(svg("text", { x: x + (bw - 6) / 2, y: H - pad - h - 4, "text-anchor": "middle", class: "lbl" }, String(v)));
      if (i % 2 === 0) g.append(svg("text", { x: x + (bw - 6) / 2, y: H - 6, "text-anchor": "middle", class: "lbl" }, C.iso(C.day(stats.week_start) + 7 * i).slice(5).replace("-", "/")));
    });
    return g;
  }
  function funnel(list) {
    const outs = list.map(C.furthest); const n = list.length || 1;
    const steps = [["Applied", list.length], ["Online Assessment+", outs.filter((o) => ["OA", "interview", "offer"].includes(o)).length],
      ["Interview+", outs.filter((o) => ["interview", "offer"].includes(o)).length], ["Offer", outs.filter((o) => o === "offer").length]];
    return el("div", { class: "bars" }, steps.map(([k, v]) => [el("span", { class: "k", text: k }), el("div", { class: "hbar" }, el("span", { style: `width:${(100 * v) / n}%` })), el("span", { class: "v", text: v })]));
  }
  function breakdownTable(title, rows) {
    return el("div", { class: "section" }, el("h3", { text: title }), el("div", { class: "table-wrap" }, el("table", { class: "grid" },
      el("thead", null, el("tr", null, ["Group", "Applications", "Responses", "Response rate", "Interviews", "Offers"].map((h, i) => el("th", { class: i ? "num" : "", text: h })))),
      el("tbody", null, rows.map((r) => el("tr", null, el("td", { text: r.group }), el("td", { class: "num", text: r.n }), el("td", { class: "num", text: r.responses }),
        el("td", { class: "num", text: `${r.response_rate}%` }), el("td", { class: "num", text: r.interviews }), el("td", { class: "num", text: r.offers }))))))) ;
  }
  function appStats(box) {
    const tracks = ui.both ? ["coop_intern", "full_time"] : [ui.track];
    for (const t of tracks) {
      const list = liveApps().filter((a) => a.track === t);
      const s2 = C.trackStats(list, today(), 12);
      const pct = (x) => (x == null ? "—" : `${x}%`);
      const sec = el("div", { class: "section stats-block" }, el("h3", null, el("span", { class: `chip ${t}`, text: TRACKS[t] }), " "),
        el("div", { class: "tiles mini" }, [["Applications", s2.total], ["Response rate", pct(s2.response_rate)], ["OA rate", pct(s2.oa_rate)], ["Interview rate", pct(s2.interview_rate)],
          ["Offers", s2.offers], ["Median days to first response", s2.median_days_to_response == null ? "—" : s2.median_days_to_response]]
          .map(([l, v]) => el("div", { class: "tile disabled", role: "group", "aria-label": `${l}: ${v}` }, el("span", { class: "n", text: String(v) }), el("span", { class: "l", text: l })))));
      if (!list.length) { sec.append(el("p", { class: "muted", text: "No applications on this track yet." })); box.append(sec); continue; }
      sec.append(el("div", { class: "stats-grid" },
        el("div", { class: "side-card" }, el("h4", { text: "Applications per week" }), weekChart(s2)),
        el("div", { class: "side-card" }, el("h4", { text: "Funnel (furthest stage reached)" }), funnel(list))));
      sec.append(breakdownTable("By source", C.outcomeBreakdown(list, (a) => a.source || "Unknown")),
        breakdownTable("By company tier", C.outcomeBreakdown(list, (a) => ((a.job || {}).tier ? `Tier ${a.job.tier}` : "Untiered / added manually"))),
        breakdownTable("By score label", C.outcomeBreakdown(list, (a) => (a.job || {}).label || "Not scored (added manually)")),
        breakdownTable("By level", C.outcomeBreakdown(list, (a) => (a.job || {}).level || "Unknown")));
      box.append(sec);
    }
    box.append(el("p", { class: "muted", style: "font-size:12px", text: "A response is any move to Online Assessment, Interviewing, Offer or Rejected. Ghosted and Withdrawn count as no response. Stats are computed from your own log." }));
  }
  function renderStatsView(box) { ui.appTab = "stats"; renderApplications(box); }

  // ----- follow-ups view -----
  function renderFollowups(box) {
    const fu = followupsDue(), gh = ghostsDue();
    box.append(el("p", { class: "muted", style: "margin-top:0", text: `Applications still at “Applied” ${appsCfg.follow_up_days[0]}–${appsCfg.follow_up_days[1]} days after applying (or after your last follow-up). You send follow-ups yourself; HireScope never contacts anyone.` }));
    const row = (a, extra) => el("div", { class: "fu-row" }, el("div", { style: "flex:1;min-width:0" }, el("b", { text: `${a.company} — ${a.title}` }),
      el("div", { class: "card-badges" }, appChips(a))), extra, el("button", { class: "btn small ghost", type: "button", onclick: () => openDrawer(a.id) }, "Open"));
    if (!fu.length && !gh.length) { box.append(el("div", { class: "empty" }, el("b", { text: "No follow-ups due." }), el("div", { text: liveApps().length ? "You're caught up." : "Applications you mark applied show up here when a follow-up is due." }))); return; }
    if (fu.length) box.append(el("div", { class: "section" }, el("h3", { text: `Follow-ups due (${fu.length})` }), fu.map((a) => row(a,
      el("button", { class: "btn small", type: "button", onclick: () => saveApp(C.logFollowUp(a, today()), `Logged a follow-up for ${a.company}`) }, "I followed up today")))));
    if (gh.length) box.append(el("div", { class: "section" }, el("h3", { text: `No update in ${appsCfg.ghosted_after_days}+ days (${gh.length})` }),
      el("p", { class: "muted", style: "margin:0 0 8px;font-size:12px", text: "Suggestions only — nothing is marked Ghosted unless you confirm." }),
      gh.map((a) => row(a, el("span", { style: "display:flex;gap:6px" },
        el("button", { class: "btn small", type: "button", onclick: () => moveTo(a, "ghosted") }, "Mark Ghosted"),
        el("button", { class: "btn small ghost", type: "button", onclick: () => saveApp(C.edit(a, { events: [...(a.events || []), { at: new Date().toISOString(), kind: "note", from: null, to: null, note: "Still waiting (checked)" }] }), "Noted — reminder reset") }, "Still waiting"))))));
  }

  // ----- hiring seasons -----
  const SEASON_TRACK = { coop_intern: "coop_intern", full_time: "new_grad" };
  const seasonsInTrack = () => (META.seasons || []).filter((x) => ui.both || x.track === SEASON_TRACK[ui.track]);
  function monthStrip(sea) {
    const W = 360, H = 40, cw = W / 12, years = Object.keys(sea.years);
    const g = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart season", role: "img",
      "aria-label": `${sea.company}: first postings ${years.map((y) => sea.years[y]).join(", ")}; next expected ${sea.predicted}` });
    for (let m = 0; m < 12; m++) {
      g.append(svg("rect", { x: m * cw + 1, y: 6, width: cw - 2, height: 18, rx: 3, class: "cell" }));
      g.append(svg("text", { x: m * cw + cw / 2, y: 37, "text-anchor": "middle", class: "lbl" }, "JFMAMJJASOND"[m]));
    }
    years.forEach((y, i) => { const m = Number(sea.years[y].slice(5, 7)) - 1;
      g.append(svg("circle", { cx: m * cw + cw / 2 - 6 + 6 * (i % 3), cy: 15, r: 4, class: "seen" }, svg("title", {}, `${y}: first posted ${sea.years[y]}`))); });
    const pm = Number(sea.predicted.slice(5, 7)) - 1;
    g.append(svg("rect", { x: pm * cw + 1, y: 6, width: cw - 2, height: 18, rx: 3, class: "pred" }, svg("title", {}, `expected ${sea.predicted}`)));
    return g;
  }
  function renderSeasons(box) {
    const list = seasonsInTrack();
    box.append(el("p", { class: "muted", style: "margin-top:0", text: `When each company first posted ${ui.both ? "co-op/intern and new-grad" : ui.track === "coop_intern" ? "co-op / intern" : "new-grad"} roles, from your own job history (posting dates, else first-seen). Dots = first posting each year; outlined month = expected next window. One year of history is a guess.` }));
    if (!list.length) { box.append(el("div", { class: "empty", text: "No history yet — seasons appear as runs accumulate." })); return; }
    const soon = list.filter((x) => x.remind);
    if (soon.length) box.append(el("div", { class: "banner warn", style: "margin:0 0 12px" }, `Coming up (no postings yet): ${soon.slice(0, 8).map((x) => `${x.company} (${x.status.replace("expected ", "")})`).join(" · ")}`));
    const q = (tstate().q || "").toLowerCase();
    const rows = list.filter((x) => !q || x.company.toLowerCase().includes(q)).slice(0, 300);
    box.append(el("div", { class: "table-wrap" }, el("table", { class: "grid seasons" },
      el("thead", null, el("tr", null, ["Company", "Track", "Months (first posting per year)", "Next window", "Status"].map((h) => el("th", { text: h })))),
      el("tbody", null, rows.map((x) => el("tr", null, el("td", null, el("b", { text: x.company })),
        el("td", { text: x.track === "new_grad" ? "New grad" : "Co-op / Intern" }), el("td", null, monthStrip(x)),
        el("td", { class: "nowrap", text: x.predicted }),
        el("td", null, el("span", { class: `chip ${x.open_now ? "good" : x.remind ? "warn" : ""}`, text: x.status }), el("div", { class: "muted", style: "font-size:11px", text: x.confidence }))))))));
    if (list.length > rows.length) box.append(el("p", { class: "muted", text: `Showing ${rows.length} of ${list.length}; search to narrow.` }));
  }

  // ----- run log view -----
  function renderRunLog(box) {
    const s2 = META.summary || {}; const im = META.statuses_import || {};
    box.append(el("div", { class: "section" }, el("h3", { text: "Last run" }), facts([
      ["Finished", META.last_run ? fmtTime(META.last_run) : "never"],
      ["Coverage", `${s2.sources_ok ?? 0} of ${s2.sources_checked ?? 0} companies/sources checked successfully · ${s2.sources_failed || 0} failed · ${s2.sources_manual || 0} manual · ${s2.sources_partial || 0} partial · ${s2.sources_skipped || 0} not due`],
      ["Jobs", `${(s2.jobs_found || 0).toLocaleString()} found · ${s2.new || 0} new · ${s2.updated || 0} updated · ${s2.closed || 0} closed`],
      ["Scored", `${(s2.scored || 0).toLocaleString()} visible · ${(s2.filtered || 0).toLocaleString()} filtered · ${s2.strong || 0} Strong`],
      ["Statuses import", im.file ? `${im.file}${im.skipped_reason ? ` — ${im.skipped_reason}` : ` — ${im.statuses_applied || 0} statuses, ${(im.apps_new || 0) + (im.apps_updated || 0)} applications merged`}` : (im.skipped_reason || "not run")],
      ["Log file", el("span", null, el("code", { class: "mono", text: META.log_path || "data/logs/jobsearch.log" }), " ",
        el("button", { class: "btn small ghost", type: "button", onclick: async () => toast((await copyText(META.log_path || "")) ? "Path copied" : "Clipboard unavailable") }, "Copy path"))],
    ])));
    const runs = META.runs || [];
    if (runs.length) box.append(el("div", { class: "section" }, el("h3", { text: "Recent runs" }), el("div", { class: "table-wrap" }, el("table", { class: "grid" },
      el("thead", null, el("tr", null, ["Started", "Scope", "Checked", "OK", "Failed", "Found", "New", "Updated", "Closed", "Strong"].map((h) => el("th", { text: h })))),
      el("tbody", null, runs.map((r) => el("tr", null, el("td", { class: "nowrap", text: fmtTime(r.started_at) }), el("td", { text: r.scope || "full" }),
        ...[r.sources_checked, r.sources_ok, r.sources_failed, r.jobs_found, r.new, r.updated, r.closed, r.strong].map((v) => el("td", { class: "num", text: v == null ? "—" : v.toLocaleString() })))))))));
  }

  // ----- drawer -----
  let dApp = null; let dLastFocus = null;
  function openDrawer(id) {
    if (!apps[id]) return;
    if (pJob) closePanel();
    if (!dApp) dLastFocus = document.activeElement;
    dApp = id; renderDrawer();
    $("#drawer").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false"); $("#scrim").classList.add("open");
    setTimeout(() => $("#drawer").focus({ preventScroll: true }), 0);
  }
  function closeDrawer() {
    $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); if (!pJob) $("#scrim").classList.remove("open"); dApp = null;
    if (dLastFocus && dLastFocus.focus && document.contains(dLastFocus)) dLastFocus.focus({ preventScroll: true });
  }
  $("#d-close").addEventListener("click", closeDrawer);
  function renderDrawer() {
    const a = apps[dApp]; if (!a) { closeDrawer(); return; }
    $("#d-title").textContent = a.title;
    $("#d-sub").textContent = `${a.company} · applied ${a.applied_date ? fmtDate(a.applied_date) : "?"}${daysSince(a.applied_date) != null ? ` (${daysSince(a.applied_date)} days ago)` : ""}`;
    const b = $("#d-badges"); b.textContent = ""; b.append(...appChips(a, true), el("span", { class: "chip", text: STAGE_LABEL[a.stage] }));
    const bar = $("#d-stages"); bar.textContent = "";
    for (const stg of STAGE_ORDER) bar.append(el("button", { type: "button", class: `stage-btn ${CLOSED_STAGES.includes(stg) ? "closed" : ""}`, "aria-pressed": String(a.stage === stg), onclick: () => moveTo(apps[dApp], stg) }, STAGE_LABEL[stg]));
    const body = $("#d-body"); const keep = body.scrollTop; body.textContent = "";
    const f = fuOf(a);
    if (f && f.state !== "upcoming") body.append(el("div", { class: `banner ${f.state === "overdue" ? "err" : "warn"}`, style: "margin:0 0 12px" },
      el("span", { text: `Follow-up ${f.state}: ${f.days} days since ${a.followed_up ? "your last follow-up" : "applying"}. You send it yourself.` }),
      el("button", { class: "btn small x", type: "button", onclick: () => saveApp(C.logFollowUp(a, today()), "Logged a follow-up") }, "I followed up today")));
    if (C.ghostSuggestion(a, today(), appsCfg.ghosted_after_days)) body.append(el("div", { class: "banner warn", style: "margin:0 0 12px" },
      el("span", { text: `No update in ${C.day(today()) - C.lastActivity(a)} days. Mark it Ghosted? (Only if you agree.)` }),
      el("button", { class: "btn small x", type: "button", onclick: () => moveTo(a, "ghosted") }, "Mark Ghosted")));
    const warns = C.duplicateWarnings(a.company, a.title, a.job_key, liveApps(), today(), appsCfg.same_company_window_days, a.id, appsCfg.near_duplicate_ratio);
    if (warns.length) body.append(el("div", { class: "banner info", style: "margin:0 0 12px" }, el("ul", { style: "margin:0" }, warns.map((w) => el("li", { text: w.message })))));

    const field = (label, key, type, attrs) => {
      const input = type === "textarea" ? el("textarea", Object.assign({ rows: 4, "aria-label": label }, attrs || {})) : el("input", Object.assign({ type, "aria-label": label }, attrs || {}));
      input.value = a[key] || "";
      input.addEventListener("change", () => {
        const v = input.value.trim(); const cur = apps[dApp]; if ((cur[key] || "") === v) return;
        if (type === "url" && v && !safeUrl(v)) { toast("Links must start with http:// or https://"); input.value = cur[key] || ""; return; }
        let next = C.edit(cur, { [key]: v });
        if (key === "next_step" || key === "next_step_date") next = C.edit(next, { events: [...(next.events || []), { at: next.updated_at, kind: "note", from: null, to: null, note: `Next step: ${next.next_step || "—"}${next.next_step_date ? ` on ${next.next_step_date}` : ""}` }] });
        if (key === "applied_date" && !/^\d{4}-\d{2}-\d{2}$/.test(v)) { toast("Applied date is required"); input.value = cur[key] || ""; return; }
        saveApp(next, `Saved ${label.toLowerCase()}`);
      });
      return el("label", { class: type === "textarea" ? "full" : "" }, label, input);
    };
    const ref = REF[a.company];
    const refList = ref && (ref.people || []).length ? el("datalist", { id: "ref-people" }, ref.people.map((p) => el("option", { value: p.name }))) : null;
    body.append(el("div", { class: "section" }, el("h3", { text: "Details" }), el("div", { class: "form-grid" },
      field("Applied on", "applied_date", "date"),
      field("Next step", "next_step", "text", { placeholder: "e.g. OA due, recruiter screen", maxlength: 300 }), field("Next step date", "next_step_date", "date"),
      field("Contact", "contact", "text", { placeholder: "Recruiter / hiring manager", maxlength: 300 }), field("Referral", "referral", "text", { placeholder: "Who referred you", maxlength: 300, list: refList ? "ref-people" : null }),
      field("Application link", "link", "url", { placeholder: "https://…" }), field("Source", "source", "text", { maxlength: 200 }),
      field("Job package used", "package", "text", { maxlength: 300, placeholder: "Company_Role_JobID.md" }),
      field("Notes", "notes", "textarea", { maxlength: 20000 })), refList));
    const trackSel = el("select", { "aria-label": "Track" }, ["coop_intern", "full_time"].map((t) => el("option", { value: t, text: TRACKS[t], selected: a.track === t })));
    trackSel.addEventListener("change", () => saveApp(C.edit(apps[dApp], { track: trackSel.value }), `Moved to ${TRACKS[trackSel.value]}`));
    body.querySelector(".form-grid").prepend(el("label", null, "Track", trackSel));
    if (ref && (ref.count || ref.possible_count)) body.append(el("div", { class: "section" }, el("h3", { text: `You know ${ref.count} ${ref.count === 1 ? "person" : "people"} at ${a.company}` }), referralList(ref)));

    const evs = [...(a.events || [])].sort((x, y) => String(y.at).localeCompare(String(x.at)));
    const evText = (e) => e.kind === "created" ? `Added (${STAGE_LABEL[e.to] || "Applied"})` : e.kind === "stage" ? `${STAGE_LABEL[e.from] || e.from || "?"} → ${STAGE_LABEL[e.to] || e.to}` : e.note || e.kind;
    body.append(el("div", { class: "section" }, el("h3", { text: "Timeline" }), el("ol", { class: "timeline" }, evs.map((e) => el("li", { class: e.kind },
      el("span", { class: "when", text: fmtTime(e.at) }), el("span", { text: evText(e) }))))));

    const jdBox = el("div", { class: "section" }, el("h3", { text: "Saved job description" }));
    if (a.jd) jdBox.append(el("details", null, el("summary", { text: `Local copy (${a.jd.length.toLocaleString()} characters) — kept even if the posting is removed` }), el("div", { class: "jd-saved", text: a.jd })));
    else {
      const ta = el("textarea", { rows: 4, placeholder: "Paste the JD to keep a local copy", "aria-label": "Paste job description", maxlength: 200000 });
      jdBox.append(el("p", { class: "muted", style: "margin:0 0 6px;font-size:12px", text: "No saved copy yet. The pipeline also keeps one from the job's stored JD after you export." }), ta,
        el("button", { class: "btn small", type: "button", style: "margin-top:6px", onclick: () => { if (ta.value.trim()) saveApp(C.edit(apps[dApp], { jd: ta.value.trim() }), "JD saved"); } }, "Save JD copy"));
    }
    body.append(jdBox);
    body.scrollTop = keep;

    const act = $("#d-actions"); act.textContent = "";
    if (safeUrl(a.link)) act.append(el("a", { class: "btn", href: a.link, target: "_blank", rel: "noopener noreferrer" }, "Open application link"));
    const j = KEY_MAP.get(a.job_key);
    if (j) act.append(el("button", { class: "btn", type: "button", onclick: () => { closeDrawer(); openPanel(j.id); } }, "Job details"));
    act.append(el("span", { class: "spacer" }),
      el("button", { class: "btn ghost", type: "button", onclick: () => { saveApp(C.edit(apps[dApp], { deleted: true }), `Removed ${a.company} application`); closeDrawer(); } }, "Remove"));
  }
  function referralList(ref) {
    const li = (p) => el("li", null, el("b", { text: p.name }), p.position ? ` — ${p.position}` : "", p.company ? el("span", { class: "muted", text: ` (${p.company})` }) : "",
      safeUrl(p.url) ? [" ", el("a", { href: p.url, target: "_blank", rel: "noopener noreferrer", text: "profile" })] : "");
    return el("div", null, (ref.people || []).length ? el("ul", { class: "list-plain" }, ref.people.map(li)) : null,
      (ref.possible || []).length ? el("div", { style: "margin-top:6px" }, el("div", { class: "muted", style: "font-size:12px", text: "Possibly related (company name only starts the same):" }), el("ul", { class: "list-plain" }, ref.possible.map(li))) : null,
      el("p", { class: "muted", style: "margin:6px 0 0;font-size:12px", text: "From your LinkedIn connections export. HireScope never contacts anyone — reach out yourself if it makes sense." }));
  }

  // ----- add application -----
  function openAddApp(prefill) {
    const d = $("#add-app"); const f = $("#add-app-form");
    f.reset(); f.elements.applied_date.value = today(); f.elements.track.value = ui.track;
    for (const [k, v] of Object.entries(prefill || {})) if (f.elements[k]) f.elements[k].value = v;
    const warnBox = $("#add-app-warn"); warnBox.textContent = "";
    const check = () => {
      warnBox.textContent = "";
      const co = f.elements.company.value.trim(), ti = f.elements.title.value.trim(); if (!co || !ti) return;
      const w = C.duplicateWarnings(co, ti, null, liveApps(), today(), appsCfg.same_company_window_days, null, appsCfg.near_duplicate_ratio);
      if (w.length) warnBox.append(el("div", { class: "banner warn" }, el("ul", { style: "margin:0" }, w.map((x) => el("li", { text: x.message })))));
    };
    f.elements.company.oninput = check; f.elements.title.oninput = check;
    d.showModal(); f.elements.company.focus();
  }
  $("#add-app").addEventListener("close", () => {
    const d = $("#add-app"); const f = $("#add-app-form");
    if (d.returnValue !== "save") return;
    const v = (k) => f.elements[k].value.trim();
    if (!v("company") || !v("title")) return;
    const link = safeUrl(v("link")) || "";
    const a = C.newApp({ company: v("company"), title: v("title"), track: v("track"), applied_date: v("applied_date") || today(), link, source: v("source"), jd: v("jd"), notes: v("notes") });
    saveApp(a, `Added ${a.company} — ${a.title}`);
    if (!ui.both && a.track !== ui.track) toast(`Added to the ${TRACKS[a.track]} tab`);
  });

  // ---------- detail panel ----------
  const TABS = ["Overview", "Requirements", "Keywords", "Score", "Full JD", "Similar", "Notes & History"];
  let pTab = "Overview"; let pJob = null; let pDetail = null; let jdHighlight = null; let kwFilter = null; let lastFocus = null;
  function openPanel(id) {
    const j = BY_ID.get(id); if (!j) return;
    if (dApp) closeDrawer();
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
  $("#p-close").addEventListener("click", closePanel); $("#scrim").addEventListener("click", () => { if (dApp) closeDrawer(); if (pJob) closePanel(); });
  function renderPanelBody(node) { const b = $("#p-body"); b.textContent = ""; b.append(node); b.scrollTop = 0; }
  function renderPanel() {
    const tabs = $("#p-tabs"); tabs.textContent = "";
    const avail = pJob.filtered || !pDetail ? ["Overview", "Notes & History"] : TABS;
    if (!avail.includes(pTab)) pTab = "Overview";
    for (const t of avail) tabs.append(el("button", { class: "panel-tab", role: "tab", "aria-selected": String(t === pTab), type: "button", onclick: () => { pTab = t; renderPanel(); } }, t));
    const fn = { Overview: overviewTab, Requirements: reqTab, Keywords: keywordsTab, Score: scoreTab, "Full JD": jdTab, Similar: similarTab, "Notes & History": notesTab }[pTab];
    renderPanelBody(fn(pJob, pDetail || {}));
  }
  const facts = (pairs) => el("dl", { class: "facts" }, pairs.filter(Boolean).map(([k, v]) => [el("dt", { text: k }), el("dd", null, v)]));
  const quote = (t) => (t ? el("div", { class: "quote", text: `“${t}”` }) : null);
  function overviewTab(j, d) {
    const out = el("div");
    if (j.filtered) {
      out.append(el("div", { class: "section" }, el("h3", { text: "Why this job is hidden" }), el("ul", { class: "list-plain" }, (j.reasons || []).map((r) => el("li", { text: r }))),
        el("p", { class: "muted", text: "Hard filters live in inputs/preferences.yaml (rules: hard | soft) and config/title_rules.yaml. If this is a false negative, shortlist it: feedback learning lists it under Learned adjustments as a rule to review (it never un-hides jobs on its own)." })));
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
    const app = appForJob(j);
    if (app) out.append(el("div", { class: "banner info", style: "margin:0 0 16px" }, el("span", { text: `In My Applications: ${STAGE_LABEL[app.stage]} since ${fmtDate((app.events || []).slice(-1)[0] ? (app.events.slice(-1)[0].at || "").slice(0, 10) : app.applied_date)} · applied ${fmtDate(app.applied_date)}` }),
      el("button", { class: "btn small x", type: "button", onclick: () => openDrawer(app.id) }, "Open application")));
    const ref = REF[j.company];
    if (ref && (ref.count || ref.possible_count)) out.append(el("div", { class: "section" }, el("h3", { text: ref.count ? `You know ${ref.count} ${ref.count === 1 ? "person" : "people"} at ${j.company}` : `Possible connections at ${j.company}` }), referralList(ref)));
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
    const k = keyOf(j); const has = overrides[k] && overrides[k].track;
    if (j.track_auto !== "unclear" && !has) return null;
    const set = (t) => {   // a reset keeps a dated tombstone so an older export can't bring the override back
      const before = overrides[k]; overrides[k] = { track: t, updated_at: new Date().toISOString() }; saveOverrides();
      toast(t ? `Moved to ${TRACKS[t]}` : "Track reset", () => { if (before) overrides[k] = before; else delete overrides[k]; saveOverrides(); renderAll(true); openPanel(j.id); });
      renderBanners(); renderAll(true); openPanel(j.id);
    };
    return el("span", { style: "display:inline-flex;gap:6px;margin-left:6px" },
      el("button", { class: "btn small", type: "button", onclick: () => set("coop_intern") }, "Move to Co-op"),
      el("button", { class: "btn small", type: "button", onclick: () => set("full_time") }, "Move to Full-time"),
      has ? el("button", { class: "btn small ghost", type: "button", onclick: () => set(null) }, "Reset") : null);
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
  function notesTab(j) {
    const out = el("div"); const k = keyOf(j); const st = statuses[k] || {};
    const ta = el("textarea", { rows: 5, placeholder: "Your notes about this job (saved in this browser, exported with your statuses)", "aria-label": "Notes", maxlength: 20000, style: "width:100%" });
    ta.value = st.notes || "";
    ta.addEventListener("change", () => { setStatus(j, { notes: ta.value }); toast("Notes saved"); });
    out.append(el("div", { class: "section" }, el("h3", { text: "Notes" }), ta));
    const app = appForJob(j);
    out.append(el("div", { class: "section" }, el("h3", { text: "Application" }), app ? el("div", null,
      facts([["Stage", STAGE_LABEL[app.stage]], ["Applied", fmtDate(app.applied_date)], ["Follow-up", (() => { const f = fuOf(app); return f ? (f.state === "upcoming" ? `on ${fmtDate(f.due)}` : `${f.state} since ${fmtDate(f.due)}`) : "—"; })()],
        ["Next step", app.next_step ? `${app.next_step}${app.next_step_date ? ` · ${fmtDate(app.next_step_date)}` : ""}` : "—"], ["Outcome so far", C.furthest(app)]]),
      el("button", { class: "btn small", type: "button", style: "margin-top:8px", onclick: () => openDrawer(app.id) }, "Open in My Applications"))
      : el("div", null, el("p", { class: "muted", style: "margin:0 0 8px", text: "Not applied yet." }), el("button", { class: "btn small", type: "button", onclick: () => markApplied(j) }, "Mark Applied"))));
    const hist = [...(st.history || [])].reverse();
    out.append(el("div", { class: "section" }, el("h3", { text: "Status history" }), hist.length ? el("ol", { class: "timeline" }, hist.map((h) =>
      el("li", null, el("span", { class: "when", text: fmtTime(h.at) }), el("span", { text: `${h.status}${h.reason ? ` (${h.reason})` : ""}` })))) : el("p", { class: "muted", style: "margin:0", text: "No status changes yet." })));
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
    const app = appForJob(j);
    box.append(el("button", { class: "btn", type: "button", onclick: () => (app ? openDrawer(app.id) : markApplied(j)), title: app ? "Open in My Applications" : "Mark applied (a)" }, app ? (app.stage === "applied" ? "Applied ✓" : `Applied ✓ · ${STAGE_LABEL[app.stage]}`) : "Mark Applied"));
    const reason = el("select", { "aria-label": "Skip with reason" }, el("option", { value: "", text: "Skip…" }), SKIP_REASONS.map((r) => el("option", { value: r, text: r })));
    reason.addEventListener("change", () => { if (reason.value) skip(j, reason.value); });
    const st = el("select", { "aria-label": "Status" }, STATUSES.map((s) => el("option", { value: s, text: s || "Status…", selected: s === statusOf(j) })));
    st.addEventListener("change", () => {
      if (st.value === "applied" && !appForJob(j)) { markApplied(j); return; }
      const undo = setStatus(j, { status: st.value }); toast(st.value ? `Status: ${st.value}` : "Status cleared", undo); renderAll(true); renderActions();
    });
    box.append(reason, st, el("span", { class: "spacer" }));
    const li = safeUrl(j.linkedin_url); if (li) box.append(el("a", { class: "btn small ghost", href: li, target: "_blank", rel: "noopener noreferrer" }, "LinkedIn search"));
    box.append(...quickLinks(j));
  }
  async function generate(j) {
    const d = await loadDetail(j);
    if (!d) { toast("Details not available for this job"); return; }
    download(d.package_filename, d.package, "text/markdown;charset=utf-8");
    const ok = await copyText(d.package);
    const app = appForJob(j);
    if (app && !app.package) { apps[app.id] = C.edit(app, { package: d.package_filename }); saveApps(); }
    const undo = setStatus(j, app ? { package: d.package_filename } : { status: "package exported", package: d.package_filename });
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
    const payload = { exported_at: new Date().toISOString(), format: 3, statuses, track_overrides: overrides, applications: apps };
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
    const onApps = ["applications", "stats", "followups"].includes(tstate().view);
    const v = tstate().view;
    ui.track = t; if (onApps) tstate().view = v; saveUI();
    $("#q").value = tstate().q || ""; closePop(); renderAll();
  }
  // URL hash: #coop | #fulltime [/view] [?label=Strong|Stretch&q=…&sort=newest&salary=120000]; #applications[/coop|/fulltime]
  function writeHash() {
    const st = tstate(); const v = st.view || defaultView(); const tr = ui.track === "coop_intern" ? "coop" : "fulltime";
    let h;
    if (v === "applications") h = `#applications/${tr}`;
    else {
      const p = new URLSearchParams();
      for (const f of FILTERS) { const vals = st.filters[f.id]; if (vals && vals.length) p.set(f.id, vals.join("|")); }
      if (st.q) p.set("q", st.q); if (st.sort && st.sort !== "overall") p.set("sort", st.sort); if (st.salary) p.set("salary", st.salary);
      if (st.salaryKnown) p.set("salary_known", "1");
      const qs = p.toString(); h = `#${tr}${v !== defaultView() || qs ? `/${v}` : ""}${qs ? `?${qs}` : ""}`;
    }
    if (location.hash !== h) history.replaceState(null, "", h);
  }
  function readHash() {
    const m = /^#(coop|fulltime|applications)(?:\/([a-z0-9]+))?(?:\?(.*))?$/.exec(location.hash || ""); if (!m) return;
    if (m[1] === "applications") { if (m[2] === "coop" || m[2] === "fulltime") ui.track = m[2] === "coop" ? "coop_intern" : "full_time"; tstate().view = "applications"; return; }
    ui.track = m[1] === "coop" ? "coop_intern" : "full_time";
    const st = tstate();
    if (m[2] && VIEWS.some((v) => v.id === m[2])) st.view = m[2];
    if (m[3] !== undefined) {
      const p = new URLSearchParams(m[3]); st.filters = {};
      for (const f of FILTERS) if (p.get(f.id)) st.filters[f.id] = p.get(f.id).split("|");
      st.q = p.get("q") || ""; if (p.get("sort") && SORTS[p.get("sort")]) st.sort = p.get("sort");
      st.salary = Number(p.get("salary")) || 0; st.salaryKnown = p.get("salary_known") === "1";
    }
  }
  window.addEventListener("hashchange", () => { readHash(); saveUI(); $("#q").value = tstate().q || ""; renderAll(); });
  $("#both").addEventListener("change", () => { ui.both = $("#both").checked; saveUI(); renderAll(); });
  $("#sort").addEventListener("change", () => { tstate().sort = $("#sort").value; saveUI(); renderList(); });
  $("#mode-toggle").addEventListener("click", () => { const st = tstate(); const v = st.view || defaultView(); st.modes = st.modes || {}; st.modes[v] = modeOf(v) === "cards" ? "table" : "cards"; saveUI(); renderList(); });
  $("#density").addEventListener("click", () => { ui.compact = !ui.compact; saveUI(); renderList(true); });
  let qTimer = null;
  $("#q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(() => { tstate().q = $("#q").value; saveUI(); renderCounts(); renderList(); }, 120); });
  $("#help-btn").addEventListener("click", () => $("#help").showModal());

  // ---------- command palette (⌘K) ----------
  let palItems = []; let palSel = 0;
  function paletteSource() {
    const st = tstate();
    const items = [
      ...VIEWS.map((v) => ({ kind: "View", label: v.label, run: () => setView(v.id) })),
      { kind: "Action", label: "Export statuses", run: exportStatuses },
      { kind: "Action", label: "Add application", run: () => openAddApp() },
      { kind: "Action", label: "Switch to Co-op / Internships", run: () => switchTrack("coop_intern") },
      { kind: "Action", label: "Switch to Full-time", run: () => switchTrack("full_time") },
      { kind: "Action", label: "Toggle light / dark theme", run: () => $("#theme").click() },
      { kind: "Action", label: "Cards / table", run: () => $("#mode-toggle").click() },
      { kind: "Action", label: "Compact density", run: () => $("#density").click() },
      { kind: "Action", label: "Clear all filters", run: () => { st.filters = {}; st.q = ""; st.salary = 0; $("#q").value = ""; ui.day = null; saveUI(); renderAll(); } },
      { kind: "Action", label: "Keyboard shortcuts", run: () => $("#help").showModal() },
    ];
    const byCo = counts(JOBS.filter((j) => !j.filtered).map((j) => j.company));
    for (const [c, n] of byCo) items.push({ kind: "Company", label: c, sub: `${n} job${n > 1 ? "s" : ""}`, run: () => { const t = tstate(); t.filters = { company: [c] }; t.q = ""; $("#q").value = ""; saveUI(); setView("all"); } });
    for (const a of liveApps()) items.push({ kind: "Application", label: a.title, sub: `${a.company} · ${STAGE_LABEL[a.stage]}`, run: () => openDrawer(a.id) });
    return items;
  }
  function openPalette() {
    const d = $("#palette"); if (d.open) return;
    const q = $("#palette-q"); q.value = ""; d.showModal(); drawPalette(""); q.focus();
  }
  function drawPalette(qs) {
    const words = qs.toLowerCase().split(/\s+/).filter(Boolean);
    const hit = (x) => words.every((w) => `${x.label} ${x.sub || ""} ${x.kind}`.toLowerCase().includes(w));
    let items = paletteSource().filter(hit);
    if (words.length) {
      const jobs = [];
      for (const j of JOBS) { if (jobs.length >= 40) break; if (!j.filtered && words.every((w) => `${j.title} ${j.company}`.toLowerCase().includes(w))) jobs.push(j); }
      items = items.concat(jobs.map((j) => ({ kind: "Job", label: j.title, sub: `${j.company} · ${TRACKS[trackOf(j)]}${j.score != null ? ` · ${Math.round(j.score)}` : ""}`, run: () => openPanel(j.id) })));
    } else items = items.filter((x) => x.kind === "View" || x.kind === "Action");
    palItems = items.slice(0, 60); palSel = 0; renderPaletteList();
  }
  function renderPaletteList() {
    const ul = $("#palette-list"); ul.textContent = "";
    if (!palItems.length) { ul.append(el("li", { class: "muted empty-pal", text: "No matches" })); return; }
    palItems.forEach((x, i) => ul.append(el("li", { role: "option", id: `pal-${i}`, "aria-selected": String(i === palSel), onmousemove: () => { if (palSel !== i) { palSel = i; mark(); } },
      onclick: () => runPalette(i) }, el("span", { class: "pk", text: x.kind }), el("span", { class: "pl", text: x.label }), x.sub ? el("span", { class: "ps", text: x.sub }) : null)));
    $("#palette-q").setAttribute("aria-activedescendant", `pal-${palSel}`);
    function mark() { for (const [i, li] of [...ul.children].entries()) li.setAttribute("aria-selected", String(i === palSel)); $("#palette-q").setAttribute("aria-activedescendant", `pal-${palSel}`); }
  }
  function runPalette(i) { const x = palItems[i]; $("#palette").close(); if (x) setTimeout(() => x.run(), 0); }
  $("#palette-q").addEventListener("input", (e) => drawPalette(e.target.value));
  $("#palette-q").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); palSel = (palSel + (e.key === "ArrowDown" ? 1 : -1) + palItems.length) % Math.max(1, palItems.length); renderPaletteList();
      const n = document.getElementById(`pal-${palSel}`); if (n) n.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") { e.preventDefault(); runPalette(palSel); }
  });
  $("#palette").addEventListener("click", (e) => { if (e.target === $("#palette")) $("#palette").close(); });
  $("#palette-btn").addEventListener("click", openPalette);

  // ---------- keyboard ----------
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
    if (document.querySelector("dialog[open]")) return;   // dialogs handle their own keys (Esc closes them)
    if (["input", "textarea", "select"].includes(tag) && e.key !== "Escape") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (dApp && e.key === "Escape") { closeDrawer(); return; }
    if (dApp) return;
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
      case "Escape": if (pJob) closePanel(); else if (picked.size) clearPicks(); else closePop(); break;
      case "s": if (sel) shortlist(sel); break;
      case "a": if (sel && !sel.filtered) markApplied(sel); break;
      case " ": if (sel && tag !== "button" && tag !== "a") { e.preventDefault(); togglePick(sel.id); } else return; break;
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
  if (!["coop_intern", "full_time"].includes(ui.track)) ui.track = "full_time";
  readHash();
  $("#q").value = tstate().q || "";
  renderHeader(); renderBanners();
  if (!JOBS.length) {
    renderCounts();
    $("#list").append(el("div", { class: "empty" }, el("b", { text: "No jobs yet." }), el("div", { text: "Run ./scripts/run.sh, then reload this page." })));
  } else {
    renderAll();
  }
})();
