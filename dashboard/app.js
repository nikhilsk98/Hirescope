/* HireScope dashboard (vanilla JS, no build, works on file://).
   Data: window.META + window.JOBS (summaries) from data.js; per-job details load on demand from details/d_N.js.
   All job text is rendered with textContent / DOM nodes: JD content is data, never markup. */
(function () {
  "use strict";

  const JOBS = Array.isArray(window.JOBS) ? window.JOBS : [];
  const META = window.META || {};
  const BY_ID = new Map(JOBS.map((j) => [j.id, j]));
  const K = { statuses: "jobsearch.statuses.v1", overrides: "jobsearch.trackOverrides.v1", theme: "jobsearch.theme", apps: "jobsearch.applications.v1",
              ui: "jobsearch.ui.v2", exported: "jobsearch.lastExport", tip: "jobsearch.tipDismissed.v2",
              manual: "jobsearch.manualChecks.v1", resumeReviews: "jobsearch.resumeReviews.v1",
              resumeQueued: "jobsearch.resumeQueued.v1", resumeMapped: "jobsearch.resumeMapped.v1", bucket: "jobsearch.bucket.v1" };
  const STATUSES = ["", "interested", "shortlisted", "package exported", "applied", "interviewing", "offer", "rejected", "skipped"];
  const SKIP_REASONS = ["not interested in role", "location", "too senior", "too junior", "missing skills", "company",
                        "compensation", "visa/sponsorship", "start date", "duplicate", "other"];
  const TRACKS = { coop_intern: "Co-op / Intern", full_time: "Full-time", unclear: "Unclear" };

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
  const V = window.HSView;
  let statuses = store.get(K.statuses, {});
  // Resume Builder (dashboard/resumes.js from SQLite): status + versions per HireScope job id. "Mark reviewed" saves here
  // and goes out with Export statuses. Generate Resume queues a background build through the hirescope:// launcher;
  // dashboard/live.js (re-read every few seconds) says what the queue is doing, so statuses move on their own:
  // Queued → Generating → Ready / Ready (questions pending) / Failed.
  let RES = ((window.RESUMES || {}).jobs) || {};
  const resumeReviews = store.get(K.resumeReviews, {});
  const resumeQueued = store.get(K.resumeQueued, {});   // {job id: ISO time you clicked} until the queue confirms it
  const resumeOf = (j) => RES[String(j.id)] || null;
  let LIVE = window.LIVE || {};
  let LIVE_BY_JOB = new Map();     // job id → its newest queue item (an active one wins)
  const indexLive = () => {
    LIVE_BY_JOB = new Map();
    for (const it of LIVE.queue || []) {
      const cur = LIVE_BY_JOB.get(it.job_pk); const act = (x) => x && ["queued", "generating"].includes(x.status);
      if (!cur || (act(it) && !act(cur)) || (act(it) === act(cur) && String(it.added_at) > String(cur.added_at))) LIVE_BY_JOB.set(it.job_pk, it);
    }
  };
  indexLive();
  const REQUEST_TTL_MS = 10 * 60 * 1000;
  function resumeStatus(j) {
    const r = resumeOf(j); const it = LIVE_BY_JOB.get(j.id); const q = resumeQueued[j.id];
    if (it && ["queued", "generating"].includes(it.status)) return it.status;
    // clicked, not confirmed by the queue yet (the launcher answers within seconds; a stale click expires)
    if (q && Date.now() - Date.parse(q) < REQUEST_TTL_MS && (!it || String(q) > String(it.added_at || "")) &&
        (!r || String(q) > String(r.updated_at || ""))) return "queued";
    const rv = resumeReviews[j.id];
    if (!r) return it && it.status === "failed" ? "failed" : "";
    let st = r.status === "validating" ? "generating" : r.status;
    // "generating" / "queued" with nothing in the queue and older than 2 h: an abandoned session (e.g. a Terminal
    // build that never finished), not something in progress: show what the versions say
    if ((st === "generating" || st === "queued") && Date.now() - Date.parse(r.updated_at || 0) > 2 * 3600e3) st = (r.current && r.current.status) || "";
    if (st === "ready" && r.current && rv && rv.version === r.current.version) return "reviewed";
    if ((st === "ready" || st === "reviewed") && r.questions_pending) return "ready_q";
    return st;
  }
  // one badge style for queue / resume statuses everywhere (cards, Bucket, panels, Resume Builder, queue panel)
  const RESUME_CHIP = { queued: ["Resume: Queued", "info", "◷"], generating: ["Resume: Generating", "info", "⟳"],
    ready: ["Resume: Ready", "good", "✓"], ready_q: ["Resume: Ready (questions pending)", "warn", "?"],
    reviewed: ["Resume: Ready · reviewed", "good", "✓"], failed: ["Resume: Failed", "flag", "✗"] };
  const resumeBadge = (rs, title) => (RESUME_CHIP[rs] ? el("span", { class: `chip rs ${RESUME_CHIP[rs][1]}`, title: title || null },
    el("span", { "aria-hidden": "true", text: RESUME_CHIP[rs][2] + " " }), RESUME_CHIP[rs][0]) : null);
  const overrides = {};
  for (const [k, v] of Object.entries(store.get(K.overrides, {}))) overrides[k] = typeof v === "string" ? { track: v, updated_at: "1970-01-01T00:00:00Z" } : v;
  let apps = store.get(K.apps, {});
  // the Bucket (your apply-list): {job id: {in, added_at, updated_at, snap: copy of the job}}; a removal stays as a
  // dated tombstone so an older copy can't bring it back. Saved here instantly, sent to SQLite through the launcher.
  const bucket = store.get(K.bucket, {});
  const saveBucket = () => store.set(K.bucket, bucket);
  // manualChecks: {company: {checked_at (YYYY-MM-DD), history: [dates], updated_at}} — the Manual Checks view
  const manualChecks = store.get(K.manual, {});
  const saveManual = () => store.set(K.manual, manualChecks);
  const MANUAL_DAYS = Number((META.manual_checks_cfg || {}).remind_days) || 7;   // preferences manual_checks.remind_days
  const ui = Object.assign({ track: "full_time", both: false, mode: "cards", compact: false, perTrack: {}, appTab: "board", page: "jobs" }, store.get(K.ui, {}));
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

  // SQLite's copy (sync.js, written by each run and by the launcher) fills in anything this browser doesn't have;
  // newer always wins. Re-run when live.js says SQLite changed (sync_at).
  function mergeSync() {
    const S = window.SYNC; if (!S) return;
    let changed = false;
    for (const [k, v] of Object.entries(S.statuses || {})) if (!statuses[k] || String(v.updated_at) > String(statuses[k].updated_at || "")) { statuses[k] = Object.assign({}, statuses[k] || {}, v); changed = true; }
    for (const [k, v] of Object.entries(S.track_overrides || {})) if (!overrides[k] || String(v.updated_at) > String(overrides[k].updated_at || "")) { overrides[k] = v; changed = true; }
    for (const a of S.applications || []) { const m = C.mergeApp(apps[a.id], a); if (JSON.stringify(m) !== JSON.stringify(apps[a.id])) { apps[a.id] = m; changed = true; } }
    for (const [k, v] of Object.entries(S.manual_checks || {})) if (!manualChecks[k] || String(v.updated_at) > String(manualChecks[k].updated_at || "")) { manualChecks[k] = v; changed = true; }
    for (const [k, v] of Object.entries(S.bucket || {})) if (!bucket[k] || String(v.updated_at) > String(bucket[k].updated_at || "")) { bucket[k] = Object.assign({}, bucket[k] || {}, v, { snap: Object.assign({}, (bucket[k] || {}).snap || {}, v.snap || {}) }); changed = true; }
    if (changed) { store.set(K.statuses, statuses); saveOverrides(); saveApps(); saveManual(); saveBucket(); }
  }
  mergeSync();

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

  // A resume that became ready moves its job to "package exported" when you haven't gone further (once per version,
  // so changing the status back afterwards sticks).
  function mapResumeStatuses() {
    const done = store.get(K.resumeMapped, {}); let n = 0;
    for (const [pk, r] of Object.entries(RES)) {
      const j = BY_ID.get(Number(pk)); const v = r.current && r.current.version;
      if (!j || !v || !["ready", "reviewed"].includes(r.status) || done[pk] === v) continue;
      done[pk] = v;
      if (["", "interested", "shortlisted"].includes(statusOf(j))) { setStatus(j, { status: "package exported", package: r.current.pdf_name || "" }); n++; }
    }
    store.set(K.resumeMapped, done);
    return n;
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
  const TRACKED = ["applied", "interviewing", "offer", "rejected"];
  const applyQueue = () => (META.apply_today ? (ui.both ? [...(META.apply_today.coop_intern || []), ...(META.apply_today.full_time || [])] : META.apply_today[ui.track] || []) : [])
    .map((id) => BY_ID.get(id)).filter((j) => j && !DONE.includes(statusOf(j))).slice(0, (ui.both ? 2 : 1) * UI_CFG.apply_today_size);

  const VIEWS = [
    { id: "last24", label: "Last 24h", ic: "⚡", group: "Discover", jobs: () => visibleJobs().filter((j) => isFresh24(j)) },
    { id: "apply", label: "Apply Today", ic: "★", jobs: () => applyQueue() },
    { id: "all", label: "All Jobs", ic: "▤", jobs: () => visibleJobs() },
    { id: "mine", label: "My Companies", ic: "♥", jobs: () => visibleJobs().filter((j) => j.my_company) },
    { id: "external", label: "External Jobs", ic: "⇲", jobs: () => visibleJobs().filter((j) => (j.badges || []).includes("external")) },
    { id: "applications", label: "My Applications", ic: "▦", group: "Mine", special: true, count: () => appsInTrack().length },
    { id: "tracker", label: "Applied / Tracker", ic: "☑", jobs: () => JOBS.filter((j) => inTrack(j) && TRACKED.includes(statusOf(j))) },
    { id: "shortlisted", label: "Shortlisted", ic: "✓", jobs: () => JOBS.filter((j) => inTrack(j) && statusOf(j) === "shortlisted") },
    { id: "resumes", label: "Resumes", ic: "✎", jobs: () => JOBS.filter((j) => inTrack(j) && resumeStatus(j)) },
    { id: "questions", label: "Questions for you", ic: "?", special: true, count: () => openQuestions().length, alert: true },
    { id: "followups", label: "Follow-ups Due", ic: "↻", special: true, count: () => new Set([...followupsDue(), ...ghostsDue()].map((a) => a.id)).size },
    { id: "closing", label: "Closing Soon", ic: "⏳", jobs: () => visibleJobs().filter((j) => j.deadline && daysUntil(j.deadline) >= 0 && daysUntil(j.deadline) <= UI_CFG.closing_soon_days) },
    { id: "skillgaps", label: "Skill Gaps", ic: "◔", group: "Insights", special: true, count: () => ((META.skill_gaps || {})[`${ui.track}:30`] || { skills: [] }).skills.length },
    { id: "stats", label: "Stats", ic: "▥", special: true, count: () => appsInTrack().length },
    { id: "seasons", label: "Hiring Seasons", ic: "◷", special: true, count: () => seasonsInTrack().filter((x) => x.remind).length },
    { id: "missed", label: "Missed Matches", ic: "◎", jobs: () => (META.missed || []).map((id) => BY_ID.get(id)).filter((j) => j && inTrack(j)) },
    { id: "filtered", label: "Filtered Out", ic: "⊘", jobs: () => JOBS.filter((j) => j.filtered && inTrack(j)) },
    { id: "coverage", label: "Coverage", ic: "◍", group: "System", special: true, count: () => (META.coverage || []).length },
    { id: "manual", label: "Manual Checks", ic: "☐", special: true, count: () => manualRows().filter((r) => r.st.state !== "ok").length, alert: true },
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
    seen: ["Seen", "seen"], external: ["External", "info"],
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
    { id: "type", label: "Type", values: (js) => counts(js.map((j) => j.employment_type || "Not stated")), test: (j, v) => v.includes(j.employment_type || "Not stated") },
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
      if ((st.salary || st.salaryMax || st.salaryKnown) && !V.salaryMatch(j.salary, st.salary, st.salaryMax, st.salaryKnown)) return false;
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
      // red only for real failures; manual checks and partial listings are amber
      $("#run-dot").className = "dot " + (s.sources_failed ? "red" : s.sources_partial || s.sources_manual || s.sources_unavailable ? "amber" : "green");
      $("#run-text").textContent = `Last run ${fmtTime(META.last_run)} · ${s.sources_ok ?? 0}/${s.sources_checked ?? 0} OK · ` +
        `${(s.new || 0).toLocaleString()} new · ${(s.updated || 0).toLocaleString()} updated · ${(s.closed || 0).toLocaleString()} closed`;
      $("#run-dot").parentElement.title = `${s.sources_ok} of ${s.sources_checked} companies/sources checked successfully this run; ` +
        `${s.sources_failed || 0} failed, ${s.sources_unavailable ? `${s.sources_unavailable} unavailable (provider maintenance), ` : ""}${s.sources_manual || 0} manual, ${s.sources_partial || 0} partial, ${s.sources_skipped || 0} not due. ` +
        `Jobs found ${s.jobs_found}, new ${s.new}, updated ${s.updated}, closed ${s.closed}.`;
    }
    const log = $("#log-link");
    if (META.log_path) { log.href = "file://" + encodeURI(META.log_path); log.hidden = false; }
    const L = META.learned || {};
    const box = $("#learned"); box.textContent = "";
    box.append(...[el("h4", { text: "Learned adjustments" }), el("div", { text: L.status || "None yet." }),
      L.adjustments && L.adjustments.length ? el("ul", { class: "list-plain", style: "margin-top:6px" }, L.adjustments.map((a) => el("li", { text: a }))) : null,
      Object.keys(L.ignored || {}).length ? el("div", { style: "margin-top:6px", text: `Skips that teach nothing (not about the job itself): ${Object.entries(L.ignored).map(([k, n]) => `${k} ×${n}`).join(", ")}` }) : null,
      ...(L.suggestions || []).map((x) => el("div", { class: "banner warn", style: "margin-top:6px", text: x }))].filter(Boolean));
  }
  // day tabs: the most recent days first-seen as tabs, older days in a dropdown
  const dayLabel = (d) => (d === today() ? "Today" : (() => { try { return new Date(d + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }); } catch (e) { return d; } })());
  function renderDayTabs() {
    const box = $("#day-tabs"); box.textContent = "";
    const { recent, older } = V.dayTabs(META.days || [], 4);
    const tab = (d, label) => el("button", { class: "day-tab", type: "button", "aria-pressed": String((ui.day || null) === d), title: d ? `Jobs first seen on ${d}` : "Every day",
      onclick: () => { ui.day = d; saveUI(); renderAll(); } }, label);
    box.append(el("span", { class: "muted day-lbl", text: "First seen" }), tab(null, "All days"), ...recent.map((d) => tab(d, dayLabel(d))));
    if (older.length) {
      const sel = el("select", { class: "day-older" + (older.includes(ui.day) ? " active" : ""), "aria-label": "Older days" },
        el("option", { value: "", text: older.includes(ui.day) ? "Older…" : `Older (${older.length})…` }), older.map((d) => el("option", { value: d, text: d, selected: ui.day === d })));
      sel.addEventListener("change", () => { ui.day = sel.value || null; saveUI(); renderAll(); });
      box.append(sel);
    }
  }
  // unsynced = changed in this browser after the last export and after SQLite's copy (sync.js) of the same item
  const syncAt = () => {
    const S = window.SYNC || {}; const m = { s: {}, o: {}, a: {}, m: {} };
    for (const [k, v] of Object.entries(S.statuses || {})) m.s[k] = v.updated_at || "";
    for (const [k, v] of Object.entries(S.track_overrides || {})) m.o[k] = v.updated_at || "";
    for (const a of S.applications || []) m.a[a.id] = a.updated_at || "";
    for (const [k, v] of Object.entries(S.manual_checks || {})) m.m[k] = v.updated_at || "";
    m.b = {}; for (const [k, v] of Object.entries(S.bucket || {})) m.b[k] = v.updated_at || "";
    return m;
  };
  let SYNC_AT = syncAt();
  function unsynced() {
    const last = store.get(K.exported, null) || "";
    const newer = (map) => ([k, x]) => { const t = String(x.updated_at || ""); return t > last && t > (map[k] || ""); };
    return Object.entries(statuses).some(newer(SYNC_AT.s)) || Object.entries(overrides).some(newer(SYNC_AT.o)) || Object.entries(apps).some(newer(SYNC_AT.a)) ||
      Object.entries(manualChecks).some(newer(SYNC_AT.m)) || Object.entries(bucket).some(newer(SYNC_AT.b));
  }
  function renderBanners() {
    const box = $("#banners"); box.textContent = "";
    const s = META.summary || {};
    const onJobs = !PAGES[ui.page];       // run / coverage notices belong to the job tabs, not the Bucket / Resume Builder
    const fails = onJobs ? s.failures || [] : [], manual = onJobs ? s.manual || [] : [], partial = onJobs ? s.partial || [] : [];
    const manDue = manualRows().filter((x) => x.st.state !== "ok").length;
    const dueText = manDue ? ` · ${manDue} not checked in ${MANUAL_DAYS} days (or never)` : "";
    const openManual = () => (manDue && (tstate().view || "") !== "manual" ? el("button", { class: "btn small x", type: "button", onclick: () => setView("manual") }, "Open Manual Checks") : null);
    const manualList = (title) => el("details", null, el("summary", { text: title }),
      el("ul", null, manual.map((m) => el("li", null, `${m.company}: `, m.error ? el("span", { text: `${m.error} ` }) : "", safeUrl(m.url) ? el("a", { href: m.url, target: "_blank", rel: "noopener noreferrer", text: "check manually" }) : ""))));
    if (fails.length) {
      const d = el("details", null, el("summary", { text: `Last run had problems: ${fails.length} failed, ${manual.length} need a manual check, ${partial.length} partial listings` }),
        el("ul", null, fails.map((f) => el("li", { text: `${f.company} (${f.source}) failed — ${f.error}` }))));
      box.append(el("div", { class: "banner err" }, d));
      if (manual.length) box.append(el("div", { class: "banner info" }, manualList(`${manual.length} companies to check by hand (job systems HireScope can't read)${dueText}`), openManual()));
    } else if (manual.length) {
      box.append(el("div", { class: "banner info" }, manualList(`No failures. ${manual.length} companies to check by hand (job systems HireScope can't read)${dueText}${partial.length ? `; ${partial.length} partial listings` : ""}`), openManual()));
    } else if (partial.length) {
      box.append(el("div", { class: "banner warn" }, `${partial.length} sources returned partial listings (page caps); closures for those are verified per job. See Coverage.`));
    }
    if (onJobs && manDue && !manual.length && (tstate().view || "") !== "manual") {
      box.append(el("div", { class: "banner warn" }, el("span", { text: `${manDue} manual compan${manDue === 1 ? "y" : "ies"} not checked in ${MANUAL_DAYS} days (or never).` }), openManual()));
    }
    if (onJobs && META.partial_run) box.append(el("div", { class: "banner-note muted", text: (META.summary || {}).scope === "quick"
      ? "Last run was a quick check (My Companies and tier-1 sources); everything else was refreshed by the last full run."
      : "The last run checked only one company/source; other companies were not re-checked." }));
    const ha = META.health_alerts || [];
    if (onJobs && ha.length) box.append(el("div", { class: "banner err" }, el("details", null, el("summary", { text: `Source health: ${ha.length} source${ha.length > 1 ? "s" : ""} returned far fewer jobs than usual (likely broken, not "no new jobs")` }),
      el("ul", null, ha.map((a) => el("li", { text: a.message }))))));
    const xl = LIVE.excel || {};
    if (xl.status === "pending" || xl.status === "error") box.append(el("div", { class: `banner ${xl.status === "error" ? "err" : "warn"} excel-banner` },
      el("span", { text: `Applications Excel: ${xl.message || "not updated"}` })));
    if (!LAUNCHER.installed) {
      const cmd = `cd ${JSON.stringify(META.repo_root || ".")} && ./scripts/install_launcher.sh`;
      box.append(el("div", { class: "banner info setup-banner" }, el("div", null, el("b", { text: "One-time setup for one-click resumes. " }),
        "Run ", el("code", { class: "mono", text: "./scripts/install_launcher.sh" }), " once in Terminal (HireScope folder). It installs the HireScope Launcher and a background worker: ",
        "Generate Resume, the Bucket and the Resume Builder tab then build and save resumes on their own. Until then Generate Resume copies a Terminal command."),
        el("button", { class: "btn small x", type: "button", onclick: async () => toast((await copyText(cmd)) ? "Setup command copied — paste it in Terminal, then reload this page" : cmd) }, "Copy setup command")));
    }
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
    renderTopTabs();
    for (const b of document.querySelectorAll(".track-tab[data-track]")) {
      const t = b.dataset.track;
      // true totals from the run (the dashboard holds the top N per track)
      const bt = ((META.summary || {}).by_track || {});
      const total = bt[t] ? bt[t].visible + ((bt.unclear || {}).visible || 0) : null;
      const shown = JOBS.filter((j) => !j.filtered && (trackOf(j) === t || trackOf(j) === "unclear")).length;
      b.querySelector(".count").textContent = (total != null ? total : shown).toLocaleString();
      b.title = total != null && total > shown ? `${total.toLocaleString()} matching jobs; the ${shown.toLocaleString()} best-scored are on this dashboard` : "";
    }
    $("#both").checked = ui.both;
    const nav = $("#views"); nav.textContent = "";
    const cur = ui.page === "jobs" ? tstate().view || defaultView() : null;
    let group = null;
    for (const v of VIEWS) {
      if (v.group && v.group !== group) { group = v.group; nav.append(el("div", { class: "group", text: group })); }
      const n = v.special ? v.count() : v.jobs().length;
      nav.append(el("button", { class: "view-btn", type: "button", "aria-current": String(v.id === cur), onclick: () => setView(v.id),
        title: v.alert && n ? `${n} to check (not checked in ${MANUAL_DAYS} days or never)` : null },
        el("span", { class: "ic", "aria-hidden": "true", text: v.ic }), v.label,
        el("span", { class: "n" + (v.alert && n ? " due" : ""), text: n.toLocaleString(), "aria-label": v.alert && n ? `${n} due` : null })));
    }
  }

  // ---------- filter chips ----------
  let openPop = null;
  const closePop = () => { if (openPop) { openPop.remove(); openPop = null; const b = $("#saved-btn"); if (b) b.setAttribute("aria-expanded", "false"); } };
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
    if (FILTERS.some((f) => (st.filters[f.id] || []).length) || st.q || ui.day || st.salary || st.salaryMax || st.salaryKnown) {
      box.append(el("button", { class: "btn small ghost", type: "button", onclick: () => { st.filters = {}; st.q = ""; st.salary = 0; st.salaryMax = 0; st.salaryKnown = false; $("#q").value = ""; ui.day = null; saveUI(); renderAll(); } }, "Clear all"));
    }
  }
  const SAL_MAX = 400000, SAL_STEP = 10000;
  function salaryChip(base) {
    const st = tstate();
    const wrap = el("div", { class: "filter-wrap" });
    const known = base.filter((j) => j.salary && (!j.salary.currency || j.salary.currency === "USD"));
    const on = !!(st.salary || st.salaryMax || st.salaryKnown);
    const btn = el("button", { class: "filter-chip" + (on ? " active" : ""), type: "button", "aria-haspopup": "true",
      onclick: (e) => {
        e.stopPropagation(); const was = openPop && openPop.dataset.f === "salary"; closePop(); if (was) return;
        const pop = el("div", { class: "popover salary-pop", role: "dialog", "aria-label": "Salary range" }); pop.dataset.f = "salary";
        const lo = el("input", { type: "range", min: 0, max: SAL_MAX, step: SAL_STEP, value: st.salary || 0, "aria-label": "Minimum salary" });
        const hi = el("input", { type: "range", min: 0, max: SAL_MAX, step: SAL_STEP, value: st.salaryMax || SAL_MAX, "aria-label": "Maximum salary" });
        const out = el("output");
        const chk = el("input", { type: "checkbox", checked: !!st.salaryKnown });
        const vals = () => { let a = Number(lo.value), b = Number(hi.value); if (a > b) [a, b] = [b, a]; return [a, b >= SAL_MAX ? 0 : b]; };
        const show = () => { const [a, b] = vals(); out.textContent = V.salaryLabel(a, b) || "any"; };
        const apply = () => { const [a, b] = vals(); st.salary = a; st.salaryMax = b; st.salaryKnown = chk.checked; saveUI(); renderCounts(); renderList(true);
          btn.classList.toggle("active", !!(a || b || chk.checked)); btn.textContent = salaryText(st);
          const w = [...document.querySelectorAll("#filters .filter-wrap")][FILTERS.length]; if (w && !w.contains(pop)) { w.append(pop); openPop = pop; } };
        for (const r of [lo, hi]) { r.addEventListener("input", show); r.addEventListener("change", apply); }
        chk.addEventListener("change", apply); show();
        pop.append(el("div", { class: "muted", style: "font-size:12px;margin-bottom:6px", text: `Posted range (USD) overlapping your range. ${known.length.toLocaleString()} of ${base.length.toLocaleString()} jobs state a salary.` }),
          el("div", { class: "dual-range" }, lo, hi), el("div", { style: "font-size:13px;margin:4px 0 8px" }, "Salary ", out),
          el("label", null, chk, "Hide jobs that don't state a salary"));
        wrap.append(pop); openPop = pop;
      } }, salaryText(st));
    wrap.append(btn);
    return wrap;
  }
  const salaryText = (st) => { const l = V.salaryLabel(st.salary, st.salaryMax); return l ? `Salary ${l}` : st.salaryKnown ? "Salary: stated only" : "Salary ▾"; };
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
  const avatarColor = (s) => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0; return `hsl(${h % 360} 42% 32%)`; };
  function ring(score, label, size = 52) {
    const r = size / 2 - 5, c = 2 * Math.PI * r, v = Math.max(0, Math.min(100, score || 0));
    return el("div", { class: "ring", style: `width:${size}px;height:${size}px`, title: `${Math.round(v)} / 100 — ${label || ""}. ${META.disclaimer || ""}` },
      svg("svg", { width: size, height: size, viewBox: `0 0 ${size} ${size}`, "aria-hidden": "true" },
        svg("circle", { cx: size / 2, cy: size / 2, r, fill: "none", "stroke-width": 5, class: "track" }),
        svg("circle", { cx: size / 2, cy: size / 2, r, fill: "none", "stroke-width": 5, class: label || "Weak", "stroke-linecap": "round", "stroke-dasharray": `${(c * v) / 100} ${c}` })),
      el("span", { class: "v", text: score == null ? "—" : Math.round(score) }));
  }
  // inline SVG bars (all charts are SVG; no chart library)
  function svgBar(frac, cls, w = 100, h = 8) {
    const v = Math.max(0, Math.min(1, frac || 0));
    return svg("svg", { class: "sbar", viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none", "aria-hidden": "true" },
      svg("rect", { x: 0, y: 0, width: w, height: h, rx: h / 2, class: "sbar-track" }),
      v ? svg("rect", { x: 0, y: 0, width: Math.max(h, w * v), height: h, rx: h / 2, class: `sbar-fill ${cls || ""}` }) : "");
  }
  function segBar(parts, w = 72, h = 6) {
    const tot = parts.reduce((a, [n]) => a + n, 0) || 1; let x = 0;
    const g = svg("svg", { class: "segbar", width: w, height: h, viewBox: `0 0 ${w} ${h}`, "aria-hidden": "true" },
      svg("rect", { x: 0, y: 0, width: w, height: h, rx: 3, class: "sbar-track" }));
    for (const [n, cls] of parts) { if (!n) continue; const pw = (w * n) / tot; g.append(svg("rect", { x, y: 0, width: pw, height: h, class: cls })); x += pw; }
    return g;
  }
  function atsMeter(j) {
    const a = j.ats; if (!a) return el("span", { class: "ats muted", text: "ATS —" });
    const tip = a.top && a.top.length ? `Top missing required keywords: ${a.top.join(", ")}` : "No missing required keywords";
    return el("span", { class: "ats", title: `${tip}\n${META.ats_note || ""}` },
      segBar([[a.m, "m"], [a.s, "s"], [a.x, "x"]]),
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
    const rs = resumeStatus(j); if (rs && RESUME_CHIP[rs]) out.push(resumeBadge(rs, resumeTitle(j)));
    return out;
  }
  const postedText = (j) => (j.posted ? `Posted ${fmtDate(j.posted)}` : j.discovered ? `Discovered ${fmtTime(j.first_seen)} (posting date unknown)` : "Posting date unknown");
  // nav: a card inside the detail panel (Similar tab). Clicking it opens that job like ⌘K does, remembering the job
  // you came from for "← Back"; only the checkbox toggles bulk selection, and nothing selects, skips or hides a job.
  function card(j, nav) {
    const open = () => (nav ? openJob(j.id, { from: true }) : openPanel(j.id));
    const c = el("article", { class: `card ${trackOf(j)}`, tabindex: "0", "data-id": j.id, "aria-label": `${j.title} at ${j.company}, score ${j.score == null ? "none" : Math.round(j.score)}`,
      onclick: (e) => { if (e.target.closest("input, button, a")) return; open(); },
      onkeydown: (e) => { if (e.key === "Enter" && e.target === c) { e.stopPropagation(); open(); } } },
      el("div", { class: "avatar", style: `background:${avatarColor(j.company)}`, "aria-hidden": "true", text: (j.company.match(/[A-Za-z0-9]/) || ["?"])[0].toUpperCase() }),
      el("div", { class: "card-body" },
        el("div", { class: "card-title" }, bucketBox(j), el("span", { text: j.title })),
        el("div", { class: "card-sub", text: `${j.company}${j.tier ? ` · tier ${j.tier}` : ""} · ${j.location} · ${j.work_mode}` }),
        el("div", { class: "card-badges" }, badgesOf(j)),
        el("div", { class: "card-meta" }, el("span", { text: postedText(j) }),
          j.level !== "unknown" ? el("span", { text: j.level }) : null,
          (j.terms || []).length ? el("span", { text: j.terms.join(", ") }) : null,
          j.deadline ? el("span", { text: `Apply by ${fmtDate(j.deadline)}` }) : null,
          j.salary ? el("span", { text: `${j.salary.currency || ""} ${j.salary.min.toLocaleString()}–${j.salary.max.toLocaleString()}` }) : null,
          j.filtered ? el("span", { class: "err", text: (j.reasons || []).join("; ") }) : atsMeter(j),
          !j.filtered && (j.similar || []).length ? el("button", { class: "link-btn", type: "button", title: "Open the nearest similar open roles",
            onclick: (e) => { e.stopPropagation(); openJob(j.id, { tab: "Similar", from: nav }); }, onkeydown: (e) => e.stopPropagation() }, `More like this (${j.similar.length})`) : null)),
      el("div", { class: "card-side" }, j.filtered ? el("span", { class: "chip", text: "Filtered" }) : ring(j.score, j.label), moveButtons(j)));
    if (selectedId === j.id) c.classList.add("selected");
    if (inBucket(j.id)) c.classList.add("in-bucket");
    return c;
  }
  // Unclear jobs: one-click Move to Co-op / Full-time right on the card (saved as an override, respected by later runs)
  function moveButtons(j) {
    if (trackOf(j) !== "unclear") return null;
    const b = (t, label) => el("button", { class: "btn small", type: "button", title: `Move to ${TRACKS[t]} (saved)`,
      onclick: (e) => { e.stopPropagation(); setTrack(j, t); }, onkeydown: (e) => e.stopPropagation() }, label);
    return el("div", { class: "move-btns", role: "group", "aria-label": "Move this Unclear job" }, b("coop_intern", "→ Co-op"), b("full_time", "→ Full-time"));
  }
  // the checkbox before every job title: add a copy of the job to the Bucket (untick: remove). Works the same on cards,
  // table rows, the Similar list and the detail panel (⌘K opens jobs there); bulk actions live inside the Bucket.
  const inBucket = (id) => !!(bucket[id] && bucket[id].in);
  const bucketItems = () => Object.entries(bucket).filter(([, e]) => e && e.in).map(([id, e]) => ({ id: Number(id), e, j: BY_ID.get(Number(id)) || null }));
  function jobSnap(j) {
    return { id: j.id, company: j.company, title: j.title, source: j.source, job_id: j.job_id, key: j.company && j.source ? keyOf(j) : undefined,
      track: j.track ? trackOf(j) : undefined, score: j.score == null ? null : Math.round(j.score), label: j.label, location: j.location,
      deadline: j.deadline, apply_url: safeUrl(j.apply_url) || undefined, level: j.level, job_status: j.job_status || "open" };
  }
  function bucketBox(j) {
    const on = inBucket(j.id);
    const cb = el("input", { type: "checkbox", class: "bucket-cb", "data-bucket": j.id, checked: on, title: on ? "In your Bucket (untick to remove)" : "Add to Bucket (your apply-list)",
      "aria-label": `${on ? "Remove" : "Add"} ${j.title} at ${j.company} ${on ? "from" : "to"} the Bucket`, onclick: (e) => e.stopPropagation(), onkeydown: (e) => { if (e.key !== " ") e.stopPropagation(); } });
    cb.addEventListener("change", () => setBucket(j, cb.checked));
    return cb;
  }
  async function setBucket(j, on, opts = {}) {
    const id = j.id; const before = bucket[id] ? JSON.parse(JSON.stringify(bucket[id])) : null;
    if (!!(before && before.in) === on) return;
    const at = new Date().toISOString();
    bucket[id] = { in: on, added_at: on ? at : (before || {}).added_at || null, updated_at: at,
      snap: Object.assign({}, (before || {}).snap || {}, BY_ID.has(id) ? jobSnap(BY_ID.get(id)) : jobSnap(j)) };
    saveBucket(); refreshBucket(id);
    if (!opts.local) launch(`hirescope://bucket?job=${id}&op=${on ? "add" : "remove"}`, on ? "Add to Bucket" : "Remove from Bucket");
    if (!opts.quiet) toast(on ? `Added to Bucket: ${j.title}` : `Removed from Bucket: ${j.title}`, () => {
      if (before) bucket[id] = before; else delete bucket[id];
      saveBucket(); refreshBucket(id);
      launch(`hirescope://bucket?job=${id}&op=${on ? "remove" : "add"}`);
    });
  }
  function refreshBucket(id) {          // checkboxes for this job everywhere, the tab count, and the Bucket page
    for (const cb of document.querySelectorAll(`input.bucket-cb[data-bucket="${id}"]`)) {
      cb.checked = inBucket(id); cb.title = cb.checked ? "In your Bucket (untick to remove)" : "Add to Bucket (your apply-list)";
      const card2 = cb.closest(".card, tr"); if (card2) card2.classList.toggle("in-bucket", cb.checked);
    }
    renderTopTabs();
    if (ui.page === "bucket") renderList(true);
  }
  const csvCell = (v) => { let x = v == null ? "" : String(v); if (/^[=+\-@]/.test(x)) x = "'" + x; return /[",\r\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x; };
  function exportJobsCsv(jobs) {
    const cols = ["company", "title", "track", "label", "score", "ats", "level", "location", "work_mode", "posted", "deadline", "status", "apply_url"];
    const rows = [cols.join(",")].concat(jobs.map((j) => [j.company, j.title, TRACKS[trackOf(j)], j.label || "", j.score == null ? "" : Math.round(j.score),
      j.ats ? Math.round(j.ats.c) : "", j.level, j.location, j.work_mode, j.posted || "", j.deadline || "", statusOf(j), j.apply_url || ""].map(csvCell).join(",")));
    download(`hirescope-jobs-${today()}.csv`, rows.join("\r\n") + "\r\n", "text/csv;charset=utf-8");
    toast(`Exported ${jobs.length} jobs`);
  }
  const COLS = [   // [header, cell, sort key, class, width]
    ["", (j) => bucketBox(j), null, "pickcol", "36px"],
    ["Score", (j) => (j.score == null ? "—" : Math.round(j.score)), "overall", "num", "64px"],
    ["Role", (j) => el("div", null, el("div", { class: "title", text: j.title }), el("div", { class: "muted", text: j.company })), "company", "role", null],
    ["Track", (j) => el("span", { class: `chip ${trackOf(j)}`, text: TRACKS[trackOf(j)] }), null, "", "112px"],
    ["Label", (j) => (j.label ? el("span", { class: `chip ${j.label}`, text: j.label }) : "—"), null, "", "84px"],
    ["Level", (j) => j.level, null, "", "84px"],
    ["Location", (j) => el("span", { title: j.location, text: j.location }), null, "loc", "180px"],
    ["ATS", (j) => atsMeter(j), "ats", "nowrap", "200px"],
    ["Match", (j) => (j.qual == null ? "—" : Math.round(j.qual)), "match", "num", "64px"],
    ["Posted", (j) => (j.posted ? j.posted : j.discovered ? "found " + j.first_seen.slice(0, 10) : "unknown"), "newest", "nowrap", "104px"],
    ["Deadline", (j) => j.deadline || "", "deadline", "nowrap", "96px"],
    ["Status", (j) => statusOf(j) || "", null, "", "104px"],
  ];
  const row = (j) => el("tr", { class: (selectedId === j.id ? "selected" : "") + (inBucket(j.id) ? " in-bucket" : ""), "data-id": j.id, onclick: () => openPanel(j.id) },
    COLS.map(([, f, , cls]) => el("td", { class: cls || "" }, f(j))));
  function table(list) {
    const st = tstate();
    const head = el("tr", null, COLS.map(([h, , sk]) => el("th", { scope: "col", "aria-sort": sk && st.sort === sk ? "descending" : null, title: sk ? "Sort" : null, "aria-label": h || "In Bucket",
      onclick: () => { if (sk) { st.sort = sk; saveUI(); renderList(); } } }, h, sk && st.sort === sk ? " ↓" : "")));
    const padTop = el("tr", { class: "vpad", "aria-hidden": "true" }, el("td", { colspan: COLS.length }));
    const padBot = el("tr", { class: "vpad", "aria-hidden": "true" }, el("td", { colspan: COLS.length }));
    const body = el("tbody", null, padTop, padBot);
    const tbl = el("table", { class: "grid vgrid" }, el("colgroup", null, COLS.map((c) => el("col", { style: c[4] ? `width:${c[4]}` : null }))), el("thead", null, head), body);
    const root = el("div", { class: "table-wrap" }, tbl);
    return { root, mount: () => virtualize(root, padTop, padBot, list, row, 52, 0,
      (a, b) => { padTop.firstChild.style.height = `${a}px`; padBot.firstChild.style.height = `${b}px`; }) };
  }

  // ---------- list virtualization: only the rows in (and near) the viewport exist in the DOM ----------
  // Heights are estimated, then measured as rows render (view.js Heights); spacers keep the scroll height exact.
  function virtualize(root, padTop, padBot, items, render, estimate, gap, setPads) {
    const H = new V.Heights(items.length, estimate + gap);
    let s0 = -1, e0 = -1, raf = 0, dead = false;
    const listTop = () => root.getBoundingClientRect().top + window.scrollY;
    const draw = (force) => {
      raf = 0; if (dead) return;
      const [s, e] = V.visibleRange(H, window.scrollY - listTop(), window.innerHeight, 8);
      if (!force && s === s0 && e === e0) return;
      s0 = s; e0 = e;
      while (padTop.nextSibling && padTop.nextSibling !== padBot) padTop.nextSibling.remove();
      const frag = document.createDocumentFragment();
      for (let i = s; i < e; i++) { const n = render(items[i]); n.dataset.vi = String(i); frag.append(n); }
      padBot.before(frag);
      for (let n = padTop.nextSibling; n && n !== padBot; n = n.nextSibling) H.set(Number(n.dataset.vi), n.getBoundingClientRect().height + gap);
      setPads(H.offsetOf(s), H.total() - H.offsetOf(e));
    };
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(() => draw(false)); };
    window.addEventListener("scroll", onScroll, { passive: true }); window.addEventListener("resize", onScroll);
    draw(true);
    return {
      destroy() { dead = true; window.removeEventListener("scroll", onScroll); window.removeEventListener("resize", onScroll); if (raf) cancelAnimationFrame(raf); },
      ensureVisible(i) {
        if (i < 0 || i >= items.length) return;
        const y = listTop() + H.offsetOf(i), h = H.h[i], cur = window.scrollY, vh = window.innerHeight;
        if (y < cur + 120 || y + h > cur + vh - 16) window.scrollTo({ top: Math.max(0, y - vh / 3) });
        draw(true);
      },
      redraw() { draw(true); },
    };
  }

  let current = []; let selectedId = null; let vlist = null;
  function setView(id) { tstate().view = id; ui.page = "jobs"; saveUI(); closePop(); renderBanners(); renderAll(); }
  // the four top tabs are one navigation: two track workspaces (with the sidebar views) and two pages
  function renderTopTabs() {
    for (const b of document.querySelectorAll(".track-tab")) {
      const sel = b.dataset.page ? ui.page === b.dataset.page : ui.page === "jobs" && b.dataset.track === ui.track;
      b.setAttribute("aria-selected", String(sel)); b.tabIndex = sel ? 0 : -1;
    }
    const n = bucketItems().length; const bc = $("#tab-bucket .count"); if (bc) bc.textContent = n.toLocaleString();
    const xc = $("#tab-builder .count"); if (xc) xc.textContent = (externalJobs().length + extPending().length).toLocaleString();
    document.body.dataset.page = ui.page;
  }
  function switchPage(page) {
    ui.page = page; saveUI(); closePop(); if (pJob) closePanel();
    renderBanners(); renderAll();
    for (const n of [$("#main")]) { n.classList.remove("swap-in"); void n.offsetWidth; n.classList.add("swap-in"); }
  }
  const modeOf = (vid) => (tstate().modes || {})[vid] || ui.mode || "cards";
  const SPECIAL = { coverage: () => renderCoverage, skillgaps: () => renderSkillGaps, applications: () => renderApplications,
    followups: () => renderFollowups, stats: () => renderStatsView, runlog: () => renderRunLog, seasons: () => renderSeasons,
    manual: () => renderManualChecks, questions: () => renderQuestions };
  const PAGES = { bucket: { title: "Bucket", render: () => renderBucket }, builder: { title: "Resume Builder", render: () => renderBuilder } };
  function renderList(keepScroll) {
    const st = tstate();
    if (PAGES[ui.page]) {
      const box = $("#list"); box.textContent = ""; box.removeAttribute("aria-busy");
      if (vlist) { vlist.destroy(); vlist = null; }
      $("#view-title").textContent = PAGES[ui.page].title; $("#count").textContent = "";
      for (const n of ["#sort", "#mode-toggle", "#density", "#saved-wrap"]) $(n).hidden = true;
      $("#filters").hidden = true; renderPinned(null); writeHash();
      PAGES[ui.page].render()(box); if (!keepScroll) window.scrollTo({ top: 0 });
      return;
    }
    const v = viewById(st.view || defaultView());
    $("#view-title").textContent = v.label;
    $("#sort").value = st.sort;
    $("#list").classList.toggle("compact", ui.compact);
    $("#mode-toggle").textContent = modeOf(v.id) === "cards" ? "Table" : "Cards";
    $("#density").setAttribute("aria-pressed", String(ui.compact));
    const box = $("#list"); box.textContent = ""; box.removeAttribute("aria-busy");
    if (vlist) { vlist.destroy(); vlist = null; }
    writeHash();
    const special = SPECIAL[v.id];
    for (const n of ["#sort", "#mode-toggle", "#density", "#saved-wrap"]) $(n).hidden = !!special;
    renderPinned(special ? null : v.id);
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
    if (v.id === "tracker") box.append(el("div", { class: "banner info", style: "margin:0 0 8px" },
      el("span", { text: "Jobs you applied to from HireScope (status applied, interviewing, offer or rejected). Stages, follow-ups, notes and applications you added yourself live in My Applications." }),
      el("button", { class: "btn small x", type: "button", onclick: () => setView("applications") }, "Open My Applications")));
    if (!current.length) {
      const last = META.last_run ? fmtTime(META.last_run) : "never";
      const msg = v.id === "last24" ? `No new jobs in the last 24 hours — last run ${last}.` : v.id === "apply" ? "Nothing queued — you're caught up." :
        v.id === "shortlisted" ? "Nothing shortlisted yet — press s on any job." : v.id === "tracker" ? "Nothing applied yet — press a on any job (or add one in My Applications)." : "No jobs match these filters.";
      box.append(el("div", { class: "empty" }, el("b", { text: msg }), el("div", { style: "margin-top:8px" },
        el("button", { class: "btn small", type: "button", onclick: () => { st.filters = {}; st.q = ""; $("#q").value = ""; ui.day = null; saveUI(); setView("all"); } }, "Show all jobs"))));
      return;
    }
    if (!keepScroll) window.scrollTo({ top: 0 });
    if (modeOf(v.id) === "table") {
      const t = table(current); box.append(t.root); vlist = t.mount();
      return;
    }
    const padTop = el("div", { class: "vpad", "aria-hidden": "true" }), padBot = el("div", { class: "vpad", "aria-hidden": "true" });
    const root = el("div", { class: "vlist" }, padTop, padBot);
    box.append(root);
    vlist = virtualize(root, padTop, padBot, current, card, ui.compact ? 84 : 116, 8, (a, b) => { padTop.style.height = `${a}px`; padBot.style.height = `${b}px`; });
  }

  // Apply Today, pinned above the main list on the discovery views (collapsible)
  function renderPinned(viewId) {
    const box = $("#pinned"); box.textContent = "";
    if (!["last24", "all", "mine"].includes(viewId)) return;
    const q = applyQueue(); if (!q.length) return;
    const head = el("div", { class: "pin-head" },
      el("button", { class: "btn small ghost", type: "button", "aria-expanded": String(!ui.pinCollapsed), "aria-controls": "pin-list",
        onclick: () => { ui.pinCollapsed = !ui.pinCollapsed; saveUI(); renderPinned(viewId); } }, ui.pinCollapsed ? "▸" : "▾"),
      el("b", { text: `★ Apply today (${q.length})` }), el("span", { class: "muted", text: "Strong / Stretch, freshest and deadline-aware" }),
      el("span", { class: "spacer" }), el("button", { class: "btn small", type: "button", onclick: () => setView("apply") }, "Open queue"));
    const list = ui.pinCollapsed ? null : el("ol", { class: "pin-list", id: "pin-list" }, q.map((j) => el("li", null,
      el("button", { class: "pin-item", type: "button", onclick: () => openPanel(j.id), "aria-label": `${j.title} at ${j.company}, score ${Math.round(j.score || 0)}` },
        el("span", { class: `chip ${j.label}`, text: Math.round(j.score || 0) }),
        el("span", { class: "pin-title", text: j.title }), el("span", { class: "muted pin-co", text: j.company }),
        isFresh24(j) ? el("span", { class: "chip good", text: "Fresh" }) : null))));
    box.append(el("section", { class: "pinned", "aria-label": "Apply today" }, head, list));
  }

  // ---------- skill gaps / coverage ----------
  function renderSkillGaps(box) {
    ui.gapDays = ui.gapDays || 30;
    const g = (META.skill_gaps || {})[`${ui.track}:${ui.gapDays}`] || { jobs: 0, skills: [] };
    const seg = el("div", { class: "seg", role: "group", "aria-label": "Time window" },
      [7, 30].map((d) => el("button", { type: "button", "aria-pressed": String(ui.gapDays === d), onclick: () => { ui.gapDays = d; saveUI(); renderList(); } }, `${d} days`)));
    box.append(el("div", { class: "toolbar" }, seg, el("span", { class: "muted", text: `Across ${g.jobs.toLocaleString()} Strong / Stretch ${TRACKS[ui.track]} jobs: skills those JDs ask for that your resume doesn't show. Gaps only — never add a skill you don't have.` })));
    const learn = (window.RESUMES || {}).learn_it || [];
    if (learn.length) box.append(el("div", { class: "side-card", style: "padding:12px 16px;margin-bottom:12px" },
      el("h4", { text: `Learn-it list (resume builds): ${learn.length} keyword${learn.length === 1 ? "" : "s"} missing in several jobs` }),
      el("p", { class: "muted", style: "margin:0 0 6px;font-size:12px", text: "JD keywords your confirmed evidence doesn't cover, across the jobs you prepared resumes for (rule 61). Learn one, or confirm you've used it in the evidence interview — never add it unconfirmed." }),
      el("ul", { class: "list-plain" }, learn.map((r) => el("li", null, el("b", { text: r.keyword }), el("span", { class: "muted", text: ` · ${r.jobs} jobs · last ${r.last}${r.declined ? " · you said you haven't used it" : ""}` }))))));
    if (!g.skills.length) { box.append(el("div", { class: "empty", text: "No skill gaps in this window." })); return; }
    const max = g.skills[0].jobs;
    const wrap = el("div", { class: "side-card", style: "padding:12px 16px" });
    for (const s of g.skills) {
      wrap.append(el("div", { class: "gap-row" }, el("b", { text: s.skill }),
        svgBar(s.jobs / max, "miss"),
        el("span", { class: "muted", text: `${s.jobs} jobs` }),
        el("div", { class: "ex" }, "e.g. ", s.examples.map((x, i) => [i ? " · " : "", el("a", { href: "#", onclick: (e) => { e.preventDefault(); openPanel(x.id); }, text: `${x.company} — ${x.title}` })]))));
    }
    box.append(wrap);
  }
  function renderCoverage(box) {
    const rows = META.coverage || [];
    const c = { ok: 0, failed: 0, unavailable: 0, manual: 0, skipped: 0 }; for (const r of rows) c[r.status] = (c[r.status] || 0) + 1;
    const s = META.summary || {};
    box.append(el("p", { style: "margin-top:0" }, el("b", { text: `${s.sources_ok ?? c.ok} of ${s.sources_checked ?? rows.length} companies/sources checked successfully in the last run.` }),
      ` ${c.failed} failed · ${c.unavailable ? `${c.unavailable} unavailable (the provider showed its maintenance page; listings kept, retried next run) · ` : ""}${c.manual} manual check · ${c.skipped} not due (checked on their tier schedule).`));
    const order = { failed: 0, unavailable: 1, manual: 2, ok: 3, skipped: 4 };
    const label = { ok: ["OK", "good"], failed: ["Failed", "flag"], unavailable: ["Provider down", "warn"], manual: ["Manual check", "warn"], skipped: ["Not due", ""] };
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

  // ---------- Manual Checks: companies HireScope can't read; you check their careers page and mark the date ----------
  function manualRows() {
    return (META.coverage || []).filter((r) => r.status === "manual").map((r) => {
      const e = manualChecks[r.company] || null;
      return { r, e, st: V.manualState(e && e.checked_at, today(), MANUAL_DAYS) };
    }).sort((a, b) => ({ never: 0, due: 1, ok: 2 }[a.st.state] - { never: 0, due: 1, ok: 2 }[b.st.state]) ||
      (b.st.days ?? 0) - (a.st.days ?? 0) || a.r.company.localeCompare(b.r.company));
  }
  function markManualChecked(company, undoable = true) {
    const before = manualChecks[company] ? JSON.parse(JSON.stringify(manualChecks[company])) : null;
    manualChecks[company] = V.markChecked(manualChecks[company], today(), new Date().toISOString()); saveManual();
    renderBanners(); renderCounts(); renderList(true);
    if (undoable) toast(`Checked ${company} today`, () => { if (before) manualChecks[company] = before; else delete manualChecks[company];
      saveManual(); renderBanners(); renderCounts(); renderList(true); });
  }
  function renderManualChecks(box) {
    const rows = manualRows(); const dueN = rows.filter((x) => x.st.state !== "ok").length;
    box.append(el("p", { class: "muted", style: "margin-top:0", text: `Companies whose jobs HireScope can't read (unsupported job system, or the site's rules forbid it). ` +
      `Open each careers page, look for roles, then press Mark checked. A company is flagged when it hasn't been checked in ${MANUAL_DAYS} days ` +
      `(preferences manual_checks.remind_days). Dates save in this browser and go out with Export statuses.` }));
    if (!rows.length) { box.append(el("div", { class: "empty", text: "No manual companies in the last run." })); return; }
    box.append(el("div", { class: "toolbar" }, el("b", { text: `${dueN} of ${rows.length} to check` }), el("span", { class: "spacer" }),
      dueN ? el("button", { class: "btn small", type: "button", title: "Mark every flagged company as checked today",
        onclick: async () => { if (await confirmDialog(`Mark ${dueN} companies checked today?`, ["Only do this if you looked at their careers pages."], "Mark all checked")) {
          const before = JSON.parse(JSON.stringify(manualChecks)); for (const x of rows) if (x.st.state !== "ok") markManualChecked(x.r.company, false);
          toast(`Marked ${dueN} checked`, () => { for (const k of Object.keys(manualChecks)) delete manualChecks[k]; Object.assign(manualChecks, before); saveManual(); renderBanners(); renderCounts(); renderList(true); }); } } }, "Mark all due as checked") : null));
    const tb = el("tbody");
    for (const { r, e, st } of rows) {
      const chip = st.state === "never" ? el("span", { class: "chip warn", text: "Never checked" }) :
        st.state === "due" ? el("span", { class: "chip warn", title: `Last checked ${st.days} days ago`, text: `Check due · ${st.days}d` }) :
          el("span", { class: "chip good", text: st.days === 0 ? "Checked today" : `Checked ${st.days}d ago` });
      tb.append(el("tr", { class: st.state === "ok" ? "" : "due-row" },
        el("td", null, el("b", { text: r.company })),
        el("td", null, chip),
        el("td", { class: "nowrap", text: e && e.checked_at ? fmtDate(e.checked_at) : "—" }),
        el("td", null, r.error ? el("div", { class: "muted", style: "font-size:12px", text: r.error }) : null),
        el("td", { class: "nowrap" },
          safeUrl(r.url) ? el("a", { class: "btn small", href: r.url, target: "_blank", rel: "noopener noreferrer", title: `Open ${r.company}'s careers page` }, "Open careers page") :
            el("span", { class: "muted", text: "no link" }), " ",
          el("button", { class: "btn small" + (st.state === "ok" ? " ghost" : " primary"), type: "button", onclick: () => markManualChecked(r.company) }, "Mark checked"))));
    }
    box.append(el("div", { class: "table-wrap" }, el("table", { class: "grid manual-table" },
      el("thead", null, el("tr", null, ["Company", "Status", "Last checked", "Why manual", ""].map((h) => el("th", { scope: "col", text: h })))), tb)));
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
    if (a.stage === "ready" && to === "applied") { const j = KEY_MAP.get(a.job_key); if (j) { applyReady(j, a); return; } }
    saveApp(C.moveStage(a, to), `${a.company}: ${STAGE_LABEL[a.stage]} → ${STAGE_LABEL[to]}`);
    launch(`hirescope://status?app=${a.id}&stage=${to}`, "Save stage");     // SQLite + the Excel file, right away
  }
  const STAGE_ORDER = ["ready", ...OPEN_STAGES, ...CLOSED_STAGES];
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
    if (existing && existing.stage === "ready") { applyReady(j, existing); return; }
    if (existing) { openDrawer(existing.id); toast("Already in My Applications"); return; }
    const warns = C.duplicateWarnings(j.company, j.title, keyOf(j), liveApps(), today(), appsCfg.same_company_window_days, null, appsCfg.near_duplicate_ratio);
    if (warns.length && !(await confirmDialog("Possible duplicate application", warns.map((w) => w.message), "Mark applied anyway"))) return;
    const d = await loadDetail(j);
    const st = statuses[keyOf(j)] || {};
    const t = trackOf(j) === "unclear" ? ui.track : trackOf(j);
    const a = C.newApp({ job_key: keyOf(j), company: j.company, title: j.title, track: t, link: safeUrl(j.apply_url) || "", source: (j.badges || []).includes("external") ? "External" : "HireScope",
      package: st.package || "", jd: (d && d.jd) || "", job: { source: j.source, tier: j.tier, label: j.label, score: j.score == null ? null : Math.round(j.score), level: j.level, job_id: j.job_id,
        resume: resumeRef(j) } });
    const undoStatus = setStatus(j, { status: "applied" });
    apps[a.id] = a; saveApps();
    const bBefore = bucket[j.id] ? JSON.parse(JSON.stringify(bucket[j.id])) : null; const wasIn = inBucket(j.id);
    if (wasIn) { bucket[j.id] = Object.assign({}, bucket[j.id], { in: false, updated_at: new Date().toISOString() }); saveBucket(); refreshBucket(j.id); }
    const ref = a.job.resume;
    launch(`hirescope://status?job=${j.id}&stage=applied&app=${a.id}&date=${a.applied_date}${ref ? `&v=${ref.version}` : ""}`, "Mark Applied");
    toast(`Marked applied: ${j.company}${wasIn ? " — moved from the Bucket to My Applications" : ""}`, () => {
      delete apps[a.id]; saveApps(); undoStatus();
      if (bBefore) { bucket[j.id] = Object.assign({}, bBefore, { updated_at: new Date().toISOString() }); saveBucket(); refreshBucket(j.id); }
      launch(`hirescope://status?job=${j.id}&stage=undo&app=${a.id}`);
      renderAll(true); if (pJob) renderActions();
    });
    renderAll(true); if (pJob) renderActions();
  }

  // ---------- Bucket page: your apply-list, both tracks ----------
  const bucketSel = new Set();      // rows ticked for the Bucket's own bulk actions
  const batchWarn = () => Number(((LIVE.settings || {}).batch_warn)) || 5;   // resume_queue.batch_warn
  function bucketJob(x) {           // the loaded job, or a stand-in from the saved copy (closed / not on this dashboard)
    if (x.j) return x.j;
    const sn = x.e.snap || {};
    return { id: x.id, company: sn.company || "?", title: sn.title || `Job ${x.id}`, source: sn.source || "", job_id: sn.job_id || "", track: sn.track || "full_time",
      score: sn.score ?? null, label: sn.label || "", location: sn.location || "", deadline: sn.deadline || null, apply_url: sn.apply_url || "", level: sn.level || "", badges: [], stub: true };
  }
  function bucketClosed(x) {
    const sn = x.e.snap || {}; if (sn.job_status === "closed") return true; if (x.j) return false;
    const st = ((window.SYNC || {}).job_state || {})[sn.key]; return !!(st && st[0] === "closed");
  }
  const HAS_RESUME = ["ready", "ready_q", "reviewed", "queued", "generating"];
  const RS_ORDER = { failed: 0, "": 1, ready_q: 2, queued: 3, generating: 4, ready: 5, reviewed: 6 };
  const BUCKET_SORTS = {
    added: [(a, b) => String(b.e.added_at || "").localeCompare(String(a.e.added_at || "")), "Recently added"],
    score: [(a, b) => (b.job.score ?? -1) - (a.job.score ?? -1), "Score"],
    deadline: [(a, b) => (a.job.deadline || "9999").localeCompare(b.job.deadline || "9999"), "Deadline"],
    company: [(a, b) => a.job.company.localeCompare(b.job.company), "Company"],
    resume: [(a, b) => (RS_ORDER[resumeStatus(a.job)] ?? 1) - (RS_ORDER[resumeStatus(b.job)] ?? 1), "Resume status"],
  };
  function renderBucket(box) {
    const items = bucketItems().map((x) => Object.assign(x, { job: bucketJob(x), closed: bucketClosed(x) }));
    for (const id of [...bucketSel]) if (!inBucket(id)) bucketSel.delete(id);
    const sortKey = BUCKET_SORTS[ui.bucketSort] ? ui.bucketSort : "added";
    items.sort((a, b) => BUCKET_SORTS[sortKey][0](a, b) || a.id - b.id);
    const todo = items.filter((x) => !HAS_RESUME.includes(resumeStatus(x.job)));
    const sort = el("select", { "aria-label": "Sort the Bucket" }, Object.entries(BUCKET_SORTS).map(([k, [, label]]) => el("option", { value: k, text: `Sort: ${label}`, selected: k === sortKey })));
    sort.addEventListener("change", () => { ui.bucketSort = sort.value; saveUI(); renderList(true); });
    box.append(el("div", { class: "toolbar page-toolbar" },
      el("span", { class: "muted", text: `${items.length} job${items.length === 1 ? "" : "s"} · both tracks · ${todo.length} without a resume` }),
      el("span", { class: "spacer" }), sort,
      el("button", { class: "btn primary", type: "button", disabled: !items.length, onclick: () => generateAll(false),
        title: "Queue every Bucket job without a current READY resume (they build one after another in the background)" }, todo.length ? `Generate resumes for all (${todo.length})` : "Generate resumes for all"),
      moreMenu([["Regenerate resumes for all (new versions)", () => generateAll(true), "Every Bucket job, even those with a READY resume"],
        ["Export Bucket as CSV", () => exportJobsCsv(items.map((x) => x.job)), null]], "More Bucket actions")));
    if (!items.length) {
      box.append(el("div", { class: "empty" }, el("b", { text: "Your Bucket is empty." }),
        el("div", { style: "margin-top:6px", text: "Tick the box before any job title (or press Space on a focused job) to add it here — from either track, the Similar list or ⌘K. Then generate all their resumes in one click." }),
        el("div", { style: "margin-top:10px" }, el("button", { class: "btn small", type: "button", onclick: () => switchTrack(ui.track) }, `Browse ${TRACKS[ui.track]} jobs`))));
      return;
    }
    if (bucketSel.size) {
      const sel = items.filter((x) => bucketSel.has(x.id));
      box.append(el("div", { class: "bulkbar inline", role: "region", "aria-label": "Bulk actions for the selected Bucket jobs" },
        el("b", { text: `${sel.length} selected` }),
        el("button", { class: "btn small", type: "button", onclick: () => generateJobs(sel.map((x) => x.job), false) }, "Generate resumes"),
        el("button", { class: "btn small", type: "button", onclick: () => removeFromBucket(sel.map((x) => x.job)) }, "Remove from Bucket"),
        el("button", { class: "btn small", type: "button", onclick: () => exportJobsCsv(sel.map((x) => x.job)) }, "Export CSV"),
        el("span", { class: "spacer" }), el("button", { class: "btn small ghost", type: "button", onclick: () => { bucketSel.clear(); renderList(true); } }, "Clear selection")));
    }
    const all = el("input", { type: "checkbox", "aria-label": "Select every Bucket job", checked: bucketSel.size === items.length && items.length > 0,
      onchange: (e) => { bucketSel.clear(); if (e.target.checked) items.forEach((x) => bucketSel.add(x.id)); renderList(true); } });
    const body = el("tbody");
    for (const x of items) body.append(bucketRow(x));
    box.append(el("div", { class: "table-wrap" }, el("table", { class: "grid bucket-table", "aria-label": "Bucket" },
      el("colgroup", null, ["36px", null, "118px", "60px", "220px", "130px", "372px"].map((w) => el("col", { style: w ? `width:${w}` : null }))),
      el("thead", null, el("tr", null, el("th", { scope: "col" }, all), ...["Role", "Track", "Score", "Resume", "Deadline", "Actions"].map((h, i) => el("th", { scope: "col", class: i === 2 ? "num" : "", text: h })))),
      body)));
    box.append(el("p", { class: "muted", style: "font-size:12px", text: "Jobs stay here until you mark them applied or remove them, even after a posting closes. Marking a job applied moves it to My Applications." }));
  }
  function bucketRow(x) {
    const j = x.job; const rs = resumeStatus(j); const r = resumeOf(j); const app = j.stub ? null : appForJob(j);
    const pick = el("input", { type: "checkbox", "aria-label": `Select ${j.title} at ${j.company}`, checked: bucketSel.has(x.id),
      onchange: (e) => { if (e.target.checked) bucketSel.add(x.id); else bucketSel.delete(x.id); renderList(true); } });
    const open = () => (BY_ID.has(x.id) ? openJob(x.id) : showMissingJob(x.id));
    const d = j.deadline ? daysUntil(j.deadline) : null;
    const apply = safeUrl(j.apply_url);
    const busy = rs === "queued" || rs === "generating";
    return el("tr", { "data-bucket-row": x.id, class: x.closed ? "closed-row" : "" },
      el("td", { class: "pickcol" }, pick),
      el("td", { class: "role" }, el("button", { class: "link-btn row-title", type: "button", onclick: open, title: "Open the job" }, j.title),
        el("div", { class: "muted" }, j.company, x.closed ? [" ", el("span", { class: "chip flag", title: "The posting disappeared from its source; kept in your Bucket", text: "Closed" })] : null,
          app ? [" ", el("span", { class: "chip info", text: STAGE_LABEL[app.stage] })] : null)),
      el("td", null, el("span", { class: `chip ${j.track}`, text: TRACKS[j.track] || j.track })),
      el("td", { class: "num", text: j.score == null ? "—" : String(Math.round(j.score)) }),
      el("td", null, rs ? resumeBadge(rs, resumeTitle(j)) : el("span", { class: "muted", text: "No resume yet" }),
        r && r.current && !busy ? el("div", null, el("a", { href: r.current.pdf, target: "_blank", rel: "noopener", class: "small-link" }, `Preview v${r.current.version}`)) : null),
      el("td", { class: "nowrap" }, j.deadline ? el("span", { class: d != null && d <= 7 ? "warn-text" : "", text: `${fmtDate(j.deadline)}${d != null ? ` (${d < 0 ? "passed" : d === 0 ? "today" : `${d}d`})` : ""}` }) : el("span", { class: "muted", text: "Not stated" })),
      el("td", null, el("div", { class: "row-actions" },
        busy ? el("button", { class: "btn small", type: "button", onclick: () => cancelBuild(j), "aria-label": `Cancel the resume build for ${j.company}` }, "Cancel")
          : el("button", { class: "btn small" + (HAS_RESUME.includes(rs) ? "" : " primary"), type: "button", onclick: () => queueResume(j, HAS_RESUME.includes(rs)),
            "aria-label": `${HAS_RESUME.includes(rs) ? "Regenerate" : "Generate"} resume for ${j.title} at ${j.company}` }, HAS_RESUME.includes(rs) ? "Regenerate" : "Generate Resume"),
        apply ? el("a", { class: "btn small", href: apply, target: "_blank", rel: "noopener noreferrer", "aria-label": `Open the application for ${j.company}` }, "Open Application") : null,
        el("button", { class: "btn small", type: "button", onclick: () => markApplied(j), "aria-label": `Mark ${j.title} at ${j.company} applied` }, "Mark Applied"),
        el("button", { class: "btn small ghost icon-btn", type: "button", onclick: () => setBucket(j, false), "aria-label": `Remove ${j.title} at ${j.company} from the Bucket`, title: "Remove from Bucket" }, "✕"))));
  }
  async function removeFromBucket(jobs) {
    if (!jobs.length) return;
    const before = jobs.map((j) => [j.id, JSON.parse(JSON.stringify(bucket[j.id] || null))]);
    const at = new Date().toISOString();
    for (const j of jobs) bucket[j.id] = Object.assign({}, bucket[j.id], { in: false, updated_at: at });
    saveBucket(); bucketSel.clear(); renderTopTabs(); renderList(true);
    launch(`hirescope://bucket?job=${jobs.map((j) => j.id).join(",")}&op=remove`, "Remove from Bucket");
    toast(`Removed ${jobs.length} from the Bucket`, () => {
      for (const [id, b] of before) { if (b) bucket[id] = b; else delete bucket[id]; }
      saveBucket(); renderTopTabs(); renderList(true);
      launch(`hirescope://bucket?job=${jobs.map((j) => j.id).join(",")}&op=add`);
    });
  }
  async function generateJobs(jobs, regenerate, viaList) {
    if (!jobs.length) { toast("Every one of these already has a current READY resume (or is in the queue)"); return; }
    if (jobs.length > batchWarn() && !(await confirmDialog(`Queue ${jobs.length} resume builds?`, [
      `${jobs.length} jobs will build one after another in the background (you can cancel any queued one).`,
      "Each build is a full Claude Code drafter + reviewer + truth pass: about the usage of running /resume-builder once per job.",
      regenerate ? "Regenerate builds a new version even for jobs whose resume is READY." : "Jobs with a current READY resume are skipped."], `Queue ${jobs.length}`))) return;
    if (!LAUNCHER.installed) { toast("Install the launcher once (./scripts/install_launcher.sh) to generate resumes from here"); return; }
    const url = viaList ? `hirescope://queue?list=bucket${regenerate ? "&regenerate=1" : ""}` : `hirescope://queue?job=${jobs.map((j) => j.id).join(",")}${regenerate ? "&regenerate=1" : ""}`;
    const sent = await launch(url, "Queue resumes");
    if (!sent) return;
    const at = new Date().toISOString(); for (const j of jobs) resumeQueued[j.id] = at; store.set(K.resumeQueued, resumeQueued);
    bucketSel.clear(); toast(`Queued ${jobs.length} resume build${jobs.length === 1 ? "" : "s"} — they build one after another in the background`);
    renderAll(true);
  }
  function generateAll(regenerate) {
    const items = bucketItems().map(bucketJob);
    const jobs = regenerate ? items.filter((j) => !["queued", "generating"].includes(resumeStatus(j))) : items.filter((j) => !HAS_RESUME.includes(resumeStatus(j)));
    // SQLite already has every one of them in its Bucket: the short link; otherwise name the jobs
    const S = (window.SYNC || {}).bucket || {};
    const synced = items.every((j) => S[j.id] && S[j.id].in);
    return generateJobs(jobs, regenerate, synced);
  }

  // an external job's application at "Ready to apply" → Applied today, with the resume version you are sending
  function applyReady(j, a) {
    const before = JSON.parse(JSON.stringify(a));
    const ref = resumeRef(j) || (a.job || {}).resume || null;
    const next = C.edit(C.moveStage(a, "applied"), { applied_date: today(), job: Object.assign({}, a.job || {}, { resume: ref }) });
    apps[a.id] = next; saveApps(); syncJobStatus(next);
    const bBefore = bucket[j.id] ? JSON.parse(JSON.stringify(bucket[j.id])) : null; const wasIn = inBucket(j.id);
    if (wasIn) { bucket[j.id] = Object.assign({}, bucket[j.id], { in: false, updated_at: new Date().toISOString() }); saveBucket(); refreshBucket(j.id); }
    launch(`hirescope://status?job=${j.id}&stage=applied&app=${a.id}&date=${next.applied_date}${ref ? `&v=${ref.version}` : ""}`, "Mark Applied");
    toast(`Marked applied: ${j.company} (Ready to apply → Applied)`, () => {
      apps[a.id] = Object.assign({}, before, { updated_at: new Date().toISOString() }); saveApps();
      if (bBefore) { bucket[j.id] = Object.assign({}, bBefore, { updated_at: new Date().toISOString() }); saveBucket(); refreshBucket(j.id); }
      launch(`hirescope://status?job=${j.id}&stage=undo&app=${a.id}`);
      renderAll(true); if (pJob) renderActions();
    });
    renderAll(true); if (pJob) renderActions();
  }

  // ----- page -----
  function renderApplications(box) {
    const list = appsInTrack();
    const seg = el("div", { class: "seg", role: "tablist", "aria-label": "My Applications view" },
      [["board", "Board"], ["table", "Table"], ["companies", "Applied Companies"], ["stats", "Stats"]].map(([id, label]) => el("button", { type: "button", role: "tab", "aria-selected": String(ui.appTab === id), "aria-pressed": String(ui.appTab === id),
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
    ({ board: appBoard, table: appTable, companies: appCompanies, stats: appStats }[ui.appTab] || appBoard)(box, list);
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

  // ----- Applied Companies: one row per company -----
  function appCompanies(box, list) {
    const rows = C.appliedCompanies(list);
    const link = (x) => (safeUrl(x.link) ? el("a", { href: x.link, target: "_blank", rel: "noopener noreferrer", title: "Application link", text: "link" }) : el("span", { class: "muted", text: "no link" }));
    const body = el("tbody");
    for (const r of rows) {
      body.append(el("tr", null,
        el("td", null, el("b", { text: r.company }), r.active && r.active < r.count ? el("div", { class: "muted", style: "font-size:12px", text: `${r.active} active` }) : null),
        el("td", { class: "num", text: r.count }),
        el("td", null, el("span", { class: `chip ${CLOSED_STAGES.includes(r.latest_stage) ? "" : "info"}`, text: STAGE_LABEL[r.latest_stage] || r.latest_stage })),
        el("td", { class: "nowrap", text: r.latest_applied ? fmtDate(r.latest_applied) : "?" }),
        el("td", { text: r.sources.join(", ") }),
        el("td", null, el("ul", { class: "list-plain", style: "margin:0" }, r.applications.map((x) => el("li", null,
          el("a", { href: "#", onclick: (e) => { e.preventDefault(); openDrawer(x.id); }, text: x.title }),
          el("span", { class: "muted", text: ` · ${STAGE_LABEL[x.stage] || x.stage} · ${x.applied_date || "?"} · ` }), link(x)))))));
    }
    box.append(el("div", { class: "table-wrap" }, el("table", { class: "grid", "aria-label": "Applied companies" },
      el("thead", null, el("tr", null, ["Company", "Applications", "Latest status", "Latest applied", "Source", "Applications and links"].map((h, i) => el("th", { scope: "col", class: i === 1 ? "num" : "", text: h })))),
      body)), el("p", { class: "muted", style: "font-size:12px", text: `${rows.length} compan${rows.length === 1 ? "y" : "ies"} · latest status = the stage of your most recent application there` }));
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
    return el("div", { class: "bars" }, steps.map(([k, v]) => [el("span", { class: "k", text: k }), svgBar(v / n), el("span", { class: "v", text: v })]));
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
      ["Log file", el("span", null, el("code", { class: "mono", text: META.log_path || "data.nosync/logs/jobsearch.log" }), " ",
        el("button", { class: "btn small ghost", type: "button", onclick: async () => toast((await copyText(META.log_path || "")) ? "Path copied" : "Clipboard unavailable") }, "Copy path"))],
    ])));
    const runs = META.runs || [];
    if (runs.length) box.append(el("div", { class: "section" }, el("h3", { text: "Recent runs" }), el("div", { class: "table-wrap" }, el("table", { class: "grid" },
      el("thead", null, el("tr", null, ["Started", "Scope", "Checked", "OK", "Failed", "Found", "New", "Updated", "Closed", "Strong"].map((h) => el("th", { text: h })))),
      el("tbody", null, runs.map((r) => el("tr", null, el("td", { class: "nowrap", text: fmtTime(r.started_at) }), el("td", { text: r.scope || "full" }),
        ...[r.sources_checked, r.sources_ok, r.sources_failed, r.jobs_found, r.new, r.updated, r.closed, r.strong].map((v) => el("td", { class: "num", text: v == null ? "—" : v.toLocaleString() })))))))));
  }

  // ----- modal side panels: background inert, Tab cycles inside, Esc closes, focus returns -----
  const BACKGROUND = [".topbar", ".track-tabs", ".day-row", "#banners", "#tiles", ".layout"];
  const setInert = (on) => { for (const sel of BACKGROUND) { const n = $(sel); if (n) n.inert = on; } };
  const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  function trapTab(e, root) {
    if (e.key !== "Tab") return;
    const f = [...root.querySelectorAll(FOCUSABLE)].filter((n) => n.getClientRects().length);
    if (!f.length) { e.preventDefault(); root.focus(); return; }
    const first = f[0], last = f[f.length - 1], a = document.activeElement;
    if (e.shiftKey && (a === first || a === root || !root.contains(a))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (a === last || !root.contains(a))) { e.preventDefault(); first.focus(); }
  }
  $("#panel").addEventListener("keydown", (e) => trapTab(e, $("#panel")));
  $("#drawer").addEventListener("keydown", (e) => trapTab(e, $("#drawer")));

  // ----- drawer -----
  let dApp = null; let dLastFocus = null;
  function openDrawer(id) {
    if (!apps[id]) return;
    if (pJob) closePanel();
    if (!dApp) dLastFocus = document.activeElement;
    dApp = id; renderDrawer(); setInert(true);
    $("#drawer").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false"); $("#scrim").classList.add("open");
    setTimeout(() => $("#drawer").focus({ preventScroll: true }), 0);
  }
  function closeDrawer() {
    $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); if (!pJob) { $("#scrim").classList.remove("open"); setInert(false); } dApp = null;
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
        if (key === "notes" && v.length <= 4000) launch(`hirescope://note?app=${encodeURIComponent(cur.id)}&text=${encodeURIComponent(v)}`, "Save notes");
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
      resumeUsed(a),
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
  let pTab = "Overview"; let pJob = null; let pDetail = null; let jdHighlight = null; let jdKw = null; let kwFilter = null; let lastFocus = null;
  // jobs you navigated from inside the panel (Similar / More like this), newest last: {id, tab} for "← Back"
  let navStack = []; let navOpening = false;
  function openPanel(id, tab) {
    const j = BY_ID.get(id); if (!j) return;
    if (!navOpening) navStack = [];          // opened from the list, j/k, a link or the URL: a fresh trail
    if (dApp) closeDrawer();
    if (!pJob) lastFocus = document.activeElement;
    selectedId = id; pJob = j; pDetail = null; jdHighlight = null; jdKw = null; kwFilter = null;
    if (tab) pTab = tab;
    setInert(true);
    for (const n of document.querySelectorAll(".card.selected, tr.selected")) n.classList.remove("selected");
    const n = document.querySelector(`#list [data-id="${id}"]`); if (n) { n.classList.add("selected"); n.scrollIntoView({ block: "nearest" }); }
    const prev = navStack.length ? BY_ID.get(navStack[navStack.length - 1].id) : null;
    $("#p-back").hidden = !prev;
    $("#p-back").textContent = prev ? `← Back to ${prev.title} · ${prev.company}` : "";
    $("#panel").classList.add("open"); $("#panel").setAttribute("aria-hidden", "false"); $("#scrim").classList.add("open");
    $("#p-title").textContent = j.title;
    const pbk = $("#p-bucket"); pbk.textContent = ""; pbk.append(bucketBox(j));
    $("#p-sub").textContent = `${j.company}${j.tier ? ` · tier ${j.tier}` : ""} · ${j.location} · ${j.work_mode} · ${postedText(j)}`;
    const pb = $("#p-badges"); pb.textContent = ""; pb.append(...badgesOf(j));
    const pr = $("#p-ring"); pr.textContent = ""; if (!j.filtered) pr.append(ring(j.score, j.label, 60));
    renderActions();
    $("#p-tabs").textContent = "";
    renderPanelBody(el("div", { class: "skeleton" }));
    loadDetail(j).then((d) => { if (pJob === j) { pDetail = d; renderPanel(); } });
    writeHash();
    setTimeout(() => $("#panel").focus({ preventScroll: true }), 0);
  }
  function closePanel() {
    $("#panel").classList.remove("open"); $("#panel").setAttribute("aria-hidden", "true"); $("#scrim").classList.remove("open"); pJob = null; navStack = [];
    setInert(false); writeHash();
    // focus goes back to the job's card (scrolled into view if virtualization had unmounted it), else where it was
    const i = current.findIndex((x) => x.id === selectedId);
    if (vlist && i >= 0) vlist.ensureVisible(i);
    const node = document.querySelector(`#list [data-id="${selectedId}"]`);
    const target = node || (lastFocus && document.contains(lastFocus) ? lastFocus : null);
    if (target && target.focus) target.focus({ preventScroll: true });
  }
  $("#p-close").addEventListener("click", closePanel);
  $("#p-back").addEventListener("click", () => { const b = navStack.pop(); if (b) openJob(b.id, { back: true, tab: b.tab }); }); $("#scrim").addEventListener("click", () => { if (dApp) closeDrawer(); if (pJob) closePanel(); });
  // after a status change: the open panel's badges, actions and (on Overview) its resume section
  function refreshPanel() {
    if (!pJob) return;
    const pb = $("#p-badges"); pb.textContent = ""; pb.append(...badgesOf(pJob));
    renderActions(); if (pTab === "Overview" && pDetail) renderPanel();
  }
  function renderPanelBody(node) { const b = $("#p-body"); b.textContent = ""; b.append(node); b.scrollTop = 0; }
  function renderPanel() {
    const tabs = $("#p-tabs"); tabs.textContent = "";
    const avail = pJob.filtered || !pDetail ? ["Overview", "Notes & History"] : TABS;
    if (!avail.includes(pTab)) pTab = "Overview";
    for (const t of avail) tabs.append(el("button", { class: "panel-tab", role: "tab", "aria-selected": String(t === pTab), type: "button", onclick: () => { pTab = t; renderPanel(); writeHash(); } }, t));
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
    out.append(resumeSection(j));
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
      d.external || (j.badges || []).includes("external") ? ["Added by you", el("span", null, el("span", { class: "chip info", text: "External" }), " ",
        el("span", { class: "muted", text: d.external ? `${{ paste: "pasted JD", file: "JD file", drop: "drop folder", dashboard: "Add external job form", resume_builder: "Resume Builder tab", cli: "command line" }[d.external.origin] || d.external.origin}${d.external.file_name ? ` (${d.external.file_name})` : ""} · ${fmtTime(d.external.added_at)}` : "added by URL" }))] : null,
      sig.rules_would_hide ? ["Your rules", el("span", null, "Would have hidden this job (shown because you added it): ", (sig.rules_would_hide || []).join("; "))] : null,
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
  function setTrack(j, t) {   // a reset keeps a dated tombstone so an older export can't bring the override back
    const k = keyOf(j); const before = overrides[k]; const inPanel = pJob === j;
    overrides[k] = { track: t, updated_at: new Date().toISOString() }; saveOverrides();
    toast(t ? `Moved to ${TRACKS[t]}` : "Track reset", () => { if (before) overrides[k] = before; else delete overrides[k]; saveOverrides(); renderAll(true); if (inPanel) openPanel(j.id); });
    renderBanners(); renderAll(true); if (inPanel) openPanel(j.id);
  }
  function trackControls(j) {
    const k = keyOf(j); const has = overrides[k] && overrides[k].track;
    if (j.track_auto !== "unclear" && !has) return null;
    const set = (t) => setTrack(j, t);
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
        const why = k.status === "synonym" ? `\nYour resume says “${k.resume_term}” → JD says “${k.term}”` :
          k.evidence ? `\n${k.evidence_section}: “${k.evidence}”` : k.status === "missing" ? "\nNot on your resume (a gap)" : "";
        const chip = el("button", { class: `kw ${k.status}`, type: "button",
          title: `${k.status} · ${k.section} · weight ${k.weight}${k.count > 1 ? ` · mentioned ${k.count}×` : ""}${why}\nClick to highlight it in the Full JD`,
          onclick: () => { jdHighlight = k.term; jdKw = k; pTab = "Full JD"; renderPanel(); } },
          el("span", { "aria-hidden": "true", text: icon[k.status] }), el("span", { class: "sr-only", text: `${k.status}: ` }), k.term);
        box.append(chip);
      }
      return box;
    };
    for (const [sec, title] of [["required", "Required"], ["preferred", "Preferred"], ["mentioned", "Also mentioned"]]) {
      const lst = ks.filter((k) => k.section === sec);
      const syn = lst.filter((k) => k.status === "synonym" && (!kwFilter || kwFilter === "synonym"));
      if (lst.length) out.append(el("div", { class: "section" }, el("h3", { text: `${title} (${lst.length})` }), chips(lst),
        syn.length ? el("ul", { class: "list-plain syn-notes" }, syn.map((k) => el("li", null, "≈ Your resume says ", el("b", { text: k.resume_term }),
          " → JD says ", el("b", { text: k.term })))) : null));
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
      const g = el("div", { class: "bars" }, el("span", { class: "k head", text: title }), svgBar((b.score || 0) / 100), el("span", { class: "v", text: b.score == null ? "?" : Math.round(b.score) }));
      for (const [k, p] of Object.entries(b.parts || {})) {
        g.append(el("span", { class: "k", text: k.replace(/_/g, " ") }), p.score == null ? svgBar(0) : svgBar(p.score / 100),
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
    const inline = (str) => (rx ? str.split(rx).map((piece, i) => (i % 2 ? el("mark", { class: hl && piece.toLowerCase() === hl.toLowerCase() ? "" : "kwm", text: piece }) : piece)) : [str]);
    const block = (b) => (b.type === "ul" ? el("ul", null, b.items.map((x) => el("li", null, inline(x)))) : el("p", null, inline(b.text)));
    let firstReq = null; const secs = [];
    for (const sec of V.jdSections(text)) {
      if (!sec.heading) { out.append(...sec.blocks.map(block)); continue; }
      const det = el("details", { class: "jd-sec", open: true }, el("summary", null, el("h4", null, inline(sec.heading))), ...sec.blocks.map(block));
      if (!firstReq && sec.requirements) firstReq = det;
      secs.push(det); out.append(det);
    }
    const all = (open) => { for (const x of secs) x.open = open; };
    const icon = { matched: "✓", synonym: "≈", missing: "✗" };
    const kwInfo = jdKw && hl ? el("div", { class: `banner ${jdKw.status === "missing" ? "err" : jdKw.status === "synonym" ? "info" : "good"}`, style: "margin:0 0 10px" },
      el("div", null, el("b", { text: `${icon[jdKw.status]} ${jdKw.term}` }), ` · ${jdKw.status} · ${jdKw.section}${jdKw.count > 1 ? ` · mentioned ${jdKw.count}×` : ""}`,
        jdKw.status === "synonym" ? el("div", null, "Your resume says ", el("b", { text: jdKw.resume_term }), " → JD says ", el("b", { text: jdKw.term }), " (use the JD's wording where it's true)") : null,
        jdKw.evidence ? el("div", null, `${jdKw.evidence_section}: `, el("q", { text: jdKw.evidence })) : jdKw.status === "missing" ? el("div", { text: "Not on your resume — a gap. Only add it if you actually have the experience." }) : null)) : null;
    const wrap = el("div", null, el("div", { class: "toolbar" },
      firstReq ? el("button", { class: "btn small", type: "button", onclick: () => { firstReq.open = true; firstReq.scrollIntoView({ behavior: "smooth", block: "start" }); } }, "Jump to requirements") : null,
      secs.length > 1 ? el("button", { class: "btn small ghost", type: "button", onclick: () => all(false) }, "Collapse all") : null,
      secs.length > 1 ? el("button", { class: "btn small ghost", type: "button", onclick: () => all(true) }, "Expand all") : null,
      hl ? el("span", { class: "chip warn", text: `Highlighting “${hl}”` }) : null,
      hl ? el("button", { class: "btn small ghost", type: "button", onclick: () => { jdHighlight = null; jdKw = null; renderPanel(); } }, "Clear highlight") : null,
      el("span", { class: "muted", text: "Required keywords are tinted." })), kwInfo, out);
    if (hl) setTimeout(() => { const m = out.querySelector("mark:not(.kwm)"); if (m) { const dd = m.closest("details"); if (dd) dd.open = true; m.scrollIntoView({ block: "center" }); } }, 60);
    return wrap;
  }
  function similarTab(j) {
    const out = el("div");
    const sims = j.similar || [];
    if (!sims.length) { out.append(el("div", { class: "empty", text: "No similar open roles found." })); return out; }
    out.append(el("p", { class: "muted", style: "margin-top:0", text: "More like this — the nearest open roles in the same track (local embedding similarity). Click one to open it; ← Back returns here." }));
    out.append(el("div", { class: "list" }, sims.map((id) => {
      const s = BY_ID.get(id);
      return s ? card(s, true) : el("button", { class: "card", type: "button", "data-id": id, onclick: () => openJob(id, { from: true }) },
        el("div", { class: "card-body" }, el("div", { class: "card-title", text: `Job ${id}` }), el("div", { class: "card-sub", text: "Details aren't loaded in this dashboard: click for what HireScope knows" })));
    })));
    return out;
  }
  function notesTab(j) {
    const out = el("div"); const k = keyOf(j); const st = statuses[k] || {};
    const ta = el("textarea", { rows: 5, placeholder: "Your notes about this job (saved in this browser, exported with your statuses)", "aria-label": "Notes", maxlength: 20000, style: "width:100%" });
    ta.value = st.notes || "";
    ta.addEventListener("change", () => {
      setStatus(j, { notes: ta.value }); toast("Notes saved");
      if (ta.value.length <= 4000) launch(`hirescope://note?job=${j.id}&text=${encodeURIComponent(ta.value)}`, "Save notes");
    });
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
    const blog = safeUrl((META.eng_blogs || {})[j.company] || "");
    const links = [["Levels.fyi", `https://www.levels.fyi/companies/${slug}/salaries`], ["Glassdoor", `https://www.glassdoor.com/Search/results.htm?keyword=${name}`],
      blog ? ["Eng blog", blog] : ["Eng blog (search)", `https://www.google.com/search?q=${name}+engineering+blog`]];
    if (cov && safeUrl(cov.careers_url || "")) links.push(["Careers", cov.careers_url]);
    return links.map(([t, u]) => el("a", { class: "btn small ghost", href: u, target: "_blank", rel: "noopener noreferrer", title: `${t} for ${j.company}` }, t));
  }
  function renderActions() {
    const j = pJob; if (!j) return; const box = $("#p-actions"); box.textContent = "";
    if (!j.filtered) {
      const rs = resumeStatus(j); const busy = rs === "queued" || rs === "generating";
      box.append(el("button", { class: "btn primary", type: "button", disabled: busy, onclick: () => queueResume(j, ["ready", "ready_q", "reviewed"].includes(rs)),
        title: LAUNCHER.installed ? "Queue a tailored resume; it builds and saves in the background (g)" : "The launcher isn't installed: copies the Terminal command (g)" },
        busy ? (rs === "queued" ? "Resume queued…" : "Generating…") : ["ready", "ready_q", "reviewed"].includes(rs) ? "Regenerate Resume" : "Generate Resume"));
      box.append(moreMenu([...resumeFallbacks(j), ["Download job package (.md)", () => generate(j), "The job package as Markdown (also copied)"]], "More resume actions"));
    }
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
  // Terminal fallback (the "…" menu, or when the launcher isn't installed): copy the /resume-builder command
  async function copyResumeCommand(j, regenerate) {
    const cmd = `cd ${JSON.stringify(META.repo_root || ".")} && claude "/resume-builder ${j.id}${regenerate ? " --regenerate" : ""}"`;
    const ok = await copyText(cmd);
    toast(ok ? "Command copied — paste it in Terminal; refresh this page when it's done" : cmd);
  }

  // ---------- hirescope:// launcher + background resume queue ----------
  // The page can't run programs (file://, no network). Each action opens one hirescope:// link, which the HireScope
  // Launcher app (scripts/install_launcher.sh) hands to the pipeline silently; live.js tells us what happened.
  const LAUNCHER = window.HS_LAUNCHER || { installed: false };
  const K_TIP = "jobsearch.launcherTip.v1";
  let lastLaunch = 0;
  function openLink(url) {         // one external-protocol navigation; the page itself stays put
    const a = el("a", { href: url, style: "display:none" }); document.body.append(a); a.click(); a.remove();
    lastLaunch = Date.now(); pollLive(true);
  }
  async function launch(url, what) {
    if (!LAUNCHER.installed) return false;
    if (!store.get(K_TIP, false)) {
      const ok = await confirmDialog("One-time tip: allow the HireScope Launcher", [
        "Your browser will ask “Open HireScope Launcher?” for this page.",
        "Tick “Always allow” (so it doesn't ask every time), then choose Open.",
        "The launcher runs in the background: no Terminal window opens."], what || "Continue");
      if (!ok) return null;
      store.set(K_TIP, true);
    }
    window.__hsLaunch ? window.__hsLaunch(url) : openLink(url);   // __hsLaunch: tests capture the link
    return true;
  }
  async function queueResume(j, regenerate) {
    if (!LAUNCHER.installed) { await copyResumeCommand(j, regenerate); return; }
    const sent = await launch(`hirescope://queue?job=${j.id}${regenerate ? "&regenerate=1" : ""}`, regenerate ? "Regenerate" : "Generate Resume");
    if (!sent) return;
    resumeQueued[j.id] = new Date().toISOString(); store.set(K.resumeQueued, resumeQueued);
    toast(`${regenerate ? "Regenerating" : "Queued"}: ${j.company} — builds in the background (keep going; it saves itself)`);
    renderAll(true); if (pJob) refreshPanel();
  }
  async function cancelBuild(j) {
    const sent = await launch(`hirescope://cancel?job=${j.id}`, "Cancel build");
    if (!sent) return;
    delete resumeQueued[j.id]; store.set(K.resumeQueued, resumeQueued);
    toast(`Cancelling: ${j.company}`);
  }
  function resumeTitle(j) {
    const it = LIVE_BY_JOB.get(j.id); const r = resumeOf(j);
    if (it && it.status === "generating" && it.started_at) return `Generating for ${elapsed(it.started_at)}${it.attempts > 1 ? ` (attempt ${it.attempts})` : ""}`;
    if (it && it.status === "queued") return queuePosition(it);
    return (it && it.status === "failed" && it.message) || (r && r.message) || "";
  }
  const elapsed = (iso) => { const s2 = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000)); return s2 < 60 ? `${s2}s` : `${Math.floor(s2 / 60)}m ${String(s2 % 60).padStart(2, "0")}s`; };
  function queuePosition(it) {
    const waiting = (LIVE.queue || []).filter((x) => x.status === "queued");
    const i = waiting.findIndex((x) => x.id === it.id);
    return i < 0 ? "Queued" : `Queued · ${i === 0 ? "next" : `${i + 1}${["", "", "nd", "rd"][i + 1] || "th"} in line`}`;
  }
  // "…" menu: rarely needed fallbacks, kept out of the way
  function moreMenu(items, label) {
    const wrap = el("div", { class: "filter-wrap more-wrap" });
    const btn = el("button", { class: "btn small ghost more-btn", type: "button", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": label || "More actions", title: label || "More actions" }, "…");
    btn.addEventListener("click", (e) => {
      e.stopPropagation(); const was = openPop && openPop.dataset.f === "more" && wrap.contains(openPop); closePop(); if (was) return;
      const pop = el("div", { class: "popover more-pop", role: "menu" }); pop.dataset.f = "more";
      for (const [text, fn, title] of items.filter(Boolean)) pop.append(el("button", { class: "btn small ghost", type: "button", role: "menuitem", title: title || null,
        onclick: () => { closePop(); fn(); } }, text));
      wrap.append(pop); openPop = pop; btn.setAttribute("aria-expanded", "true");
      setTimeout(() => { const f = pop.querySelector("button"); if (f) f.focus(); }, 0);
      pop.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { ev.stopPropagation(); closePop(); btn.focus(); } });
    });
    wrap.append(btn);
    return wrap;
  }
  const resumeFallbacks = (j) => [
    ["Copy Terminal command", () => copyResumeCommand(j), "Copies the /resume-builder command (fallback when the launcher can't run)"],
    LAUNCHER.installed ? ["Open a live session in Terminal", () => launch(`hirescope://interactive?job=${j.id}`, "Open Terminal"), "For a build where you answer the keyword questions as it goes"] : null];

  // ---------- Questions for you: JD keywords queue builds left out until you answer (one answer covers every job) ----------
  const K_ANS = "jobsearch.questionAnswers.v1";       // {question id: {a, at}} sent, until resumes.js says it's answered
  const sentAnswers = store.get(K_ANS, {});
  const questionGroups = () => ((window.RESUMES || {}).questions || []);
  const openQuestions = () => questionGroups().filter((g) => g.open && !g.ids.some((id) => sentAnswers[id]));
  function renderQuestions(box) {
    const groups = questionGroups();
    box.append(el("p", { class: "muted", style: "margin-top:0", text: "Background builds never stop to ask. When a job description wants a tool or skill your evidence doesn't confirm, the build leaves it out (truthfully) and the question waits here — one answer covers every job that asked. Answer only what's true; then Regenerate those jobs." }));
    if (!groups.length) { box.append(el("div", { class: "empty" }, el("b", { text: "No questions for you." }), el("div", { text: "Questions appear here when a background build skips a keyword it can't confirm." }))); return; }
    const entries = (window.RESUMES || {}).entries || [{ id: "skills", label: "Skills (no specific role)" }];
    const err = LIVE.launcher && LIVE.launcher.action === "answer" && !LIVE.launcher.ok ? LIVE.launcher : null;
    if (err) box.append(el("div", { class: "banner err", style: "margin:0 0 12px", text: `Your last answer wasn't saved: ${err.message}` }));
    for (const g of groups) {
      const sent = g.ids.map((id) => sentAnswers[id]).find(Boolean);
      const jobs = g.jobs.map((x) => BY_ID.get(x.job_pk) || { id: x.job_pk, company: x.company, title: x.title });
      const card2 = el("section", { class: "side-card question", "aria-label": `Question about ${g.keyword}` },
        el("div", { class: "q-head" }, el("b", { text: g.keyword }),
          g.open && !sent ? el("span", { class: "chip warn", text: "Waiting for you" }) : el("span", { class: "chip good", text: `Answered: ${(sent || {}).a || g.answer}` }),
          el("span", { class: "muted", text: `${g.jobs.length} job${g.jobs.length === 1 ? "" : "s"}` })),
        el("p", { class: "q-text", text: g.question }),
        el("div", { class: "q-jobs muted" }, "Asked by: ", jobs.map((j, i) => [i ? " · " : "", el("a", { href: "#", onclick: (e) => { e.preventDefault(); BY_ID.has(j.id) ? openJob(j.id) : showMissingJob(j.id); }, text: `${j.company} — ${j.title}` })])));
      if (g.open && !sent) card2.append(answerForm(g, entries));
      else card2.append(el("div", { class: "row-actions", style: "margin-top:8px" },
        el("button", { class: "btn small primary", type: "button", onclick: () => generateJobs(jobs, true) }, `Regenerate ${jobs.length === 1 ? "this job" : `these ${jobs.length} jobs`}`),
        el("span", { class: "muted", style: "font-size:12px;align-self:center", text: "New versions can use your answer." })));
      box.append(card2);
    }
  }
  function answerForm(g, entries) {
    const id = g.ids[0];
    const f = el("form", { class: "answer-form", "aria-label": `Answer about ${g.keyword}` });
    const yes = el("input", { type: "radio", name: `a-${id}`, value: "yes" }); const no = el("input", { type: "radio", name: `a-${id}`, value: "no" });
    const where = el("select", { "aria-label": "Where you used it" }, el("option", { value: "", text: "Where did you use it?" }), entries.map((x) => el("option", { value: x.id, text: x.label })));
    const level = el("select", { "aria-label": "Level" }, el("option", { value: "", text: "Level (needed for a tool)" }), ["production", "project", "coursework", "tutorial"].map((l) => el("option", { value: l, text: l })));
    const text = el("textarea", { rows: 2, maxlength: 500, "aria-label": "What you did with it", placeholder: `What did you do with ${g.keyword}? (one or two sentences, only what's true)` });
    const ok = el("input", { type: "checkbox" });
    const yesBox = el("div", { class: "answer-yes", hidden: true }, el("div", { class: "form-grid" }, el("label", null, "Where", where), el("label", null, "Level", level),
      el("label", { class: "full" }, "What you did", text)), el("label", { class: "confirm-line" }, ok, " I confirm this is true and may be used on my resumes"));
    for (const r of [yes, no]) r.addEventListener("change", () => { yesBox.hidden = !yes.checked; });
    f.append(el("div", { class: "answer-choice", role: "radiogroup", "aria-label": `Have you used ${g.keyword}?` },
      el("label", null, yes, ` Yes, I've used ${g.keyword}`), el("label", null, no, " No (don't ask again)")), yesBox,
      el("div", { class: "row-end" }, el("button", { class: "btn small primary", type: "submit" }, "Save answer")));
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!yes.checked && !no.checked) { toast("Choose Yes or No"); return; }
      if (yes.checked && (!where.value || text.value.trim().length < 5)) { toast("Say where you used it and what you did"); return; }
      if (yes.checked && !ok.checked) { toast("Tick the confirmation: only true answers are saved"); return; }
      if (!LAUNCHER.installed) { toast("Install the launcher once (./scripts/install_launcher.sh), or answer with: python -m resume_builder questions"); return; }
      const q = new URLSearchParams({ q: String(id), a: yes.checked ? "yes" : "no" });
      if (yes.checked) { q.set("entry", where.value); if (level.value) q.set("level", level.value); q.set("text", text.value.trim()); }
      const sent = await launch(`hirescope://answer?${q.toString()}`, "Save answer");
      if (!sent) return;
      sentAnswers[id] = { a: yes.checked ? "yes" : "no", at: new Date().toISOString() }; store.set(K_ANS, sentAnswers);
      toast(`Answer saved for ${g.keyword} — Regenerate the jobs that asked to use it`); renderCounts(); renderList(true);
    });
    return f;
  }

  // ---------- queue panel: what's building, what's waiting, Cancel per job ----------
  let queueTimer = null;
  const QSTATE = { queued: "queued", generating: "generating", done: "ready", failed: "failed" };
  function openQueuePanel() {
    const p2 = $("#queue-panel"); p2.hidden = false; $("#queue-btn").setAttribute("aria-expanded", "true");
    renderQueuePanel(); setTimeout(() => p2.focus(), 0);
    clearInterval(queueTimer); queueTimer = setInterval(() => { if (!$("#queue-panel").hidden) renderQueueTimes(); }, 1000);
  }
  function closeQueuePanel() { $("#queue-panel").hidden = true; $("#queue-btn").setAttribute("aria-expanded", "false"); clearInterval(queueTimer); $("#queue-btn").focus(); }
  function renderQueueTimes() { for (const n of document.querySelectorAll("[data-started]")) n.textContent = elapsed(n.dataset.started); }
  function renderQueuePanel() {
    const items = LIVE.queue || [];
    const act = items.filter((x) => ["queued", "generating"].includes(x.status));
    $("#queue-count").textContent = String(act.length); $("#queue-btn").classList.toggle("busy", act.length > 0);
    const p2 = $("#queue-panel"); if (p2.hidden) return;
    const w = LIVE.worker || {};
    $("#queue-worker").textContent = !LAUNCHER.installed ? "Launcher not installed" : w.state === "paused" ? "Paused: a HireScope run is writing" :
      w.state === "publishing" ? "Adding new jobs to the dashboard…" : act.length ? "Working" : "Idle";
    const body = $("#queue-body"); body.textContent = "";
    const jobOf = (x) => BY_ID.get(x.job_pk) || { id: x.job_pk, company: x.company || "?", title: x.title || `Job ${x.job_pk}` };
    const line = (x, extra) => el("li", { class: `q-item ${x.status}` }, el("div", { class: "q-main" }, el("b", { text: x.company || "?" }), el("span", { class: "muted", text: ` — ${x.title || ""}` }),
      el("div", { class: "q-meta" }, extra)), ["queued", "generating"].includes(x.status) ? el("button", { class: "btn small ghost", type: "button", onclick: () => cancelBuild(jobOf(x)), "aria-label": `Cancel the resume build for ${x.company}` }, "Cancel") : null);
    const gen = act.filter((x) => x.status === "generating"); const waiting = act.filter((x) => x.status === "queued");
    if (!items.length) body.append(el("p", { class: "muted", text: "Nothing queued. Generate Resume (or Generate resumes for all in the Bucket) adds builds here; they run one after another in the background." }));
    if (gen.length) body.append(el("h4", { text: "Building now" }), el("ul", { class: "q-list" }, gen.map((x) => line(x, [resumeBadge("generating"), " ", el("span", { "data-started": x.started_at || "", text: x.started_at ? elapsed(x.started_at) : "" }), x.attempts > 1 ? ` · attempt ${x.attempts}` : ""]))));
    if (waiting.length) body.append(el("h4", { text: `Waiting (${waiting.length})` }), el("ol", { class: "q-list" }, waiting.map((x) => line(x, [resumeBadge("queued"), x.message && /retrying/.test(x.message) ? ` ${x.message.slice(0, 120)}` : ""]))));
    const done = items.filter((x) => !["queued", "generating"].includes(x.status)).slice(0, 8);
    if (done.length) body.append(el("h4", { text: "Finished today" }), el("ul", { class: "q-list" }, done.map((x) => line(x, [
      x.status === "done" ? resumeBadge(x.result === "ready_questions" ? "ready_q" : "ready") : x.status === "failed" ? resumeBadge("failed") : el("span", { class: "chip", text: x.status === "skipped" ? "Skipped" : "Cancelled" }),
      " ", el("span", { class: "muted", text: String(x.message || "").slice(0, 160) }),
      x.status === "failed" ? [" ", el("button", { class: "btn small", type: "button", onclick: () => queueResume(jobOf(x), false) }, "Retry")] : null]))));
  }
  $("#queue-btn").addEventListener("click", () => ($("#queue-panel").hidden ? openQueuePanel() : closeQueuePanel()));
  $("#queue-close").addEventListener("click", closeQueuePanel);
  $("#queue-panel").addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); closeQueuePanel(); } });

  // live.js: re-read every few seconds while something is happening (queue active, or a link was just sent)
  let liveTimer = null; let liveBusy = false; let liveSeen = String(LIVE.generated_at || "");
  function reloadScript(src) {
    return new Promise((res) => {
      const sEl = el("script", { src: `${src}?t=${Date.now()}` });
      sEl.onload = () => { sEl.remove(); res(true); }; sEl.onerror = () => { sEl.remove(); res(false); };
      document.head.append(sEl);
    });
  }
  const liveActive = () => (LIVE.queue || []).some((x) => ["queued", "generating"].includes(x.status));
  function pollLive(soon) {
    clearTimeout(liveTimer);
    if (!LAUNCHER.installed) return;
    const fast = soon || liveActive() || Date.now() - lastLaunch < 60000;
    liveTimer = setTimeout(async () => { if (!liveBusy) { liveBusy = true; await reloadScript("live.js"); liveBusy = false; } pollLive(false); }, soon ? 1500 : fast ? 3000 : 20000);
  }
  const liveSig = (d) => JSON.stringify([(d.queue || []).map((x) => [x.id, x.status, x.attempts, x.message]), d.launcher, d.excel, d.questions_open, d.sync_at,
    (d.worker || {}).state, (d.external || []).map((x) => [x.rid, x.status, x.at])]);
  window.__hsLive = (d) => {
    if (!d || String(d.generated_at || "") === liveSeen) return;
    if (liveSig(d) === liveSig(LIVE)) { LIVE = d; liveSeen = String(d.generated_at || ""); return; }
    const before = new Map([...LIVE_BY_JOB].map(([k, v]) => [k, v.status + "|" + v.id]));
    const prevSync = LIVE.sync_at; const prevLauncher = LIVE.launcher; LIVE = d; liveSeen = String(d.generated_at || ""); indexLive();
    const finished = [...LIVE_BY_JOB].filter(([k, v]) => before.has(k) && before.get(k) !== v.status + "|" + v.id && !["queued", "generating"].includes(v.status));
    for (const [, v] of finished) {
      if (v.status === "skipped") toast(`${v.company}: ${v.message || "already has a READY resume"}`);
      else if (v.status === "failed") toast(`Resume failed: ${v.company} — ${String(v.message || "").slice(0, 120)}`);
      else if (v.status === "done") toast(`Resume ready: ${v.company}${v.result === "ready_questions" ? " (questions pending)" : ""}`);
    }
    const work = [];
    const answered = d.launcher && d.launcher.action === "answer" && d.launcher.at !== (prevLauncher || {}).at;
    if (finished.length || answered) work.push(reloadScript("resumes.js").then(() => { RES = ((window.RESUMES || {}).jobs) || {}; mapResumeStatuses(); }));
    if (d.sync_at && d.sync_at !== prevSync) work.push(reloadScript("sync.js").then(() => { mergeSync(); SYNC_AT = syncAt(); }));
    Promise.all(work).then(() => { renderBanners(); renderAll(true); if (pJob) refreshPanel(); renderQueuePanel(); });
  };
  function resumeUsed(a) {
    const r = (a.job || {}).resume;
    if (!r) return el("div", { class: "full muted", style: "font-size:12px", text: "Resume used: none recorded (Mark Applied records the current tailored resume)" });
    return el("div", { class: "full", style: "font-size:12px" }, "Resume used: ",
      r.pdf ? el("a", { href: r.pdf.startsWith("/") ? "file://" + r.pdf : r.pdf, target: "_blank", rel: "noopener", text: `v${r.version} — ${r.pdf_name || r.pdf.split("/").pop()}` }) : `v${r.version}`,
      r.linked === "auto" ? el("span", { class: "muted", text: " (matched by date)" }) : null);
  }
  function markResumeReviewed(j) {
    const r = resumeOf(j); if (!r || !r.current) return;
    resumeReviews[j.id] = { version: r.current.version, reviewed_at: new Date().toISOString() }; store.set(K.resumeReviews, resumeReviews);
    toast(`Resume v${r.current.version} marked reviewed (saved with Export statuses)`, () => { delete resumeReviews[j.id]; store.set(K.resumeReviews, resumeReviews); renderAll(true); if (pJob) renderPanel(); });
    renderAll(true); if (pJob) renderPanel();
  }
  function resumeRef(j) {
    const r = resumeOf(j); const v = r && r.current;
    // pdf_sha identifies the exact file sent (the manifest changes when a version is marked reviewed; the PDF never does)
    return v ? { job_pk: j.id, version: v.version, pdf: v.pdf, pdf_name: v.pdf_name, pdf_sha: v.pdf_sha } : null;
  }
  function resumeSection(j) {
    const r = resumeOf(j); const rs = resumeStatus(j);
    const box = el("div", { class: "section" }, el("h3", { text: "Resume" }));
    if (!r || rs === "queued" || rs === "generating") {
      const it = LIVE_BY_JOB.get(j.id);
      if (rs === "queued" || rs === "generating") box.append(el("div", { style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px" },
        resumeBadge(rs), el("span", { class: "muted", text: resumeTitle(j) }),
        it ? el("button", { class: "btn small ghost", type: "button", onclick: () => cancelBuild(j) }, "Cancel") : null));
      if (!r) {
        box.append(el("p", { class: "muted", style: "margin:0 0 8px", text: rs ? "It saves itself when done (version folder, Desktop copy) and shows up here." :
          LAUNCHER.installed ? "No tailored resume yet. Generate Resume queues one; it builds and saves in the background." :
            "No tailored resume yet. Install the launcher once (./scripts/install_launcher.sh) and Generate Resume builds it in the background; until then it copies a Terminal command." }));
        return box;
      }
    }
    const v = r.current; const latest = r.latest;
    box.append(el("div", { style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px" },
      el("span", { class: `chip ${(RESUME_CHIP[rs] || ["", ""])[1]}`, text: (RESUME_CHIP[rs] || [rs])[0] }),
      v ? el("span", { class: "muted", text: `v${v.version} · ${fmtTime(v.created_at)}${v.reviewed_at ? ` · reviewed ${fmtTime(v.reviewed_at)}` : ""}` }) : null));
    if (r.message) box.append(el("div", { class: `banner ${rs === "failed" ? "err" : "info"}`, style: "margin:0 0 8px", text: r.message }));
    if (latest && latest.status === "failed" && latest.reasons && latest.reasons.length)
      box.append(el("ul", { class: "list-plain", style: "font-size:12px" }, latest.reasons.map((x) => el("li", { text: `✗ ${x}` }))));
    if (v) {
      const links = el("div", { style: "display:flex;gap:6px;flex-wrap:wrap;margin:6px 0" },
        el("a", { class: "btn small", href: v.pdf, target: "_blank", rel: "noopener", title: "Open the PDF in a new tab" }, "Preview PDF"),
        el("a", { class: "btn small", href: v.pdf, download: v.pdf_name || "resume.pdf" }, "Download PDF"),
        el("a", { class: "btn small", href: v.tex, download: "main.tex", title: "The full LaTeX source (main.tex + content.tex in the version folder)" }, "Download .tex"),
        el("a", { class: "btn small ghost", href: v.report, target: "_blank", rel: "noopener" }, "Validation report"),
        el("a", { class: "btn small ghost", href: v.reports_dir + "/", target: "_blank", rel: "noopener", title: "Truth, coverage, change and interview-defense reports" }, "All reports"));
      box.append(links);
      box.append(facts([
        ["Checks", `${v.pages || "?"} page · ${v.font || "?"} · ${v.truth_unsupported === 0 ? "0 unsupported claims" : `${v.truth_unsupported} unsupported claim(s)`}`],
        ["Coverage", v.coverage_pct == null ? "—" : `${Math.round(v.coverage_pct)}% (internal coverage measure, not an ATS score)`],
        ["File", v.pdf_name || ""], ["Profile", v.profile || ""]]));
      if (v.preview) box.append(el("a", { href: v.pdf, target: "_blank", rel: "noopener", title: "Open the PDF" },
        el("img", { src: v.preview, alt: `Page 1 of the resume for ${j.company}`, style: "width:100%;max-width:420px;border:1px solid var(--line);border-radius:6px;margin-top:6px" })));
      const acts = el("div", { style: "display:flex;gap:6px;flex-wrap:wrap;margin-top:8px" });
      const reviewedHere = (resumeReviews[j.id] || {}).version === v.version;
      // review the current version even while a new generation is queued
      if (v.status === "ready" && !reviewedHere) acts.append(el("button", { class: "btn small", type: "button", onclick: () => markResumeReviewed(j) }, "Mark reviewed"));
      if (reviewedHere && v.status === "ready") acts.append(el("span", { class: "muted", style: "font-size:12px;align-self:center", text: "Reviewed here — saved with Export statuses" }));
      acts.append(el("button", { class: "btn small", type: "button", onclick: () => queueResume(j, true), title: "Queue a new version (builds in the background)" },
        rs === "ready_q" ? "Regenerate (after answering)" : "Regenerate"));
      if (rs === "ready_q" && VIEWS.some((v) => v.id === "questions")) acts.append(el("button", { class: "btn small ghost", type: "button", onclick: () => setView("questions") }, `Answer ${r.questions_pending} question${r.questions_pending === 1 ? "" : "s"}`));
      acts.append(moreMenu(resumeFallbacks(j), "More resume actions"));
      box.append(acts);
    }
    return box;
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
  function openSkipPicker(j) {
    const d = $("#skip-dialog");
    $("#skip-job").textContent = `${j.title} — ${j.company}`;
    const box = $("#skip-reasons"); box.textContent = "";
    const pick = (r) => { d.close(); skip(j, r); };
    SKIP_REASONS.forEach((r, i) => box.append(el("button", { class: "btn skip-reason", type: "button", onclick: () => pick(r) },
      V.keyForIndex(i) ? el("span", { class: "kbd", text: V.keyForIndex(i) }) : null, r)));
    d.onkeydown = (e) => { const r = V.reasonForKey(e.key, SKIP_REASONS); if (r) { e.preventDefault(); pick(r); } };
    $("#skip-cancel").onclick = () => d.close();
    d.showModal(); box.querySelector("button").focus();
  }
  function exportStatuses() {
    const payload = { exported_at: new Date().toISOString(), format: 6, statuses, track_overrides: overrides, applications: apps,
                      manual_checks: manualChecks, resume_reviews: resumeReviews, bucket };
    download("jobsearch-statuses.json", JSON.stringify(payload, null, 2), "application/json");
    store.set(K.exported, payload.exported_at); renderBanners(); toast("Exported jobsearch-statuses.json — the next run imports it, or now: ./scripts/import_pending.sh");
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

  // ---------- Resume Builder tab: jobs found outside HireScope → saved, imported and built in one click ----------
  // The page saves the job as hirescope-external-job-*.json (your Downloads) and opens hirescope://external?file=…;
  // the launcher imports that file (dedupe: an existing job keeps its ID) and queues its resume. Without the launcher
  // the file waits for the next run (or ./scripts/external_resume.sh "<file>").
  // Each submission is remembered here for a week, by its id (rid; older entries: their created_at, or the saved file's
  // name). The worker answers each one in live.js (LIVE.external: imported / linked to an existing job / failed + why);
  // no answer within EXT_TIMEOUT_MS shows as failed with Retry, so nothing sits at "Importing…" forever.
  const EXT_KEY = "jobsearch.externalPending.v1";   // [{rid, company, role, url, file, saved_at, payload}]
  const EXT_TIMEOUT_MS = 5 * 60 * 1000;
  const EXT_DAYS = 7;
  const extId = (x) => x.file || x.rid || x.saved_at;
  const extOutcome = (x) => (LIVE.external || []).find((o) => o.rid === extId(x)) || null;
  const extEntries = () => store.get(EXT_KEY, []).filter((x) => Date.now() - Date.parse(x.saved_at || 0) < EXT_DAYS * 864e5);
  function extState(x) {
    const o = extOutcome(x);
    if (o && (o.status === "imported" || o.status === "linked")) return { kind: o.status, o, pk: o.job_pk };
    if (o && o.status === "failed") return { kind: "failed", o, reason: o.reason || o.message || "import failed" };
    const age = Date.now() - Date.parse(x.saved_at || 0);
    const w = (LIVE.worker || {}).state;
    if (!LAUNCHER.installed) return { kind: "waiting" };
    if (age < EXT_TIMEOUT_MS) return { kind: "importing", hint: w === "paused" ? "waiting: a HireScope run is writing the database" : "" };
    return { kind: "failed", timeout: true, reason: `no answer from HireScope after ${Math.round(EXT_TIMEOUT_MS / 60000)} minutes` +
      (w === "paused" ? " (a HireScope run is still writing the database; it is imported once the run ends)" :
       w === "stopped" || !w ? " (the background worker isn't running; Retry starts it)" : "") };
  }
  // pending rows: everything not yet on the dashboard as an external job (a linked job stays listed: it isn't one)
  const extPending = () => extEntries().filter((x) => { const st = extState(x); return !(st.kind === "imported" && externalJobs().some((j) => j.id === st.pk)); });
  const externalJobs = () => JOBS.filter((j) => (j.badges || []).includes("external"));
  const b64url = (str) => { const bytes = new TextEncoder().encode(str); let s2 = ""; for (let i = 0; i < bytes.length; i += 0x8000) s2 += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s2).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); };
  const b64 = (buf) => { let s2 = ""; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i += 0x8000) s2 += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return btoa(s2); };
  let extForce = false; let extLast = null;     // the last submission (shown above the list until the queue picks it up)
  let builderForm = null;                       // built once: live updates re-render the page without losing your typing
  function renderBuilder(box) {
    box.append(el("p", { class: "muted page-intro", text: "For jobs you found outside HireScope. Paste the job description (or choose a file, or give just the URL): " +
      "HireScope saves the job, imports it (a job it already has keeps its ID — no copy), and builds the tailored resume in the background. No Terminal." }));
    box.append(builderForm || (builderForm = buildForm()));
    if (extLast) box.append(extStatus(extLast));
    renderExternalList(box);
  }
  function buildForm() {
    const f = el("form", { class: "side-card builder-form", autocomplete: "off", "aria-label": "Add an external job and build its resume" });
    const field = (label, input, cls) => el("label", { class: cls || "" }, label, input);
    const inp = (name, attrs) => el("input", Object.assign({ name, type: "text" }, attrs || {}));
    const track = el("select", { name: "track" }, el("option", { value: "coop_intern", text: "Co-op / Intern" }), el("option", { value: "full_time", text: "Full-time" }));
    track.value = ui.track;
    const file = el("input", { name: "file", type: "file", accept: ".txt,.md,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
    const jd = el("textarea", { name: "jd", rows: 9, maxlength: 200000, placeholder: "Paste the full job description here…" });
    const warn = el("div", { class: "builder-warn", "aria-live": "polite" });
    f.append(el("div", { class: "form-grid" },
      field("Company", inp("company", { maxlength: 200, required: false })), field("Role", inp("role", { maxlength: 300 })),
      field("Location", inp("location", { maxlength: 200, placeholder: "e.g. Boston, MA / Remote" })), field("Job URL (optional)", inp("url", { type: "url", placeholder: "https://…" })),
      field("Track", track), field("JD file (.txt, .md, .pdf, .docx)", file),
      field("Job description (paste it, choose a file, or leave both empty and give the URL)", jd, "full")),
      warn,
      el("div", { class: "row-end" }, el("span", { class: "muted", style: "font-size:12px;margin-right:auto", text: LAUNCHER.installed ? "Builds in the background; the result lands in My Applications as “Ready to apply”." : "The launcher isn't installed: the job is saved to your Downloads for the next run." }),
        el("button", { class: "btn primary", type: "submit" }, LAUNCHER.installed ? "Save and build resume" : "Save external job")));
    const v = (k) => String(f.elements[k].value || "").trim();
    const check = () => {
      warn.textContent = ""; extForce = false;
      if (!v("company") || (!v("role") && !v("url"))) return null;
      const dup = C.externalDuplicate(JOBS, { company: v("company"), role: v("role"), url: v("url") });
      if (!dup) return null;
      const force = el("input", { type: "checkbox", onchange: (e) => { extForce = e.target.checked; } });
      warn.append(el("div", { class: "banner info", style: "margin:8px 0 0" }, el("span", { text: `${dup.message}. Submitting builds that job's resume (no copy is added). ` }),
        el("button", { class: "btn small x", type: "button", onclick: () => openJob(dup.id) }, "Open it"),
        el("label", { style: "margin-left:8px;font-size:12px" }, force, " It's a different job: add it anyway")));
      return dup;
    };
    for (const k of ["company", "role", "url"]) f.elements[k].addEventListener("change", check);
    f.addEventListener("submit", (e) => { e.preventDefault(); submitExternal(f, check()); });
    return f;
  }
  async function submitExternal(f, dup) {
    const v = (k) => String(f.elements[k].value || "").trim(); const file = f.elements.file.files[0];
    const url = v("url");
    if (url && !safeUrl(url)) { toast("The job URL must start with http:// or https://"); return; }
    if (!v("jd") && !file && !url) { toast("Paste the job description, choose a file, or give the job URL"); return; }
    if ((v("jd") || file) && (!v("company") || !v("role"))) { toast("Company and role are needed with a pasted JD or a file"); return; }
    if (file && !/\.(txt|md|pdf|docx)$/i.test(file.name)) { toast("JD files can be .txt, .md, .pdf or .docx"); return; }
    if (file && file.size > 10e6) { toast("That file is over 10 MB"); return; }
    const payload = { kind: "hirescope_external_job", format: 1, origin: "resume_builder", created_at: new Date().toISOString(), company: v("company"), role: v("role"),
      location: v("location"), url, track: v("track"), jd_text: v("jd"), force: !!(dup && extForce) };
    if (file) payload.file = { name: file.name, type: file.type, b64: b64(await file.arrayBuffer()) };
    const sent = await sendExternal(payload);
    if (sent) { f.reset(); toast(`Saved and queued: ${payload.company || url} — importing, then building in the background`); }
  }
  // one submission → the launcher (a pasted JD or URL inside the link; a file saved to Downloads and named in it),
  // remembered by its id so the worker's answer finds it. `again`: the entry Retry re-sends (keeps its row).
  async function sendExternal(payload, again) {
    const url = payload.url || "";
    payload = Object.assign({}, payload, { rid: `rb-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`, created_at: new Date().toISOString() });
    const slug = (payload.company || (url ? new URL(url).hostname : "job")).replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "job";
    const name = `hirescope-external-job-${slug}-${payload.created_at.replace(/[-:]/g, "").slice(0, 15)}-${Math.random().toString(36).slice(2, 6)}.json`;
    // a pasted JD (or a URL) travels inside the link: nothing is downloaded and no folder permission is needed; a JD
    // file is saved to Downloads and named in the link (the worker reads it there)
    const data = payload.file ? null : b64url(JSON.stringify(payload));
    const inline = LAUNCHER.installed && data && data.length < 110000;
    if (!inline) download(name, JSON.stringify(payload, null, 1), "application/json");
    const keep = payload.file ? null : (JSON.stringify(payload).length < 60000 ? payload : Object.assign({}, payload, { jd_text: "" }));
    const entry = { rid: payload.rid, company: payload.company || (url ? new URL(url).hostname : ""), role: payload.role || "(from the URL)", url,
      file: inline ? null : name, saved_at: payload.created_at, payload: keep && (keep.jd_text || !payload.jd_text) ? keep : null };
    const rest = store.get(EXT_KEY, []).filter((x) => !again || extId(x) !== extId(again));
    store.set(EXT_KEY, [...rest.slice(-30), entry]);
    extLast = { rid: entry.rid, entry, name: inline ? null : name, company: payload.company, role: payload.role, url, at: payload.created_at, sent: false };
    let sent = false;
    if (LAUNCHER.installed) {
      sent = await launch(inline ? `hirescope://external?data=${data}` : `hirescope://external?file=${encodeURIComponent(name)}`, "Build resume");
      extLast.sent = !!sent;
    } else toast(`Saved ${name} to your Downloads`);
    renderList(true); renderTopTabs();
    return sent;
  }
  async function retryExternal(x) {
    if (x.payload) { if (await sendExternal(x.payload, x)) toast(`Retrying: ${x.company || x.url}`); return; }
    if (x.file && LAUNCHER.installed) {        // a JD file still in Downloads: name it again
      const entry = Object.assign({}, x, { saved_at: new Date().toISOString() });
      store.set(EXT_KEY, [...store.get(EXT_KEY, []).filter((y) => extId(y) !== extId(x)), entry]);
      if (await launch(`hirescope://external?file=${encodeURIComponent(x.file)}`, "Retry import")) toast(`Retrying: ${x.file}`);
      renderList(true); return;
    }
    pasteInstead(x, "This entry predates Retry (its URL wasn't kept): paste the job description and save it again");
  }
  function pasteInstead(x, why) {         // the form, filled in with what is known; the JD is yours to paste
    const f = builderForm; if (!f) return;
    const o = extOutcome(x) || {};
    const pl = x.payload || {};
    f.elements.company.value = pl.company || o.company || (x.company && x.company !== safeHost(x.url) && x.company !== o.role ? x.company : "");
    f.elements.role.value = pl.role || o.role || (x.role !== "(from the URL)" ? x.role || "" : "");
    f.elements.url.value = x.url || pl.url || o.url || "";
    if (pl.location) f.elements.location.value = pl.location;
    if (pl.track) f.elements.track.value = pl.track;
    f.scrollIntoView({ block: "start" }); f.elements.jd.focus();
    toast(why || "Paste the job description, then Save and build resume");
  }
  const safeHost = (u) => { try { return new URL(u).hostname; } catch (e) { return ""; } };
  function dismissExternal(x) {
    store.set(EXT_KEY, store.get(EXT_KEY, []).filter((y) => extId(y) !== extId(x)));
    if (extLast && extLast.rid === x.rid) extLast = null;
    renderList(true); renderTopTabs();
  }
  function extStatus(x) {
    const cmd = `cd ${JSON.stringify(META.repo_root || ".")} && ./scripts/external_resume.sh ${JSON.stringify(x.name)}`;
    const st = x.entry ? extState(x.entry) : { kind: "importing" };
    const L = LIVE.launcher;      // the worker's last answer (before this page knew its id: by time)
    const ack = !st.o && L && L.action === "external" && (L.rid ? L.rid === x.rid : String(L.at || "") >= String(x.at || "").slice(0, 19)) ? L : null;
    if (ack) { st.o = { message: ack.ok ? ack.message : `Launcher: ${ack.message || "failed"}` }; if (!ack.ok) st.kind = "failed"; }
    const lines = [el("b", { text: `${x.company || x.url} — ${x.role || "from the URL"}` }), x.name ? [" · saved as ", el("code", { class: "mono", text: x.name })] : " · sent to HireScope"];
    const msg = !LAUNCHER.installed ? "Waiting for the next run (or run the command below in Terminal)." : !x.sent ? "Not sent to the launcher." :
      st.o ? st.o.message : st.kind === "failed" ? `Import failed: ${st.reason}` : "Sent to the launcher — importing…" + (st.hint ? ` (${st.hint})` : "");
    return el("div", { class: `banner ${st.kind === "failed" ? "err" : "info"} ext-status`, "aria-live": "polite" }, el("div", null, el("div", null, lines), el("div", { text: msg }),
      x.name ? el("div", { style: "margin-top:6px" }, el("button", { class: "btn small", type: "button", onclick: async () => toast((await copyText(cmd)) ? "Command copied — paste it in Terminal" : cmd) }, "Copy Terminal fallback")) : null));
  }
  function renderExternalList(box) {
    const jobs = externalJobs().sort((a, b) => String(b.first_seen || "").localeCompare(String(a.first_seen || "")));
    const pend = extPending();
    box.append(el("h3", { class: "section-head", text: `Your external jobs (${jobs.length + pend.length})` }));
    if (!jobs.length && !pend.length) { box.append(el("div", { class: "empty", text: "No external jobs yet. Add one above: its resume builds on its own and shows up here." })); return; }
    const body = el("tbody");
    for (const x of pend) {
      const st = extState(x); const o = st.o || {};
      const name = o.job_title ? [o.job_title, o.job_company] : [x.role, x.company];
      const rs = st.pk ? resumeStatus({ id: st.pk }) : "";
      let state; const acts = [];
      if (st.kind === "imported" || st.kind === "linked") {
        state = [st.kind === "linked" ? el("span", { class: "chip info", title: o.message || "", text: `Linked to job ${st.pk} (already in HireScope)` }) : el("span", { class: "chip good", text: "Imported" }),
          " ", rs ? resumeBadge(rs, resumeTitle({ id: st.pk })) : el("span", { class: "muted", text: o.queue === "skipped" ? "Resume: already READY" : "" })];
        acts.push(el("button", { class: "btn small", type: "button", onclick: () => openJob(st.pk) }, "Open job"));
      } else if (st.kind === "failed") {
        state = [el("span", { class: "chip flag", text: "Import failed" }), el("div", { class: "muted ext-reason", text: st.reason })];
        acts.push(el("button", { class: "btn small", type: "button", onclick: () => retryExternal(x) }, "Retry"));
        if (x.url || o.paste || st.timeout) acts.push(el("button", { class: "btn small primary", type: "button", onclick: () => pasteInstead(x) }, "Paste JD instead"));
        acts.push(el("button", { class: "btn small ghost", type: "button", "aria-label": `Remove ${x.company || x.url} from this list`, onclick: () => dismissExternal(x) }, "Remove"));
      } else state = el("span", { class: "muted", text: st.kind === "waiting" ? "Waiting for the next run" : `Importing…${st.hint ? ` (${st.hint})` : ""}` });
      body.append(el("tr", { "data-ext-pending": extId(x), "data-ext-state": st.kind }, el("td", { class: "role" }, el("b", { text: name[0] }), el("div", { class: "muted", text: name[1] }),
          x.url ? el("div", { class: "muted small-links" }, el("a", { href: x.url, target: "_blank", rel: "noopener" }, safeHost(x.url))) : null),
        el("td", { class: "muted", text: "—" }), el("td", { class: "nowrap", text: fmtDate(String(x.saved_at).slice(0, 10)) }),
        el("td", null, state), el("td", { class: "muted", text: st.kind === "linked" ? "the existing job" : st.kind === "imported" ? "on the dashboard after the next refresh" : "—" }),
        el("td", null, el("div", { class: "row-actions" }, acts))));
    }
    for (const j of jobs) {
      const rs = resumeStatus(j); const r = resumeOf(j); const app = appForJob(j); const v = r && r.current;
      const busy = rs === "queued" || rs === "generating";
      body.append(el("tr", { "data-ext-row": j.id },
        el("td", { class: "role" }, el("button", { class: "link-btn row-title", type: "button", onclick: () => openJob(j.id) }, j.title), el("div", { class: "muted", text: j.company })),
        el("td", null, el("span", { class: `chip ${trackOf(j)}`, text: TRACKS[trackOf(j)] })),
        el("td", { class: "nowrap", text: j.first_seen ? fmtDate(j.first_seen.slice(0, 10)) : "" }),
        el("td", null, rs ? resumeBadge(rs, resumeTitle(j)) : el("span", { class: "muted", text: "No resume yet" }),
          v && !busy ? el("div", { class: "small-links" }, el("a", { href: v.pdf, target: "_blank", rel: "noopener" }, `Preview v${v.version}`), " · ",
            el("a", { href: v.pdf, download: v.pdf_name || "resume.pdf" }, "Download")) : null),
        el("td", null, app ? el("span", { class: `chip ${app.stage === "ready" ? "warn" : "info"}`, text: STAGE_LABEL[app.stage] }) : el("span", { class: "muted", text: "—" })),
        el("td", null, el("div", { class: "row-actions" },
          busy ? el("button", { class: "btn small", type: "button", onclick: () => cancelBuild(j) }, "Cancel")
            : el("button", { class: "btn small", type: "button", onclick: () => queueResume(j, HAS_RESUME.includes(rs)) }, HAS_RESUME.includes(rs) ? "Regenerate" : "Generate Resume"),
          app && app.stage !== "ready" ? el("button", { class: "btn small", type: "button", onclick: () => openDrawer(app.id), title: "Open in My Applications" }, "Applied ✓")
            : el("button", { class: "btn small" + (app ? " primary" : ""), type: "button", onclick: () => markApplied(j), "aria-label": `Mark ${j.title} at ${j.company} applied` }, "Mark Applied")))));
    }
    box.append(el("div", { class: "table-wrap" }, el("table", { class: "grid ext-table", "aria-label": "External jobs" },
      el("thead", null, el("tr", null, ["Role", "Track", "Added", "Resume", "Application", "Actions"].map((h) => el("th", { scope: "col", text: h })))), body)));
  }
  $("#add-ext-open").addEventListener("click", () => switchPage("builder"));
  // an import with no answer turns into "Import failed" + Retry on its own (no live.js change needed)
  setInterval(() => { if (ui.page === "builder" && !(builderForm && builderForm.contains(document.activeElement)) && extEntries().some((x) => extState(x).kind === "importing")) { renderList(true); renderTopTabs(); } }, 15000);

  // ---------- saved views (per track: filters + sort + search + salary + view) ----------
  function loadSavedView(sv) {
    V.applyView(tstate(), sv); saveUI(); $("#q").value = tstate().q || ""; closePop(); renderAll(); toast(`View: ${sv.name}`);
  }
  $("#saved-btn").addEventListener("click", (e) => {
    e.stopPropagation(); const was = openPop && openPop.dataset.f === "saved"; closePop(); if (was) return;
    const st = tstate(); const wrap = $("#saved-wrap");
    const pop = el("div", { class: "popover saved-pop", role: "dialog", "aria-label": "Saved views" }); pop.dataset.f = "saved";
    const list = el("div");
    const draw = () => {
      list.textContent = "";
      if (!(st.saved || []).length) list.append(el("div", { class: "muted", style: "padding:4px 6px;font-size:12px", text: `No saved views for ${TRACKS[ui.track]} yet.` }));
      for (const sv of st.saved || []) list.append(el("div", { class: "saved-row" },
        el("button", { class: "btn small ghost saved-load", type: "button", onclick: () => loadSavedView(sv), title: "Load this view" }, sv.name),
        el("button", { class: "btn small ghost", type: "button", "aria-label": `Delete saved view ${sv.name}`, onclick: () => { st.saved = V.deleteView(st.saved, sv.name); saveUI(); draw(); toast(`Deleted view ${sv.name}`); } }, "✕")));
    };
    const name = el("input", { type: "text", placeholder: "Name this view…", maxlength: 60, "aria-label": "Saved view name" });
    const save = () => { if (!name.value.trim()) { name.focus(); return; } st.saved = V.saveView(st.saved, name.value, st); saveUI(); toast(`Saved view “${name.value.trim()}”`); name.value = ""; draw(); };
    name.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); save(); } });
    draw();
    pop.append(el("div", { class: "muted", style: "font-size:12px;margin-bottom:6px", text: "Saves this track's current view, filters, sort, search and salary range." }), list,
      el("div", { class: "row" }, name, el("button", { class: "btn small primary", type: "button", onclick: save }, "Save current")));
    wrap.append(pop); openPop = pop; $("#saved-btn").setAttribute("aria-expanded", "true"); setTimeout(() => name.focus(), 0);
  });

  // ---------- track tabs, toolbar ----------
  for (const b of document.querySelectorAll(".track-tab")) b.addEventListener("click", () => (b.dataset.page ? switchPage(b.dataset.page) : switchTrack(b.dataset.track)));
  // arrow keys move between the four top tabs (one tablist)
  $(".track-tabs").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key) || !e.target.classList.contains("track-tab")) return;
    const tabs = [...document.querySelectorAll(".track-tab")]; let i = tabs.indexOf(e.target);
    i = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    e.preventDefault(); tabs[i].focus(); tabs[i].click();
  });
  function switchTrack(t) {
    const onApps = ["applications", "stats", "followups"].includes(tstate().view);
    const v = tstate().view;
    const fromPage = ui.page !== "jobs";
    ui.track = t; ui.page = "jobs"; if (onApps) tstate().view = v; saveUI();
    if (fromPage) renderBanners();
    $("#q").value = tstate().q || ""; closePop(); renderAll();
    for (const n of [$("#main"), $("#tiles")]) { n.classList.remove("swap-in"); void n.offsetWidth; n.classList.add("swap-in"); }
  }
  // URL hash: #coop | #fulltime [/view] [?label=Strong|Stretch&q=…&sort=newest&salary=120000]; #applications[/coop|/fulltime]
  function writeHash() {
    const st = tstate(); const v = st.view || defaultView(); const tr = ui.track === "coop_intern" ? "coop" : "fulltime";
    let h;
    if (PAGES[ui.page]) h = `#${ui.page}`;
    else if (v === "applications") h = `#applications/${tr}`;
    else {
      const p = new URLSearchParams();
      for (const f of FILTERS) { const vals = st.filters[f.id]; if (vals && vals.length) p.set(f.id, vals.join("|")); }
      if (st.q) p.set("q", st.q); if (st.sort && st.sort !== "overall") p.set("sort", st.sort); if (st.salary) p.set("salary", st.salary);
      if (st.salaryMax) p.set("salary_max", st.salaryMax);
      if (st.salaryKnown) p.set("salary_known", "1");
      if (pJob) { p.set("job", pJob.id); if (pTab !== "Overview") p.set("tab", pTab); }
      const qs = p.toString(); h = `#${tr}${v !== defaultView() || qs ? `/${v}` : ""}${qs ? `?${qs}` : ""}`;
    }
    if (location.hash !== h) history.replaceState(null, "", h);
  }
  let bootJob = null; let bootTab = null;
  function readHash() {
    if (/^#(bucket|builder)$/.test(location.hash || "")) { ui.page = location.hash.slice(1); return; }
    const m = /^#(coop|fulltime|applications)(?:\/([a-z0-9]+))?(?:\?(.*))?$/.exec(location.hash || ""); if (!m) return;
    ui.page = "jobs";
    if (m[1] === "applications") { if (m[2] === "coop" || m[2] === "fulltime") ui.track = m[2] === "coop" ? "coop_intern" : "full_time"; tstate().view = "applications"; return; }
    ui.track = m[1] === "coop" ? "coop_intern" : "full_time";
    const st = tstate();
    if (m[2] && VIEWS.some((v) => v.id === m[2])) st.view = m[2];
    if (m[3] !== undefined) {
      const p = new URLSearchParams(m[3]); st.filters = {};
      for (const f of FILTERS) if (p.get(f.id)) st.filters[f.id] = p.get(f.id).split("|");
      st.q = p.get("q") || ""; if (p.get("sort") && SORTS[p.get("sort")]) st.sort = p.get("sort");
      st.salary = Number(p.get("salary")) || 0; st.salaryMax = Number(p.get("salary_max")) || 0; st.salaryKnown = p.get("salary_known") === "1";
      if (p.get("job")) { bootJob = Number(p.get("job")); bootTab = TABS.includes(p.get("tab")) ? p.get("tab") : null; }
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
      ...manualRows().map(({ r }) => ({ kind: "Manual check", label: r.company, sub: "Mark checked today", run: () => markManualChecked(r.company) })),
      { kind: "Action", label: "Open the resume queue", run: () => openQueuePanel() },
      { kind: "Action", label: "Open the Bucket", sub: `${bucketItems().length} jobs`, run: () => switchPage("bucket") },
      { kind: "Action", label: "Generate resumes for all Bucket jobs", run: () => generateAll(false) },
      { kind: "Action", label: "Add application", run: () => openAddApp() },
      { kind: "Action", label: "Resume Builder: add an external job", run: () => switchPage("builder") },
      { kind: "Action", label: "Switch to Co-op / Internships", run: () => switchTrack("coop_intern") },
      { kind: "Action", label: "Switch to Full-time", run: () => switchTrack("full_time") },
      { kind: "Action", label: "Toggle light / dark theme", run: () => $("#theme").click() },
      { kind: "Action", label: "Cards / table", run: () => $("#mode-toggle").click() },
      { kind: "Action", label: "Compact density", run: () => $("#density").click() },
      { kind: "Action", label: "Clear all filters", run: () => { st.filters = {}; st.q = ""; st.salary = 0; st.salaryMax = 0; st.salaryKnown = false; $("#q").value = ""; ui.day = null; saveUI(); renderAll(); } },
      { kind: "Action", label: "Keyboard shortcuts", run: () => $("#help").showModal() },
    ];
    for (const sv of st.saved || []) items.push({ kind: "Saved view", label: sv.name, sub: TRACKS[ui.track], run: () => loadSavedView(sv) });
    const byCo = counts(JOBS.filter((j) => !j.filtered).map((j) => j.company));
    for (const [c, n] of byCo) items.push({ kind: "Company", label: c, sub: `${n} job${n > 1 ? "s" : ""}`, run: () => { const t = tstate(); t.filters = { company: [c] }; t.q = ""; $("#q").value = ""; saveUI(); setView("all"); } });
    for (const a of liveApps()) items.push({ kind: "Application", label: a.title, sub: `${a.company} · ${STAGE_LABEL[a.stage]}`, run: () => openDrawer(a.id) });
    return items;
  }
  // every job (search.js, written by each run; loaded the first time the palette opens): both tracks, every view,
  // Filtered Out, beyond the top N, closed. Until it loads (or if it is missing) the palette searches the loaded jobs.
  let SEARCH = null; let searchState = "idle"; const searchWaiters = [];
  const flushWaiters = () => { for (const f of searchWaiters.splice(0)) f(); };
  function loadSearch() {
    if (searchState !== "idle") return;
    searchState = "loading";
    window.__search = (d) => { SEARCH = d; searchState = "ready"; flushWaiters(); if ($("#palette").open) drawPalette($("#palette-q").value); };
    const s = el("script", { src: "search.js" });
    s.onerror = () => { searchState = "missing"; flushWaiters(); };
    document.head.append(s);
  }
  let JOBS_INDEX = null;
  function jobsIndex() {        // the same shape as search.js, from the jobs this page has loaded
    if (JOBS_INDEX) return JOBS_INDEX;
    const cos = new Map(); const rows = [];
    for (const j of JOBS) {
      if (!cos.has(j.company)) cos.set(j.company, cos.size);
      rows.push([j.id, cos.get(j.company), j.title, { coop_intern: 0, full_time: 1, unclear: 2 }[trackOf(j)] ?? 1, j.filtered ? "f" : "v", -1, ""]);
    }
    JOBS_INDEX = { companies: [...cos.keys()], reasons: [], rows };
    return JOBS_INDEX;
  }
  function openFromPalette(res) {
    if (!BY_ID.get(res.id)) { showJobInfo(res); return; }
    openJob(res.id);
  }
  // Open any job the way ⌘K does: its track tab and view, filters / search cleared (with a notice) when they would hide
  // it, its card focused in the list, the detail panel on `tab` (Overview by default). `from`: opened from inside the
  // panel (the current job goes on the Back trail); `back`: returning along it. A job the dashboard holds no details
  // for (closed, below the top N, unlisted) shows the ⌘K info card instead.
  function openJob(id, o = {}) {
    const j = BY_ID.get(id);
    if (!j) { showMissingJob(id); return; }
    if (o.from && pJob && pJob.id !== id) navStack.push({ id: pJob.id, tab: pTab });
    else if (!o.from && !o.back) navStack = [];
    revealJob(j);
    navOpening = true;
    try { openPanel(j.id, o.tab || "Overview"); } finally { navOpening = false; }
  }
  function showMissingJob(id) {
    const show = () => showJobInfo(V.resultById(SEARCH, id) || { id, title: `Job ${id}`, company: "Not in this dashboard", trackLabel: "", state: "",
      view: "Not in the loaded jobs or the search index" });
    if (SEARCH || searchState === "missing") { show(); return; }
    searchWaiters.push(show); loadSearch();
  }
  function revealJob(j) {
    const res = { track: trackOf(j) };
    const tgt = V.openTarget(res, ui.track);
    if (tgt.track !== ui.track) switchTrack(tgt.track);
    const want = j.filtered ? "filtered" : (["all", "last24", "apply", "mine", "external", "closing", "missed", "tracker",
      "shortlisted", "resumes"].includes(tstate().view) ? tstate().view : "all");
    const st = tstate();
    const inView = (v) => viewById(v).jobs && viewById(v).jobs().some((x) => x.id === j.id);
    st.view = inView(want) ? want : j.filtered ? "filtered" : "all";
    if (!applyFilters(viewById(st.view).jobs()).some((x) => x.id === j.id)) {
      st.filters = {}; st.q = ""; st.salary = 0; st.salaryMax = 0; st.salaryKnown = false; ui.day = null; $("#q").value = "";
      toast("Filters cleared to show this job");
    }
    saveUI(); renderBanners(); renderAll();
    const i = current.findIndex((x) => x.id === j.id);
    if (vlist && i >= 0) vlist.ensureVisible(i);
  }
  function showJobInfo(res) {            // closed / unlisted jobs: the dashboard holds no details for them
    const d = $("#job-info");
    $("#job-info-title").textContent = res.title;
    $("#job-info-sub").textContent = `${res.company} · ${res.trackLabel}`;
    const body = $("#job-info-body"); body.textContent = "";
    const where = { c: `Closed${res.date ? ` on ${fmtDate(res.date)}` : ""}: the posting disappeared from its source.`,
      u: "Filtered out by your rules and not in the Filtered Out list (it lists the most relevant ones).",
      b: "Open and scored, but below the top N jobs per track that the dashboard shows." }[res.state] || res.view;
    body.append(facts([["Where", res.view], ["Status", where], res.reason ? ["Filter reason", res.reason] : null,
      ["HireScope ID", String(res.id)]]),
      el("p", { class: "muted", style: "font-size:12px", text: "Full details aren't loaded for this job. You can still queue its resume." }),
      el("button", { class: "btn small", type: "button", onclick: () => { $("#job-info").close(); queueResume({ id: res.id, company: res.company, title: res.title }, false); } }, "Generate Resume"));
    $("#job-info-li").href = linkedinSearch(res.company, res.title);
    const jb = $("#job-info-bucket"); jb.textContent = "";
    const stub = { id: res.id, company: res.company, title: res.title, track: res.track || "full_time", job_status: res.state === "c" ? "closed" : "open" };
    jb.append(bucketBox(stub), "Add to Bucket (kept even if the posting closed)");
    d.showModal(); $("#job-info-close").focus();
  }
  $("#job-info-close").addEventListener("click", () => $("#job-info").close());
  const linkedinSearch = (co, t) => "https://www.linkedin.com/jobs/search/?keywords=" + encodeURIComponent(`${co} ${t}`);
  function openPalette() {
    const d = $("#palette"); if (d.open) return;
    loadSearch();
    const q = $("#palette-q"); q.value = ""; d.showModal(); drawPalette(""); q.focus();
  }
  function drawPalette(qs) {
    const words = qs.toLowerCase().split(/\s+/).filter(Boolean);
    const hit = (x) => words.every((w) => `${x.label} ${x.sub || ""} ${x.kind}`.toLowerCase().includes(w));
    let items = paletteSource().filter(hit);
    if (words.length) {
      const found = V.searchJobs(SEARCH || jobsIndex(), qs, ui.track, 50);
      items = items.concat(found.map((r) => {
        const j = BY_ID.get(r.id);
        const track = j ? TRACKS[trackOf(j)] : r.trackLabel;
        return { kind: r.state === "c" ? "Closed job" : "Job", label: r.title,
          sub: `${r.company} · ${track} · ${r.view}${j && j.score != null ? ` · ${Math.round(j.score)}` : ""}`,
          run: () => openFromPalette(r) };
      }));
      if (searchState === "loading") items.push({ kind: "Note", label: "Loading every job (closed, filtered, beyond the top N)…", run: () => {} });
      if (searchState === "missing") items.push({ kind: "Note", label: "search.js not found: searching the loaded jobs only (the next run writes it)", run: () => {} });
    } else items = items.filter((x) => x.kind === "View" || x.kind === "Action" || x.kind === "Saved view");
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
      if (vlist) vlist.ensureVisible(current.indexOf(n));
      const node = document.querySelector(`#list [data-id="${n.id}"]`);
      if (node) { node.classList.add("selected"); node.scrollIntoView({ block: "nearest" }); }
      if (pJob) openPanel(n.id);
    };
    const sel = BY_ID.get(selectedId);
    switch (e.key) {
      case "j": move(1); break;
      case "k": move(-1); break;
      case "Enter": if (sel && tag !== "button" && tag !== "a") openPanel(sel.id); else return; break;
      case "Escape": if (!$("#queue-panel").hidden) closeQueuePanel(); else if (pJob) closePanel(); else closePop(); break;
      case "s": if (sel) shortlist(sel); break;
      case "a": if (sel && !sel.filtered) markApplied(sel); break;
      case " ": if (sel && tag !== "button" && tag !== "a") { e.preventDefault(); setBucket(sel, !inBucket(sel.id)); } else return; break;
      case "x": if (sel) openSkipPicker(sel); break;
      case "o": if (sel && safeUrl(sel.apply_url)) window.open(sel.apply_url, "_blank", "noopener"); break;
      case "g": if (sel && !sel.filtered) queueResume(sel, false); break;
      case "/": e.preventDefault(); $("#q").focus(); break;
      case "?": $("#help").showModal(); break;
      case "1": switchTrack("coop_intern"); break;
      case "2": switchTrack("full_time"); break;
      case "3": switchPage("bucket"); break;
      case "4": switchPage("builder"); break;
      default: return;
    }
  });

  // ---------- boot ----------
  function renderAll(keepScroll) {
    const jobsPage = !PAGES[ui.page];
    $(".day-row").hidden = !jobsPage; $("#tiles").hidden = !jobsPage;
    renderCounts(); if (jobsPage) { renderDayTabs(); renderTiles(); } renderList(keepScroll);
  }
  if (!["coop_intern", "full_time"].includes(ui.track)) ui.track = "full_time";
  readHash();
  $("#q").value = tstate().q || "";
  mapResumeStatuses();
  renderHeader(); renderBanners();
  if (!JOBS.length) {
    renderCounts();
    $("#list").append(el("div", { class: "empty" }, el("b", { text: "No jobs yet." }), el("div", { text: "Run ./scripts/run.sh, then reload this page." })));
  } else {
    renderAll();
    if (bootJob != null && BY_ID.has(bootJob)) openPanel(bootJob, bootTab);
  }
  pollLive(false); renderQueuePanel();
})();
